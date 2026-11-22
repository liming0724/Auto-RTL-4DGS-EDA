#!/usr/bin/env python3
"""Flatten an rtl_baseline / rtl_4dgs module with Yosys into netlists eda_core can ingest.

Outputs (in --out, default build/formal/netlist/<target>/):
  <target>.v     primitive-gate Verilog (and/or/nand/nor/xor/xnor/not/buf + dff),
                 one scalar net per bit, read directly by eda_core/parser.py
  <target>.blif  the same mapped logic as written by Yosys
  <target>.json  manifest: ports, registers (q/d nets, async reset value) and the
                 opaque DesignWare/SRAM cut points, whose outputs become free inputs
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SSIM = "rtl_baseline/ssim_eval"
GS = "rtl_baseline/gs_rasterizer"
LIBS = ("scripts/synth/dw_interfaces.v",)
TARGETS = {
    "fp16_div8": dict(top="fp16_div8", sources=(f"{SSIM}/fp_operation.v",), sv2v=True),
    "fp16_mult2": dict(top="fp16_mult2", sources=(f"{SSIM}/fp_operation.v",), sv2v=True),
    "ssim_custom_shift_safe": dict(top="ssim_custom_shift_safe",
                                   sources=(f"{SSIM}/ssim_custom_shift_safe.v",), sv2v=True),
    "gs_rasterizer": dict(top="GS_RASTERIZER_pipeline",
                          sources=(f"{GS}/GS_RASTERIZER_pipeline.v",), sv2v=False,
                          libs=LIBS + ("scripts/synth/gs_sram_interfaces.v",)),
}
# Phase 3 (rtl_4dgs): small formal configurations (4 cache sets, 4-bit ids); the cache
# SRAM model is netlisted (not cut) so cache contents are visible to the properties.
G4 = "rtl_4dgs"
G4_ALL = tuple(f"{G4}/{m}.sv" for m in ("temporal_skip_unit", "SRAM_gs4d_cache", "motion_aware_cache",
                                        "gs4d_raster_lane", "gs4d_accelerator_top"))
G4_TARGETS = {
    "gs4d_skip_unit": dict(top="temporal_skip_unit", sources=(f"{G4}/temporal_skip_unit.sv",),
                           sv2v=True, params=dict(W=16)),
    "gs4d_cache": dict(top="motion_aware_cache",
                       sources=(f"{G4}/SRAM_gs4d_cache.sv", f"{G4}/motion_aware_cache.sv"), sv2v=True,
                       params=dict(CACHE_SETS=4, CACHE_WAYS=2, DATA_WIDTH=8, TAG_WIDTH=2)),
    "gs4d_top": dict(top="gs4d_accelerator_top", sources=G4_ALL, sv2v=True, incdir=G4,
                     observe=dict(numerator=17, quotient=17, remainder_q=18, denominator=18),
                     params=dict(NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2, CACHE_SETS=4, ID_W=4)),
}
BASELINE_TARGETS = tuple(TARGETS)
TARGETS.update(G4_TARGETS)
PRIMS = {"$_AND_": "and", "$_NAND_": "nand", "$_OR_": "or", "$_NOR_": "nor",
         "$_XOR_": "xor", "$_XNOR_": "xnor", "$_NOT_": "not", "$_BUF_": "buf"}
DFF = re.compile(r"^\$_DFF_([PN])(?:([PN])([01]))?_$")


def run(cmd, log):
    with open(log, "w", encoding="utf-8") as out:
        result = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError(f"{cmd[0]} failed; see {log}")


def ident(name):
    return re.sub(r"\W", "_", name.lstrip("\\")).strip("_") or "n"


def bit_name(base, index, width):
    return f"{base}__{index}__" if width > 1 else base


def emit(module, top):
    """Translate a flattened Yosys JSON module into eda_core primitive Verilog."""
    names, taken = {}, set()

    def claim(bit, name):
        if isinstance(bit, int) and bit not in names and name not in taken:
            names[bit] = name
            taken.add(name)

    ports = {}
    for pname, info in module["ports"].items():
        width = len(info["bits"])
        ports[ident(pname)] = dict(direction=info["direction"], width=width,
                                   bits=[bit_name(ident(pname), i, width) for i in range(width)])
    for pname, info in module["ports"].items():  # inputs own their bits
        if info["direction"] == "input":
            for i, bit in enumerate(info["bits"]):
                claim(bit, ports[ident(pname)]["bits"][i])
    cuts = []
    for cname, cell in sorted(module["cells"].items()):
        if cell["type"] in PRIMS or DFF.match(cell["type"]):
            continue
        if not re.search(r"(DW_fp_\w+|SRAM_\w+)$", cell["type"]):
            raise ValueError(f"unsupported cell {cname} ({cell['type']}) in {top}")
        cut = dict(cell=cname, type=cell["type"], inputs={}, outputs={})
        for pin, bits in sorted(cell["connections"].items()):
            base = ident(f"cut_{cname}_{pin}")
            if cell["port_directions"][pin] == "output":
                cut["outputs"][pin] = [bit_name(base, i, len(bits)) for i in range(len(bits))]
                for i, bit in enumerate(bits):
                    claim(bit, cut["outputs"][pin][i])
            else:
                cut["inputs"][pin] = bits
        cuts.append(cut)
    for nname, info in sorted(module["netnames"].items(), key=lambda kv: (kv[1].get("hide_name", 0), len(kv[0]))):
        if info.get("hide_name"):
            continue
        width = len(info["bits"])
        for i, bit in enumerate(info["bits"]):
            claim(bit, bit_name(ident(nname), i, width))

    def net(bit):
        if bit in ("0", "1"):
            return f"1'b{bit}"
        if bit in ("x", "z"):
            raise ValueError(f"undefined bit in {top}; run setundef first")
        if bit not in names:
            claim(bit, f"n{bit}")
        return names[bit]

    body, regs, wires = [], [], set()
    for cname, cell in sorted(module["cells"].items()):
        kind, conn = cell["type"], cell["connections"]
        inst = ident(cname)
        if kind in PRIMS:
            pins = [p for p in ("A", "B") if p in conn]
            ins = [net(conn[p][0]) for p in pins]
            out = net(conn["Y"][0])
            wires.update([out, *ins])
            body.append(f"  {PRIMS[kind]} g_{inst} ({', '.join([out, *ins])});")
        elif DFF.match(kind):
            _, rpol, rval = DFF.match(kind).groups()
            q, d, clk = net(conn["Q"][0]), net(conn["D"][0]), net(conn["C"][0])
            reg = dict(q=q, d=d, clk=clk, reset_value=None if rval is None else int(rval))
            ports_str = f".C({clk}), .D({d}), .Q({q})"
            if rval is not None:
                reg.update(reset=net(conn["R"][0]), reset_active_low=rpol == "N")
                ports_str = f".C({clk}), .R({reg['reset']}), .D({d}), .Q({q})"
            regs.append(reg)
            wires.update([q, d])
            body.append(f"  dff r_{inst} ({ports_str});")
    for cut in cuts:
        cut["inputs"] = {pin: [net(b) for b in bits] for pin, bits in cut["inputs"].items()}
    for pname, info in module["ports"].items():
        if info["direction"] == "output":
            for i, bit in enumerate(info["bits"]):
                src, dst = net(bit), ports[ident(pname)]["bits"][i]
                if src != dst:
                    body.append(f"  assign {dst} = {src};")
    inputs = [b for p in ports.values() if p["direction"] == "input" for b in p["bits"]]
    inputs += [b for c in cuts for bits in c["outputs"].values() for b in bits]
    outputs = [b for p in ports.values() if p["direction"] == "output" for b in p["bits"]]
    wires = sorted(w for w in wires - set(inputs) - set(outputs) if not w.startswith("1'b"))
    text = [f"module {ident(top)} ({', '.join(inputs + outputs)});"]
    text += [f"  input {n};" for n in inputs] + [f"  output {n};" for n in outputs]
    text += [f"  wire {n};" for n in wires] + body + ["endmodule", ""]
    # A public netname whose bit no longer has a driver after flatten/ABC (e.g. logic folded
    # into the consumers) would read as a free variable; expose it as None instead.
    driven = {b for p in module["ports"].values() if p["direction"] == "input" for b in p["bits"]}
    for cell in module["cells"].values():
        for pin, bits in cell["connections"].items():
            if cell.get("port_directions", {}).get(pin) == "output" or pin in ("Y", "Q"):
                driven.update(bits)
    aliases = {}
    for nname, info in module["netnames"].items():
        if not info.get("hide_name"):
            aliases[ident(nname)] = [b if b in ("0", "1") else names.get(b) if b in driven else None
                                     for b in info["bits"]]
    return "\n".join(text), dict(top=top, ports=ports, registers=regs, cuts=cuts, aliases=aliases)


def validate_observations(module, widths):
    for name, width in widths.items():
        actual = len(module["netnames"].get(name, {}).get("bits", []))
        if actual != width:
            raise ValueError(f"formal observation {name}: expected {width} bits, got {actual}")


def build(target, out, yosys, sv2v, abc, sources=None):
    """Netlist `target`; `sources` overrides its RTL files (e.g. a fault-injected copy)."""
    spec = TARGETS[target]
    out.mkdir(parents=True, exist_ok=True)
    sources = [str(ROOT / s) for s in (sources or spec["sources"])]
    if spec["sv2v"]:
        converted = out / f"{target}.sv2v.v"
        incdir = [f"-I{ROOT / spec['incdir']}"] if "incdir" in spec else []
        run([sv2v, *incdir, "-w", str(converted), *sources], out / "sv2v.log")
        sources = [str(converted)]
    reads = [f"read_verilog -lib -sv {ROOT / lib}" for lib in spec.get("libs", LIBS)]
    reads += [f"read_verilog {'' if spec['sv2v'] else '-sv '}{s}" for s in sources]
    script = out / f"{target}.ys"
    reads += [f"chparam -set {k} {v} {spec['top']}" for k, v in spec.get("params", {}).items()]
    if spec.get("observe"):
        # Preserve observations after hierarchy specializes parameterized modules.
        reads += [f"hierarchy -top {spec['top']}", f"rename -top {spec['top']}"]
        reads += ["setattr -set keep 1 " + " ".join(f"w:{name}" for name in spec["observe"])]
    script.write_text("\n".join(reads + [
        f"synth -top {spec['top']} -flatten -noabc -nofsm",
        *([f"rename -top {spec['top']}"] if "params" in spec and not spec.get("observe") else []),
        "setundef -zero", "dffunmap",
        f"abc -exe {abc} -g AND,NAND,OR,NOR,XOR,XNOR",
        "opt_clean", "check -assert", "stat",
        f"write_json {out / (target + '.yosys.json')}",
        f"write_blif {out / (target + '.blif')}",
    ]) + "\n", encoding="utf-8")
    run([yosys, "-QT", "-s", str(script)], out / "yosys.log")
    design = json.loads((out / f"{target}.yosys.json").read_text(encoding="utf-8"))
    module = design["modules"][spec["top"]]
    validate_observations(module, spec.get("observe", {}))
    verilog, manifest = emit(module, spec["top"])
    (out / f"{target}.v").write_text(verilog, encoding="utf-8")
    (out / f"{target}.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return out / f"{target}.v", out / f"{target}.json"


def ingest_check(netlist):
    """Prove the netlist loads unmodified into eda_core (parser + fast_eda)."""
    sys.path[:0] = [str(ROOT / "eda_core"), str(ROOT / "build/eda")]
    import fast_eda  # noqa: E402
    from parser import VerilogParser  # noqa: E402
    design = VerilogParser().parse(str(netlist))
    graph = fast_eda.FastGraph()
    for gate in design.gates.values():
        if gate.gate_type != "dff":
            graph.add_net_driver(gate.output_nets[0].name, gate.inst_name,
                                 [n.name for n in gate.input_nets])
    dffs = sum(g.gate_type == "dff" for g in design.gates.values())
    return f"{design.name}: {len(design.gates) - dffs} gates, {dffs} dffs, {len(design.inputs)} inputs, {len(design.outputs)} outputs"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", metavar="TARGET", help="one of: " + ", ".join(TARGETS))
    ap.add_argument("--out", type=Path, default=ROOT / "build/formal/netlist")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--sv2v", default="sv2v")
    ap.add_argument("--abc", default="yosys-abc")
    ap.add_argument("--check", action="store_true", help="load each netlist with eda_core")
    args = ap.parse_args()
    for target in args.targets or BASELINE_TARGETS:
        if target not in TARGETS:
            raise ValueError(f"unknown target {target}")
        netlist, _ = build(target, args.out / target, args.yosys, args.sv2v, args.abc)
        print(f"{target}: {netlist.relative_to(ROOT) if netlist.is_relative_to(ROOT) else netlist}"
              + (f" ({ingest_check(netlist)})" if args.check else ""))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
