#!/usr/bin/env python3
"""Generic Yosys/ABC baseline with explicit, identical CEC boundaries."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TOPS = {"ssim": "SSIM_v2", "gs": "GS_RASTERIZER_pipeline"}
SSIM_RTL = tuple(
    f"rtl_baseline/ssim_eval/{name}.v"
    for name in ("SSIM_v2", "ssim_custom_shift_safe", "fp_operation")
)
# Yosys single-bit gates: ordered input pins and BLIF on-set.
GATES = {
    "$_AND_": ("AB", ("11 1",)),
    "$_OR_": ("AB", ("1- 1", "-1 1")),
    "$_XOR_": ("AB", ("01 1", "10 1")),
    "$_XNOR_": ("AB", ("00 1", "11 1")),
    "$_MUX_": ("ABS", ("1-0 1", "-11 1")),
    "$_NOT_": ("A", ("0 1",)),
    "$_BUF_": ("A", ("1 1",)),
}


def run(command, cwd, log, timeout=180):
    with log.open("w", encoding="utf-8") as out:
        try:
            subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"{error}; see {log}") from error
    return log.read_text(encoding="utf-8", errors="replace")


def yosys(executable, out, name, commands):
    script = out / f"{name}.ys"
    script.write_text("\n".join(commands) + "\n", encoding="utf-8")
    return run([executable, "-QT", "-s", str(script)], ROOT, out / f"{name}.log")


def cut_blif(source, destination, top, mapped):
    """Expose each opaque pin and register pin with a stable CEC port name."""
    net = json.loads(source.read_text(encoding="utf-8"))
    module = net["modules"][top]
    stats = Counter()
    manifest = []
    ports = {"input": {}, "output": {}}
    bodies = [".names x0", ".names x1\n1"]
    used, driven = set(), {"x0", "x1"}

    def signal(bit):
        if isinstance(bit, int):
            return f"n{bit}"
        if bit in ("0", "1"):
            return f"x{bit}"
        raise ValueError(f"unexpected logic constant {bit!r} in {top}")

    def node(inputs, output, rows):
        if output in driven:
            raise ValueError(f"multiple drivers for {output} in {top}")
        used.update(inputs)
        driven.add(output)
        bodies.append("\n".join([" ".join([".names", *inputs, output]), *rows]))

    def port(name, direction, bits):
        for index, bit in enumerate(bits):
            net_name = signal(bit)
            pin = f"{direction}_{name}_{index}"
            ports[direction][pin] = None
            if direction == "input":
                driven.add(pin)
                node([pin], net_name, ("1 1",))
            else:
                node([net_name], pin, ("1 1",))

    for name, info in sorted(module["ports"].items()):
        port(f"top_{name}", info["direction"], info["bits"])
    for name, cell in sorted(module["cells"].items()):
        kind = cell["type"]
        connections = cell["connections"]
        if kind in GATES:
            if mapped and kind == "$_XNOR_":
                raise ValueError(f"unexpected mapped gate {kind} in {top}")
            stats["generic_gates"] += 1
            pins, rows = GATES[kind]
            node([signal(connections[pin][0]) for pin in pins],
                 signal(connections["Y"][0]), rows)
            continue
        if kind.startswith(("$_DFF", "$_SDFF", "$_ALDFF")):
            stats["flip_flops"] += 1
        elif kind.startswith(("DW_fp_", "SRAM_")) or re.search(r"\\DW_fp_\w+$", kind):
            stats["opaque_macros"] += 1
        else:
            raise ValueError(f"unsupported cell in {top}: {name} ({kind})")
        directions = cell["port_directions"]
        manifest.append((name, kind, cell["parameters"], {
            pin: (directions[pin], len(bits)) for pin, bits in connections.items()
        }))
        for pin, bits in sorted(connections.items()):
            direction = {"input": "output", "output": "input"}[directions[pin]]
            port(f"cut_{len(manifest):05d}_{pin}", direction, bits)

    if used - driven:
        raise ValueError(f"undriven signals in {top}: {sorted(used - driven)[:5]}")
    with destination.open("w", encoding="utf-8") as out:
        print(f".model {top}", file=out)
        for direction, pins in ports.items():
            print(f".{direction}s", *pins, file=out)
        print(*bodies, ".end", sep="\n", file=out)
    return manifest, dict(stats)


def synthesize(target, top, out, args):
    for name in ("cec.log", "metrics.json", "optimized.blif"):
        (out / name).unlink(missing_ok=True)
    if target == "ssim":
        run([args.sv2v, "-w", str(out / "ssim_converted.v"), *SSIM_RTL],
            ROOT, out / "sv2v.log")
    variables = [f"read_verilog {json.dumps(str(out / 'ssim_converted.v'))}"] if target == "ssim" else []
    prefix = (ROOT / f"scripts/synth/{target}.ys").read_text(encoding="utf-8").splitlines()
    log = yosys(args.yosys, out, "synth", [
        *prefix, *variables, "setattr -set keep 1 t:SRAM_*",
        f"synth -top {top} -flatten -noabc", "setundef -zero",
        "check -assert", f"write_json {json.dumps(str(out / 'pre.json'))}",
        f"abc -exe {json.dumps(args.abc)} -g AND,OR,XOR,MUX",
        "check -assert", "stat",
        f"write_json {json.dumps(str(out / 'mapped.json'))}",
        f"write_verilog -noattr {json.dumps(str(out / 'mapped_top.v'))}",
    ])
    if "Warning: Resizing cell port" in log:
        raise ValueError(f"macro pin width mismatch; see {out / 'synth.log'}")

    manifests = []
    stats = {}
    for name in ("pre", "mapped"):
        manifest, stats[name] = cut_blif(
            out / f"{name}.json", out / f"{name}.blif", top, mapped=name == "mapped"
        )
        manifests.append(manifest)
    if manifests[0] != manifests[1]:
        raise ValueError(f"{top} mapping changed an opaque cell or state boundary")

    log = run([args.abc, "-f", str(ROOT / "scripts/synth/aig.abc")], out, out / "abc.log")
    aig = re.findall(r"\band\s*=\s*(\d+)\s+lev\s*=\s*(\d+)", log)
    if len(aig) != 2 or not (out / "optimized.blif").is_file():
        raise ValueError(f"ABC did not produce both AIG measurements; see {out / 'abc.log'}")
    for name, (nodes, levels) in zip(("mapped", "optimized"), aig):
        stats.setdefault(name, {}).update(and_nodes=int(nodes), levels=int(levels))
    if not all(stats[name].get(key, 0) for name, key in (
        ("mapped", "generic_gates"), ("mapped", "flip_flops"), ("mapped", "opaque_macros"),
        ("optimized", "and_nodes"),
    )):
        raise ValueError(f"empty synthesis result for {top}: {stats}")
    (out / "metrics.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"{top}: {json.dumps(stats)}")


def prove(top, out, args):
    log = run([args.abc, "-f", str(ROOT / "scripts/synth/cec.abc")], out, out / "cec.log",
              timeout=150)
    if log.count("Networks are equivalent.") != 2:
        raise ValueError(f"{top} CEC failed or was inconclusive; see {out / 'cec.log'}")
    print(f"{top}: CEC PASS (pre vs. mapped, pre vs. optimized); {out / 'cec.log'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("synth", "cec"))
    parser.add_argument("--out", type=Path, default=ROOT / "build/baseline/synth")
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--abc", default="berkeley-abc")
    parser.add_argument("--sv2v", default="sv2v")
    args = parser.parse_args()
    for target, top in TOPS.items():
        out = args.out.resolve() / target
        out.mkdir(parents=True, exist_ok=True)
        if args.action == "synth":
            synthesize(target, top, out, args)
        else:
            prove(top, out, args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
