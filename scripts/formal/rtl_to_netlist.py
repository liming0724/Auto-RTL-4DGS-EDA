#!/usr/bin/env python3
"""Flatten an rtl_baseline sub-module with Yosys into netlists eda_core can ingest.

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
    aliases = {}
    for nname, info in module["netnames"].items():
        if not info.get("hide_name"):
            aliases[ident(nname)] = [b if b in ("0", "1") else names.get(b) for b in info["bits"]]
    return "\n".join(text), dict(top=top, ports=ports, registers=regs, cuts=cuts, aliases=aliases)


def build(target, out, yosys, sv2v, abc, sources=None):
    """Netlist `target`; `sources` overrides its RTL files (e.g. a fault-injected copy)."""
    spec = TARGETS[target]
    out.mkdir(parents=True, exist_ok=True)
    sources = [str(ROOT / s) for s in (sources or spec["sources"])]
    if spec["sv2v"]:
        converted = out / f"{target}.sv2v.v"
        run([sv2v, "-w", str(converted), *sources], out / "sv2v.log")
        sources = [str(converted)]
    reads = [f"read_verilog -lib -sv {ROOT / lib}" for lib in spec.get("libs", LIBS)]
    reads += [f"read_verilog {'' if spec['sv2v'] else '-sv '}{s}" for s in sources]
    script = out / f"{target}.ys"
    script.write_text("\n".join(reads + [
        f"synth -top {spec['top']} -flatten -noabc -nofsm",
        "setundef -zero", "dffunmap",
        f"abc -exe {abc} -g AND,NAND,OR,NOR,XOR,XNOR",
        "opt_clean", "check -assert", "stat",
        f"write_json {out / (target + '.yosys.json')}",
        f"write_blif {out / (target + '.blif')}",
    ]) + "\n", encoding="utf-8")
    run([yosys, "-QT", "-s", str(script)], out / "yosys.log")
    design = json.loads((out / f"{target}.yosys.json").read_text(encoding="utf-8"))
    verilog, manifest = emit(design["modules"][spec["top"]], spec["top"])
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
    for target in args.targets or TARGETS:
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
