#!/usr/bin/env python3
"""Phase 5c split-fetch AXI4-Stream wrapper flow (gs4d_axi_wrapper around gs4d_accelerator_top).

  sim    Tier 1: Verilator -Wall lint of gs4d_axi_wrapper (Q8, Q12) and of the bare top with
         SPLIT_FETCH=1, build rtl_4dgs/sim/sim_main_4dgs_axi.cpp (CACHE_WAYS=4, the Phase 5a
         profiling configuration) and run scenes {benign, pan, fast} x Q_FRAC {8, 12} x
         MOTION_THRESH {0, 1, 16} (18 runs), plus benign Q8 T=3 (Part D row) and a
         cache_bypass=1 smoke (every Gaussian fetches its attributes). Every run drives the
         wrapper with LFSR stalls on meta tvalid / attr tvalid / out_ready, checks every output
         bit-true against golden_model/4dgs_ref.hpp and counts AXI handshakes. Writes
         build/4dgs/axi/axi_bandwidth.{csv,json,md}.
         MEASURED = AXI beat counts (handshakes) x beat bytes in the cycle-accurate simulation.
         MODELED  = B_meta + (1 - skip_rate) * B_attr per Gaussian with the golden skip rate.
  synth  Tier 2: Yosys + ABC generic-cell synthesis and CEC of gs4d_axi_wrapper and of the bare
         gs4d_accelerator_top (SPLIT_FETCH=0) at NUM_PE=2, CACHE_WAYS=4, Q_FRAC=12, CACHE_SETS=512,
         using the gs4d_flow.py synth flow; reports the wrapper overhead (wrapper + top) - top.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gs4d_flow  # noqa: E402  pylint: disable=wrong-import-position
import quality_sweep as qs  # noqa: E402  pylint: disable=wrong-import-position

ROOT = gs4d_flow.ROOT
RTL = gs4d_flow.RTL
OUT = ROOT / "build/4dgs/axi"
WRAPPER = "gs4d_axi_wrapper"
WRAP_FILES = [RTL / f"{WRAPPER}.sv", *gs4d_flow.RTL_FILES]
WAYS = qs.PROFILE_WAYS
# (scene, Q, T, bypass, extra plusargs). Extra runs: a slow Channel A source (tvalid 1/8 of the
# cycles, the wrapper waits on it) and a negative run (wrong tuser on attr beat 100, expected to
# set sticky axi_err; PASS means the error was observed and the bad beat was dropped).
RUNS = ([(scene, q, t, 0, ()) for scene in qs.SCENES for q in qs.Q_FRACS for t in (0, 1, 16)]
        + [("benign", 8, 3, 0, ()), ("benign", 8, 16, 1, ()),
           ("benign", 8, 1, 0, ("+meta_slow=8",)), ("pan", 12, 1, 0, ("+meta_slow=8",)),
           ("benign", 8, 1, 0, ("+bad_tuser=100",))])
SYNTH_CFG = dict(num_pe=2, q_frac=12, cache_ways=4, cache_sets=512)
# Part D of the Phase 5c task: MODELED prediction from the Phase 5a/5b skip rates.
PART_D = [("benign", 8, 1, 0.842, 37.89, 21.1), ("benign", 8, 3, 0.868, 37.59, 21.7),
          ("benign", 8, 16, 0.904, 37.15, 22.6), ("benign", 12, 1, 0.826, 46.61, 21.0),
          ("benign", 12, 16, 0.852, 46.22, 21.7), ("pan", 8, 1, 0.120, 46.56, 3.0),
          ("fast", 8, 1, 0.855, 37.74, 21.4)]
KEYS = ["mode", "scene", "q_frac", "motion_thresh", "cache_bypass", "transactions", "golden_skipped", "skip_rate",
        "meta_beats", "attr_beats", "b_meta", "b_attr", "b_baseline", "measured_bytes", "modeled_bytes",
        "baseline_bytes", "measured_avg_bytes", "modeled_avg_bytes", "measured_reduction_pct",
        "attr_requests_on_skip_hit", "cycles", "meta_stall_cycles", "attr_stall_cycles",
        "out_backpressure_cycles", "max_out_stall_run", "mismatches", "axi_err", "normal_checks_pass", "pass"]


def build(q_frac, verilator):
    out = OUT / f"build_q{q_frac}_w{WAYS}"
    out.mkdir(parents=True, exist_ok=True)
    g = [f"-GQ_FRAC={q_frac}", f"-GCACHE_WAYS={WAYS}"]
    for top, extra, files, name in ((WRAPPER, [], WRAP_FILES, "lint"),
                                    (gs4d_flow.TOP, ["-GSPLIT_FETCH=1"], gs4d_flow.RTL_FILES, "lint_top_split")):
        log = qs.run([verilator, "--lint-only", "-Wall", f"-I{RTL}", "--top-module", top, *g, *extra, *files],
                     out / f"{name}.log")
        if "%Warning" in log or "%Error" in log:
            raise RuntimeError(f"lint warnings; see {out / f'{name}.log'}")
    qs.run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", f"-I{RTL}", "--top-module", WRAPPER, *g,
            "--Mdir", out / "obj_dir", "-CFLAGS", f"{qs.CFLAGS} -DQ_FRAC={q_frac} -DCACHE_WAYS={WAYS}",
            *WRAP_FILES, RTL / "sim/sim_main_4dgs_axi.cpp"], out / "build.log")
    return out / f"obj_dir/V{WRAPPER}"


def point(scene, q_frac, thresh, bypass, extra, binary):
    tag = "".join("_" + a[1:].replace("=", "") for a in extra)
    out = OUT / f"q{q_frac}_{scene}_t{thresh}_b{bypass}{tag}"
    out.mkdir(parents=True, exist_ok=True)
    log = out / "sim.log"
    text = qs.run([binary, f"+scene={scene}", f"+thresh={thresh}", f"+bypass={bypass}", *extra], log, timeout=3600)
    r = qs.result_of(text, log)
    if not r["pass"]:
        raise RuntimeError(f"AXI sim FAILED: {log}")
    return r


def write_sim(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "axi_bandwidth.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    with open(OUT / "axi_bandwidth.csv", "w", newline="", encoding="utf-8") as f:
        f.write("# measured_* = MEASURED AXI handshakes x beat bytes; modeled_* = MODELED B_meta + (1-skip)*B_attr\n")
        w = csv.DictWriter(f, fieldnames=KEYS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    find = {(r["scene"], r["q_frac"], r["motion_thresh"], r["cache_bypass"]): r for r in rows if r["mode"] == "normal"}
    lines = ["# Phase 5c split-fetch AXI bandwidth", "",
             "MEASURED: AXI4-Stream handshakes counted in the cycle-accurate Verilator simulation of",
             "gs4d_axi_wrapper (CACHE_WAYS=4), x beat bytes (B_meta = ceil((12+17W)/8), B_attr = ceil(6W/8)).",
             "MODELED: Phase 5c Part D prediction B_meta + (1 - skip_rate) * B_attr (task table).", "",
             "## Part D: MODELED prediction vs MEASURED", "",
             "| scene | Q | T | MODELED skip | MODELED avg B | MODELED red. | MEASURED skip | MEASURED avg B "
             "| MEASURED red. | baseline B |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for scene, q, t, skip, avg, red in PART_D:
        r = find[(scene, q, t, 0)]
        lines.append(f"| {scene} | Q{q} | {t} | {skip:.3f} | {avg:.2f} | {red:.1f}% | {r['skip_rate']:.4f} | "
                     f"{r['measured_avg_bytes']:.2f} | {r['measured_reduction_pct']:.1f}% | {r['b_baseline']} |")
    lines += ["", "## All runs (MEASURED unless noted)", "",
              "| mode | scene | Q | T | bypass | Gaussians | meta beats | attr beats | MEASURED bytes | MODELED bytes "
              "| MEASURED avg B | MEASURED red. | cycles | meta stall cycles | mismatches | axi_err cycles |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['mode']} | {r['scene']} | Q{r['q_frac']} | {r['motion_thresh']} | {r['cache_bypass']} | {r['transactions']} | "
                     f"{r['meta_beats']} | {r['attr_beats']} | {r['measured_bytes']} | {r['modeled_bytes']} | "
                     f"{r['measured_avg_bytes']:.2f} | {r['measured_reduction_pct']:.2f}% | {r['cycles']} | "
                     f"{r['meta_stall_cycles']} | {r['mismatches']} | {r['axi_err']} |")
    lines += ["", "mode: normal = default sources; meta_slow = Channel A tvalid 1/8 of cycles; bad_tuser = negative",
              "run (wrong tuser on attr beat 100), PASS = sticky axi_err observed and the bad beat dropped."]
    (OUT / "axi_bandwidth.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sim(args):
    with ThreadPoolExecutor(2) as pool:
        bins = dict(zip(qs.Q_FRACS, pool.map(lambda q: build(q, args.verilator), qs.Q_FRACS)))
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda p: point(*p, bins[p[1]]), RUNS))
    write_sim(rows)
    for r in rows:
        print(f"axi {r['mode']:9s} {r['scene']:6s} Q{r['q_frac']:<2d} T={r['motion_thresh']:<2d} bypass={r['cache_bypass']}: PASS "
              f"gaussians={r['transactions']} meta_beats={r['meta_beats']} attr_beats={r['attr_beats']} "
              f"MEASURED avg={r['measured_avg_bytes']:.2f} B ({r['measured_reduction_pct']:.2f}% vs {r['b_baseline']} B) "
              f"MODELED avg={r['modeled_avg_bytes']:.2f} B cycles={r['cycles']} meta_stall={r['meta_stall_cycles']} "
              f"mismatches={r['mismatches']} axi_err={r['axi_err']}")
    print(f"wrote {OUT / 'axi_bandwidth.md'}")


def synth(args):
    cfg = dict(num_pe=args.num_pe, q_frac=args.q_frac, cache_ways=args.cache_ways, cache_sets=args.cache_sets)
    out = OUT / f"synth_{gs4d_flow.tag(cfg)}"
    jobs = {"wrapper": (WRAPPER, WRAP_FILES), "top": (gs4d_flow.TOP, gs4d_flow.RTL_FILES)}
    with ThreadPoolExecutor(2) as pool:
        futures = {k: pool.submit(gs4d_flow.synth, cfg, out / k, args, top, files)
                   for k, (top, files) in jobs.items()}
        stats = {k: f.result() for k, f in futures.items()}
    rep = {"config": cfg, "note": "Yosys/ABC generic-cell (AND/OR/XOR/MUX) metrics, not a 130nm library mapping"}
    for k, s in stats.items():
        rep[k] = dict(generic_gates=s["mapped"]["generic_gates"], flip_flops=s["mapped"]["flip_flops"],
                      aig_and=s["optimized"]["and_nodes"], aig_levels=s["optimized"]["levels"], cec=s["cec"])
    d = rep["wrapper"]["generic_gates"] - rep["top"]["generic_gates"]
    rep["overhead"] = dict(generic_gates=d, pct=100.0 * d / rep["top"]["generic_gates"],
                           flip_flops=rep["wrapper"]["flip_flops"] - rep["top"]["flip_flops"],
                           aig_and=rep["wrapper"]["aig_and"] - rep["top"]["aig_and"])
    (out / "axi_synth.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    for k in ("top", "wrapper"):
        r = rep[k]
        print(f"synth {k} {gs4d_flow.tag(cfg)} (generic-cell): gates={r['generic_gates']} ffs={r['flip_flops']} "
              f"aig_and={r['aig_and']} aig_levels={r['aig_levels']} CEC {r['cec']}")
    o = rep["overhead"]
    print(f"wrapper overhead (wrapper+top - top, generic-cell): {o['generic_gates']} gates ({o['pct']:.2f}%), "
          f"{o['flip_flops']} ffs, {o['aig_and']} AIG ANDs")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("sim", "synth"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--fast-abc", action="store_true")
    ap.add_argument("--num-pe", type=int, default=SYNTH_CFG["num_pe"])
    ap.add_argument("--q-frac", type=int, default=SYNTH_CFG["q_frac"])
    ap.add_argument("--cache-ways", type=int, default=SYNTH_CFG["cache_ways"])
    ap.add_argument("--cache-sets", type=int, default=SYNTH_CFG["cache_sets"])
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--sv2v", default="sv2v")
    ap.add_argument("--abc", default="berkeley-abc")
    args = ap.parse_args()
    if args.action == "synth" and 4 % args.num_pe:  # TILE=4 pixels are shaded TILE/NUM_PE cycles x NUM_PE lanes
        ap.error(f"--num-pe {args.num_pe} does not divide TILE=4")
    (sim if args.action == "sim" else synth)(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
