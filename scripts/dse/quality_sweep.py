#!/usr/bin/env python3
"""Phase 4 frame-level quality flow for gs4d_accelerator_top (build/4dgs/quality/).

  frame  Verilator -Wall lint of the RTL, build the frame testbench
         (rtl_4dgs/sim/sim_main_4dgs_frame.cpp) and the SSIM_v2 frame scorer
         (rtl_4dgs/sim/sim_main_ssim_frame.cpp), run the FP32-vs-fixed validation point
         (Q_FRAC=12, MOTION_THRESH=0) and one lossy point (Q_FRAC=8, MOTION_THRESH=16)
  sweep    Q_FRAC {8, 12} x MOTION_THRESH {0, 1, 2, 3, 4, 8, 16, 32, 64}: skip rate, total
           cycles, speedup vs. unskipped, PSNR vs. FP32, hardware SSIM (SSIM_v2); writes
           quality_vs_skip.csv / .json / .md
  profile  Bandwidth profiler (build/4dgs/bandwidth/), Q_FRAC {8, 12} x five runs on a
           CACHE_WAYS=4 build: Baseline (cache_bypass=1, T=0), Skip-Only (cache_bypass=1,
           T=16), Full Co-Design (cache_bypass=0) at T=1 (lossless knee), T=3 and T=16.
           Modeled External DRAM Traffic per frame (fetch_count * BYTES_PER_GAUSSIAN), 8-frame
           and steady-state (frames 1-7) averages, cycles, skip rate, PSNR, hardware SSIM;
           writes bandwidth_profile.csv / .json / .md
  stress   Phase 5b stress scenes (build/4dgs/stress/): scene {benign, pan, fast} x Q_FRAC {8, 12}
           on the CACHE_WAYS=4 profiler build: Baseline (cache_bypass=1, T=0), Skip-Only
           (cache_bypass=1, T=16) and Full Co-Design (cache_bypass=0) at every STRESS_THRESHOLDS
           point. Phase 5a profiler columns plus effective hit rate, PSNR drop vs. the scene's
           T=0 and the scene conic operating range; writes stress_<scene>.csv, stress.json,
           stress.md, plus miss-penalty Pareto tables for the stress scenes
  conic    Phase 5b small-determinant conic characterization (build/4dgs/conic/): streams
           gs4d_ref::small_det_grid through the default-config RTL (sim_main_4dgs +smalldet)
           at Q_FRAC {8, 12}, bit-true vs. golden; writes small_det_cliff.csv / .md

Miss-penalty latency model (sweep and stress, reporting layer only): measured cycles are
compute-only (RTL FSM == golden cycle model); effective cycles @ P-cycle miss penalty =
cycles + fetch_count * P, a conservative no-overlap bound (every fetch stalls for the full P
cycles, no pipelining or burst overlap). The sweep writes miss_penalty_pareto.csv / .md: the
within-Q_FRAC Pareto frontier of (effective cycles, PSNR) at MISS_PENALTY_CYCLES {0, 10, 50, 100}.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import re
import subprocess
import sys

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
PROFILE_MODES = (("Baseline", 1, 0), ("Skip-Only", 1, 16), ("Full Co-Design", 0, 1),
                 ("Full Co-Design", 0, 3), ("Full Co-Design", 0, 16))
STEADY_FRAMES = slice(1, None)  # frame 0 is the cold-cache start
STRESS_OUT = ROOT / "build/4dgs/stress"
CONIC_OUT = ROOT / "build/4dgs/conic"
SCENES = ("benign", "pan", "fast")
STRESS_THRESHOLDS = (0, 1, 2, 3, 4, 8, 16, 32, 64)
STRESS_MODES = (("Baseline", 1, 0), ("Skip-Only", 1, 16), *(("Full Co-Design", 0, t) for t in STRESS_THRESHOLDS))
# Beyond-grid ghosting probe (fast scene only), thresholds in px of L1 deformation offset.
FAST_PROBE_PX = (0.5, 1, 2, 4, 8)
MISS_PENALTY_CYCLES = (0, 10, 50, 100)
PSNR_CLIFF_DB = 1.0
CONIC_CLIFF = 0.10


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
    text = run([frame_bin, f"+thresh={thresh}", f"+out={out}"], out / "frame.log")
    res = result_of(text, out / "frame.log")
    res["fetch_count"] = result_of(text, out / "frame.log", "BANDWIDTH")["fetch_count"]
    ssim = result_of(run([ssim_bin, out, 8], out / "ssim.log"), out / "ssim.log")
    res.update(hw_ssim=ssim["hw_ssim"], sw_ssim=ssim["sw_ssim"], ssim_max_tile_diff=ssim["max_tile_diff"],
               identical_hw_ssim=ssim["identical_hw_ssim"], pass_=res["pass"] and ssim["pass"])
    print(f"Q_FRAC={q_frac:2d} MOTION_THRESH={thresh:2d}: skip={100 * res['skip_rate']:5.1f}% "
          f"cycles={res['cycles']} speedup={res['speedup']:.3f}x PSNR={res['psnr_db']:.2f} dB "
          f"HW-SSIM={res['hw_ssim']:.5f} {'PASS' if res['pass_'] else 'FAIL'}", flush=True)
    return res


def profile_point(q_frac, mode, bypass, thresh, frame_bin, ssim_bin, scene=None):
    root = BW_OUT if scene is None else STRESS_OUT / scene
    out = root / f"q{q_frac}_{mode.lower().replace(' ', '_').replace('-', '_')}_t{thresh}"
    out.mkdir(parents=True, exist_ok=True)
    text = run([frame_bin, f"+thresh={thresh}", f"+bypass={bypass}", f"+out={out}",
                *([] if scene is None else [f"+scene={scene}"])], out / "frame.log")
    res = result_of(text, out / "frame.log")
    res.update(result_of(text, out / "frame.log", "BANDWIDTH"))
    if scene is not None:
        res.update(result_of(text, out / "frame.log", "SCENE"))
        res["hit_rate"] = res["cache_served"] / res["transactions"]
        res["tag_hit_rate"] = res["cache_tag_hits"] / res["transactions"]
    ssim = result_of(run([ssim_bin, out, 8], out / "ssim.log"), out / "ssim.log")
    res.update(mode=mode, hw_ssim=ssim["hw_ssim"], pass_=res["pass"] and ssim["pass"])
    res["skip_eligible_rate"] = res["skip_eligible"] / res["transactions"]
    res["dram_read_bytes_per_frame"] = res["dram_read_bytes"] / res["frames"]
    res["dram_read_bytes_conservative_per_frame"] = res["dram_read_bytes_conservative"] / res["frames"]
    served = [int(n) for n in re.findall(r"^bandwidth frame_\d+: .*cache_served=(\d+)", text, re.M)]
    assert len(served) == res["frames"] and sum(served) == res["cache_served"]
    cons = [f * res["bytes_per_gaussian"] + s * res["served_bytes_per_gaussian"]
            for f, s in zip(res["frame_fetch_count"], served)]
    res["frame_dram_read_bytes_conservative"] = cons
    steady = res["frame_dram_read_bytes"][STEADY_FRAMES]
    res["steady_dram_read_bytes_per_frame"] = sum(steady) / len(steady)
    res["steady_dram_read_bytes_conservative_per_frame"] = sum(cons[STEADY_FRAMES]) / len(steady)
    print(f"{'' if scene is None else scene + ' '}Q_FRAC={q_frac:2d} {mode:<14} bypass={bypass} T={thresh:2d}: fetch={res['fetch_count']} "
          f"dram_read_bytes/frame={res['dram_read_bytes_per_frame']:.1f} cycles={res['cycles']} "
          f"skip={100 * res['skip_rate']:.1f}% PSNR={res['psnr_db']:.2f} dB HW-SSIM={res['hw_ssim']:.5f} "
          f"{'PASS' if res['pass_'] else 'FAIL'}", flush=True)
    return res


def add_reductions(rows):
    for r in rows:
        base = next(b for b in rows if b["q_frac"] == r["q_frac"] and b.get("scene") == r.get("scene")
                    and b["mode"] == PROFILE_MODES[0][0])
        r["read_reduction_pct"] = 100 * (1 - r["dram_read_bytes"] / base["dram_read_bytes"])
        r["read_reduction_conservative_pct"] = 100 * (1 - r["dram_read_bytes_conservative"]
                                                      / base["dram_read_bytes_conservative"])
        for k in ("", "_conservative"):
            r[f"steady_read_reduction{k}_pct"] = 100 * (1 - r[f"steady_dram_read_bytes{k}_per_frame"]
                                                        / base[f"steady_dram_read_bytes{k}_per_frame"])
        r["speedup_vs_baseline"] = base["cycles"] / r["cycles"]


PROFILE_KEYS = ["q_frac", "mode", "cache_bypass", "cache_ways", "motion_thresh", "frames", "transactions",
            "input_bits_per_gaussian", "bytes_per_gaussian", "served_bytes_per_gaussian", "skip_eligible",
            "skip_eligible_rate", "skipped", "skip_rate", "fetch_count", "cache_served", "dram_read_bytes",
            "dram_read_bytes_per_frame", "read_reduction_pct", "dram_read_bytes_conservative",
            "dram_read_bytes_conservative_per_frame", "read_reduction_conservative_pct",
            "steady_dram_read_bytes_per_frame", "steady_read_reduction_pct",
            "steady_dram_read_bytes_conservative_per_frame", "steady_read_reduction_conservative_pct",
            "dram_write_bytes", "frame_fetch_count", "frame_dram_read_bytes", "frame_dram_read_bytes_conservative",
            "cycles", "speedup_vs_baseline", "psnr_db", "hw_ssim",
            "mismatches"]


def flatten(rows):
    return [{**r, "frame_fetch_count": " ".join(map(str, r["frame_fetch_count"])),
             "frame_dram_read_bytes": " ".join(map(str, r["frame_dram_read_bytes"])),
             "frame_dram_read_bytes_conservative": " ".join(map(str, r["frame_dram_read_bytes_conservative"]))}
            for r in rows]


def write_csv(path, rows, keys, comment=None):
    with open(path, "w", newline="", encoding="utf-8") as f:
        if comment:
            f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


TRAFFIC_NOTE = "Modeled External DRAM Traffic (fetch_count * BYTES_PER_GAUSSIAN), not measured DRAM-controller traffic"


def write_profile(rows):
    add_reductions(rows)
    write_csv(BW_OUT / "bandwidth_profile.csv", flatten(rows), PROFILE_KEYS, TRAFFIC_NOTE)
    (BW_OUT / "bandwidth_profile.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    lines = ["Modeled External DRAM Traffic (not measured DRAM-controller traffic)", "",
             "| Q_FRAC | Mode | cache_bypass | T | dram_read_bytes/frame (8-frame) | Reduction | "
             "Steady (frames 1-7) | Steady reduction | Conservative reduction (8-frame / steady) | "
             "dram_write_bytes | Total cycles | Speedup | Skip rate | PSNR (dB) | HW SSIM |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['q_frac']} | {r['mode']} | {r['cache_bypass']} | {r['motion_thresh']} | "
              f"{r['dram_read_bytes_per_frame']:,.0f} | {r['read_reduction_pct']:.1f}% | "
              f"{r['steady_dram_read_bytes_per_frame']:,.0f} | {r['steady_read_reduction_pct']:.1f}% | "
              f"{r['read_reduction_conservative_pct']:.1f}% / {r['steady_read_reduction_conservative_pct']:.1f}% | "
              f"0 | {r['cycles']:,} | {r['speedup_vs_baseline']:.3f}x | {100 * r['skip_rate']:.1f}% | "
              f"{r['psnr_db']:.4f} | {r['hw_ssim']:.5f} |" for r in rows]
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


def effective(r, penalty):
    """Effective cycles @ penalty-cycle miss penalty, no-overlap bound."""
    return r["cycles"] + r["fetch_count"] * penalty


def dominated(r, rows, penalty):
    er = effective(r, penalty)
    return any(o is not r and effective(o, penalty) <= er and o["psnr_db"] >= r["psnr_db"]
               and (effective(o, penalty) < er or o["psnr_db"] > r["psnr_db"]) for o in rows)


def miss_penalty_rows(rows, group=("q_frac",), penalties=MISS_PENALTY_CYCLES, scan=1000):
    """(effective cycles down, PSNR up) Pareto frontier within each group at every penalty, and the
    first integer penalty (0..scan) at which each frontier point leaves the frontier."""
    out = []
    for r in rows:
        peers = [o for o in rows if all(o.get(k) == r.get(k) for k in group)]
        leave = next((p for p in range(scan + 1) if dominated(r, peers, p)), None)
        for p in penalties:
            base = r["noskip_cycles"] + r["transactions"] * p  # no cache, no skip: every transaction fetched
            out.append({**{k: r.get(k) for k in group}, "motion_thresh": r["motion_thresh"],
                        "miss_penalty_cycles": p, "skip_rate": r["skip_rate"], "fetch_count": r["fetch_count"],
                        "compute_cycles": r["cycles"], "effective_cycles": effective(r, p),
                        "effective_speedup": base / effective(r, p), "psnr_db": r["psnr_db"],
                        "hw_ssim": r["hw_ssim"], "pareto": not dominated(r, peers, p),
                        "leaves_frontier_at": "" if leave is None else leave,
                        "on_frontier_at_0": not dominated(r, peers, 0)})
    return out


def write_penalty(rows, out, name, group=("q_frac",)):
    prow = miss_penalty_rows(rows, group)
    keys = [*group, "motion_thresh", "miss_penalty_cycles", "skip_rate", "fetch_count", "compute_cycles",
            "effective_cycles", "effective_speedup", "psnr_db", "hw_ssim", "pareto", "leaves_frontier_at"]
    write_csv(out / f"{name}.csv", prow, keys, "effective cycles = compute-only cycles + fetch_count * "
              "MISS_PENALTY_CYCLES (no-overlap bound: full penalty per fetch, no pipelining or burst overlap)")
    hdr = " | ".join(f"Eff. cycles @ {p} (no-overlap) | Pareto @ {p}" for p in MISS_PENALTY_CYCLES)
    lines = ["Effective cycles @ P-cycle miss penalty, no-overlap bound (compute-only cycles + fetch_count * P); "
             "Pareto = not dominated in (effective cycles, PSNR) within the group", "",
             f"| {' | '.join(group)} | T | fetch_count | PSNR (dB) | {hdr} | Leaves frontier at P = |",
             "|" + "---:|" * (len(group) + 3) + "---:|:---:|" * len(MISS_PENALTY_CYCLES) + "---:|"]
    keyed = {}
    for r in prow:
        keyed.setdefault(tuple(r[k] for k in group) + (r["motion_thresh"],), []).append(r)
    for k, rs in keyed.items():
        cells = " | ".join(f"{r['effective_cycles']:,} | {'yes' if r['pareto'] else ''}" for r in rs)
        leave = rs[0]["leaves_frontier_at"]
        leave = "never (<=1000)" if leave == "" else ("dominated at 0" if not rs[0]["on_frontier_at_0"] else leave)
        lines.append(f"| {' | '.join(map(str, k[:-1]))} | {k[-1]} | {rs[0]['fetch_count']:,} | "
                     f"{rs[0]['psnr_db']:.2f} | {cells} | {leave} |")
    (out / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return prow


STRESS_KEYS = ["scene", "probe", "thresh_px", *PROFILE_KEYS, "hit_rate", "tag_hit_rate", "cache_tag_hits", "psnr_drop_db",
               "median_step_px", "conic_pairs", "min_det_lsb", "max_conic_rel_err", "max_conic_flicker",
               "conic_err_over_10pct", "conic_err_over_10pct_small_s", "conic_inv_saturated", "min_s_px", "max_abs_rho",
               "worst_err_sx", "worst_err_sy", "worst_err_rho", "worst_flk_sx", "worst_flk_sy", "worst_flk_rho"]


def write_stress(rows):
    add_reductions(rows)
    for r in rows:
        t0 = next(b for b in rows if b["scene"] == r["scene"] and b["q_frac"] == r["q_frac"]
                  and b["cache_bypass"] == 0 and b["motion_thresh"] == 0)
        r["psnr_drop_db"] = t0["psnr_db"] - r["psnr_db"]
    for scene in SCENES:
        write_csv(STRESS_OUT / f"stress_{scene}.csv", flatten([r for r in rows if r["scene"] == scene]),
                  STRESS_KEYS, f"{TRAFFIC_NOTE}; scene={scene}; CACHE_WAYS={PROFILE_WAYS}")
    (STRESS_OUT / "stress.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    lines = ["Modeled External DRAM Traffic (not measured DRAM-controller traffic); CACHE_WAYS=4 profiler build", "",
             "| Scene | Q_FRAC | Mode | T | Skip rate | Total cycles | Speedup | PSNR (dB) | PSNR drop vs T=0 | "
             "HW SSIM | fetch_count | Hit rate (served / tag) | dram_read_bytes/frame | Reduction | Mismatches |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['scene']} | {r['q_frac']} | {r['mode']} | {r['motion_thresh']} | {100 * r['skip_rate']:.1f}% | "
              f"{r['cycles']:,} | {r['speedup_vs_baseline']:.3f}x | {r['psnr_db']:.2f} | {r['psnr_drop_db']:+.2f} | "
              f"{r['hw_ssim']:.5f} | {r['fetch_count']:,} | {100 * r['hit_rate']:.1f}% / {100 * r['tag_hit_rate']:.1f}% | "
              f"{r['dram_read_bytes_per_frame']:,.0f} | {r['read_reduction_pct']:.1f}% | {r['mismatches']} |"
              for r in rows if not r["probe"]]
    lines += ["", "Fast-scene beyond-grid ghosting probe (Full Co-Design, T in px of L1 deformation offset):", "",
              "| Q_FRAC | T (LSB) | T (px) | Skip rate | Total cycles | Speedup | PSNR (dB) | PSNR drop vs T=0 | "
              "HW SSIM | fetch_count | dram_read_bytes/frame | Mismatches |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['q_frac']} | {r['motion_thresh']} | {r['thresh_px']:g} | {100 * r['skip_rate']:.1f}% | "
              f"{r['cycles']:,} | {r['speedup_vs_baseline']:.3f}x | {r['psnr_db']:.2f} | {r['psnr_drop_db']:+.2f} | "
              f"{r['hw_ssim']:.5f} | {r['fetch_count']:,} | {r['dram_read_bytes_per_frame']:,.0f} | {r['mismatches']} |"
              for r in rows if r["probe"]]
    lines += ["", "PSNR cliff (first Full Co-Design T with PSNR drop > 1 dB vs. the scene's T=0):", ""]
    for scene in SCENES:
        for q in Q_FRACS:
            fc = [r for r in rows if r["scene"] == scene and r["q_frac"] == q and r["cache_bypass"] == 0]
            grid = [r for r in fc if not r["probe"]]
            cliff = next((r["motion_thresh"] for r in grid if r["psnr_drop_db"] > PSNR_CLIFF_DB), None)
            worst = max(grid, key=lambda r: (r["psnr_drop_db"], -r["motion_thresh"]))
            line = (f"- {scene} Q_FRAC={q}: " + (f"cliff at T={cliff}" if cliff is not None else "no cliff in grid")
                    + f"; max grid drop {worst['psnr_drop_db']:.2f} dB at T={worst['motion_thresh']}")
            probe = sorted((r for r in fc if r["probe"]), key=lambda r: r["motion_thresh"])
            if probe:
                pc = next((r for r in probe if r["psnr_drop_db"] > PSNR_CLIFF_DB), None)
                line += ("; beyond-grid probe: " + (f"cliff at T={pc['motion_thresh']} ({pc['thresh_px']:g} px, "
                                                    f"drop {pc['psnr_drop_db']:.2f} dB)" if pc else
                                                    f"no cliff up to {probe[-1]['thresh_px']:g} px"))
            lines.append(line)
    r0 = {r["scene"]: r for r in rows if r["mode"] == "Baseline" and r["q_frac"] == 8}
    lines += ["", "Scene stats (golden model, Q_FRAC=8):", "",
              "| Scene | Median step (px/frame) | Conic pairs | min det (LSB) | Max conic rel err | Max flicker | "
              ">10% err pairs | inv_saturated | min s (px) | max abs rho |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {k} | {r['median_step_px']:.4f} | {r['conic_pairs']} | {r['min_det_lsb']} | "
              f"{100 * r['max_conic_rel_err']:.2f}% | {100 * r['max_conic_flicker']:.2f}% | {r['conic_err_over_10pct']} | "
              f"{r['conic_inv_saturated']} | {r['min_s_px']:.3f} | {r['max_abs_rho']:.3f} |" for k, r in r0.items()]
    lines += ["", "| Scene | Worst-error (sx, sy, rho) | Worst-flicker (sx, sy, rho) | >10% pairs with min(sx, sy) < 1 px |",
              "|---|---|---|---:|"]
    lines += [f"| {k} | ({r['worst_err_sx']:.3f}, {r['worst_err_sy']:.3f}, {r['worst_err_rho']:.3f}) | "
              f"({r['worst_flk_sx']:.3f}, {r['worst_flk_sy']:.3f}, {r['worst_flk_rho']:.3f}) | "
              f"{r['conic_err_over_10pct_small_s']} |" for k, r in r0.items()]
    (STRESS_OUT / "stress.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    fc = [r for r in rows if r["cache_bypass"] == 0]
    write_penalty(fc, STRESS_OUT, "stress_miss_penalty_pareto", ("scene", "q_frac"))


def num(v):
    try:
        return int(v)
    except ValueError:
        return float(v)


def conic_summary(rows):
    """Aggregate the grid per (Q_FRAC, min(sx, sy), abs(rho)): worst error / flicker, saturation count."""
    groups = {}
    for r in rows:
        groups.setdefault((r["q_frac"], min(r["sx"], r["sy"]), abs(r["rho"])), []).append(r)
    return [{"q_frac": q, "min_s": s, "abs_rho": rho, "points": len(rs),
             "inv_saturated": sum(r["inv_saturated"] for r in rs), "min_det_lsb": min(r["det_lsb"] for r in rs),
             "max_rel_err_unsat": max((r["rel_err"] for r in rs if not r["inv_saturated"]), default=""),
             "points_over_10pct": sum(r["rel_err"] > CONIC_CLIFF for r in rs),
             "max_flicker_unsat": max((r["flicker"] for r in rs if not r["inv_saturated"]), default=""),
             "max_flicker_exact": max(r["flicker_exact"] for r in rs)}
            for (q, s, rho), rs in sorted(groups.items())]


def conic_report(rows):
    """Small-det cliff summary per Q_FRAC from the RTL-driven grid rows."""
    lines = ["Small-determinant conic characterization (exact double conic_from_scale reference; not a gate)", "",
             "| Q_FRAC | Points | inv_saturated | det LSB boundary (sat. at det <=) | Max rel err (unsat.) | "
             "Max rel err (all) | Points >10% | Worst flicker (+-1 LSB, unsat.) | Worst flicker exact | "
             "Smallest unsat. det (LSB) |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    env, dlines = [], []
    for q in Q_FRACS:
        rs = [r for r in rows if r["q_frac"] == q]
        sat = [r for r in rs if r["inv_saturated"]]
        uns = [r for r in rs if not r["inv_saturated"]]
        lines.append(f"| {q} | {len(rs)} | {len(sat)} | {max((r['det_lsb'] for r in sat), default='-')} | "
                     f"{100 * max(r['rel_err'] for r in uns):.2f}% | {100 * max(r['rel_err'] for r in rs):.2f}% | "
                     f"{sum(r['rel_err'] > CONIC_CLIFF for r in rs)} | {100 * max(r['flicker'] for r in uns):.2f}% | "
                     f"{100 * max(r['flicker_exact'] for r in uns):.2f}% | {min(r['det_lsb'] for r in uns)} |")
        def ok(r):
            return r["rel_err"] <= CONIC_CLIFF and not r["inv_saturated"]
        for rho in sorted({abs(r["rho"]) for r in rs}):
            band = [r for r in rs if abs(r["rho"]) == rho]
            cand = sorted({min(r["sx"], r["sy"]) for r in band})
            smin = next((c for c in cand if all(ok(r) for r in band if min(r["sx"], r["sy"]) >= c)), None)
            sub = [r for r in band if smin is not None and min(r["sx"], r["sy"]) >= smin]
            env.append(dict(q_frac=q, abs_rho=rho, min_iso_s=smin,
                            max_err=max((r["rel_err"] for r in sub), default=None),
                            max_flicker=max((r["flicker"] for r in sub), default=None)))
        dets = sorted({r["sx"] ** 2 * r["sy"] ** 2 * (1 - r["rho"] ** 2) for r in rs})
        dmin = next(d for d in dets if all(ok(r) for r in rs if r["sx"] ** 2 * r["sy"] ** 2 * (1 - r["rho"] ** 2) >= d))
        dlines.append(f"- Q_FRAC={q}: every grid point with det = sx^2 sy^2 (1 - rho^2) >= {dmin:.4g} px^4 "
                      f"(sqrt(det) >= {dmin ** 0.5:.4g} px^2) keeps < {CONIC_CLIFF:.0%} error and no saturation")
    lines += ["", "Determinant envelope:", "", *dlines]
    lines += ["", f"Operating envelope per abs(rho): smallest s such that every grid point with "
              f"min(sx, sy) >= s keeps < {CONIC_CLIFF:.0%} relative conic error and no reciprocal saturation "
              "(max error / flicker inside that region)", "",
              "| abs(rho) | " + " | ".join(f"Q{q} min(sx, sy) | Q{q} max err | Q{q} max flicker" for q in Q_FRACS) + " |",
              "|---:|" + "---:|---:|---:|" * len(Q_FRACS)]
    for rho in sorted({e["abs_rho"] for e in env}):
        cells = []
        for q in Q_FRACS:
            e = next(e for e in env if e["q_frac"] == q and e["abs_rho"] == rho)
            cells.append("none | - | -" if e["min_iso_s"] is None else
                         f"{e['min_iso_s']:.2f} | {100 * e['max_err']:.2f}% | {100 * e['max_flicker']:.2f}%")
        lines.append(f"| {rho:.2f} | " + " | ".join(cells) + " |")
    return lines


def run_conic(verilator, jobs):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gs4d_flow  # pylint: disable=import-outside-toplevel
    CONIC_OUT.mkdir(parents=True, exist_ok=True)

    def one(q):
        cfg = {**gs4d_flow.DEFAULT, "q_frac": q}
        out = CONIC_OUT / f"q{q}"
        binary = gs4d_flow.build_sim(cfg, out, verilator)
        path = out / "small_det.csv"
        text = run([binary, "+thresh=16", f"+smalldet={path}"], out / "sim.log")
        res = result_of(text, out / "sim.log")
        if not res["pass"] or res["mismatches"]:
            raise RuntimeError(f"small-det RTL run failed at Q_FRAC={q}; see {out / 'sim.log'}")
        print(f"Q_FRAC={q}: {res['transactions']} transactions, mismatches={res['mismatches']} PASS", flush=True)
        with open(path, encoding="utf-8") as f:
            return [{k: num(v) for k, v in r.items()} for r in csv.DictReader(f)]

    with ThreadPoolExecutor(min(jobs, len(Q_FRACS))) as pool:
        rows = [r for rs in pool.map(one, Q_FRACS) for r in rs]
    write_csv(CONIC_OUT / "small_det_cliff.csv", rows, list(rows[0]))
    summary = conic_summary(rows)
    write_csv(CONIC_OUT / "small_det_cliff_summary.csv", summary, list(summary[0]),
              "per (Q_FRAC, min(sx, sy), abs(rho)) over the small_det_grid; rel_err / flicker normalized by max|conic|")
    lines = conic_report(rows)
    (CONIC_OUT / "small_det_cliff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("frame", "sweep", "profile", "stress", "conic"))
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    if args.mode == "conic":
        return run_conic(args.verilator, args.jobs)
    OUT.mkdir(parents=True, exist_ok=True)
    ssim_bin = build_ssim(args.verilator)
    if args.mode == "stress":
        STRESS_OUT.mkdir(parents=True, exist_ok=True)
        bins = {q: build_frame(q, args.verilator, PROFILE_WAYS) for q in Q_FRACS}
        runs = [(q, *m, s) for s in SCENES for q in Q_FRACS for m in STRESS_MODES]
        runs += [(q, "Full Co-Design", 0, int(px * (1 << q)), "fast") for q in Q_FRACS for px in FAST_PROBE_PX]
        with ThreadPoolExecutor(args.jobs) as pool:
            rows = list(pool.map(lambda p: profile_point(*p[:4], bins[p[0]], ssim_bin, p[4]), runs))
        for r, p in zip(rows, runs):
            r["scene"] = p[4]
            r["probe"] = r["motion_thresh"] not in STRESS_THRESHOLDS
            r["thresh_px"] = r["motion_thresh"] / (1 << r["q_frac"])
        write_stress(rows)
        bad = [r for r in rows if not r["pass_"] or r["mismatches"]]
        for r in bad:
            print(f"FAIL: {r['scene']} Q_FRAC={r['q_frac']} {r['mode']} T={r['motion_thresh']}; see {STRESS_OUT}")
        return 1 if bad else 0
    if args.mode == "profile":
        BW_OUT.mkdir(parents=True, exist_ok=True)
        bins = {q: build_frame(q, args.verilator, PROFILE_WAYS) for q in Q_FRACS}
        runs = [(q, *m) for q in Q_FRACS for m in PROFILE_MODES]
        with ThreadPoolExecutor(args.jobs) as pool:
            rows = list(pool.map(lambda p: profile_point(*p, bins[p[0]], ssim_bin), runs))
        write_profile(rows)
        bad = [r for r in rows if not r["pass_"]]
        for r in bad:
            print(f"FAIL: Q_FRAC={r['q_frac']} {r['mode']} T={r['motion_thresh']}; see {BW_OUT}")
        return 1 if bad else 0
    bins = {q: build_frame(q, args.verilator) for q in Q_FRACS}
    points = [(12, 0), (8, 16)] if args.mode == "frame" else [(q, t) for q in Q_FRACS for t in THRESHOLDS]
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda p: point(p[0], p[1], bins[p[0]], ssim_bin), points))
    if args.mode == "sweep":
        pareto(rows)
        write_reports(rows)
        write_penalty(rows, OUT, "miss_penalty_pareto")
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
