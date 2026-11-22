#!/usr/bin/env python3
"""Phase 4 frame-level quality flow for gs4d_accelerator_top (build/4dgs/quality/).

  frame  Verilator -Wall lint of the RTL, build the frame testbench
         (rtl_4dgs/sim/sim_main_4dgs_frame.cpp) and the SSIM_v2 frame scorer
         (rtl_4dgs/sim/sim_main_ssim_frame.cpp), run the FP32-vs-fixed validation point
         (Q_FRAC=12, MOTION_THRESH=0) and one lossy point (Q_FRAC=8, MOTION_THRESH=16)
  sweep  Q_FRAC {8, 12} x MOTION_THRESH {0, 4, 8, 16, 32, 64}: skip rate, total cycles,
         speedup vs. unskipped, PSNR vs. FP32, hardware SSIM (SSIM_v2); writes
         quality_vs_skip.csv / .json / .md
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl_4dgs"
OUT = ROOT / "build/4dgs/quality"
G4_FILES = [RTL / f"{m}.sv" for m in ("gs4d_accelerator_top", "deform_engine", "temporal_skip_unit",
                                       "motion_aware_cache", "SRAM_gs4d_cache", "gs4d_raster_lane")]
SSIM_FILES = [ROOT / "ref_model/rtl/sim_substitutes/ssim_fp16.v",
              *[ROOT / "rtl_baseline/ssim_eval" / f for f in ("SSIM_v2.v", "ssim_custom_shift_safe.v",
                                                               "fp_operation.v", "SRAM_IMG_GT.v", "SRAM_other.v")],
              ROOT / "rtl_baseline/sim/ssim_sim_top.sv"]
CFLAGS = f"-std=c++17 -O2 -I{ROOT / 'ref_model/include'} -I{ROOT / 'golden_model'}"
Q_FRACS = (8, 12)
THRESHOLDS = (0, 4, 8, 16, 32, 64)


def run(cmd, log, timeout=1800):
    with open(log, "w", encoding="utf-8") as out:
        result = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    text = Path(log).read_text(encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} failed; see {log}")
    return text


def result_of(text, log):
    match = re.search(r"^RESULT (\{.*\})$", text, re.M)
    if not match:
        raise RuntimeError(f"no RESULT line in {log}")
    return json.loads(match.group(1))


def build_frame(q_frac, verilator):
    out = OUT / f"build_q{q_frac}"
    out.mkdir(parents=True, exist_ok=True)
    log = run([verilator, "--lint-only", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
               f"-GQ_FRAC={q_frac}", *G4_FILES], out / "lint.log")
    if "%Warning" in log or "%Error" in log:
        raise RuntimeError(f"lint warnings; see {out / 'lint.log'}")
    run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
         f"-GQ_FRAC={q_frac}", "--Mdir", out / "obj_dir", "-CFLAGS", f"{CFLAGS} -DQ_FRAC={q_frac}",
         *G4_FILES, RTL / "sim/sim_main_4dgs_frame.cpp"], out / "build.log")
    return out / "obj_dir/Vgs4d_accelerator_top"


def build_ssim(verilator):
    out = OUT / "build_ssim"
    out.mkdir(parents=True, exist_ok=True)
    run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSED",
         "-Wno-EOFNEWLINE" if not verilator_v4(verilator) else "-Wno-fatal",
         "--top-module", "ssim_sim_top", "--Mdir", out / "obj_dir", "-CFLAGS", CFLAGS,
         *SSIM_FILES, RTL / "sim/sim_main_ssim_frame.cpp"], out / "build.log")
    return out / "obj_dir/Vssim_sim_top"


def verilator_v4(verilator):
    text = subprocess.run([verilator, "--version"], capture_output=True, text=True, check=True).stdout
    return text.split()[1].startswith("4.")


def point(q_frac, thresh, frame_bin, ssim_bin):
    out = OUT / f"q{q_frac}_t{thresh}"
    out.mkdir(parents=True, exist_ok=True)
    res = result_of(run([frame_bin, f"+thresh={thresh}", f"+out={out}"], out / "frame.log"), out / "frame.log")
    ssim = result_of(run([ssim_bin, out, 8], out / "ssim.log"), out / "ssim.log")
    res.update(hw_ssim=ssim["hw_ssim"], sw_ssim=ssim["sw_ssim"], ssim_max_tile_diff=ssim["max_tile_diff"],
               identical_hw_ssim=ssim["identical_hw_ssim"], pass_=res["pass"] and ssim["pass"])
    print(f"Q_FRAC={q_frac:2d} MOTION_THRESH={thresh:2d}: skip={100 * res['skip_rate']:5.1f}% "
          f"cycles={res['cycles']} speedup={res['speedup']:.3f}x PSNR={res['psnr_db']:.2f} dB "
          f"HW-SSIM={res['hw_ssim']:.5f} {'PASS' if res['pass_'] else 'FAIL'}", flush=True)
    return res


def pareto(rows):
    """Mark points not dominated in (speedup up, PSNR up) within each Q_FRAC."""
    for r in rows:
        r["pareto"] = not any(o["q_frac"] == r["q_frac"] and o is not r and o["speedup"] >= r["speedup"]
                              and o["psnr_db"] >= r["psnr_db"] and (o["speedup"] > r["speedup"]
                                                                    or o["psnr_db"] > r["psnr_db"]) for o in rows)


def write_reports(rows):
    keys = ["q_frac", "motion_thresh", "skip_rate", "cycles", "noskip_cycles", "speedup", "psnr_db", "hw_ssim",
            "sw_ssim", "ssim_max_tile_diff", "transactions", "skipped", "mismatches", "pareto"]
    with open(OUT / "quality_vs_skip.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (OUT / "quality_vs_skip.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    lines = ["| Q_FRAC | MOTION_THRESH | Skip rate | Total cycles | Speedup | PSNR (dB) | HW SSIM | Pareto |",
             "|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    lines += [f"| {r['q_frac']} | {r['motion_thresh']} | {100 * r['skip_rate']:.1f}% | {r['cycles']:,} | "
              f"{r['speedup']:.3f}x | {r['psnr_db']:.2f} | {r['hw_ssim']:.5f} | {'yes' if r['pareto'] else ''} |"
              for r in rows]
    (OUT / "quality_vs_skip.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("frame", "sweep"))
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ssim_bin = build_ssim(args.verilator)
    bins = {q: build_frame(q, args.verilator) for q in Q_FRACS}
    points = [(12, 0), (8, 16)] if args.mode == "frame" else [(q, t) for q in Q_FRACS for t in THRESHOLDS]
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda p: point(p[0], p[1], bins[p[0]], ssim_bin), points))
    if args.mode == "sweep":
        pareto(rows)
        write_reports(rows)
    bad = [r for r in rows if not r["pass_"]]
    for r in bad:
        print(f"FAIL: Q_FRAC={r['q_frac']} MOTION_THRESH={r['motion_thresh']}; see {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(error)
        raise SystemExit(1) from error
