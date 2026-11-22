#!/usr/bin/env python3
"""Tier-3 Z3 proofs for the Phase 3 rtl_4dgs modules (netlists from rtl_to_netlist.py,
loaded through eda_core, 30 s / 3-retry / COI-slicing / BMC-20 circuit breaker).

Targets (small formal configurations, see rtl_to_netlist.py G4_TARGETS):
  gs4d_skip_unit  W=16: |.| stages, partial sums, delta and skip_deform bit-true to the L1
                  spec (composed stage-wise); motion_thresh == 0
                  never skips
  gs4d_cache      CACHE_SETS=4, CACHE_WAYS=2, DATA_WIDTH=8, TAG_WIDTH=2: inductive
                  coherency invariants (no duplicate tags, consistent write selection,
                  response == array), hit/miss responses == post-write array under
                  simultaneous lookup + write-back, write locality
  gs4d_top        NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2, CACHE_SETS=4, ID_W=4: lossless skip
                  equivalence (motion_thresh == 0 -> no skip, and every datapath
                  register / output is independent of the cache contents, i.e. equal
                  to full evaluation), backpressure freeze (no state, output or cache
                  change while out_ready == 0), legal FSM, ranking function (no
                  deadlock through PROJ/DIV/INV), BMC-20 safety from reset.
                  Full recompute latency exceeds 20: liveness is proved by the
                  unbounded inductive control invariant and decreasing rank, not BMC.
"""
import argparse
import json
from pathlib import Path
import re
import time

import z3

from verify_z3 import b1, verdict
from z3_engine import BMC_DEPTH, Breaker, Netlist, ROOT, Unrolled

BV = z3.BitVecVal
SETS, WAYS, DW, TAG = 4, 2, 8, 2
WORD = DW + TAG
SKIP_W = 16
S_ACCEPT, S_LOOKUP, S_PROJ, S_RASTER, S_OUT, S_DIV, S_INV = range(7)
RASTER_CYCLES = 4  # TILE / NUM_PE
DIV_BITS = 17  # 2*Q_FRAC+1, Q_FRAC=8 in gs4d_top
CONTROL = ("state", "cnt", "phase", "div_count")


def bit(v, i):
    return z3.Extract(i, i, v)


# ---------------------------------------------------------------- skip unit
def skip_props(nl):
    """Stage-wise bit-true proof through the kept nets mag_* / sum_mu / sum_s (avoids one
    4-operand adder miter); the stages compose to delta == |dmu_x|+|dmu_y|+|ds_x|+|ds_y|."""
    cur = lambda n, c: nl.bv(nl.nets(n), c)
    mag = lambda x: z3.If(bit(x, SKIP_W - 1) == 1, -x, x)
    z1 = lambda x: z3.ZeroExt(1, x)
    stages = [(f"mag_{n}", (lambda n: lambda c: mag(cur(n, c)))(n), SKIP_W) for n in ("dmu_x", "dmu_y", "ds_x", "ds_y")]
    stages += [("sum_mu", lambda c: z1(cur("mag_dmu_x", c)) + z1(cur("mag_dmu_y", c)), SKIP_W + 1),
               ("sum_s", lambda c: z1(cur("mag_ds_x", c)) + z1(cur("mag_ds_y", c)), SKIP_W + 1),
               ("delta", lambda c: z1(cur("sum_mu", c)) + z1(cur("sum_s", c)), SKIP_W + 2)]
    checks = [(f"{net} == {'|' + net[4:] + '|' if net.startswith('mag_') else 'sum of its two operands'} (bit-true)",
               "unsat", [(f"{net}[{i}]", [nl.nets(net)[i]],
                          (lambda net, spec, i: lambda c: bit(cur(net, c), i) != bit(spec(c), i))(net, spec, i))
                         for i in range(width)])
              for net, spec, width in stages]
    roots = nl.nets("skip_deform")
    return checks + [
        ("skip_deform == (delta < motion_thresh)", "unsat", [("skip_deform", roots, lambda c: cur(
            "skip_deform", c) != b1(z3.ULT(cur("delta", c), cur("motion_thresh", c))))]),
        ("lossless: motion_thresh == 0 -> skip_deform == 0", "unsat", [("skip_deform", roots, lambda c: z3.And(
            cur("motion_thresh", c) == 0, cur("skip_deform", c) == 1))]),
        ("non-vacuity witness: motion_thresh == 16 can skip", "sat", [("skip_deform", roots, lambda c: z3.And(
            cur("motion_thresh", c) == 16, cur("skip_deform", c) == 1))]),
    ]


# ---------------------------------------------------------------- cache
class CacheView:
    """Array / valid view of motion_aware_cache state (current or next) in a netlist."""

    def __init__(self, nl, prefix, nxt):
        self.nl, self.p, self.nxt = nl, prefix, nxt

    def get(self, name, c):
        nets = self.nl.next_nets(self.p + name) if self.nxt else self.nl.nets(self.p + name)
        return self.nl.bv(nets, c)

    def word(self, s, w, c):
        return z3.Extract(s * WORD + WORD - 1, s * WORD, self.get(f"g_way_{w}__u_sram_rows", c))

    def valid(self, s, w, c):
        return bit(self.get("valid_q", c), s * WAYS + w) == 1

    def tag(self, s, w, c):
        return z3.Extract(WORD - 1, DW, self.word(s, w, c))

    def at_set(self, set_bv, fn):
        """fn(s) evaluated at a symbolic set index."""
        out = fn(SETS - 1)
        for s in range(SETS - 2, -1, -1):
            out = z3.If(set_bv == s, fn(s), out)
        return out


def cache_inv(v, c):
    """I: no duplicate tags; J: write selection consistent; K: response == array."""
    g = lambda n: v.get(n, c)
    inv_i = z3.And(*[z3.Not(z3.And(v.valid(s, 0, c), v.valid(s, 1, c), v.tag(s, 0, c) == v.tag(s, 1, c)))
                     for s in range(SETS)])
    inv_j = z3.Implies(g("sel_valid") == 1, z3.And(*[
        v.at_set(g("sel_set"), lambda s, w=w: z3.Implies(
            z3.And(v.valid(s, w, c), v.tag(s, w, c) == g("sel_tag")), g("sel_way") == w))
        for w in range(WAYS)]))
    eff = lambda w: z3.If(z3.And(g("fwd_valid") == 1, g("fwd_way") == w), g("fwd_word"), g(f"q_word_{w}"))
    inv_k = z3.Implies(g("resp_q") == 1, z3.And(*[
        v.at_set(g("resp_set"), lambda s, w=w: eff(w) == v.word(s, w, c)) for w in range(WAYS)]))
    return inv_i, inv_j, inv_k


def cache_props(nl):
    cur, nxt = CacheView(nl, "", False), CacheView(nl, "", True)
    g = lambda n, c: nl.bv(nl.nets(n), c)
    state_roots = [r["d"] for r in nl.regs.values()]
    out_roots = nl.nets("lk_hit") + nl.nets("lk_rdata")
    inv = lambda c: z3.And(*cache_inv(cur, c))
    reset = [nl.expr(q) == z3.BoolVal(bool(r["reset_value"]))
             for q, r in nl.regs.items() if r["reset_value"] is not None]

    def post(s, w, c):  # post-write entry (next state of the array)
        return nxt.valid(s, w, c), nxt.tag(s, w, c), z3.Extract(DW - 1, 0, nxt.word(s, w, c))

    def hit_ok(c):
        rs, rt = g("resp_set", c), g("resp_tag", c)
        found = z3.Or(*[cur.at_set(rs, lambda s, w=w: z3.And(
            post(s, w, c)[0], post(s, w, c)[1] == rt, post(s, w, c)[2] == g("lk_rdata", c))) for w in range(WAYS)])
        absent = z3.And(*[cur.at_set(rs, lambda s, w=w: z3.Not(z3.And(post(s, w, c)[0], post(s, w, c)[1] == rt)))
                          for w in range(WAYS)])
        return z3.If(g("lk_hit", c) == 1, found, z3.Implies(g("lk_resp", c) == 1, absent))

    def locality(c):
        fire = z3.And(g("wr_en", c) == 1, g("sel_valid", c) == 1)
        terms = []
        for s in range(SETS):
            for w in range(WAYS):
                tgt = z3.And(fire, g("sel_set", c) == s, g("sel_way", c) == w)
                terms.append(z3.If(tgt, z3.And(nxt.valid(s, w, c), nxt.word(s, w, c) == z3.Concat(
                    g("sel_tag", c), g("wr_data", c))), z3.And(nxt.valid(s, w, c) == cur.valid(s, w, c),
                                                              nxt.word(s, w, c) == cur.word(s, w, c))))
        return z3.And(*terms)

    names = ("I: no tag stored twice in a set", "J: write selection is the unique way for its tag",
             "K: lookup response equals the array (forwarding)")
    checks = [("base: invariants I, J, K hold in the reset state", "unsat", [
        ("reset", state_roots, lambda c: z3.And(*reset, z3.Not(inv(c))))])]
    for i, name in enumerate(names):
        checks.append((f"inductive {name}", "unsat", [
            (name.split(":")[0], state_roots, (lambda i: lambda c: z3.And(inv(c), z3.Not(cache_inv(nxt, c)[i])))(i))]))
    checks += [
        ("coherency: hit data / miss == post-write array under simultaneous lookup + write", "unsat", [
            ("lk_hit/lk_rdata", out_roots + state_roots, lambda c: z3.And(inv(c), z3.Not(hit_ok(c))))]),
        ("write locality: only (sel_set, sel_way) changes, with {sel_tag, wr_data}", "unsat", [
            ("array_next", state_roots, lambda c: z3.Not(locality(c)))]),
        ("non-vacuity witness: a hit coincident with a write-back is reachable under I, J, K", "sat", [
            ("lk_hit", out_roots + state_roots, lambda c: z3.And(inv(c), g("lk_hit", c) == 1, g("wr_en", c) == 1,
                                                               g("sel_valid", c) == 1))]),
    ]
    return checks


# ---------------------------------------------------------------- accelerator top
def control_inv(get, c):
    s, k, p, d = (get(n, c) for n in CONTROL)
    return z3.And(z3.ULE(s, S_INV),
                  z3.Implies(s == S_RASTER, z3.ULE(k, RASTER_CYCLES-1)),
                  z3.Implies(s == S_PROJ, z3.Or(z3.ULE(p, 5), z3.And(z3.UGE(p, 7), z3.ULE(p, 9)))),
                  z3.Implies(s == S_DIV, z3.And(p == 6, z3.ULT(d, DIV_BITS))),
                  z3.Implies(s == S_INV, z3.And(p == 6, d == DIV_BITS)))


def progress_rank(get, c):
    s, k, p, d = (get(n, c) for n in CONTROL)
    widen = lambda x: z3.ZeroExt(8-x.size(), x)
    return z3.If(s == S_LOOKUP, BV(32, 8), z3.If(s == S_PROJ,
                 z3.If(z3.ULE(p, 5), BV(31, 8)-widen(p), BV(14, 8)-widen(p)),
                 z3.If(s == S_DIV, BV(25, 8)-widen(d), z3.If(s == S_INV, BV(8, 8),
                 z3.If(s == S_RASTER, BV(RASTER_CYCLES, 8)-widen(k), BV(0, 8))))))


def top_props(nl):
    cur = lambda n, c: nl.bv(nl.nets(n), c)
    nxt = lambda n, c: nl.bv(nl.next_nets(n), c)
    state = lambda c: cur("state", c)
    state_d = nl.next_nets("state")
    cache_q = sorted(q for q in nl.regs if q.startswith("u_cache_"))
    alt = [(z3.Bool(q), z3.Bool(q + "#alt")) for q in cache_q]
    groups = {}
    for q in nl.regs:
        if not q.startswith("u_cache_"):
            groups.setdefault(re.sub(r"__\d+__$", "", q), []).append(q)
    lossless = lambda c: z3.And(cur("motion_thresh", c) == 0, cur("skip_q", c) == 0, cur("out_skipped", c) == 0)

    def noninterference(name):
        d = [nl.regs[q]["d"] for q in sorted(groups[name])]
        def viol(c):
            e = nl.bv(d, c)
            return z3.And(lossless(c), e != z3.substitute(e, *alt))
        return (f"{name}_next", d, viol)

    out_regs = [n for n in ("out_id", "out_skipped", "out_delta", "out_mean_x", "out_mean_y", "out_cov_a",
                            "out_cov_b", "out_cov_c", "out_opacity", "out_alpha")]
    cache_state = ["u_cache_valid_q", "u_cache_rr_q"] + [f"u_cache_g_way_{w}__u_sram_rows" for w in range(WAYS)]
    stall = lambda c: z3.And(state(c) == S_OUT, cur("out_ready", c) == 0)

    busy = lambda c: z3.And(state(c) != S_ACCEPT, state(c) != S_OUT)
    control_roots = sum((nl.next_nets(n) for n in CONTROL), [])
    reset_control = [nl.expr(q) == z3.BoolVal(bool(nl.regs[q]["reset_value"]))
                     for n in CONTROL for q in nl.nets(n)]
    reset_skip = [nl.expr(q) == z3.BoolVal(bool(nl.regs[q]["reset_value"]))
                  for n in ("skip_q", "out_skipped") for q in nl.nets(n)]
    hold_roots = state_d + sum((nl.next_nets(n) for n in out_regs + cache_state), [])
    return [
        ("lossless: motion_thresh == 0 -> skip_deform == 0", "unsat", [("skip_deform", nl.nets("skip_deform"),
            lambda c: z3.And(cur("motion_thresh", c) == 0, cur("skip_deform", c) == 1))]),
        ("lossless base: skip_q and out_skipped are zero at reset", "unsat", [(
            "reset_skip", nl.nets("skip_q") + nl.nets("out_skipped"), lambda c: z3.And(*reset_skip,
                z3.Or(cur("skip_q", c) != 0, cur("out_skipped", c) != 0)))]),
        ("lossless: skip_q == 0 and out_skipped == 0 are inductive under motion_thresh == 0", "unsat", [(
            "skip_q/out_skipped", nl.next_nets("skip_q") + nl.next_nets("out_skipped"), lambda c: z3.And(
                lossless(c), z3.Or(nxt("skip_q", c) != 0, nxt("out_skipped", c) != 0)))]),
        ("lossless: every LOOKUP enters full recompute at PROJ phase 0", "unsat", [(
            "recompute", state_d + nl.next_nets("phase"), lambda c: z3.And(lossless(c), state(c) == S_LOOKUP,
                z3.Or(nxt("state", c) != S_PROJ, nxt("phase", c) != 0)))]),
        ("skip equivalence: under motion_thresh == 0 every datapath/control register is independent "
         "of cache contents (== full evaluation)", "unsat", [noninterference(n) for n in sorted(groups)]),
        ("FSM closed over legal states (0..6), including DIV/INV", "unsat", [("state_next", state_d, lambda c: z3.And(
            z3.ULE(state(c), S_INV), z3.UGT(nxt("state", c), S_INV)))]),
        ("base: control invariant (state/cnt/phase/div_count) holds at reset", "unsat", [(
            "reset_control", control_roots, lambda c: z3.And(*reset_control, z3.Not(control_inv(cur, c))))]),
        ("inductive: legal phase and divider count in every reachable control state", "unsat", [(
            "control_next", control_roots, lambda c: z3.And(control_inv(cur, c), z3.Not(control_inv(nxt, c))))]),
        ("backpressure: out_ready == 0 in OUT freezes state, outputs and cache arrays/valid/LRU; no accept",
         "unsat", [("hold", hold_roots, lambda c: z3.And(stall(c), z3.Or(
             nxt("state", c) != S_OUT, cur("in_ready", c) != 0,
             *[nxt(n, c) != cur(n, c) for n in out_regs + cache_state])))]),
        ("handshakes: OUT & out_ready -> ACCEPT, ACCEPT & in_valid -> LOOKUP", "unsat", [("state_next", state_d,
            lambda c: z3.Or(z3.And(state(c) == S_OUT, cur("out_ready", c) == 1, nxt("state", c) != S_ACCEPT),
                            z3.And(state(c) == S_ACCEPT, cur("in_valid", c) == 1, nxt("state", c) != S_LOOKUP)))]),
        ("handshakes: in_ready iff ACCEPT, out_valid iff OUT", "unsat", [(
            "ready/valid", nl.nets("in_ready") + nl.nets("out_valid"), lambda c: z3.Or(
                cur("in_ready", c) != b1(state(c) == S_ACCEPT), cur("out_valid", c) != b1(state(c) == S_OUT)))]),
        ("non-vacuity witness: with motion_thresh != 0 the result does depend on cache contents", "sat", [
            ("datapath_next", [nl.regs[q]["d"] for g in groups.values() for q in g], lambda c: z3.And(
                cur("motion_thresh", c) != 0, z3.Or(*[(lambda e: e != z3.substitute(e, *alt))(
                    nl.bv([nl.regs[q]["d"] for q in sorted(g)], c)) for g in groups.values()])))]),
        ("unbounded progress: positive rank in LOOKUP/PROJ/DIV/INV/RASTER strictly decreases", "unsat", [(
            "rank", control_roots, lambda c: z3.And(control_inv(cur, c), busy(c), z3.Or(
                progress_rank(cur, c) == 0, z3.UGT(progress_rank(cur, c), 32),
                z3.Not(z3.ULT(progress_rank(nxt, c), progress_rank(cur, c))),
                nxt("state", c) == S_ACCEPT)))]),
    ]


def divider_props(nl):
    cur = lambda n, c: nl.bv(nl.nets(n), c)
    nxt = lambda n, c: nl.bv(nl.next_nets(n), c)
    maximum = (1 << 15)-1
    div = lambda c: cur("state", c) == S_DIV
    widen = lambda x: z3.ZeroExt(18-x.size(), x)
    trial = lambda c: (widen(cur("remainder_q", c)) << 1) | widen(bit(cur("numerator", c), 16))
    take = lambda c: z3.UGE(trial(c), widen(cur("denominator", c)))
    specs = {
        "quotient": lambda c: (cur("quotient", c) << 1) | z3.ZeroExt(16, b1(take(c))),
        "remainder_q": lambda c: z3.Extract(len(nl.nets("remainder_q"))-1, 0,
            z3.If(take(c), trial(c)-widen(cur("denominator", c)), trial(c))),
        "numerator": lambda c: cur("numerator", c) << 1,
    }
    cones = [(f"{n}[{i}]", [nl.next_nets(n)[i]],
              (lambda n, i: lambda c: z3.And(div(c), bit(nxt(n, c), i) != bit(specs[n](c), i)))(n, i))
             for n in specs for i in range(len(nl.nets(n)))]
    init = lambda c: z3.And(cur("state", c) == S_PROJ, cur("phase", c) == 5)
    det_next = lambda c: z3.Extract(15, 0, nxt("denominator", c))
    return [
        ("divider: initialization rounds numerator from latched determinant, zeros quotient/remainder", "unsat", [(
            "divide_init", sum((nl.next_nets(n) for n in ("numerator", "denominator", "quotient", "remainder_q")), []),
            lambda c: z3.And(init(c), z3.Or(
                widen(nxt("denominator", c)) != z3.ZeroExt(2, det_next(c)),
                nxt("numerator", c) != BV(1 << 16, 17) + z3.LShR(z3.ZeroExt(1, det_next(c)), 1),
                nxt("quotient", c) != 0, nxt("remainder_q", c) != 0)))]),
        ("divider: det <= 0 latches degenerate at initialization", "unsat", [(
            "degenerate_next", nl.next_nets("degenerate"), lambda c: z3.And(init(c),
                nxt("degenerate", c) != b1(det_next(c) <= 0)))]),
        ("divider: DIV preserves the latched denominator and degeneracy", "unsat", [(
            "divide_hold", nl.next_nets("denominator") + nl.next_nets("degenerate"), lambda c: z3.And(div(c),
                z3.Or(nxt("denominator", c) != cur("denominator", c), nxt("degenerate", c) != cur("degenerate", c))))]),
        ("divider: degenerate or overflowing quotient -> FX_MAX, otherwise exact quotient", "unsat", [(
            f"inv_q_next[{i}]", [nl.next_nets("inv_q")[i]], (lambda i: lambda c: z3.And(cur("state", c) == S_INV,
                bit(nxt("inv_q", c), i) != bit(
                z3.If(z3.Or(cur("degenerate", c) == 1, z3.UGT(cur("quotient", c), maximum)),
                      BV(maximum, 16), z3.Extract(15, 0, cur("quotient", c))), i)))(i)) for i in range(16)]),
        ("divider: bit-true radix-2 restoring quotient/remainder/numerator step", "unsat", cones),
    ]


def top_bmc(nl, breaker):
    """BMC-20 checks control safety; inductive rank proves liveness beyond that horizon."""
    roots = sum((nl.nets(n) for n in CONTROL), [])
    unrolled = Unrolled([(nl, "")], {"": roots}, BMC_DEPTH)
    env = [unrolled.var("", n, t) for t in range(BMC_DEPTH + 1) for n in nl.nets("in_valid") + nl.nets("out_ready")]
    frame = lambda e, t: unrolled.at(nl, "", e, t)
    get = lambda n, c: nl.bv(nl.nets(n), c)
    illegal = z3.Or(*[frame(z3.Not(control_inv(get, None)), t) for t in range(BMC_DEPTH + 1)])
    start = time.monotonic()
    breaker.say(f"[{nl.target}] BMC-{BMC_DEPTH} from reset ({len(unrolled.regs[''])} registers in sequential COI)")
    breaker.say("  Full recompute needs 33 edges to out_valid (>20); liveness uses the unbounded inductive rank, not a deeper BMC.")
    result, _, _ = breaker.solve([*unrolled.constraints, *env, illegal], f"bmc-{BMC_DEPTH}")
    return dict(name=f"BMC-{BMC_DEPTH} from reset: control invariant safety (liveness proved by rank)",
                target=nl.target, status=str(result).upper(), method=f"BMC-{BMC_DEPTH}",
                seconds=round(time.monotonic() - start, 2), cones=1, expect="unsat")


PROPS = {"gs4d_skip_unit": skip_props, "gs4d_cache": cache_props, "gs4d_top": top_props}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", metavar="TARGET", help="one of: " + ", ".join(PROPS))
    ap.add_argument("--netlists", type=Path, default=ROOT / "build/formal/netlist")
    ap.add_argument("--out", type=Path, default=ROOT / "build/formal")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with open(args.out / "formal_4dgs.log", "w", encoding="utf-8") as log:
        breaker = Breaker(log)
        start = time.monotonic()
        for target in args.targets or list(PROPS):
            nl = Netlist(args.netlists / target, target)
            breaker.say(f"== {target}: {nl.gates} gates, {len(nl.regs)} registers")
            checks = PROPS[target](nl)
            if target == "gs4d_top":
                checks += divider_props(nl)
            for name, expect, cones in checks:
                res = breaker.check(nl, name, cones)
                witness = res.pop("witness", None)
                if witness is not None and expect == "unsat":
                    model, failing = witness, res["failing_cones"]
                    path = args.out / f"{target}_counterexample_{len(results)}.txt"
                    path.write_text(model.sexpr() + "\n", encoding="utf-8")
                    breaker.say(f"  SAT witness: failing cones {failing}; model saved to {path}")
                    if target == "gs4d_top":
                        for signal in (*CONTROL, "numerator", "quotient", "remainder_q", "denominator", "degenerate"):
                            current = nl.bv(nl.nets(signal))
                            following = nl.bv(nl.next_nets(signal))
                            breaker.say(f"    {signal}[{current.size()}]: "
                                        f"{model.eval(current, model_completion=True)} -> "
                                        f"{model.eval(following, model_completion=True)}")
                res["expect"], res["note"] = expect, None
                results.append(res)
            if target == "gs4d_top":
                results.append(top_bmc(nl, breaker))
        for res in results:
            res["verdict"] = "FAIL" if res["status"] == "BOUNDED" else verdict(res)
        summary = dict(z3=z3.get_version_string(), timeout_s=breaker.timeout_ms / 1000,
                       queries=breaker.queries, seconds=round(time.monotonic() - start, 2), results=results)
        (args.out / "formal_4dgs.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
        breaker.say("\n== summary")
        for res in results:
            breaker.say(f"{res['verdict']:>16}  {res['target']}: {res['name']} -> {res['status']} "
                        f"via {res['method']} in {res['seconds']}s")
        bad = [r for r in results if r["verdict"] == "FAIL"]
        breaker.say(f"formal_4dgs: {len(results) - len(bad)}/{len(results)} obligations as expected, "
                    f"{breaker.queries} Z3 queries")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
