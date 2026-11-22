#!/usr/bin/env python3
"""Frame-level quality and traffic modeling for gs4d_accelerator_top.

  frame  Verilator -Wall lint of the RTL, build the frame testbench
         (rtl_4dgs/sim/sim_main_4dgs_frame.cpp) and the SSIM_v2 frame scorer
         (rtl_4dgs/sim/sim_main_ssim_frame.cpp), run the FP32-vs-fixed validation point
         (Q_FRAC=12, MOTION_THRESH=0) and one lossy point (Q_FRAC=8, MOTION_THRESH=16)
  sweep  Q_FRAC {8, 12} x MOTION_THRESH {0, 1, 2, 3, 4, 8, 16, 32, 64}: skip rate, total cycles,
         speedup vs. unskipped, PSNR vs. FP32, hardware SSIM (SSIM_v2); writes
         quality_vs_skip.csv / .json / .md
  profile  Q_FRAC {8, 12} x three cache/threshold modes: Modeled External DRAM Traffic
           per frame and frame-set quality/cycles; writes build/4dgs/bandwidth/bandwidth_profile.csv
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
PROFILE_OUT = ROOT / "build/4dgs/bandwidth"
TRAFFIC_LABEL = "Modeled External DRAM Traffic"
G4_FILES = [RTL / f"{m}.sv" for m in ("gs4d_accelerator_top", "deform_engine", "temporal_skip_unit",
                                       "motion_aware_cache", "SRAM_gs4d_cache", "gs4d_raster_lane")]
SSIM_FILES = [ROOT / "ref_model/rtl/sim_substitutes/ssim_fp16.v",
              *[ROOT / "rtl_baseline/ssim_eval" / f for f in ("SSIM_v2.v", "ssim_custom_shift_safe.v",
                                                               "fp_operation.v", "SRAM_IMG_GT.v", "SRAM_other.v")],
              ROOT / "rtl_baseline/sim/ssim_sim_top.sv"]
CFLAGS = f"-std=c++17 -O2 -I{ROOT / 'ref_model/include'} -I{ROOT / 'golden_model'}"
Q_FRACS = (8, 12)
THRESHOLDS = (0, 1, 2, 3, 4, 8, 16, 32, 64)
MODES = (("Baseline", 0, 2, True), ("Skip-Only", 16, 2, True), ("Full Co-Design", 16, 4, False))


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


def build_frame(q_frac, verilator, cache_ways=2):
    out = OUT / (f"build_q{q_frac}" if cache_ways == 2 else f"build_q{q_frac}_w{cache_ways}")
    params = [f"-GQ_FRAC={q_frac}", f"-GCACHE_WAYS={cache_ways}"]
    cflags = f"{CFLAGS} -DQ_FRAC={q_frac} -DCACHE_WAYS={cache_ways}"
    out.mkdir(parents=True, exist_ok=True)
    log = run([verilator, "--lint-only", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
               *params, *G4_FILES], out / "lint.log")
    if "%Warning" in log or "%Error" in log:
        raise RuntimeError(f"lint warnings; see {out / 'lint.log'}")
    run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", f"-I{RTL}", "--top-module", "gs4d_accelerator_top",
         *params, "--Mdir", out / "obj_dir", "-CFLAGS", cflags,
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


def point(q_frac, thresh, frame_bin, ssim_bin, out, bypass=False):
    out.mkdir(parents=True, exist_ok=True)
    frame_log = out / "frame.log"
    cmd = [frame_bin, f"+thresh={thresh}", f"+out={out}"]
    if bypass:
        cmd.append("+cache_bypass")
    res = result_of(run(cmd, frame_log), frame_log)
    ssim = result_of(run([ssim_bin, out, len(res[TRAFFIC_LABEL]["frames"])], out / "ssim.log"), out / "ssim.log")
    res.update(hw_ssim=ssim["hw_ssim"], sw_ssim=ssim["sw_ssim"], ssim_max_tile_diff=ssim["max_tile_diff"],
               identical_hw_ssim=ssim["identical_hw_ssim"], pass_=res["pass"] and ssim["pass"])
    print(f"Q_FRAC={q_frac:2d} MOTION_THRESH={thresh:2d}: skip={100 * res['skip_rate']:5.1f}% "
          f"cycles={res['cycles']} speedup={res['speedup']:.3f}x PSNR={res['psnr_db']:.2f} dB "
          f"HW-SSIM={res['hw_ssim']:.5f} {'PASS' if res['pass_'] else 'FAIL'}", flush=True)
    return res


def profile(bins, ssim_bin, jobs):
    def measure(case):
        q, (mode, thresh, ways, bypass) = case
        out = PROFILE_OUT / f"q{q}_{mode.lower().replace(' ', '_')}"
        row = point(q, thresh, bins[q, ways], ssim_bin, out, bypass)
        return dict(mode=mode, cache_ways=ways, cache_bypass=int(bypass), **row)

    with ThreadPoolExecutor(jobs) as pool:
        runs = list(pool.map(measure, [(q, mode) for q in Q_FRACS for mode in MODES]))
    baseline = {r["q_frac"]: r for r in runs if r["mode"] == "Baseline"}
    keys = ["traffic_model", "q_frac", "mode", "motion_thresh", "cache_bypass", "cache_ways", "frame",
            "bytes_per_gaussian", "fetch_count", "dram_read_bytes", "dram_write_bytes", "read_reduction_pct",
            "total_cycles", "speedup_vs_baseline", "skip_rate", "psnr_db", "hw_ssim", "mismatches"]
    with (PROFILE_OUT / "bandwidth_profile.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in runs:
            base = baseline[row["q_frac"]]
            model = row[TRAFFIC_LABEL]
            frames = model["frames"]
            base_frames = base[TRAFFIC_LABEL]["frames"]
            if len(frames) != len(base_frames):
                raise RuntimeError("profiler runs must have the same frame count")
            if row["cache_bypass"] and (frames != base_frames or row["cycles"] != base["cycles"] or row["skipped"]):
                raise RuntimeError(f"bypass must fetch and recompute every transaction for Q_FRAC={row['q_frac']}")
            if (sum(fr["fetch_count"] for fr in frames) != row["transactions"] - row["skipped"]
                    or any(fr["dram_write_bytes"] != 0
                           or fr["dram_read_bytes"] != fr["fetch_count"] * model["bytes_per_gaussian"]
                           for fr in frames)):
                raise RuntimeError(f"invalid traffic counters for Q_FRAC={row['q_frac']} mode={row['mode']}")
            for i, (fr, ref) in enumerate(zip(frames, base_frames)):
                reduction = 100 * (1 - fr["dram_read_bytes"] / ref["dram_read_bytes"])
                writer.writerow(dict(row, **fr, traffic_model=TRAFFIC_LABEL, frame=i,
                                     bytes_per_gaussian=model["bytes_per_gaussian"], read_reduction_pct=f"{reduction:.6f}",
                                     total_cycles=row["cycles"], speedup_vs_baseline=f"{base['cycles'] / row['cycles']:.6f}"))
    print(f"{TRAFFIC_LABEL}: {len(runs)} runs; wrote {PROFILE_OUT / 'bandwidth_profile.csv'}")
    return 1 if any(not r["pass_"] for r in runs) else 0


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
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
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
        bins = {(q, ways): build_frame(q, args.verilator, ways)
                for q in Q_FRACS for ways in sorted({m[2] for m in MODES})}
        return profile(bins, ssim_bin, args.jobs)
    bins = {q: build_frame(q, args.verilator) for q in Q_FRACS}
    points = [(12, 0), (8, 16)] if args.mode == "frame" else [(q, t) for q in Q_FRACS for t in THRESHOLDS]
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda p: point(*p, bins[p[0]], ssim_bin, OUT / f"q{p[0]}_t{p[1]}"), points))
    for row in rows:
        row.pop(TRAFFIC_LABEL)
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
