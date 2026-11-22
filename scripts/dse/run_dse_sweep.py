#!/usr/bin/env python3
"""Automated Pareto DSE sweep for rtl_4dgs/gs4d_accelerator_top.

Grid: NUM_PE {1,2,4} x CACHE_WAYS {1,2,4} x Q_FRAC {8,12} x MOTION_THRESH {0,16}
(CACHE_SETS = 512). MOTION_THRESH is a run-time port, so each of the 18 hardware
configurations is linted, simulated (Tier 1, both thresholds, >= 20k cycles, 0 bit-true
mismatches vs golden_model/4dgs_ref.hpp) and synthesized once (Tier 2, Yosys/ABC generic
cells + CEC). Any failure aborts the sweep.

Metrics (all synthesis numbers are generic-cell Yosys/ABC proxies, not 130nm):
  area        = optimized AIG AND nodes + FF_WEIGHT * flip-flops (SRAM ways opaque,
                reported separately as sram_bits)
  delay       = optimized AIG logic levels (critical-path depth)
  throughput  = shaded pixels per active cycle (simulation, stalls excluded)
  eff_thr     = 1000 * throughput / delay  (pixels per 1000 level-delays)
Pareto front: minimize area*delay, maximize eff_thr and skip rate (non-dominated set);
the 2-objective (area*delay vs eff_thr) front is reported as well.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import itertools
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gs4d_flow import ROOT, build_sim, sim, source_digest, synth, tag  # noqa: E402  pylint: disable=wrong-import-position

NUM_PE = (1, 2, 4)
CACHE_WAYS = (1, 2, 4)
Q_FRAC = (8, 12)
MOTION_THRESH = (0, 16)
CACHE_SETS = 512
FF_WEIGHT = 6


def select_configs(names):
    grid = [dict(num_pe=p, q_frac=q, cache_ways=w, cache_sets=CACHE_SETS)
            for p, w, q in itertools.product(NUM_PE, CACHE_WAYS, Q_FRAC)]
    if names is None:
        return grid
    available = {tag(c): c for c in grid}
    unknown = set(names) - available.keys()
    if not names or unknown:
        raise ValueError(f"expected configuration tags from {', '.join(available)}; unknown: {sorted(unknown)}")
    return [c for name, c in available.items() if name in names]


def evaluate(cfg, args):
    out = args.out / tag(cfg)
    t0 = time.monotonic()
    binary = build_sim(cfg, out, args.verilator)
    sims = {th: sim(cfg, th, binary, out) for th in MOTION_THRESH}
    cached = out / "synth/metrics.json"
    syn = json.loads(cached.read_text(encoding="utf-8")) if args.resume and cached.is_file() else None
    if syn is None or syn.get("cec") != "PASS" or syn.get("source_sha256") != source_digest(cfg, args):
        syn = synth(cfg, out / "synth", args)
    print(f"  {tag(cfg)}: done in {time.monotonic() - t0:.0f}s", flush=True)
    return cfg, sims, syn


def dominated(a, b, keys):
    """b dominates a (keys: (name, +1 maximize / -1 minimize))."""
    ge = all(b[k] * s >= a[k] * s for k, s in keys)
    gt = any(b[k] * s > a[k] * s for k, s in keys)
    return ge and gt


def pareto(rows, keys):
    return [r for r in rows if not any(dominated(r, o, keys) for o in rows if o is not r)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "build/dse")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--configs", nargs="+", help="only these hardware tags (e.g. pe1_q8_w2_s512); default: full grid")
    ap.add_argument("--report-thresholds", type=int, nargs="+", choices=MOTION_THRESH, default=MOTION_THRESH,
                    help="threshold rows to report; both thresholds are always simulated for verification")
    ap.add_argument("--resume", action="store_true",
                    help="reuse build/dse/<cfg>/synth/metrics.json if its source_sha256 matches (simulation always reruns)")
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--sv2v", default="sv2v")
    ap.add_argument("--abc", default="berkeley-abc")
    args = ap.parse_args()
    try:
        cfgs = select_configs(args.configs)
    except ValueError as error:
        ap.error(str(error))
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"dse_4dgs: {len(cfgs)} hardware configurations x {len(MOTION_THRESH)} thresholds, "
          f"{args.jobs} parallel jobs", flush=True)
    start = time.monotonic()
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda c: evaluate(c, args), cfgs))

    rows = []
    for cfg, sims, syn in results:
        area = syn["optimized"]["and_nodes"] + FF_WEIGHT * syn["mapped"]["flip_flops"]
        delay = syn["optimized"]["levels"]
        for th, s in sims.items():
            if th not in args.report_thresholds:
                continue
            rows.append(dict(
                num_pe=cfg["num_pe"], cache_ways=cfg["cache_ways"], q_frac=cfg["q_frac"], motion_thresh=th,
                sim_cycles=s["sim_cycles"], active_cycles=s["active_cycles"], noskip_cycles=s["noskip_cycles"],
                mismatches=s["mismatches"], skip_rate=round(s["skip_rate"], 4), speedup=round(s["speedup"], 4),
                throughput=round(s["throughput_px_per_cycle"], 4), lossy_alpha_px=s["lossy_alpha_px"],
                max_alpha_err_lsb=s["max_alpha_err_lsb"], gates=syn["mapped"]["generic_gates"],
                ffs=syn["mapped"]["flip_flops"], aig_and=syn["optimized"]["and_nodes"], levels=delay,
                sram_bits=syn["sram_bits"], cec=syn["cec"], area=area, adp=area * delay,
                eff_thr=round(1000.0 * s["throughput_px_per_cycle"] / delay, 4)))
    front3 = pareto(rows, (("adp", -1), ("eff_thr", 1), ("skip_rate", 1)))
    front2 = pareto(rows, (("adp", -1), ("eff_thr", 1)))
    for r in rows:
        r["pareto3"] = r in front3
        r["pareto2"] = r in front2
    rows.sort(key=lambda r: (r["num_pe"], r["cache_ways"], r["q_frac"], r["motion_thresh"]))

    with open(args.out / "dse_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "dse_results.json").write_text(json.dumps(dict(
        grid=dict(NUM_PE=NUM_PE, CACHE_WAYS=CACHE_WAYS, Q_FRAC=Q_FRAC, MOTION_THRESH=MOTION_THRESH,
                  CACHE_SETS=CACHE_SETS), configs=[tag(c) for c in cfgs],
        report_thresholds=args.report_thresholds, ff_weight=FF_WEIGHT, rows=rows,
        seconds=round(time.monotonic() - start, 1)), indent=1) + "\n", encoding="utf-8")

    cols = ("num_pe", "cache_ways", "q_frac", "motion_thresh", "sim_cycles", "mismatches", "skip_rate",
            "speedup", "throughput", "lossy_alpha_px", "max_alpha_err_lsb", "gates", "ffs", "aig_and",
            "levels", "sram_bits", "area", "adp", "eff_thr", "pareto2", "pareto3")
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(("**yes**" if r[c] else "") if isinstance(r[c], bool) else str(r[c])
                                       for c in cols) + " |")
    (args.out / "dse_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nPareto (area*delay vs eff_thr): {len(front2)} points; "
          f"3-objective (+ skip rate): {len(front3)} points; all {len(rows)} rows 0 mismatches, CEC PASS")
    print(f"results: {args.out / 'dse_results.csv'}, {args.out / 'dse_table.md'} "
          f"({time.monotonic() - start:.0f}s)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
