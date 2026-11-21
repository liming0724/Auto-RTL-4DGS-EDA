#!/usr/bin/env python3
"""Tier-1/Tier-2 flow for one rtl_4dgs configuration (used by make sim_4dgs / synth_4dgs and
scripts/dse/run_dse_sweep.py).

  lint   verilator --lint-only -Wall on gs4d_accelerator_top (any warning fails)
  sim    build rtl_4dgs/sim/sim_main_4dgs.cpp and run it against golden_model/4dgs_ref.hpp
         (>= 20,000 cycles, 0 bit-true mismatches) for each MOTION_THRESH
  synth  sv2v + Yosys (generic cells, SRAM ways opaque) + ABC AIG metrics + ABC CEC
All numbers are generic-cell Yosys/ABC metrics, not a 130nm standard-cell measurement.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/synth"))
from baseline import cut_blif, run  # noqa: E402  pylint: disable=wrong-import-position

RTL = ROOT / "rtl_4dgs"
RTL_FILES = [RTL / f"{m}.sv" for m in ("gs4d_accelerator_top", "temporal_skip_unit", "motion_aware_cache",
                                       "SRAM_gs4d_cache", "gs4d_raster_lane")]
SIM_MAIN = RTL / "sim/sim_main_4dgs.cpp"
TOP = "gs4d_accelerator_top"
DEFAULT = dict(num_pe=1, q_frac=8, cache_ways=2, cache_sets=512)


def tag(cfg):
    return f"pe{cfg['num_pe']}_q{cfg['q_frac']}_w{cfg['cache_ways']}_s{cfg['cache_sets']}"


def gparams(cfg):
    return [f"-GNUM_PE={cfg['num_pe']}", f"-GQ_FRAC={cfg['q_frac']}",
            f"-GCACHE_WAYS={cfg['cache_ways']}", f"-GCACHE_SETS={cfg['cache_sets']}"]


def lint(cfg, out, verilator):
    out.mkdir(parents=True, exist_ok=True)
    log = run([verilator, "--lint-only", "-Wall", f"-I{RTL}", "--top-module", TOP, *gparams(cfg),
               *map(str, RTL_FILES)], ROOT, out / "lint.log")
    if "%Warning" in log or "%Error" in log:
        raise ValueError(f"lint warnings for {tag(cfg)}; see {out / 'lint.log'}")


def build_sim(cfg, out, verilator):
    lint(cfg, out, verilator)
    mdir = out / "obj_dir"
    defs = " ".join(f"-D{k.upper()}={v}" for k, v in cfg.items())
    run([verilator, "--cc", "--exe", "--build", "-O2", "-Wall", "--public-flat-rw", f"-I{RTL}",
         "--top-module", TOP, "--Mdir", str(mdir), *gparams(cfg),
         "-CFLAGS", f"-std=c++17 -O2 -I{ROOT / 'ref_model/include'} -I{ROOT / 'golden_model'} {defs}",
         *map(str, RTL_FILES), str(SIM_MAIN)], ROOT, out / "build.log", timeout=900)
    return mdir / f"V{TOP}"


def sim(cfg, thresh, binary, out):
    log = run([str(binary), f"+thresh={thresh}"], out, out / f"sim_t{thresh}.log")
    match = re.search(r"^RESULT (\{.*\})$", log, re.M)
    if not match:
        raise ValueError(f"no RESULT line; see {out / f'sim_t{thresh}.log'}")
    res = json.loads(match.group(1))
    if not res["pass"] or res["mismatches"] or res["sim_cycles"] < 20000:
        raise ValueError(f"simulation FAILED for {tag(cfg)} thresh={thresh}; see {out / f'sim_t{thresh}.log'}")
    return res


def synth(cfg, out, tools):
    out.mkdir(parents=True, exist_ok=True)
    for name in ("metrics.json", "optimized.blif", "cec.log"):
        (out / name).unlink(missing_ok=True)
    converted = out / "gs4d_converted.v"
    rtl = [str(f) for f in RTL_FILES if f.stem != "SRAM_gs4d_cache"]
    run([tools.sv2v, f"-I{RTL}", "-w", str(converted), *rtl], ROOT, out / "sv2v.log")
    params = [f"chparam -set {k.upper()} {v} {TOP}" for k, v in cfg.items()]
    q = json.dumps
    script = out / "synth.ys"
    script.write_text("\n".join([
        f"read_verilog -lib {q(str(ROOT / 'scripts/dse/gs4d_sram_interfaces.v'))}",
        f"read_verilog {q(str(converted))}", *params,
        f"synth -top {TOP} -flatten -noabc", f"rename -top {TOP}", "setundef -zero", "check -assert",
        f"write_json {q(str(out / 'pre.json'))}",
        f"abc -exe {q(tools.abc)} -g AND,OR,XOR,MUX", "check -assert", "stat",
        f"write_json {q(str(out / 'mapped.json'))}",
    ]) + "\n", encoding="utf-8")
    log = run([tools.yosys, "-QT", "-s", str(script)], ROOT, out / "synth.log", timeout=7200)
    if "Warning: Resizing cell port" in log:
        raise ValueError(f"macro pin width mismatch; see {out / 'synth.log'}")
    stats, manifests = {}, []
    for name in ("pre", "mapped"):
        manifest, stats[name] = cut_blif(out / f"{name}.json", out / f"{name}.blif", TOP, mapped=name == "mapped")
        manifests.append(manifest)
    if manifests[0] != manifests[1]:
        raise ValueError(f"{tag(cfg)}: mapping changed an opaque cell or state boundary")
    log = run([tools.abc, "-f", str(ROOT / "scripts/synth/aig.abc")], out, out / "abc.log", timeout=7200)
    aig = re.findall(r"\band\s*=\s*(\d+)\s+lev\s*=\s*(\d+)", log)
    if len(aig) != 2:
        raise ValueError(f"ABC did not report both AIG measurements; see {out / 'abc.log'}")
    for name, (nodes, levels) in zip(("mapped", "optimized"), aig):
        stats.setdefault(name, {}).update(and_nodes=int(nodes), levels=int(levels))
    log = run([tools.abc, "-f", str(ROOT / "scripts/synth/cec.abc")], out, out / "cec.log", timeout=7200)
    stats["cec"] = "PASS" if log.count("Networks are equivalent.") == 2 else "FAIL"
    w = 8 + cfg["q_frac"]
    tag_w = 12 - (cfg["cache_sets"] - 1).bit_length()
    stats["sram_bits"] = cfg["cache_ways"] * cfg["cache_sets"] * (tag_w + 6 * w)
    (out / "metrics.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    if stats["cec"] != "PASS":
        raise ValueError(f"{tag(cfg)}: CEC failed or inconclusive; see {out / 'cec.log'}")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("lint", "sim", "synth"))
    ap.add_argument("--num-pe", type=int, default=DEFAULT["num_pe"])
    ap.add_argument("--q-frac", type=int, default=DEFAULT["q_frac"])
    ap.add_argument("--cache-ways", type=int, default=DEFAULT["cache_ways"])
    ap.add_argument("--cache-sets", type=int, default=DEFAULT["cache_sets"])
    ap.add_argument("--thresh", type=int, nargs="+", default=[0, 16])
    ap.add_argument("--out", type=Path, default=ROOT / "build/4dgs")
    ap.add_argument("--verilator", default="verilator")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--sv2v", default="sv2v")
    ap.add_argument("--abc", default="berkeley-abc")
    args = ap.parse_args()
    cfg = dict(num_pe=args.num_pe, q_frac=args.q_frac, cache_ways=args.cache_ways, cache_sets=args.cache_sets)
    out = args.out.resolve() / tag(cfg)
    if args.action == "lint":
        lint(cfg, out, args.verilator)
        print(f"lint {tag(cfg)}: verilator -Wall clean")
    elif args.action == "sim":
        binary = build_sim(cfg, out, args.verilator)
        for th in args.thresh:
            r = sim(cfg, th, binary, out)
            print(f"sim {tag(cfg)} MOTION_THRESH={th}: PASS cycles={r['sim_cycles']} mismatches={r['mismatches']} "
                  f"skip_rate={r['skip_rate']:.4f} cycle_reduction={100 * (1 - 1 / r['speedup']):.2f}% "
                  f"speedup={r['speedup']:.4f}x")
    else:
        s = synth(cfg, out / "synth", args)
        print(f"synth {tag(cfg)} (generic-cell): gates={s['mapped']['generic_gates']} "
              f"ffs={s['mapped']['flip_flops']} aig_and={s['optimized']['and_nodes']} "
              f"aig_levels={s['optimized']['levels']} opaque_sram_ways={s['mapped']['opaque_macros']} "
              f"sram_bits={s['sram_bits']} CEC {s['cec']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
