#!/usr/bin/env bash
# Simulates the baseline GS_RASTERIZER_pipeline.v (Verilator + sim-only DW
# substitutes) on seeded synthetic 4DGS scenes and checks it pixel-for-pixel
# against the C++ bit-true model (RtlRasterizer<F32Policy>).
# NOTE: rtl/sim_substitutes are NOT Synopsys originals; agreement here does
# not establish bit-exactness against the real DesignWare models.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
B="$ROOT/build/rtl_sim"
mkdir -p "$B"
g++ -std=c++17 -O2 -Wall -Wextra -I"$ROOT/include" "$ROOT/tools/gs4d_ref.cpp" -o "$B/gs4d_ref"
RTL="$REPO/rtl_baseline/gs_rasterizer"
SUB="$ROOT/rtl/sim_substitutes"
verilator --cc --exe --build -O2 -Wno-fatal -Wno-lint -Wno-style --top-module GS_RASTERIZER_pipeline \
  --Mdir "$B/obj" -I"$SUB" \
  "$RTL/GS_RASTERIZER_pipeline.v" "$RTL/SRAM_SortedGaussianBall.v" "$RTL/SRAM_GaussianValueDataBase.v" \
  "$RTL/SRAM_OutputPixel.v" "$SUB"/DW_fp_*.v "$ROOT/verif/tb_gs_rasterizer.cpp" > "$B/verilator.log" 2>&1 \
  || { tail -40 "$B/verilator.log"; exit 1; }
status=0
SCENES=("$@")
[ ${#SCENES[@]} -eq 0 ] && SCENES=("1:600:0.5" "2:300:0.1" "3:1088:0.9")
for spec in "${SCENES[@]}"; do
  IFS=: read -r seed n t <<< "$spec"
  d="$B/scene_s${seed}_n${n}_t${t}"
  "$B/gs4d_ref" --seed "$seed" --n "$n" --t "$t" --out "$d" > "$d.model.log"
  echo "scene seed=$seed n=$n t=$t"
  (cd "$d" && "$B/obj/VGS_RASTERIZER_pipeline") || status=1
done
exit $status
