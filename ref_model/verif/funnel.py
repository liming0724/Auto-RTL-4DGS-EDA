#!/usr/bin/env python3
"""DEVIN.md tiered verification funnel for the reference-model arithmetic RTL.

Tier 0  C++ model unit tests + deterministic synthetic 4DGS render.
Tier 1  Verilator -Wall lint, then >= 10k directed + constrained-random vectors
        per configuration of fx_mul_rs against gs4d::fx_mul (zero mismatches),
        then the full GS_RASTERIZER_pipeline.v (sim-only DW substitutes) on
        seeded synthetic scenes against RtlRasterizer<F32Policy>, pixel by pixel.
Tier 2  Yosys synthesis + Berkeley ABC `cec` of the RTL against an independent
        formulation (verif/fx_mul_rs_spec.v); reports AIG area / logic depth.
Tier 3  Z3 (via yosys-smtbmc) on the isolated fx_round_sat sub-module only.
        30 s per query, at most 3 consecutive retries, then COI slicing
        (one query per output bit), then depth-20 BMC.

A tier only runs after every check of the previous tier passed. On a failure
the counterexample is printed; the CEGAR protocol in DEVIN.md section 2 then
applies (inject the vector into tb_fx_mul_rs.cpp, inspect, patch, restart at
Tier 1). Generated files go to ref_model/build/ (git-ignored).
"""
import argparse
import itertools
import os
import re
import subprocess
import sys
import functools

print = functools.partial(print, flush=True)  # noqa: A001

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build", "funnel")
RTL = [os.path.join(ROOT, "rtl", "fx_round_sat.v"), os.path.join(ROOT, "rtl", "fx_mul_rs.v")]
SPEC = os.path.join(ROOT, "verif", "fx_mul_rs_spec.v")
Z3_TIMEOUT_S = 30
MAX_RETRIES = 3
FULL_MUL_CEC_S = 120  # multiplier miters are hard for SAT-based cec

# Operator instances taken from FixedConfig (arith.hpp): (name, WA, FA, WB, FB, WO, FO)
INSTANCES = [
    ("power  = conic*delta_sq", 20, 16, 29, 8, 25, 14),
    ("trans  = alpha*trans", 18, 16, 22, 20, 22, 20),
    ("color  = trans*color", 22, 20, 19, 16, 19, 16),
]
MODES = list(itertools.product((0, 1, 2), (1, 0)))  # (ROUND, SAT)


def run(cmd, timeout=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return p.returncode, p.stdout


def fail(msg, log=""):
    print("FAIL:", msg)
    if log:
        print(log[-4000:])
    sys.exit(1)


def tier0():
    print("== Tier 0: C++ reference model")
    os.makedirs(BUILD, exist_ok=True)
    inc = os.path.join(ROOT, "include")
    for src, exe in (("tests/test_fixed_point.cpp", "test_fixed_point"), ("tools/gs4d_ref.cpp", "gs4d_ref")):
        rc, log = run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + inc,
                       os.path.join(ROOT, src), "-o", os.path.join(BUILD, exe)])
        if rc:
            fail("compile " + src, log)
    rc, log = run([os.path.join(BUILD, "test_fixed_point")])
    print("  " + log.strip().splitlines()[-1])
    if rc:
        fail("unit tests", log)
    hashes = set()
    for _ in range(2):
        rc, log = run([os.path.join(BUILD, "gs4d_ref"), "--seed", "1", "--n", "600", "--t", "0.5"])
        if rc:
            fail("gs4d_ref", log)
        hashes.add(re.search(r"hash (.*)", log).group(1))
    if len(hashes) != 1:
        fail("render not deterministic")
    print("  deterministic render: " + hashes.pop())


def tier1(vectors):
    print("== Tier 1: Verilator lint + simulation")
    rc, log = run(["verilator", "--lint-only", "-Wall", "--top-module", "fx_mul_rs"] + RTL)
    if rc:
        fail("verilator lint", log)
    print("  lint -Wall: clean")
    tb = os.path.join(ROOT, "verif", "tb_fx_mul_rs.cpp")
    total = 0
    for (name, wa, fa, wb, fb, wo, fo), (rnd, sat) in itertools.product(INSTANCES, MODES):
        tag = "m_%d_%d_%d_%d_%d_%d_r%d_s%d" % (wa, fa, wb, fb, wo, fo, rnd, sat)
        mdir = os.path.join(BUILD, "obj_" + tag)
        params = dict(WA=wa, FA=fa, WB=wb, FB=fb, WO=wo, FO=fo, ROUND=rnd, SAT=sat)
        cmd = ["verilator", "--cc", "--exe", "--build", "-Wall", "-O2", "--top-module", "fx_mul_rs",
               "--Mdir", mdir, "-CFLAGS", "-std=c++17 -I" + os.path.join(ROOT, "include") +
               "".join(" -D%s=%d" % kv for kv in params.items())]
        cmd += ["-G%s=%d" % kv for kv in params.items()] + RTL + [tb]
        rc, log = run(cmd)
        if rc:
            fail("verilator build " + tag, log)
        rc, log = run([os.path.join(mdir, "Vfx_mul_rs"), str(vectors), "1"])
        line = log.strip().splitlines()[-1]
        print("  " + line)
        if rc:
            fail("simulation mismatch (CEGAR: replay the vector above)", log)
        total += int(re.search(r"(\d+) vectors", line).group(1))
    print("  total: %d vectors, 0 mismatches" % total)
    # Full baseline rasterizer (sim-only DW substitutes) vs RtlRasterizer<F32Policy>.
    rc, log = run(["bash", os.path.join(ROOT, "verif", "run_rtl_sim.sh")])
    for line in log.strip().splitlines():
        print("  " + line)
    if rc:
        fail("GS_RASTERIZER_pipeline vs C++ model mismatch", log)


def yosys_blif(srcs, top, params, out):
    sets = " ".join("-set %s %d" % kv for kv in params.items())
    script = ("read_verilog %s; chparam %s %s; hierarchy -top %s; synth -flatten -top %s; "
              "abc -g AND; opt_clean; stat; write_blif %s" % (" ".join(srcs), sets, top, top, top, out))
    rc, log = run(["yosys", "-q", "-p", script.replace("stat;", "tee -o %s.stat stat;" % out)])
    if rc:
        fail("yosys " + top, log)
    stat = open(out + ".stat").read()
    m = re.search(r"Number of cells:\s+(\d+)", stat)
    return int(m.group(1)) if m else -1


def abc_cec(a, b, timeout):
    try:
        rc, log = run(["yosys-abc", "-c", "cec %s %s" % (a, b)], timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    return ("Networks are equivalent" in log), log


def abc_depth(blif):
    rc, log = run(["yosys-abc", "-c", "read_blif %s; strash; print_stats" % blif])
    m = re.search(r"lev\s*=\s*(\d+)", log)
    return int(m.group(1)) if m else -1


def tier2():
    print("== Tier 2: Yosys + Berkeley ABC cec")
    for (name, wa, fa, wb, fb, wo, fo), (rnd, sat) in itertools.product(INSTANCES, MODES):
        sh, wp = fa + fb - fo, wa + wb
        tag = "%d_%d_%d_r%d_s%d" % (wp, sh, wo, rnd, sat)
        rs = dict(WP=wp, SH=sh, WO=wo, ROUND=rnd, SAT=sat)
        impl = os.path.join(BUILD, "rs_impl_%s.blif" % tag)
        spec = os.path.join(BUILD, "rs_spec_%s.blif" % tag)
        area = yosys_blif(RTL[:1], "fx_round_sat", rs, impl)
        yosys_blif([SPEC], "fx_round_sat_spec", rs, spec)
        ok, log = abc_cec(impl, spec, 300)
        if not ok:
            fail("cec fx_round_sat " + tag, log)
        tail = "full mul: not run (done for ROUND=2 SAT=1 only)"
        if (rnd, sat) != (2, 1):
            print("  %-24s ROUND=%d SAT=%d  round_sat: equivalent, %4d AND, depth %2d | %s"
                  % (name, rnd, sat, area, abc_depth(impl), tail))
            continue
        mp = dict(WA=wa, FA=fa, WB=wb, FB=fb, WO=wo, FO=fo, ROUND=rnd, SAT=sat)
        mimpl = os.path.join(BUILD, "mul_impl_%s.blif" % tag)
        mspec = os.path.join(BUILD, "mul_spec_%s.blif" % tag)
        marea = yosys_blif(RTL, "fx_mul_rs", mp, mimpl)
        yosys_blif([SPEC], "fx_mul_rs_spec", mp, mspec)
        mok, mlog = abc_cec(mimpl, mspec, FULL_MUL_CEC_S)
        if mok is None:
            fail("cec fx_mul_rs %s: timeout after %d s, equivalence not proven" % (tag, FULL_MUL_CEC_S), mlog)
        if not mok:
            fail("cec fx_mul_rs " + tag, mlog)
        print("  %-24s ROUND=%d SAT=%d  round_sat: equivalent, %4d AND, depth %2d | full mul: %s, %5d AND, depth %3d"
              % (name, rnd, sat, area, abc_depth(impl), "equivalent" if mok else "cec timeout (%d s)" % FULL_MUL_CEC_S,
                 marea, abc_depth(mimpl)))


SMT_SCRIPT = ("read_verilog {impl} {spec}; chparam {sets} fx_round_sat; chparam {sets} fx_round_sat_spec; "
              "{bitsel}proc; flatten; opt; miter -equiv -flatten -make_assert {gold} {gate} miter; "
              "hierarchy -top miter; opt -fast; write_smt2 {out}")


def smt_query(rs, out, bit=None):
    sets = " ".join("-set %s %d" % kv for kv in rs.items())
    gold, gate, bitsel = "fx_round_sat_spec", "fx_round_sat", ""
    if bit is not None:  # COI slice: keep only output bit `bit` in both designs
        wrap = out + ".wrap.v"
        with open(wrap, "w") as f:
            for m in ("fx_round_sat", "fx_round_sat_spec"):
                f.write("module %s_b(input [%d:0] p, output o); wire [%d:0] y; %s #(%s) u(%s, y); assign o = y[%d]; "
                        "endmodule\n" % (m, rs["WP"] - 1, rs["WO"] - 1, m,
                                         ", ".join(".%s(%d)" % kv for kv in rs.items()),
                                         ".p(p)", bit))
        bitsel = "read_verilog %s; " % wrap
        gold, gate = "fx_round_sat_spec_b", "fx_round_sat_b"
    script = SMT_SCRIPT.format(impl=RTL[0], spec=SPEC, sets=sets, bitsel=bitsel, gold=gold, gate=gate, out=out)
    rc, log = run(["yosys", "-q", "-p", script])
    if rc:
        fail("yosys write_smt2", log)


def smtbmc(smt2, depth):
    """One Z3 query with the 30 s budget. Returns 'pass', 'fail' or 'timeout'."""
    try:
        rc, log = run(["yosys-smtbmc", "-s", "z3", "-t", str(depth), "--dump-vlogtb", smt2 + ".cex.v", smt2],
                      timeout=Z3_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    return ("pass" if rc == 0 and "PASSED" in log else "fail"), log


def tier3():
    print("== Tier 3: Z3 on isolated fx_round_sat (timeout %d s, max %d retries)" % (Z3_TIMEOUT_S, MAX_RETRIES))
    for (name, wa, fa, wb, fb, wo, fo), (rnd, sat) in itertools.product(INSTANCES, MODES):
        rs = dict(WP=wa + wb, SH=fa + fb - fo, WO=wo, ROUND=rnd, SAT=sat)
        smt2 = os.path.join(BUILD, "rs_%d_%d_%d_r%d_s%d.smt2" % (rs["WP"], rs["SH"], wo, rnd, sat))
        smt_query(rs, smt2)
        res, log = "timeout", ""
        for attempt in range(1, MAX_RETRIES + 1):
            res, log = smtbmc(smt2, 1)
            if res != "timeout":
                break
            print("  timeout, retry %d/%d" % (attempt, MAX_RETRIES))
        how = "full miter"
        if res == "timeout":  # COI slicing fallback: one query per output bit
            how, res = "COI slices", "pass"
            for bit in range(wo):
                s = smt2 + ".b%d.smt2" % bit
                smt_query(rs, s, bit)
                r, log = smtbmc(s, 1)
                if r == "timeout":
                    r, log = smtbmc(s, 20)  # depth-20 BMC fallback
                    how = "COI slices + BMC-20"
                if r != "pass":
                    res = r
                    break
        if res != "pass":
            fail("Z3 %s on %s (counterexample testbench: %s.cex.v)" % (res, name, smt2), log)
        print("  %-24s ROUND=%d SAT=%d  UNSAT (equivalent) via %s" % (name, rnd, sat, how))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", type=int, default=20000, help="random vectors per Tier-1 configuration")
    ap.add_argument("--upto", type=int, default=3, help="last tier to run")
    args = ap.parse_args()
    tier0()
    if args.upto >= 1:
        tier1(args.vectors)
    if args.upto >= 2:
        tier2()
    if args.upto >= 3:
        tier3()
    print("FUNNEL PASS")


if __name__ == "__main__":
    main()
