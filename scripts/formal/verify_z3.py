#!/usr/bin/env python3
"""Tier-3 Z3 property and equivalence checks on rtl_baseline netlists.

Targets (netlists from rtl_to_netlist.py, loaded through eda_core):
  fp16_div8 / fp16_mult2   bit-true equivalence to the C++/Python FP16 reference,
                           magnitude/sign wrap checks and the MIN/MAX/zero corner table
  ssim_custom_shift_safe   every next-state bit (counter, window valid, 48-bit taps and
                           row buffers) equals the bit-true Python spec of the RTL
  gs_rasterizer            FSM next state, stage-valid propagation, inductive pipeline
                           invariants, ranking functions for deadlock freedom, BMC-20

Each obligation states its expected result. SAT on a property marked
`known_deviation` is a documented legacy behaviour (docs/FORMAL_VERIFICATION.md),
not a failure; any other mismatch with the expectation fails the run.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import z3

from z3_engine import BMC_DEPTH, Breaker, Netlist, ROOT, Unrolled, bits_of

BV = z3.BitVecVal


def b1(cond):
    return z3.If(cond, BV(1, 1), BV(0, 1))


def bitwise(label, impl, spec, width, assume=lambda c: z3.BoolVal(True), roots=()):
    """One cone per output bit: impl(cache)[i] != spec(cache)[i] under assume(cache)."""
    def viol(i):
        return lambda c: z3.And(assume(c), z3.Extract(i, i, impl(c)) != z3.Extract(i, i, spec(c)))
    return [(f"{label}[{i}]", [roots[i]] if roots else [], viol(i)) for i in range(width)]


# ---------------------------------------------------------------- FP16 arithmetic
def div8_spec(x):
    """reference_div8() from rtl_baseline/sim/sim_main_ssim.cpp (legacy RTL semantics)."""
    sign, frac, e = x & 0x8000, x & 0x3FF, z3.LShR(x, 10) & 31
    return z3.If(e == 31, BV(0, 16), z3.If(e == 0, sign | z3.LShR(frac, 3),
                 z3.If(z3.ULE(e, 3), z3.LShR(0x400 | frac, 4 - e), x - (3 << 10))))


def mult2_spec(x):
    """fp16_mult2: x*2 with NaN/Inf -> 0, subnormal mantissa << 1 (10 bits), exp 30 -> 0."""
    sign, frac, e = x & 0x8000, x & 0x3FF, z3.LShR(x, 10) & 31
    return z3.If(e == 31, BV(0, 16), z3.If(e == 0, sign | ((frac << 1) & 0x3FF),
                 z3.If(e == 30, BV(0, 16), x + 0x400)))


CORNERS = {"+0": 0x0000, "-0": 0x8000, "min subnormal": 0x0001, "max subnormal": 0x03FF,
           "min normal (exp 1)": 0x0400, "exp 2": 0x0800, "exp 3": 0x0C00, "exp 4": 0x1000,
           "-min normal": 0x8400, "MAX_VAL": 0x7BFF, "MIN_VAL": 0xFBFF, "exp 30": 0x7800,
           "+Inf": 0x7C00, "-Inf": 0xFC00, "NaN": 0x7E00}
FP_SPECS = {"fp16_div8": div8_spec, "fp16_mult2": mult2_spec}


def corner_table(nl, spec):
    """Evaluate the netlist on MIN_VAL/MAX_VAL/zero/boundary words against the reference."""
    xin, out, table = nl.nets("fp_in"), nl.bv(nl.nets("fp_out")), []
    for name, word in CORNERS.items():
        y = z3.simplify(z3.substitute(out, *[(nl.expr(b), z3.BoolVal(bool(word >> i & 1)))
                                             for i, b in enumerate(xin)])).as_long()
        ref = z3.simplify(spec(BV(word, 16))).as_long()
        table.append(dict(case=name, input=f"{word:04x}", rtl=f"{y:04x}", reference=f"{ref:04x}", match=y == ref))
    return table


def fp_props(nl):
    xin, out, spec = nl.nets("fp_in"), nl.nets("fp_out"), FP_SPECS[nl.target]
    x = lambda c: nl.bv(xin, c)
    y = lambda c: nl.bv(out, c)
    mag = lambda v: v & 0x7FFF
    finite = lambda c: z3.LShR(x(c), 10) & 31 != 31
    sign_flip = lambda c: z3.And(finite(c), mag(x(c)) != 0, mag(y(c)) != 0,
                                 z3.Extract(15, 15, x(c)) != z3.Extract(15, 15, y(c)))
    checks = [("equivalence to bit-true reference", "unsat", None,
               bitwise("fp_out", y, lambda c: spec(x(c)), 16, roots=out))]
    if nl.target == "fp16_div8":
        checks += [
            ("no magnitude wrap: |x/8| <= |x| for finite x", "unsat", None,
             [("fp_out", out, lambda c: z3.And(finite(c), z3.UGT(mag(y(c)), mag(x(c)))))]),
            ("sign preserved for finite non-zero results", "sat",
             "legacy: exponents 1-3 drop the sign bit", [("fp_out[15]", out, sign_flip)])]
    else:
        checks += [
            ("no silent wrap: |2x| > |x| for finite non-zero x", "sat",
             "legacy: exp 30 flushes to +0; a subnormal loses its mantissa MSB",
             [("fp_out", out, lambda c: z3.And(finite(c), mag(x(c)) != 0, z3.ULE(mag(y(c)), mag(x(c)))))]),
            ("sign preserved for finite non-zero results", "unsat", None, [("fp_out[15]", out, sign_flip)])]
    return checks, corner_table(nl, spec)


# ---------------------------------------------------------------- SSIM shift window
TAPS = {"out_p00": ("row0", 16), "out_p01": ("row0", 15), "out_p02": ("row0", 14),
        "out_p10": ("row1", 16), "out_p11": ("row1", 15), "out_p12": ("row1", 14),
        "out_p20": ("row2", 2), "out_p21": ("row2", 1), "out_p22": ("row2", 0)}


def shift_state(nl):
    regs = {f"row0_{i}": 48 for i in range(17)} | {f"row1_{i}": 48 for i in range(17)}
    regs |= {f"row2_{i}": 48 for i in range(3)} | {"row3": 48, "dir": 1, "counter": 9,
                                                   "is_t1_reg": 1, "is_valid": 1, "out_valid": 1}
    regs |= {t: 48 for t in TAPS}
    return regs


def shift_spec(s, in_valid, px):
    """Bit-true one-cycle spec of ssim_custom_shift_safe (always_comb + always_ff)."""
    z48 = BV(0, 48)
    row0 = [s[f"row0_{i}"] for i in range(17)]
    row1 = [s[f"row1_{i}"] for i in range(17)]
    row2 = [s[f"row2_{i}"] for i in range(3)]
    cnt, d, t1r = s["counter"], s["dir"], s["is_t1_reg"]
    n = {k: v for k, v in s.items()}
    for tap, (row, i) in TAPS.items():
        n[tap] = s[f"{row}_{i}"]
    is_t1 = z3.Extract(3, 0, cnt) == 0
    act = dict(n)
    act["counter"] = cnt + 1
    act["is_t1_reg"] = b1(is_t1)
    act["out_valid"] = b1(z3.And(z3.UGT(cnt, 17), z3.ULT(cnt, 290)))

    def put(state, row, i, v):
        state[f"{row}_{i}"] = v

    fill = dict(act)  # counter < 14
    for i in range(14):
        put(fill, "row1", i, row1[i + 1])
    put(fill, "row1", 14, px)
    turn = dict(act)  # counter 14/15
    put(turn, "row2", 0, row2[1]); put(turn, "row2", 1, row2[2]); put(turn, "row2", 2, px)
    t2 = dict(act)
    t2["dir"] = ~d
    put(t2, "row2", 2, z3.If(cnt != 273, px, z48)); put(t2, "row2", 1, z3.If(cnt != 273, s["row3"], z48))
    hi, lo = dict(t2), dict(t2)
    for st, edge in ((hi, 14), (lo, 16)):
        put(st, "row1", edge, row2[1]); put(st, "row0", edge, row1[edge])
        put(st, "row1", 15, row2[2]); put(st, "row0", 15, row1[15])
    t2 = merge(d == 1, hi, lo)
    mid = dict(act)
    mid["row3"] = z3.If(is_t1, px, s["row3"])
    put(mid, "row2", 0, z3.If(is_t1, z48, row2[1]))
    put(mid, "row2", 1, z3.If(is_t1, row2[1], row2[2]))
    put(mid, "row2", 2, z3.If(is_t1, row2[2], px))
    fwd, back = dict(mid), dict(mid)
    for i in range(16, 0, -1):
        put(fwd, "row0", i, row0[i - 1]); put(fwd, "row1", i, row1[i - 1])
    put(fwd, "row0", 0, row1[16]); put(fwd, "row1", 0, row2[0])
    for i in range(16):
        put(back, "row0", i, row0[i + 1]); put(back, "row1", i, row1[i + 1])
    put(back, "row0", 16, row0[0]); put(back, "row1", 16, row1[0])
    put(back, "row1", 13, z3.If(cnt != 16, row2[0], row1[14]))
    put(back, "row0", 13, z3.If(cnt != 16, row1[14], row0[14]))
    window = merge(z3.And(t1r == 1), t2, merge(d == 0, fwd, back))
    drain = dict(act)
    for i in range(16, 0, -1):
        put(drain, "row0", i, row0[i - 1]); put(drain, "row1", i, row1[i - 1])
    put(drain, "row0", 0, z48); put(drain, "row1", 0, z48)
    active = merge(z3.ULT(cnt, 14), fill, merge(z3.Or(cnt == 14, cnt == 15), turn,
                   merge(z3.And(z3.UGE(cnt, 16), z3.ULE(cnt, 273)), window, drain)))
    out = merge(z3.Or(in_valid == 1, s["is_valid"] == 1), active, n)
    out["is_valid"] = z3.If(in_valid == 1, BV(1, 1), z3.If(z3.UGE(cnt, 290), BV(0, 1), s["is_valid"]))
    return out


def merge(cond, a, b):
    return {k: a[k] if z3.eq(a[k], b[k]) else z3.If(cond, a[k], b[k]) for k in a}


def shift_props(nl):
    regs = shift_state(nl)
    cones, memo = [], {}

    def spec_state(c):
        key = id(c)
        if key not in memo:
            s = {r: nl.bv(nl.nets(r), c) for r in regs}
            memo[key] = (c, shift_spec(s, nl.bv(nl.nets("in_valid"), c), nl.bv(nl.nets("in_pixel"), c)))
        return memo[key][1]
    for reg, width in regs.items():
        def impl(c, reg=reg):
            return nl.bv(nl.next_nets(reg), c)

        def spec(c, reg=reg):
            return spec_state(c)[reg]
        cones += bitwise(f"{reg}_next", impl, spec, width, roots=nl.next_nets(reg))
    return [("next state equals bit-true Python spec (all registers)", "unsat", None, cones)], []


# ---------------------------------------------------------------- GS rasterizer control
IDLE, INIT, PROC, FINAL, WRITE, DONE = range(6)
STAGES = 11


def gs_props(nl):
    cur = lambda name, c: nl.bv(nl.nets(name), c)
    nxt = lambda name, c: nl.bv(nl.next_nets(name), c)
    sv = lambda i, c: cur(f"stage_valid_{i}", c)
    svn = lambda i, c: nxt(f"stage_valid_{i}", c)
    state = lambda c: cur("state", c)
    active = lambda c: z3.Or(state(c) == INIT, state(c) == PROC)

    def fsm_spec(c):
        s = state(c)
        exit_ = z3.Or(z3.UGE(z3.Extract(10, 0, cur("current_pixel", c)), 1089),
                      cur("early_termination", c) == 1, cur("addr0", c) == 1099)
        return z3.If(s == IDLE, BV(INIT, 3), z3.If(s == INIT, BV(PROC, 3), z3.If(
            s == PROC, z3.If(exit_, BV(FINAL, 3), BV(PROC, 3)), z3.If(s == FINAL, BV(WRITE, 3), z3.If(
                s == WRITE, z3.If(cur("addr2", c) == 2550, BV(DONE, 3), BV(INIT, 3)), s)))))

    state_d = nl.next_nets("state")
    sv_roots = [nl.next_nets(f"stage_valid_{i}")[0] for i in range(STAGES)]
    prefix = lambda c, f: z3.And(*[z3.Implies(f(i + 1, c) == 1, f(i, c) == 1) for i in range(STAGES - 1)])
    one = lambda label, roots, viol: [(label, roots, viol)]
    checks = [
        ("FSM next state equals RTL case spec", "unsat", None,
         bitwise("state_next", lambda c: nxt("state", c), fsm_spec, 3, roots=state_d)),
        ("FSM closed over legal states (0..5)", "unsat", None, one("state_next", state_d, lambda c: z3.And(
            z3.ULE(state(c), DONE), z3.UGT(nxt("state", c), DONE)))),
        ("stage-valid propagation: v[i]' = active & v[i-1], v[0]' = active", "unsat", None,
         [(f"stage_valid_{i}_next", [sv_roots[i]], (lambda i: lambda c: svn(i, c) != z3.If(
             active(c), sv(i - 1, c) if i else BV(1, 1), BV(0, 1)))(i)) for i in range(STAGES)]),
        ("inductive: state' == PROCESS -> stage_valid_0'", "unsat", None, one(
            "stage_valid_0_next", state_d + sv_roots[:1],
            lambda c: z3.And(nxt("state", c) == PROC, svn(0, c) == 0))),
        ("inductive: valid bits form a bubble-free prefix", "unsat", None, one(
            "stage_valid_next", sv_roots, lambda c: z3.And(prefix(c, sv), z3.Not(prefix(c, svn))))),
        ("deadlock-free PROCESS: addr0 ranks down to 1099 (exit)", "unsat", None, one(
            "addr0_next", state_d + nl.next_nets("addr0"), lambda c: z3.And(
                state(c) == PROC, sv(0, c) == 1, nxt("state", c) == PROC,
                z3.Or(nxt("addr0", c) != cur("addr0", c) + 1, cur("addr0", c) == 1099)))),
        ("deadlock-free frame: addr2 advances once per WRITE_RESULT to 2550", "unsat", None, one(
            "addr2_next", state_d + nl.next_nets("addr2"), lambda c: z3.If(
                state(c) == WRITE, z3.Or(nxt("addr2", c) != cur("addr2", c) + 1,
                                         z3.And(nxt("state", c) != DONE, cur("addr2", c) == 2550)),
                nxt("addr2", c) != cur("addr2", c)))),
        ("COMPLETE absorbing and Finish == (state == COMPLETE)", "unsat", None, one(
            "Finish", state_d + nl.nets("Finish"), lambda c: z3.Or(
                z3.And(state(c) == DONE, nxt("state", c) != DONE),
                cur("Finish", c) != b1(state(c) == DONE)))),
    ]
    return checks, []


def gs_bmc(nl, breaker):
    """Bounded check from reset: no early Finish and a legal state for 20 cycles."""
    roots = nl.nets("Finish") + nl.nets("state")
    unrolled = Unrolled([(nl, "")], {"": roots}, BMC_DEPTH)
    bad = []
    for t in range(BMC_DEPTH + 1):
        frame = lambda e: unrolled.at(nl, "", e, t)
        st = nl.bv(nl.nets("state"))
        bad.append(frame(z3.Or(nl.expr(nl.nets("Finish")[0]), z3.UGT(st, DONE))))
    start = time.monotonic()
    breaker.say(f"[{nl.target}] BMC-{BMC_DEPTH} from reset: no Finish, legal state "
                f"({len(unrolled.regs[''])} registers in sequential COI)")
    result, _, _ = breaker.solve([*unrolled.constraints, z3.Or(*bad)], f"bmc-{BMC_DEPTH}")
    return dict(name=f"BMC-{BMC_DEPTH} from reset: Finish low, state legal", target=nl.target,
                status=str(result).upper(), method=f"BMC-{BMC_DEPTH}",
                seconds=round(time.monotonic() - start, 2), cones=1, expect="unsat")


PROPS = {"fp16_div8": fp_props, "fp16_mult2": fp_props, "ssim_custom_shift_safe": shift_props,
         "gs_rasterizer": gs_props}


def run(targets, netdir, breaker):
    results, tables = [], {}
    for target in targets:
        nl = Netlist(netdir / target, target)
        breaker.say(f"== {target}: {nl.gates} gates, {len(nl.regs)} registers, "
                    f"{len(nl.manifest['cuts'])} opaque cut cells")
        checks, table = PROPS[target](nl)
        if table:
            tables[target] = table
        for name, expect, note, cones in checks:
            res = breaker.check(nl, name, cones)
            res["expect"], res["note"] = expect, note
            witness = res.pop("witness", None)
            if witness is not None and target.startswith("fp16"):
                res["counterexample"] = f"fp_in=16'h{bits_of(witness, [nl.expr(b) for b in nl.nets('fp_in')]):04x}"
            results.append(res)
        if target == "gs_rasterizer":
            results.append(gs_bmc(nl, breaker))
    return results, tables


def verdict(res):
    if res["status"] == res["expect"].upper():
        return "known deviation" if res.get("note") else "pass"
    if res["status"] == "BOUNDED" and res["expect"] == "unsat":
        return "pass (bounded)"
    return "FAIL"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", metavar="TARGET", help="one of: " + ", ".join(PROPS))
    ap.add_argument("--netlists", type=Path, default=ROOT / "build/formal/netlist")
    ap.add_argument("--out", type=Path, default=ROOT / "build/formal")
    ap.add_argument("--drill", action="store_true",
                    help="circuit-breaker drill: force every monolithic GS query and the stage_valid_5 slice "
                         "to `unknown` (Z3 rlimit) to exercise COI slicing and the BMC-20 downgrade")
    args = ap.parse_args()
    targets = args.targets or list(PROPS)
    args.out.mkdir(parents=True, exist_ok=True)
    name = "breaker_drill" if args.drill else "formal_z3"
    with open(args.out / f"{name}.log", "w", encoding="utf-8") as log:
        drill = {"monolithic": 1, "stage_valid_5_next": 1} if args.drill else None
        breaker = Breaker(log, drill=drill)
        if args.drill:
            targets = ["gs_rasterizer"]
        start = time.monotonic()
        results, tables = run(targets, args.netlists, breaker)
        for res in results:
            res["verdict"] = verdict(res)
        summary = dict(z3=z3.get_version_string(), timeout_s=breaker.timeout_ms / 1000,
                       queries=breaker.queries, seconds=round(time.monotonic() - start, 2),
                       results=results, corner_cases=tables)
        (args.out / f"{name}.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
        breaker.say("\n== summary")
        for res in results:
            extra = f" [{res['note']}; cex {res.get('counterexample', 'n/a')}]" if res.get("note") else ""
            breaker.say(f"{res['verdict']:>16}  {res['target']}: {res['name']} -> {res['status']} "
                        f"via {res['method']} in {res['seconds']}s{extra}")
        for target, table in tables.items():
            bad = [row for row in table if not row["match"]]
            breaker.say(f"{'pass' if not bad else 'FAIL':>16}  {target}: {len(table)} corner cases "
                        f"(MIN_VAL/MAX_VAL/zero/subnormal/exp boundaries/Inf/NaN) match the reference")
    failed = [r for r in results if r["verdict"] == "FAIL"]
    failed += [row for table in tables.values() for row in table if not row["match"]]
    print(f"formal_z3: {len(results) - len(failed)}/{len(results)} obligations as expected, "
          f"{breaker.queries} Z3 queries, log {args.out / (name + '.log')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
