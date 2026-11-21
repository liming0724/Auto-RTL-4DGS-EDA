#!/usr/bin/env python3
"""Pixel-by-pixel comparison of GS rasterizer outputs against golden_ans.

  golden_compare.py GOLDEN_ANS_DIR [LABEL=OUTPUT_DIR ...] [--csv DIR]

Every directory holds the four TB.sv files (N_img.txt, T_img.txt,
rendered_img.txt, depth_img.txt), one line per pixel in write order.
Pixel index i is (px, py) = (i % 75, i // 75): GS_RASTERIZER_pipeline.v
steps px 0..74 then py 0..33 and TB.sv checks write address waddr against
line waddr of each golden file.

Per field it reports exact (bit-for-bit) mismatches and TB.sv-tolerance
mismatches (N exact, |dT| and |dD| > 1e-4, any of R/G/B off by > 1).
With --csv, every mismatching (pixel, field) is written to DIR/<label>.csv.
Without outputs it only prints golden statistics.
"""
import argparse
import csv
import os
import struct
import sys

W, H = 75, 34
NPIX = W * H
FIELDS = ("N", "T", "D", "RGB")
T_MIN = struct.unpack(">f", bytes.fromhex("38D1B717"))[0]  # RTL early-termination threshold 1e-4


def f32(h):
    return struct.unpack(">f", int(h, 16).to_bytes(4, "big"))[0]


def load(d):
    def lines(name):
        with open(os.path.join(d, name)) as f:
            v = [l.strip() for l in f if l.strip()]
        if len(v) != NPIX:
            sys.exit("%s/%s: %d lines, expected %d" % (d, name, len(v), NPIX))
        return v
    return {
        "N": [int(x) for x in lines("N_img.txt")],
        "T": [int(x, 16) for x in lines("T_img.txt")],
        "D": [int(x, 16) for x in lines("depth_img.txt")],
        "RGB": [tuple(int(c) for c in x.split()) for x in lines("rendered_img.txt")],
    }


def fmt(field, v):
    if field in ("T", "D"):
        return "%08X (%.6g)" % (v, f32("%x" % v))
    if field == "RGB":
        return "%d %d %d" % v
    return str(v)


def compare(g, o):
    res = {}
    for fld in FIELDS:
        exact, tol = [], []
        for i in range(NPIX):
            a, b = g[fld][i], o[fld][i]
            if a == b:
                continue
            exact.append(i)
            if fld == "N":
                bad = True
            elif fld == "RGB":
                bad = any(abs(x - y) > 1 for x, y in zip(a, b))
            else:
                bad = abs(f32("%x" % a) - f32("%x" % b)) > 1e-4
            if bad:
                tol.append(i)
        res[fld] = (exact, tol)
    anyp = sorted(set().union(*(set(res[f][1]) for f in FIELDS)))
    return res, anyp


def ranges(idx):
    out, i = [], 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        out.append(str(idx[i]) if i == j else "%d-%d" % (idx[i], idx[j]))
        i = j + 1
    s = ",".join(out)
    return s if len(s) <= 100 else s[:97] + "..."


def analyze(g):
    n, t = g["N"], [f32("%x" % x) for x in g["T"]]
    d = [f32("%x" % x) for x in g["D"]]
    rgb = [c for p in g["RGB"] for c in p]
    early = [i for i in range(NPIX) if t[i] < T_MIN]
    print("golden: %d pixels (%dx%d, row-major write order)" % (NPIX, W, H))
    print("  N %d..%d, N=1089 at %d pixels, N=0 at %d" % (min(n), max(n), n.count(1089), n.count(0)))
    print("  T %.4g..%.4g, T<1e-4 (early termination) at %d pixels, first at pixel %s, T=1.0 at %d"
          % (min(t), max(t), len(early), early[0] if early else "-", g["T"].count(0x3F800000)))
    print("  D %.4g..%.4g, RGB %d..%d" % (min(d), max(d), min(rgb), max(rgb)))
    if early:
        print("  sticky_early prediction: the current RTL never clears early_termination, so every pixel"
              " after pixel %d would end with N=0, T=1 (%d pixels)" % (early[0], NPIX - 1 - early[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("golden")
    ap.add_argument("outputs", nargs="*", metavar="LABEL=DIR")
    ap.add_argument("--csv", help="write per-pixel mismatch lists here")
    a = ap.parse_args()
    g = load(a.golden)
    analyze(g)
    if not a.outputs:
        return
    if a.csv:
        os.makedirs(a.csv, exist_ok=True)
    print("%-12s %13s %13s %13s %13s %15s" % ("vs golden", "N exact/tol", "T exact/tol", "D exact/tol",
                                             "RGB exact/tol", "pixels (tol)"))
    for spec in a.outputs:
        label, d = spec.split("=", 1)
        o = load(d)
        res, anyp = compare(g, o)
        print("%-12s %13s %13s %13s %13s %15d" % ((label,) + tuple("%d/%d" % (len(res[f][0]), len(res[f][1]))
                                                                 for f in FIELDS) + (len(anyp),)))
        for f in FIELDS:
            if res[f][1]:
                print("    %-3s tol-mismatch pixels: %s" % (f, ranges(res[f][1])))
        if a.csv:
            with open(os.path.join(a.csv, label + ".csv"), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["pixel", "px", "py", "field", "golden", "got", "beyond_tb_tolerance"])
                for f in FIELDS:
                    tol = set(res[f][1])
                    for i in res[f][0]:
                        w.writerow([i, i % W, i // W, f, fmt(f, g[f][i]), fmt(f, o[f][i]), int(i in tol)])


if __name__ == "__main__":
    main()
