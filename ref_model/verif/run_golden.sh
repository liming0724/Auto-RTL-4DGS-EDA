#!/usr/bin/env bash
# Golden-answer check for GS_RASTERIZER_pipeline.v.
#
#   run_golden.sh [GOLDEN_DIR]     (default: ref_model/verif/golden/gs_rasterizer)
#
# GOLDEN_DIR/golden_ans/ holds the reference N/T/rendered/depth images. The
# scene that produced them must sit next to it as GOLDEN_DIR/ram_file.txt and
# GOLDEN_DIR/gs_value.txt (the two SRAM images TB.sv loads). Without them only
# the golden statistics are printed and the script exits 3.
#
# With them it renders the scene with the C++ model for every RtlQuirks
# combination (q<dxdy><trans><early>, q111 = current RTL), q000 with each
# golden-behaviour flag off (q000_s/n/r/d, q000_snrd = all off) and the ideal
# algorithm, simulates the RTL (Verilator + sim-only DW substitutes), and
# reports per-field mismatch counts of each against golden_ans. Exit status is
# the bit-true gate: 0 iff the RTL matches the q111 model on every pixel.
# Golden mismatches are reported, never gated: the RTL quirks are expected to
# cause them. Per-pixel mismatch lists go to build/golden/mismatch/*.csv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
GOLD="$(cd "${1:-$ROOT/verif/golden/gs_rasterizer}" && pwd)"
B="$ROOT/build/golden"
CMP="$ROOT/verif/golden_compare.py"
if [ ! -f "$GOLD/ram_file.txt" ] || [ ! -f "$GOLD/gs_value.txt" ]; then
  python3 "$CMP" "$GOLD/golden_ans"
  echo "golden comparison NOT RUN: $GOLD/ram_file.txt and gs_value.txt (the scene behind golden_ans) are missing"
  exit 3
fi
mkdir -p "$B"
rm -rf "$B/run" "$B/models" "$B/mismatch"
g++ -std=c++17 -O2 -Wall -Wextra -Werror -I"$ROOT/include" "$ROOT/tools/gs4d_render.cpp" -o "$B/gs4d_render"
RTL="$REPO/rtl_baseline/gs_rasterizer"
SUB="$ROOT/rtl/sim_substitutes"
verilator --cc --exe --build -O2 -Wno-fatal -Wno-lint -Wno-style --top-module GS_RASTERIZER_pipeline \
  --Mdir "$B/obj" -I"$SUB" \
  "$RTL/GS_RASTERIZER_pipeline.v" "$RTL/SRAM_SortedGaussianBall.v" "$RTL/SRAM_GaussianValueDataBase.v" \
  "$RTL/SRAM_OutputPixel.v" "$SUB"/DW_fp_*.v "$ROOT/verif/tb_gs_rasterizer.cpp" > "$B/verilator.log" 2>&1 \
  || { tail -40 "$B/verilator.log"; exit 1; }
"$B/gs4d_render" --ram "$GOLD/ram_file.txt" --gs "$GOLD/gs_value.txt" --out "$B/models"
mkdir -p "$B/run"
cp "$GOLD/ram_file.txt" "$GOLD/gs_value.txt" "$B/run/"
cp -r "$B/models/q111" "$B/run/golden_ans"
status=0
echo "RTL vs C++ model (q111, all quirks on):"
(cd "$B/run" && "$B/obj/VGS_RASTERIZER_pipeline") || status=1
args=("rtl=$B/run/rtl_out")
for d in "$B"/models/*/; do args+=("$(basename "$d")=$d"); done
python3 "$CMP" "$GOLD/golden_ans" "${args[@]}" --csv "$B/mismatch"
exit $status
