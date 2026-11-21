#!/usr/bin/env python3
"""CEGAR self-test: injected RTL fault -> Z3 SAT -> directed trace -> Verilator FAIL.

1. Copy ssim_custom_shift_safe.v to build/formal/cegar/<fault>/ and inject an
   off-by-one boundary bug (--inject-fault).
2. Netlist the faulty copy (rtl_to_netlist.py) and run the verify_z3.py next-state
   proof on it under the circuit breakers: it must come back SAT.
3. Build a golden-vs-faulty miter, unroll it from reset for k <= 20 cycles (one Z3
   query per output port, cut to that port's sequential COI) and extract the input
   assignment of the SAT model.
4. Write the witness as cegar_trace.hex (inputs plus golden expected outputs from a
   cycle-accurate simulation of the golden netlist) and replay it with the existing
   sim_main_ssim.cpp testbench (--cegar): golden RTL must PASS, faulty RTL must FAIL
   at the predicted cycle.
"""
import argparse
from pathlib import Path
import re
import shlex
import subprocess
import sys

import z3

import rtl_to_netlist
from verify_z3 import shift_props
from z3_engine import BMC_DEPTH, Breaker, Netlist, ROOT, Unrolled, simulate

SHIFT_RTL = "rtl_baseline/ssim_eval/ssim_custom_shift_safe.v"
FAULTS = {  # off-by-one boundary bugs in the window shifter
    "valid_boundary": ("counter > 9'd17 && counter < 9'd290", "counter >= 9'd17 && counter < 9'd290"),
    "shift_boundary": ("if (counter < 9'd14) begin", "if (counter <= 9'd14) begin"),
}
OUTPUTS = ["out_valid", "out_p00", "out_p01", "out_p02", "out_p10", "out_p11", "out_p12",
           "out_p20", "out_p21", "out_p22"]
SSIM_RTL = ["ref_model/rtl/sim_substitutes/ssim_fp16.v", "rtl_baseline/ssim_eval/SSIM_v2.v", SHIFT_RTL,
            "rtl_baseline/ssim_eval/fp_operation.v", "rtl_baseline/ssim_eval/SRAM_IMG_GT.v",
            "rtl_baseline/ssim_eval/SRAM_other.v", "rtl_baseline/sim/ssim_sim_top.sv"]


def inject(fault, out):
    old, new = FAULTS[fault]
    text = (ROOT / SHIFT_RTL).read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"fault site for {fault} not found exactly once in {SHIFT_RTL}")
    out.mkdir(parents=True, exist_ok=True)
    faulty = out / "ssim_custom_shift_safe.v"
    faulty.write_text(text.replace(old, new), encoding="utf-8")
    return faulty, f"{old}  ->  {new}"


def miter(golden, faulty, breaker, ports):
    """First output port whose golden/faulty values differ within BMC_DEPTH cycles."""
    for port in ports:
        roots = golden.nets(port)
        u = Unrolled([(golden, "g."), (faulty, "f.")], {"g.": roots, "f.": faulty.nets(port)}, BMC_DEPTH)
        cones = len(u.regs["g."]) + len(u.regs["f."])
        breaker.say(f"  miter {port}: sequential COI {cones} registers x {BMC_DEPTH} frames")
        diff = [z3.Or(*[u.at(golden, "g.", golden.expr(g), t) != u.at(faulty, "f.", faulty.expr(f), t)
                        for g, f in zip(roots, faulty.nets(port))]) for t in range(1, BMC_DEPTH + 1)]
        result, model, _ = breaker.solve([*u.constraints, z3.Or(*diff)], f"bmc-{BMC_DEPTH} miter {port}")
        if result == z3.sat:
            return port, u, model
        if result == z3.unknown:
            breaker.say(f"  BOTTLENECK miter {port}: unknown after 3 attempts, trying next port")
    return None, None, None


def word(values, nets):
    return sum(1 << i for i, n in enumerate(nets) if values[n])


def write_trace(path, golden, u, model, fault, port):
    frames = []
    for t in range(BMC_DEPTH):
        frames.append({n: z3.is_true(model.eval(u.var("", n, t), model_completion=True))
                       for name in ("in_valid", "in_pixel") for n in golden.nets(name)})
    sim = simulate(golden, frames)
    lines = [f"# CEGAR witness for injected fault {fault} (Z3 SAT on miter port {port}, k={BMC_DEPTH})",
             "# cycle shift_valid pixel | expected after rising edge: window_valid p00 p01 p02 p10 p11 p12 p20 p21 p22"]
    for t, inputs in enumerate(frames):
        after = sim[t + 1]
        outs = [word(after, golden.nets(o)) for o in OUTPUTS]
        lines.append(" ".join([f"{t:x}", f"{word(inputs, golden.nets('in_valid')):x}",
                               f"{word(inputs, golden.nets('in_pixel')):012x}", f"{outs[0]:x}"]
                              + [f"{o:012x}" for o in outs[1:]]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return frames, sim


def first_mismatch(golden_sim, faulty_sim, golden, faulty):
    for t in range(1, len(golden_sim)):
        for o in OUTPUTS:
            if word(golden_sim[t], golden.nets(o)) != word(faulty_sim[t], faulty.nets(o)):
                return t - 1, o
    return None, None


def verilate(mdir, shift_rtl, args, trace=False):
    sources = [str(ROOT / s) for s in SSIM_RTL]
    sources[2] = str(shift_rtl)
    cmd = [args.verilator, "--cc", "--exe", "--build", "-O2", *(["--trace"] if trace else []),
           *shlex.split(args.lint_flags),
           "--top-module", "ssim_sim_top", "--Mdir", str(mdir), *sources,
           str(ROOT / "rtl_baseline/sim/sim_main_ssim.cpp")]
    with open(mdir.parent / f"{mdir.name}.verilator.log", "w", encoding="utf-8") as log:
        if subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode:
            raise RuntimeError(f"Verilator build failed; see {log.name}")
    return mdir / "Vssim_sim_top"


def replay(binary, trace, breaker, label, wave=None):
    cmd = [str(binary), "--cegar", str(trace), *([str(wave)] if wave else [])]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    breaker.say(f"  {label}: exit {result.returncode}: {(result.stdout + result.stderr).strip()}")
    return result.returncode, result.stdout


def run_fault(fault, args, breaker):
    out = args.out / fault
    faulty_rtl, diff = inject(fault, out / "rtl")
    breaker.say(f"== fault {fault}: {diff}")
    tools = (args.yosys, args.sv2v, args.abc)
    rtl_to_netlist.build("ssim_custom_shift_safe", out / "netlist", *tools, sources=[str(faulty_rtl)])
    golden_dir = args.netlists / "ssim_custom_shift_safe"
    if not (golden_dir / "ssim_custom_shift_safe.json").exists():
        rtl_to_netlist.build("ssim_custom_shift_safe", golden_dir, *tools)
    golden = Netlist(golden_dir, "ssim_custom_shift_safe")
    faulty = Netlist(out / "netlist", "ssim_custom_shift_safe")

    (name, _, _, cones), = shift_props(faulty)[0]
    res = breaker.check(faulty, name, cones)
    if res["status"] != "SAT":
        raise RuntimeError(f"Z3 did not refute the faulty RTL ({res['status']})")
    model, failing = res["witness"], res["failing_cones"]
    state = {r: sum(1 << i for i, n in enumerate(faulty.nets(r))
                    if z3.is_true(model.eval(faulty.expr(n), True))) for r in ("counter", "dir", "is_valid")}
    breaker.say(f"  Z3 SAT: failing cones {failing[:6]}{' ...' if len(failing) > 6 else ''}; "
                f"witness state {state}")

    ports = sorted(OUTPUTS, key=lambda o: not any(f.startswith(o + "_next") for f in failing))
    port, u, bmc_model = miter(golden, faulty, breaker, ports)
    if port is None:
        raise RuntimeError(f"no reset-reachable mismatch within {BMC_DEPTH} cycles")
    trace = out / "cegar_trace.hex"
    frames, golden_sim = write_trace(trace, golden, u, bmc_model, fault, port)
    cycle, signal = first_mismatch(golden_sim, simulate(faulty, frames), golden, faulty)
    breaker.say(f"  trace {trace.relative_to(ROOT)}: {len(frames)} cycles, predicted FAIL at cycle {cycle} ({signal})")

    golden_bin = args.golden_bin or verilate(out / "obj_golden", ROOT / SHIFT_RTL, args)
    faulty_bin = verilate(out / "obj_faulty", faulty_rtl, args, trace=True)
    rc_golden, _ = replay(golden_bin, trace, breaker, "golden RTL replay")
    wave = out / "cegar_faulty.vcd"
    rc_faulty, log = replay(faulty_bin, trace, breaker, "faulty RTL replay", wave)
    breaker.say(f"  waveform {wave.relative_to(ROOT) if wave.is_relative_to(ROOT) else wave}"
                f" ({wave.stat().st_size if wave.exists() else 0} bytes)")
    reproduced = re.search(r"CEGAR FAIL: cycle (\d+)", log)
    ok = rc_golden == 0 and rc_faulty == 1 and wave.exists() and reproduced and int(reproduced.group(1)) == cycle
    breaker.say(f"  {'PASS' if ok else 'FAIL'}: fault {fault} "
                f"{'reproduced as Verilator FAIL at cycle %d' % cycle if ok else 'not reproduced as predicted'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject-fault", action="append", choices=list(FAULTS),
                    help="fault to inject (repeatable; default: all)")
    ap.add_argument("--out", type=Path, default=ROOT / "build/formal/cegar")
    ap.add_argument("--netlists", type=Path, default=ROOT / "build/formal/netlist")
    ap.add_argument("--golden-bin", type=Path, help="prebuilt golden Vssim_sim_top (make sim)")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--sv2v", default="sv2v")
    ap.add_argument("--abc", default="yosys-abc")
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--lint-flags", default="-Wall -Wno-DECLFILENAME -Wno-UNUSED")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "cegar.log", "w", encoding="utf-8") as log:
        breaker = Breaker(log)
        results = {f: run_fault(f, args, breaker) for f in args.inject_fault or FAULTS}
    print(f"test_cegar: {sum(results.values())}/{len(results)} injected faults refuted by Z3 "
          f"and reproduced in Verilator; log {args.out / 'cegar.log'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"test_cegar: {error}", file=sys.stderr)
        sys.exit(1)
