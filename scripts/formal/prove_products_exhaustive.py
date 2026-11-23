#!/usr/bin/env python3
"""Exhaustive proof of every deform_engine product on the Tier-3 netlist.

Obligation (unchanged from the direct Z3 miter that returns UNKNOWN, see
docs/BACKLOG_INVESTIGATION.md item 4): for every product o in 0..4, k in 0..1,
    prod[o][k] == sext(coef[o][k]) * sext(basis_k)     (2W bits, bit-true)
with basis_0 = t and basis_1 = the netlist's own basis_t2 net (basis_t2 == fxmul(t, t) is a
separate Z3 obligation in verify_z3_4dgs.py). deform_engine has no registers, so each
product is a pure function of its 2W input bits; enumerating all 2^(2W) operand pairs on the
gate-level netlist is a complete (unbounded) proof, not a bounded one. All 5 coef slices are
driven with the same operand, which covers all 10 products in one sweep; `check_leaves`
first proves structurally that each product's cone reads only its own coef slice and t.

A self-test first flips one XOR in a product cone and requires the sweep to see mismatches.
Input: the BLIF written by rtl_to_netlist.py gs4d_deform (needs numpy).
"""
import argparse
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
W, PW, NPROD = 12, 24, 10  # Q_FRAC=4 formal configuration (DEF_W in verify_z3_4dgs.py)
CHUNK = 1 << 18


def read_blif(text):
    """.names SOP covers -> {output: (fanins, rows)}."""
    lines = [l.strip() for l in text.replace("\\\n", " ").splitlines()]
    nodes, i = {}, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(".names"):
            sig, rows = line.split()[1:], []
            while i < len(lines) and lines[i] and not lines[i].startswith("."):
                rows.append(lines[i].split())
                i += 1
            if len({r[-1] for r in rows if len(r) > 1}) > 1:
                raise ValueError(f"mixed on/off-set cover for {sig[-1]}")
            nodes[sig[-1]] = (sig[:-1], rows)
    return nodes


def evaluate(nodes, roots, inputs):
    """Bit-parallel evaluation (uint64 words) of the cones of `roots`."""
    val = dict(inputs)
    ones = np.full_like(next(iter(inputs.values())), ~np.uint64(0))
    zero = ones ^ ones
    order, seen = [], set()
    for root in roots:
        stack = [(root, False)]
        while stack:
            net, done = stack.pop()
            if net in seen or net in val:
                continue
            if done:
                seen.add(net)
                order.append(net)
                continue
            stack.append((net, True))
            stack.extend((f, False) for f in nodes[net][0] if f not in seen)
    for net in order:
        fanins, rows = nodes[net]
        if not fanins:
            val[net] = ones.copy() if rows and rows[0][-1] == "1" else zero.copy()
            continue
        acc = zero.copy()
        for cube, _ in rows:
            term = ones.copy()
            for f, c in zip(fanins, cube):
                if c == "1":
                    term &= val[f]
                elif c == "0":
                    term &= ~val[f]
            acc |= term
        val[net] = ~acc if rows[0][1] == "0" else acc
    return {r: val[r] for r in roots}


def leaves(nodes, roots):
    """Primary inputs in the transitive fan-in of `roots`."""
    stack, seen, out = list(roots), set(), set()
    while stack:
        net = stack.pop()
        if net in seen:
            continue
        seen.add(net)
        if net in nodes:
            stack.extend(nodes[net][0])
        else:
            out.add(net)
    return out


def check_leaves(nodes):
    """Each product reads only its own coef slice and t (so equal slices cannot mask miswiring)."""
    for p in range(NPROD):
        allowed = {f"coef[{p * W + i}]" for i in range(W)} | {f"t[{i}]" for i in range(W)}
        extra = leaves(nodes, [f"prod[{p * PW + i}]" for i in range(PW)]) - allowed
        if extra:
            raise ValueError(f"product {p} reads {sorted(extra)}")


def sweep(nodes):
    """Number of (product, operand pair) mismatches over all 2^(2W) pairs."""
    pack = lambda bits: np.packbits(bits.astype(np.uint8), bitorder="little").view(np.uint64)
    unpack = lambda w: np.unpackbits(w.view(np.uint8), bitorder="little")[:CHUNK].astype(np.int64)
    sext = lambda x: np.where(x >= 1 << (W - 1), x - (1 << W), x)
    roots = [f"prod[{i}]" for i in range(NPROD * PW)] + [f"basis_t2[{i}]" for i in range(W)]
    bad = 0
    for chunk in range((1 << (2 * W)) // CHUNK):
        v = np.arange(chunk * CHUNK, (chunk + 1) * CHUNK, dtype=np.int64)
        coef, t = v & ((1 << W) - 1), v >> W
        inputs = {f"t[{i}]": pack((t >> i) & 1) for i in range(W)}
        inputs.update({f"coef[{i}]": pack((coef >> (i % W)) & 1) for i in range(NPROD * W)})
        out = evaluate(nodes, roots, inputs)
        word = lambda name, lo, w: sum(unpack(out[f"{name}[{lo + i}]"]) << i for i in range(w))
        t2 = word("basis_t2", 0, W)
        for p in range(NPROD):
            spec = (sext(coef) * sext(t if p % 2 == 0 else t2)) & ((1 << PW) - 1)
            bad += int(np.count_nonzero(word("prod", p * PW, PW) != spec))
    return bad


def mutate(nodes):
    """Copy of the netlist with the first XOR/XNOR cover in prod[W]'s fan-in inverted."""
    stack, seen = [f"prod[{W}]"], set()
    while stack:
        net = stack.pop()
        if net in seen or net not in nodes:
            continue
        seen.add(net)
        fanins, rows = nodes[net]
        if len(fanins) == 2 and sorted(r[0] for r in rows) in (["01", "10"], ["00", "11"]):
            flip = {"01": "00", "10": "11", "00": "01", "11": "10"}
            return {**nodes, net: (fanins, [[flip[r[0]], r[1]] for r in rows])}, net
        stack.extend(fanins)
    raise RuntimeError("no XOR in product cone")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blif", type=Path, default=ROOT / "build/formal/netlist/gs4d_deform/gs4d_deform.blif")
    args = ap.parse_args()
    nodes = read_blif(args.blif.read_text(encoding="utf-8"))
    check_leaves(nodes)
    start = time.monotonic()
    mutant, net = mutate(nodes)
    caught = sweep(mutant)
    print(f"self-test: inverted XOR {net} -> {caught} mismatches ({time.monotonic() - start:.1f}s)")
    if caught == 0:
        print("FAIL: sweep is vacuous")
        return 1
    start = time.monotonic()
    bad = sweep(nodes)
    total = NPROD << (2 * W)
    print(f"{'PASS' if bad == 0 else 'FAIL'}: {NPROD} products x 2^{2 * W} operand pairs = {total} checks, "
          f"{bad} mismatches ({time.monotonic() - start:.1f}s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
