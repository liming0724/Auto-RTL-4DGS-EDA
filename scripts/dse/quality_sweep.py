#!/usr/bin/env python3
"""Phase 4 frame-level quality flow for gs4d_accelerator_top (build/4dgs/quality/).

  frame  Verilator -Wall lint of the RTL, build the frame testbench
         (rtl_4dgs/sim/sim_main_4dgs_frame.cpp) and the SSIM_v2 frame scorer
         (rtl_4dgs/sim/sim_main_ssim_frame.cpp), run the FP32-vs-fixed validation point
         (Q_FRAC=12, MOTION_THRESH=0) and one lossy point (Q_FRAC=8, MOTION_THRESH=16)
  sweep    Q_FRAC {8, 12} x MOTION_THRESH {0, 1, 2, 3, 4, 8, 16, 32, 64}: skip rate, total
           cycles, speedup vs. unskipped, PSNR vs. FP32, hardware SSIM (SSIM_v2); writes
           quality_vs_skip.csv / .json / .md
  profile  Bandwidth profiler (build/4dgs/bandwidth/), Q_FRAC {8, 12} x three modes on a
           CACHE_WAYS=4 build: Baseline (cache_bypass=1, T=0), Skip-Only (cache_bypass=1,
           T=16), Full Co-Design (cache_bypass=0, T=16). Modeled External DRAM Traffic per
           frame (fetch_count * BYTES_PER_GAUSSIAN), cycles, skip rate, PSNR, hardware SSIM;
           writes bandwidth_profile.csv / .json / .md
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
BW_OUT = ROOT / "build/4dgs/bandwidth"
G4_FILES = [RTL / f"{m}.sv" for m in ("gs4d_accelerator_top", "deform_engine", "temporal_skip_unit",
                                       "motion_aware_cache", "SRAM_gs4d_cache", "gs4d_raster_lane")]
SSIM_FILES = [ROOT / "ref_model/rtl/sim_substitutes/ssim_fp16.v",
              *[ROOT / "rtl_baseline/ssim_eval" / f for f in ("SSIM_v2.v", "ssim_custom_shift_safe.v",
                                                               "fp_operation.v", "SRAM_IMG_GT.v", "SRAM_other.v")],
              ROOT / "rtl_baseline/sim/ssim_sim_top.sv"]
CFLAGS = f"-std=c++17 -O2 -I{ROOT / 'ref_model/include'} -I{ROOT / 'golden_model'}"
Q_FRACS = (8, 12)
THRESHOLDS = (0, 1, 2, 3, 4, 8, 16, 32, 64)
PROFILE_WAYS = 4
# (mode, cache_bypass, MOTION_THRESH)
PROFILE_MODES = (("Baseline", 1, 0), ("Skip-Only", 1, 16), ("Full Co-Design", 0, 16))


def run(cmd, log, timeout=1800):
    with open(log, "w", encoding="utf-8") as out:
        result = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    text = Path(log).read_text(encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} failed; see {log}")
    return text


def result_of(text, log, key="RESULT"):
    match = re.search(rf"^{key} (\{{.*\}})$", text, re.M)
    if not match:
        raise RuntimeError(f"no {key} line in {log}")
    return json.loads(match.group(1))


def build_frame(q_frac, verilator, ways=None):
    out = OUT / f"build_q{q_frac}" if ways is None else BW_OUT / f"build_q{q_frac}_w{ways}"
    out.mkdir(parents=True, exist_ok=True)
    gparams = [f"-GQ_FRAC={q_frac}"] + ([] if ways is None else [f"-GCACHE_WAYS={ways}"])
    defs = f"-DQ_FRAC={q_frac}" + ("" if ways is None else f" -DCACHE_WAYS={ways}")
    log = run([verilator, "--lint-only", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
               *gparams, *G4_FILES], out / "lint.log")
    if "%Warning" in log or "%Error" in log:
        raise RuntimeError(f"lint warnings; see {out / 'lint.log'}")
    run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
         *gparams, "--Mdir", out / "obj_dir", "-CFLAGS", f"{CFLAGS} {defs}",
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


def profile_point(q_frac, mode, bypass, thresh, frame_bin, ssim_bin):
    out = BW_OUT / f"q{q_frac}_{mode.lower().replace(' ', '_').replace('-', '_')}"
    out.mkdir(parents=True, exist_ok=True)
    text = run([frame_bin, f"+thresh={thresh}", f"+bypass={bypass}", f"+out={out}"], out / "frame.log")
    res = result_of(text, out / "frame.log")
    res.update(result_of(text, out / "frame.log", "BANDWIDTH"))
    ssim = result_of(run([ssim_bin, out, 8], out / "ssim.log"), out / "ssim.log")
    res.update(mode=mode, hw_ssim=ssim["hw_ssim"], pass_=res["pass"] and ssim["pass"])
    res["skip_eligible_rate"] = res["skip_eligible"] / res["transactions"]
    res["dram_read_bytes_per_frame"] = res["dram_read_bytes"] / res["frames"]
    res["dram_read_bytes_conservative_per_frame"] = res["dram_read_bytes_conservative"] / res["frames"]
    print(f"Q_FRAC={q_frac:2d} {mode:<14} bypass={bypass} T={thresh:2d}: fetch={res['fetch_count']} "
          f"dram_read_bytes/frame={res['dram_read_bytes_per_frame']:.1f} cycles={res['cycles']} "
          f"skip={100 * res['skip_rate']:.1f}% PSNR={res['psnr_db']:.2f} dB HW-SSIM={res['hw_ssim']:.5f} "
          f"{'PASS' if res['pass_'] else 'FAIL'}", flush=True)
    return res


def write_profile(rows):
    for r in rows:
        base = next(b for b in rows if b["q_frac"] == r["q_frac"] and b["mode"] == PROFILE_MODES[0][0])
        r["read_reduction_pct"] = 100 * (1 - r["dram_read_bytes"] / base["dram_read_bytes"])
        r["read_reduction_conservative_pct"] = 100 * (1 - r["dram_read_bytes_conservative"]
                                                      / base["dram_read_bytes_conservative"])
        r["speedup_vs_baseline"] = base["cycles"] / r["cycles"]
    keys = ["q_frac", "mode", "cache_bypass", "cache_ways", "motion_thresh", "frames", "transactions",
            "input_bits_per_gaussian", "bytes_per_gaussian", "served_bytes_per_gaussian", "skip_eligible",
            "skip_eligible_rate", "skipped", "skip_rate", "fetch_count", "cache_served", "dram_read_bytes",
            "dram_read_bytes_per_frame", "read_reduction_pct", "dram_read_bytes_conservative",
            "dram_read_bytes_conservative_per_frame", "read_reduction_conservative_pct", "dram_write_bytes",
            "frame_fetch_count", "frame_dram_read_bytes", "cycles", "speedup_vs_baseline", "psnr_db", "hw_ssim",
            "mismatches"]
    flat = [{**r, "frame_fetch_count": " ".join(map(str, r["frame_fetch_count"])),
             "frame_dram_read_bytes": " ".join(map(str, r["frame_dram_read_bytes"]))} for r in rows]
    with open(BW_OUT / "bandwidth_profile.csv", "w", newline="", encoding="utf-8") as f:
        f.write("# Modeled External DRAM Traffic (fetch_count * BYTES_PER_GAUSSIAN), not measured "
                "DRAM-controller traffic\n")
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)
    (BW_OUT / "bandwidth_profile.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    lines = ["Modeled External DRAM Traffic (not measured DRAM-controller traffic)", "",
             "| Q_FRAC | Mode | cache_bypass | T | dram_read_bytes/frame | Reduction | dram_write_bytes | "
             "Total cycles | Speedup | Skip rate | PSNR (dB) | HW SSIM |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['q_frac']} | {r['mode']} | {r['cache_bypass']} | {r['motion_thresh']} | "
              f"{r['dram_read_bytes_per_frame']:,.0f} | {r['read_reduction_pct']:.1f}% | 0 | {r['cycles']:,} | "
              f"{r['speedup_vs_baseline']:.3f}x | {100 * r['skip_rate']:.1f}% | {r['psnr_db']:.2f} | "
              f"{r['hw_ssim']:.5f} |" for r in rows]
    (BW_OUT / "bandwidth_profile.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


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
    ap.add_argument("mode", choices=("frame", "sweep", "profile"))
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ssim_bin = build_ssim(args.verilator)
    if args.mode == "profile":
        BW_OUT.mkdir(parents=True, exist_ok=True)
        bins = {q: build_frame(q, args.verilator, PROFILE_WAYS) for q in Q_FRACS}
        runs = [(q, *m) for q in Q_FRACS for m in PROFILE_MODES]
        with ThreadPoolExecutor(args.jobs) as pool:
            rows = list(pool.map(lambda p: profile_point(*p, bins[p[0]], ssim_bin), runs))
        write_profile(rows)
        bad = [r for r in rows if not r["pass_"]]
        for r in bad:
            print(f"FAIL: Q_FRAC={r['q_frac']} {r['mode']}; see {BW_OUT}")
        return 1 if bad else 0
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
