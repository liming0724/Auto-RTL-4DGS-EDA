"""Z3 query engine shared by verify_z3.py and cegar_to_verilator.py.

Loads rtl_to_netlist.py output through eda_core (parser.VerilogParser builds the
Z3 expressions, fast_eda.FastGraph computes cones of influence) and runs every
proof obligation through the DEVIN.md circuit breakers: 30 s per query, at most
3 consecutive attempts per cone, per-output-bit COI slicing when the monolithic
query is unknown, then bounded model checking at k = 20 for a slice that still
times out.
"""
import json
from pathlib import Path
import sys
import time

import z3

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "eda_core"), str(ROOT / "build/eda")]
sys.setrecursionlimit(1 << 16)
import fast_eda  # noqa: E402  pylint: disable=wrong-import-position
from parser import LogicAnalyzer, VerilogParser  # noqa: E402  pylint: disable=wrong-import-position

Z3_TIMEOUT_MS = 30000
MAX_RETRIES = 3
BMC_DEPTH = 20


class Netlist:
    """A flattened target: parser Design + FastGraph + rtl_to_netlist manifest."""

    def __init__(self, netdir, target):
        netdir = Path(netdir)
        self.target = target
        self.manifest = json.loads((netdir / f"{target}.json").read_text(encoding="utf-8"))
        self.design = VerilogParser().parse(str(netdir / f"{target}.v"))
        self.analyzer = LogicAnalyzer(self.design)
        self.graph = fast_eda.FastGraph()
        self.gates = 0
        for gate in self.design.gates.values():
            if gate.gate_type != "dff":
                self.gates += 1
                self.graph.add_net_driver(gate.output_nets[0].name, gate.inst_name,
                                          [n.name for n in gate.input_nets])
        self.regs = {r["q"]: r for r in self.manifest["registers"]}
        self.vars, self.cache = {}, {}

    def nets(self, name):
        """LSB-first net names of an RTL signal (port, register or wire)."""
        if name in self.manifest["ports"]:
            return list(self.manifest["ports"][name]["bits"])
        bits = self.manifest["aliases"].get(name)
        if bits is None or None in bits:
            raise KeyError(f"{self.target}: no netlist bits for RTL signal {name}")
        return list(bits)

    def next_nets(self, name):
        """D-input nets of a register (the RTL `<name>_next` cone)."""
        return [self.regs[q]["d"] if q in self.regs else q for q in self.nets(name)]

    def expr(self, net, cache=None):
        if net in ("0", "1"):
            return z3.BoolVal(net == "1")
        return self.analyzer.build_z3_expr(net, self.vars, self.cache if cache is None else cache)

    def bv(self, nets, cache=None):
        bits = [z3.If(self.expr(n, cache), z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)) for n in nets]
        return bits[0] if len(bits) == 1 else z3.Concat(*reversed(bits))

    def cone(self, roots):
        roots = [r for r in roots if r not in ("0", "1")]
        return self.graph.cone_of_influence(roots) if roots else ([], [])

    def seq_cone(self, roots):
        """Registers in the sequential COI of `roots` (fixed point over D cones)."""
        regs, frontier = set(), list(roots)
        while frontier:
            _, leaves = self.cone(frontier)
            frontier = []
            for leaf in leaves:
                if leaf in self.regs and leaf not in regs:
                    regs.add(leaf)
                    frontier.append(self.regs[leaf]["d"])
        return regs


class Unrolled:
    """Time-frame expansion from reset for one or more netlists sharing primary inputs."""

    def __init__(self, members, roots, depth):
        # members: [(netlist, tag)]; roots: {tag: [nets]} whose behaviour must be modeled
        self.members, self.depth, self.constraints, self.leaf_cache = members, depth, [], {}
        self.regs = {tag: sorted(nl.seq_cone(roots[tag])) for nl, tag in members}
        for nl, tag in members:
            inputs = {b for p in nl.manifest["ports"].values() if p["direction"] == "input" for b in p["bits"]}
            self.shared = inputs
            resets = {r["reset"]: r["reset_active_low"] for r in nl.regs.values() if "reset" in r}
            for q in self.regs[tag]:
                rv = nl.regs[q]["reset_value"]
                if rv is not None:
                    self.constraints.append(self.var(tag, q, 0) == z3.BoolVal(bool(rv)))
            for t in range(depth + 1):
                for net, active_low in resets.items():
                    self.constraints.append(self.var(tag, net, t) == z3.BoolVal(active_low))
            for t in range(depth):
                for q in self.regs[tag]:
                    d = self.at(nl, tag, nl.expr(nl.regs[q]["d"]), t)
                    self.constraints.append(self.var(tag, q, t + 1) == d)

    def var(self, tag, name, t):
        return z3.Bool(f"{name}@{t}" if name in self.shared else f"{tag}{name}@{t}")

    def at(self, nl, tag, expr, t):
        """`expr` (over leaf nets of `nl`) evaluated in time frame t."""
        if expr.get_id() not in self.leaf_cache:
            self.leaf_cache[expr.get_id()] = (expr, z3_leaves(expr))
        leaves = self.leaf_cache[expr.get_id()][1]
        return z3.substitute(expr, *[(v, self.var(tag, v.decl().name(), t)) for v in leaves]) if leaves else expr


def z3_leaves(expr):
    seen, out, stack = set(), [], [expr]
    while stack:
        e = stack.pop()
        if e.get_id() in seen:
            continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out.append(e)
        stack.extend(e.children())
    return out


class Breaker:
    """Runs obligations under the 30 s / 3-retry / COI / BMC-20 policy and logs every query."""

    def __init__(self, log, timeout_ms=Z3_TIMEOUT_MS, drill=None):
        self.log, self.timeout_ms, self.drill = log, timeout_ms, drill or {}
        self.queries = 0

    def say(self, msg):
        self.log.write(msg + "\n")
        self.log.flush()
        print(msg, flush=True)

    def solve(self, formulas, label, limits=None):
        """At most MAX_RETRIES attempts; each attempt is one Z3 query with the hard timeout."""
        for attempt in range(MAX_RETRIES):
            solver = z3.Solver() if attempt != 2 else z3.SolverFor("QF_BV")
            solver.set("timeout", self.timeout_ms)
            solver.set("random_seed", attempt)
            if limits:  # breaker drill: a deterministic resource limit forces `unknown`
                solver.set("rlimit", limits)
            solver.add(*formulas)
            start = time.monotonic()
            result = solver.check()
            elapsed = time.monotonic() - start
            self.queries += 1
            reason = f" ({solver.reason_unknown()})" if result == z3.unknown else ""
            self.say(f"    query {label} attempt {attempt + 1}/{MAX_RETRIES}: {result}{reason} in {elapsed:.2f}s")
            if result != z3.unknown:
                return result, solver.model() if result == z3.sat else None, elapsed
        return z3.unknown, None, None

    def check(self, nl, name, cones, assume=(), bmc=True):
        """cones: [(label, roots, violation(cache) -> BoolRef)]. Returns a result dict."""
        start = time.monotonic()
        self.say(f"[{nl.target}] {name}: {len(cones)} output cone(s)")
        mono = z3.Or(*[viol(None) for _, _, viol in cones])
        result, model, _ = self.solve([*assume, mono], "monolithic", self.drill.get("monolithic"))
        how, witness = "monolithic", None
        if result == z3.sat:
            witness = (model, [lab for lab, _, viol in cones if z3.is_true(model.eval(viol(None), True))])
        elif result == z3.unknown:
            how, result = "COI slices", z3.unsat
            self.say(f"  monolithic query unknown after {MAX_RETRIES} attempts -> COI slicing per output bit")
            for label, roots, viol in cones:
                cone, leaves = nl.cone(roots)
                self.say(f"  slice {label}: {len(cone)} nets, {len(leaves)} leaves (fast_eda COI)")
                r, m, _ = self.solve([*assume, viol({})], f"slice {label}", self.drill.get(label))
                if r == z3.unknown and bmc:
                    self.say(f"  BOTTLENECK slice {label} ({len(cone)} nets) still unknown -> BMC k={BMC_DEPTH}")
                    how = f"COI slices + BMC-{BMC_DEPTH}"
                    r, m = self.bmc(nl, label, roots, viol, assume)
                    if r == z3.unsat:
                        r = "bounded"
                if r == z3.sat:
                    result, witness = z3.sat, (m, [label])
                    break
                if r == z3.unknown:
                    result = z3.unknown
                    break
                if r == "bounded":
                    result = "bounded"
        status = "BOUNDED" if result == "bounded" else str(result).upper()
        out = dict(name=name, target=nl.target, status=status, method=how,
                   seconds=round(time.monotonic() - start, 2), cones=len(cones))
        if witness:
            out["witness"], out["failing_cones"] = witness
        return out

    def bmc(self, nl, label, roots, viol, assume):
        """Depth-20 BMC from reset: is the violation reachable within BMC_DEPTH cycles?"""
        unrolled = Unrolled([(nl, "")], {"": list(roots)}, BMC_DEPTH)
        bad = z3.Or(*[unrolled.at(nl, "", z3.And(*assume, viol(None)), t) for t in range(BMC_DEPTH)])
        result, model, _ = self.solve([*unrolled.constraints, bad], f"bmc-{BMC_DEPTH} {label}")
        return result, model


def bits_of(model, bools):
    """Integer value of LSB-first Bool terms in a model."""
    return sum(1 << i for i, b in enumerate(bools) if z3.is_true(model.eval(b, model_completion=True)))


GATE_EVAL = {"and": all, "or": any, "nand": lambda v: not all(v), "nor": lambda v: not any(v),
             "xor": lambda v: sum(v) % 2 == 1, "xnor": lambda v: sum(v) % 2 == 0,
             "not": lambda v: not v[0], "buf": lambda v: v[0]}


def simulate(nl, frames):
    """Cycle-accurate two-valued simulation from reset (resets held inactive).

    frames: per-cycle {primary input net: bool}. Returns the net values of each
    frame 0..len(frames); frame t+1 holds the register outputs after edge t.
    """
    comb = [g for g in nl.design.gates.values() if g.gate_type != "dff"]
    driver = {g.output_nets[0].name: g for g in comb}
    order, done = [], set()
    for root in driver:
        stack = [(root, False)]
        while stack:
            net, expanded = stack.pop()
            if net in done or net not in driver:
                continue
            if expanded:
                done.add(net)
                order.append(driver[net])
                continue
            stack.append((net, True))
            stack.extend((n.name, False) for n in driver[net].input_nets if n.name not in done)
    resets = {r["reset"]: r["reset_active_low"] for r in nl.regs.values() if "reset" in r}
    state = {q: bool(r["reset_value"]) for q, r in nl.regs.items()}
    values = []
    for inputs in [*frames, {}]:
        v = {"1'b0": False, "1'b1": True, "1'h0": False, "1'h1": True, **state, **resets}
        v.update({n: inputs.get(n, False) for n in nl.manifest["ports"] and
                  [b for p in nl.manifest["ports"].values() if p["direction"] == "input" for b in p["bits"]]
                  if n not in resets})
        for gate in order:
            v[gate.output_nets[0].name] = GATE_EVAL[gate.gate_type]([v[n.name] for n in gate.input_nets])
        values.append(v)
        state = {q: v[r["d"]] for q, r in nl.regs.items()}
    return values
