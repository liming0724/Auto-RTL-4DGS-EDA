#!/usr/bin/env python3
"""Tier-3 Z3 proofs for the Phase 5c split-fetch wrapper (gs4d_axi_wrapper composed with
gs4d_accelerator_top, SPLIT_FETCH = 1). Netlist `gs4d_axi` from rtl_to_netlist.py
(NUM_PE=1, Q_FRAC=4, CACHE_WAYS=2, CACHE_SETS=4, ID_W=4), loaded through eda_core, with the
same 30 s / 3-retry / COI-slicing / BMC-20 circuit breaker as verify_z3_4dgs.py.

Every obligation is a single-step (inductive) query over the composed netlist: all inputs
(meta/attr tdata/tvalid/tuser, out_ready, motion_thresh, cache_bypass) and all register values
are free, constrained only by the inductive composition invariant J (proved at reset and
inductive below). Obligations:
  J      wrapper/top phase correspondence (W_DECIDE <-> LOOKUP, W_ATTR <-> ATTR, W_IDLE /
         W_PRESENT -> top not in LOOKUP/ATTR), ATTR -> out_skipped == 0, LOOKUP/ATTR ->
         out_id == pending meta gauss_id. Base at reset + inductive step.
  1      no attribute request on skip + hit: s_axis_attr_tready -> top in ATTR with
         out_skipped == 0; the DECIDE branch requests Channel B iff needs_attr iff the
         transaction is not served from the cache (next out_skipped == 0); tready rises only
         from DECIDE with needs_attr == 1.
  2      ID match: a beat reaches the top (attr_valid) only if tuser == pending meta id ==
         top out_id; a mismatching beat sets axi_err, is not forwarded and the wrapper keeps
         waiting; axi_err is sticky.
  3      exactly-once in-order: meta beats are latched only in W_IDLE; the top accepts only in
         W_PRESENT with in_id == latched id; W_PRESENT leaves exactly on the top accept; out_id
         changes only on a top accept; ACCEPT is re-entered only through an OUT handshake (one
         output per accept); W_IDLE is re-entered only after the transaction is complete.
  4      ATTR: a miss waits in ATTR until attr_valid with its datapath state frozen; on
         attr_valid it enters PROJ phase 0 with every consumer of the base fields equal to the
         attr beat (rho_q == fx_add(rho, dr_q), means, opacity, scales); a cache-served
         transaction never enters ATTR; needs_attr is valid throughout LOOKUP.
  5      liveness: R = 4*(3 - phase) + attr_owed + top_owed on wrapper phases META (W_IDLE,
         12), DECIDE before the top accept (W_PRESENT, 9), DECIDE in LOOKUP (W_DECIDE, 8),
         ATTR (5), DONE (0). R never increases, strictly decreases on every handshake (meta
         accept, top accept, DECIDE branch, attr accept), stays equal on stalls, reaches 0
         exactly when the Gaussian completes; each state's handshake is enabled by its
         environment assumption. Top-side rank through ATTR (no deadlock in the top).
  6      SAT witnesses: a skip + hit DECIDE (no attribute request) and a miss DECIDE under J,
         and a miss reached from reset (BMC-3).
"""
import argparse
import json
from pathlib import Path
import time

import z3

from verify_z3 import b1, verdict
from verify_z3_4dgs import control_inv, progress_rank
from z3_engine import Breaker, Netlist, ROOT, Unrolled

BV = z3.BitVecVal
F, W, ID_W = 4, 12, 4
W_IDLE, W_PRESENT, W_DECIDE, W_ATTR = range(4)
S_ACCEPT, S_LOOKUP, S_PROJ, S_RASTER, S_OUT, S_DIV, S_INV, S_ATTR = range(8)
DIV_BITS = 2 * F + 1
TOP_DP = ("rho_q", "sx_q", "sy_q", "out_mean_x", "out_mean_y", "out_opacity", "proj_a", "proj_b", "dr_q",
          "dmux_q", "dmuy_q", "dsx_q", "dsy_q", "pix_x0_q", "pix_y_q", "out_id", "out_delta", "out_skipped")


def fx_add(a, b):
    s = z3.SignExt(2, a) + z3.SignExt(2, b)
    hi, lo = BV(2 ** (W - 1) - 1, W + 2), BV(-(2 ** (W - 1)), W + 2)
    return z3.If(s > hi, BV(2 ** (W - 1) - 1, W), z3.If(s < lo, BV(2 ** (W - 1), W), z3.Extract(W - 1, 0, s)))


def axi_props(nl):
    cur = lambda n, c: nl.bv(nl.nets(n), c)
    nxt = lambda n, c: nl.bv(nl.next_nets(n), c)
    t = lambda n, c: cur(f"u_top_{n}", c)
    tn = lambda n, c: nxt(f"u_top_{n}", c)
    ws, wsn = (lambda c: cur("wstate", c)), (lambda c: nxt("wstate", c))
    st, stn = (lambda c: t("state", c)), (lambda c: tn("state", c))
    one = lambda n, c: cur(n, c) == 1
    meta_fire = lambda c: z3.And(one("s_axis_meta_tvalid", c), one("s_axis_meta_tready", c))
    attr_fire = lambda c: z3.And(one("s_axis_attr_tvalid", c), one("s_axis_attr_tready", c))
    top_accept = lambda c: t("accept", c) == 1
    attr_ok = lambda c: one("top_attr_valid", c)
    needs = lambda c: one("needs_attr", c)

    def J(get, gt, c):
        w, s = get("wstate", c), gt("state", c)
        return z3.And((w == W_DECIDE) == (s == S_LOOKUP), (w == W_ATTR) == (s == S_ATTR),
                      z3.Implies(s == S_ATTR, gt("out_skipped", c) == 0),
                      z3.Implies(z3.Or(s == S_LOOKUP, s == S_ATTR), gt("out_id", c) == get("meta_id", c)))
    jc = lambda c: J(cur, t, c)
    meta_id_n = lambda c: z3.Extract(nxt("meta_q", c).size() - 1, nxt("meta_q", c).size() - ID_W, nxt("meta_q", c))
    jn = lambda c: J(lambda n, cc: meta_id_n(cc) if n == "meta_id" else nxt(n, cc), tn, c)
    j_roots = sum((nl.next_nets(n) for n in ("wstate", "u_top_state", "u_top_out_skipped", "u_top_out_id", "meta_q")), [])
    reset = [nl.expr(q) == z3.BoolVal(bool(nl.regs[q]["reset_value"]))
             for n in ("wstate", "u_top_state", "u_top_out_skipped", "u_top_out_id", "meta_q") for q in nl.nets(n)]

    # ---- 5: wrapper ranking function
    def rank(w):
        return z3.If(w == W_IDLE, BV(12, 8), z3.If(w == W_PRESENT, BV(9, 8), z3.If(w == W_DECIDE, BV(8, 8), BV(5, 8))))
    done = lambda c: z3.Or(z3.And(ws(c) == W_DECIDE, z3.Not(needs(c))), z3.And(ws(c) == W_ATTR, attr_ok(c)))
    rank_next = lambda c: z3.If(done(c), BV(0, 8), rank(wsn(c)))
    handshake = lambda c: z3.Or(z3.And(ws(c) == W_IDLE, meta_fire(c)), z3.And(ws(c) == W_PRESENT, top_accept(c)),
                                ws(c) == W_DECIDE, z3.And(ws(c) == W_ATTR, attr_ok(c)))
    w_roots = nl.next_nets("wstate") + nl.nets("needs_attr") + nl.nets("top_attr_valid")

    # ---- top-side rank with ATTR (SPLIT_FETCH = 1)
    get_t = lambda n, c: t(n, c)
    def top_rank(c):
        return z3.If(st(c) == S_LOOKUP, BV(34, 8), z3.If(st(c) == S_ATTR, BV(33, 8), progress_rank(get_t, c)))
    def top_rank_n(c):
        g = lambda n, cc: tn(n, cc)
        return z3.If(stn(c) == S_LOOKUP, BV(34, 8), z3.If(stn(c) == S_ATTR, BV(33, 8), progress_rank(g, c)))
    def ctl_inv(g, c):  # control_inv of verify_z3_4dgs with ATTR legal and the Q_FRAC=4 divider
        s, k, p, d = (g(n, c) for n in ("state", "cnt", "phase", "div_count"))
        return z3.And(z3.Implies(s == S_RASTER, z3.ULE(k, 3)),
                      z3.Implies(s == S_PROJ, z3.Or(z3.ULE(p, 5), z3.And(z3.UGE(p, 7), z3.ULE(p, 9)))),
                      z3.Implies(s == S_DIV, z3.And(p == 6, z3.ULT(d, DIV_BITS))),
                      z3.Implies(s == S_INV, z3.And(p == 6, d == DIV_BITS)))
    ctl = ("u_top_state", "u_top_cnt", "u_top_phase", "u_top_div_count")
    ctl_roots = sum((nl.next_nets(n) for n in ctl), [])
    ctl_reset = [nl.expr(q) == z3.BoolVal(bool(nl.regs[q]["reset_value"])) for n in ctl for q in nl.nets(n)]
    busy = lambda c: z3.And(st(c) != S_ACCEPT, st(c) != S_OUT)

    # ---- 4: attr fields
    ad = lambda c: cur("s_axis_attr_tdata", c)
    fld = lambda c, i: z3.Extract((i + 1) * W - 1, i * W, ad(c))  # 0 opacity,1 rho,2 s0_y,3 s0_x,4 mu0_y,5 mu0_x
    attr_dp = [("rho_q", lambda c: fx_add(fld(c, 1), t("dr_q", c))),
               ("out_mean_x", lambda c: fx_add(fld(c, 5), t("dmux_q", c))),
               ("out_mean_y", lambda c: fx_add(fld(c, 4), t("dmuy_q", c))),
               ("out_opacity", lambda c: fld(c, 0)),
               ("sx_q", lambda c: fx_add(fld(c, 3), t("dsx_q", c))),
               ("sy_q", lambda c: fx_add(fld(c, 2), t("dsy_q", c))),
               ("proj_a", lambda c: fx_add(fld(c, 3), t("dsx_q", c))),
               ("proj_b", lambda c: fx_add(fld(c, 3), t("dsx_q", c)))]
    frozen = [n for n in TOP_DP if f"u_top_{n}" in nl.manifest["aliases"] or f"u_top_{n}" in nl.manifest["ports"]]
    dp_roots = sum((nl.next_nets(f"u_top_{n}") for n in frozen), []) + nl.next_nets("u_top_state") + nl.next_nets("u_top_phase")
    meta_id = lambda c: cur("meta_id", c)

    return [
        # ---- J
        ("J base: composition invariant holds at reset", "unsat", [("J_reset", j_roots,
            lambda c: z3.And(*reset, z3.Not(jc(c))))]),
        ("J inductive: wrapper phase <-> top state, ATTR -> out_skipped == 0, LOOKUP/ATTR -> out_id == meta id",
         "unsat", [("J_next", j_roots, lambda c: z3.And(jc(c), z3.Not(jn(c))))]),
        # ---- 1
        ("1a no attr request on skip+hit: s_axis_attr_tready -> top ATTR with out_skipped == 0", "unsat", [
            ("attr_tready", nl.nets("s_axis_attr_tready"), lambda c: z3.And(jc(c), one("s_axis_attr_tready", c), z3.Or(
                st(c) != S_ATTR, t("out_skipped", c) != 0, ws(c) != W_ATTR)))]),
        ("1b DECIDE: requests Channel B iff needs_attr iff not cache-served (next out_skipped == 0, next state != RASTER)",
         "unsat", [("decide", nl.next_nets("wstate") + nl.next_nets("u_top_out_skipped") + nl.next_nets("u_top_state")
                    + nl.nets("needs_attr"), lambda c: z3.And(jc(c), ws(c) == W_DECIDE, z3.Or(
                        (wsn(c) == W_ATTR) != needs(c), needs(c) != (tn("out_skipped", c) == 0),
                        needs(c) == (stn(c) == S_RASTER))))]),
        ("1c s_axis_attr_tready rises only from DECIDE with needs_attr == 1", "unsat", [
            ("tready_rise", nl.next_nets("wstate") + nl.nets("needs_attr"), lambda c: z3.And(
                jc(c), ws(c) != W_ATTR, wsn(c) == W_ATTR, z3.Not(z3.And(ws(c) == W_DECIDE, needs(c)))))]),
        # ---- 2
        ("2a ID match: attr_valid to the top -> tuser == pending meta id == top out_id; attr_data == the beat",
         "unsat", [("attr_id", nl.nets("top_attr_valid") + nl.nets("top_attr_data"), lambda c: z3.And(jc(c), attr_ok(c), z3.Or(
             cur("s_axis_attr_tuser", c) != meta_id(c), t("out_id", c) != meta_id(c), z3.Not(attr_fire(c)),
             cur("top_attr_data", c) != ad(c))))]),
        ("2b mismatch: beat consumed, not forwarded, axi_err set, wrapper and top keep waiting; no beat -> attr_data == 0",
         "unsat", [("attr_mismatch", nl.next_nets("axi_err") + nl.next_nets("wstate") + nl.next_nets("u_top_state")
                    + nl.nets("top_attr_valid") + nl.nets("top_attr_data"), lambda c: z3.And(jc(c), z3.Or(
                        z3.And(attr_fire(c), cur("s_axis_attr_tuser", c) != meta_id(c), z3.Or(
                            attr_ok(c), nxt("axi_err", c) != 1, wsn(c) != W_ATTR, stn(c) != S_ATTR)),
                        z3.And(z3.Not(attr_ok(c)), cur("top_attr_data", c) != 0))))]),
        ("2c axi_err is sticky (cleared only by reset)", "unsat", [("axi_err", nl.next_nets("axi_err"),
            lambda c: z3.And(one("axi_err", c), nxt("axi_err", c) != 1))]),
        # ---- 3
        ("3a meta beat latched only in W_IDLE on tvalid (tready == W_IDLE), meta_q == tdata; else held", "unsat", [
            ("meta_q", nl.next_nets("meta_q") + nl.nets("s_axis_meta_tready"), lambda c: z3.Or(
                one("s_axis_meta_tready", c) != (ws(c) == W_IDLE),
                z3.And(meta_fire(c), z3.Or(nxt("meta_q", c) != cur("s_axis_meta_tdata", c), wsn(c) != W_PRESENT)),
                z3.And(z3.Not(meta_fire(c)), nxt("meta_q", c) != cur("meta_q", c))))]),
        ("3b top accepts only in W_PRESENT with in_id == latched id; W_PRESENT leaves exactly on that accept", "unsat", [
            ("present", nl.next_nets("wstate") + nl.nets("u_top_accept") + nl.next_nets("u_top_out_id") + nl.nets("u_top_in_id"),
             lambda c: z3.And(jc(c), z3.Or(
                 z3.And(top_accept(c), ws(c) != W_PRESENT),
                 z3.And(top_accept(c), z3.Or(tn("out_id", c) != meta_id(c), t("in_id", c) != meta_id(c))),
                 z3.And(ws(c) == W_PRESENT, (wsn(c) == W_DECIDE) != top_accept(c)),
                 z3.And(ws(c) == W_PRESENT, wsn(c) != W_DECIDE, wsn(c) != W_PRESENT))))]),
        ("3c out_id changes only on a top accept; ACCEPT re-entered only via OUT & out_ready (one output per accept)",
         "unsat", [("once", nl.next_nets("u_top_out_id") + nl.next_nets("u_top_state") + nl.nets("out_valid"),
                    lambda c: z3.Or(z3.And(z3.Not(top_accept(c)), tn("out_id", c) != t("out_id", c)),
                                    z3.And(stn(c) == S_ACCEPT, st(c) != S_ACCEPT,
                                           z3.Not(z3.And(st(c) == S_OUT, one("out_ready", c)))),
                                    z3.And(top_accept(c), stn(c) != S_LOOKUP),
                                    one("out_valid", c) != (st(c) == S_OUT)))]),
        ("3d W_IDLE re-entered only when the transaction completes (DECIDE & !needs_attr, or matching attr beat)",
         "unsat", [("idle", nl.next_nets("wstate") + nl.nets("needs_attr") + nl.nets("top_attr_valid"),
                    lambda c: z3.And(wsn(c) == W_IDLE, ws(c) != W_IDLE, z3.Not(done(c))))]),
        # ---- 4
        ("4a needs_attr valid throughout LOOKUP: == (LOOKUP and not cache-served), 0 elsewhere", "unsat", [
            ("needs_attr", nl.nets("needs_attr") + nl.next_nets("u_top_out_skipped") + nl.next_nets("u_top_state"),
             lambda c: z3.Or(z3.And(st(c) != S_LOOKUP, needs(c)),
                             z3.And(st(c) == S_LOOKUP, needs(c) != (tn("out_skipped", c) == 0)),
                             z3.And(st(c) == S_LOOKUP, needs(c), stn(c) != S_ATTR),
                             z3.And(st(c) == S_LOOKUP, z3.Not(needs(c)), stn(c) != S_RASTER)))]),
        ("4b ATTR waits for attr_valid with state and datapath registers frozen", "unsat", [
            ("attr_wait", dp_roots, lambda c: z3.And(st(c) == S_ATTR, z3.Not(attr_ok(c)), z3.Or(
                stn(c) != S_ATTR, *[tn(n, c) != t(n, c) for n in frozen])))]),
        ("4c ATTR & attr_valid -> PROJ phase 0 with base-field consumers == the attr beat (rho_q = fx_add(rho, dr_q))",
         "unsat", [("attr_take", dp_roots, lambda c: z3.And(jc(c), st(c) == S_ATTR, attr_ok(c), z3.Or(
             stn(c) != S_PROJ, tn("phase", c) != 0, *[tn(n, c) != f(c) for n, f in attr_dp])))]),
        ("4d a cache-served transaction never enters ATTR; ATTR entered only from LOOKUP with needs_attr", "unsat", [
            ("attr_entry", nl.next_nets("u_top_state") + nl.next_nets("u_top_out_skipped") + nl.nets("needs_attr"),
             lambda c: z3.Or(z3.And(stn(c) == S_ATTR, st(c) != S_ATTR, z3.Not(z3.And(st(c) == S_LOOKUP, needs(c)))),
                             z3.And(st(c) == S_LOOKUP, tn("out_skipped", c) == 1, stn(c) == S_ATTR)))]),
        # ---- 5
        ("5a rank R non-increasing; == 0 exactly on completion; equal on stalls", "unsat", [
            ("rank_mono", w_roots, lambda c: z3.And(jc(c), z3.Or(
                z3.UGT(rank_next(c), rank(ws(c))), (rank_next(c) == 0) != done(c),
                z3.And(z3.Not(handshake(c)), rank_next(c) != rank(ws(c))))))]),
        ("5b rank strictly decreases on meta accept, top accept, DECIDE branch and attr accept", "unsat", [
            ("rank_dec", w_roots, lambda c: z3.And(jc(c), handshake(c), z3.Not(z3.ULT(rank_next(c), rank(ws(c))))))]),
        ("5c enabledness: W_IDLE & meta tvalid -> accept; W_PRESENT & top ready -> accept; W_DECIDE never stalls; "
         "W_ATTR & tvalid & matching tuser -> attr accept", "unsat", [
            ("enabled", w_roots + nl.nets("s_axis_meta_tready") + nl.nets("u_top_accept"), lambda c: z3.Or(
                z3.And(ws(c) == W_IDLE, one("s_axis_meta_tvalid", c), z3.Not(meta_fire(c))),
                z3.And(ws(c) == W_PRESENT, one("top_in_ready", c), z3.Not(top_accept(c))),
                z3.And(ws(c) == W_DECIDE, z3.Not(handshake(c))),
                z3.And(ws(c) == W_ATTR, one("s_axis_attr_tvalid", c), cur("s_axis_attr_tuser", c) == meta_id(c),
                       z3.Not(attr_ok(c)))))]),
        ("5d top control invariant (ATTR legal, Q_FRAC=4 divider) at reset", "unsat", [
            ("ctl_reset", ctl_roots, lambda c: z3.And(*ctl_reset, z3.Not(ctl_inv(get_t, c))))]),
        ("5e top control invariant inductive", "unsat", [
            ("ctl_next", ctl_roots, lambda c: z3.And(ctl_inv(get_t, c), z3.Not(ctl_inv(lambda n, cc: tn(n, cc), c))))]),
        ("5f top rank: strict decrease in LOOKUP/PROJ/DIV/INV/RASTER, ATTR non-increasing and strict on attr_valid",
         "unsat", [("top_rank", ctl_roots + nl.nets("top_attr_valid"), lambda c: z3.And(
             ctl_inv(get_t, c), busy(c), stn(c) != S_ACCEPT, z3.Or(
                 z3.And(st(c) != S_ATTR, z3.Not(z3.ULT(top_rank_n(c), top_rank(c)))),
                 z3.And(st(c) == S_ATTR, z3.UGT(top_rank_n(c), top_rank(c))),
                 z3.And(st(c) == S_ATTR, attr_ok(c), z3.Not(z3.ULT(top_rank_n(c), top_rank(c)))))))]),
        # ---- 6
        ("6a witness: skip + hit DECIDE completes without an attribute request (cache_bypass == 0)", "sat", [
            ("skip_hit", w_roots + nl.next_nets("u_top_out_skipped"), lambda c: z3.And(
                jc(c), cur("cache_bypass", c) == 0, ws(c) == W_DECIDE, z3.Not(needs(c)), tn("out_skipped", c) == 1,
                wsn(c) == W_IDLE))]),
        ("6b witness: miss DECIDE requests attributes", "sat", [
            ("miss", w_roots, lambda c: z3.And(jc(c), ws(c) == W_DECIDE, needs(c), wsn(c) == W_ATTR))]),
    ]


def miss_from_reset(nl, breaker, depth=3):
    roots = nl.nets("needs_attr") + nl.nets("wstate")
    unrolled = Unrolled([(nl, "")], {"": roots}, depth)
    get = lambda n: nl.bv(nl.nets(n))
    goal = unrolled.at(nl, "", z3.And(get("wstate") == W_DECIDE, get("needs_attr") == 1), depth)
    start = time.monotonic()
    result, _, _ = breaker.solve([*unrolled.constraints, goal], f"bmc-{depth} miss")
    return dict(name=f"6c witness: miss DECIDE reachable from reset (BMC-{depth})", target=nl.target,
                status=str(result).upper(), method=f"BMC-{depth}", seconds=round(time.monotonic() - start, 2),
                cones=1, expect="sat")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlists", type=Path, default=ROOT / "build/formal/netlist")
    ap.add_argument("--out", type=Path, default=ROOT / "build/formal")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with open(args.out / "formal_4dgs_axi.log", "w", encoding="utf-8") as log:
        breaker = Breaker(log)
        start = time.monotonic()
        nl = Netlist(args.netlists / "gs4d_axi", "gs4d_axi")
        breaker.say(f"== gs4d_axi: {nl.gates} gates, {len(nl.regs)} registers")
        checks = axi_props(nl)
        for name, expect, cones in checks:
            res = breaker.check(nl, name, cones)
            witness = res.pop("witness", None)
            if witness is not None and expect == "unsat":
                path = args.out / f"gs4d_axi_counterexample_{len(results)}.txt"
                path.write_text(witness[0].sexpr() + "\n", encoding="utf-8")
                breaker.say(f"  SAT witness: failing cones {witness[1]}; model saved to {path}")
            res["expect"], res["note"] = expect, None
            results.append(res)
        results.append(miss_from_reset(nl, breaker))
        for res in results:
            res["verdict"] = "FAIL" if res["status"] == "BOUNDED" else verdict(res)
        summary = dict(z3=z3.get_version_string(), timeout_s=breaker.timeout_ms / 1000,
                       queries=breaker.queries, seconds=round(time.monotonic() - start, 2), results=results)
        (args.out / "formal_4dgs_axi.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
        breaker.say("\n== summary")
        for res in results:
            breaker.say(f"{res['verdict']:>16}  {res['target']}: {res['name']} -> {res['status']} "
                        f"via {res['method']} in {res['seconds']}s")
        bad = [r for r in results if r["verdict"] == "FAIL"]
        breaker.say(f"formal_4dgs_axi: {len(results) - len(bad)}/{len(results)} obligations as expected, "
                    f"{breaker.queries} Z3 queries")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
