# Phase 4: 4DGS deformation engine and quality-vs-skip analysis

Phase 4 replaces the externally precomputed temporal deltas of the Phase 3 accelerator
with an on-chip spatiotemporal deformation front-end (`rtl_4dgs/deform_engine.sv`). It
then measures what temporal skipping costs in rendering quality on a 64×64, 8-frame
dynamic scene: PSNR against an FP32 reference, and hardware SSIM from `SSIM_v2`.

Reproduce (Verilator ≥ 4.038, Yosys 0.23, sv2v 0.0.12, Python venv with `z3-solver`, `pybind11`; see
[BASELINE_METRICS.md](BASELINE_METRICS.md)):

```sh
make sim_4dgs            # Tier 1: Phase 3 bit-true testbench + deformation vectors
make sim_4dgs_frame      # FP32-vs-fixed validation (Q_FRAC=12, T=0) + SSIM_v2 frame scoring
make eval_quality_4dgs   # 18-point sweep -> build/4dgs/quality/quality_vs_skip.{csv,json,md}
make synth_4dgs          # Tier 2: Yosys/ABC generic cells + ABC CEC
make formal_4dgs         # Tier 3: Z3 through eda_core (includes the new gs4d_deform target)
```

## 1. Deformation engine

`deform_engine` is combinational and sits between the input handshake and
`temporal_skip_unit`. It uses a lightweight temporal basis `b(t) = [1, t, t²]` with a
dot-product accumulator for each of the five outputs (Δμx, Δμy, Δsx, Δsy, Δρ):

```
acc_o = (c0_o << F) + c1_o·t + c2_o·t²      exact, AW = 2W+2 bits
out_o = sat_W((acc_o + 2^(F-1)) >>> F)       one half-up rounding, Q(7,F) saturation
t²    = fxmul(t, t)
```

- **Inputs:** Gaussian base parameters (`mu0_x/y`, `s0_x/y`, `rho0`), normalized
  time `t` (new top port `in_t`, Q(7,F)), and the order-0 terms `c0_*`. The `c0_*` terms
  are the existing `in_dmu_*` / `in_ds_*` ports, so `t = 0` with zero coefficients is
  exactly Phase 3. The packed basis coefficients use the new top port
  `in_coef[COEF_W-1:0]`, where `COEF_W = 10·W` and `coef[(2o+k-1)·W +: W] = c{k}_o`.
- **Outputs:** Δμ(t) and Δs(t) feed `temporal_skip_unit`, so the skip metric is
  unchanged. Δρ(t) is added to `in_rho` with `fx_add` before projection. The skip
  metric has no ρ term, so a Gaussian is only skip-eligible when Δρ(t) == 0
  (`skip_q <= skip_deform && dr == 0`; `GaussIn::drho` in the golden model). A rotating
  Gaussian therefore never reuses a stale conic.
- **Swappability:** all basis-specific math is inside `deform_engine.sv`. An MLP or
  HexPlane / bilinear feature-grid front-end replaces only this file. It keeps the
  ports and redefines `COEF_W` and what the coefficient vector holds (for example,
  per-Gaussian grid features). `temporal_skip_unit`, `motion_aware_cache`,
  `gs4d_raster_lane`, and the PROJ/raster stages of `gs4d_accelerator_top` are
  unchanged.
- **Operand isolation:** the skip decision is computed from the engine's outputs, so
  the engine is never gated by the skip signal of the Gaussian it is deforming.
  Projection, covariance, and raster operand isolation on skip or cache-hit reuse is
  unchanged. The Phase 3 testbench still reports `isolation_errors=0`.
- **Golden model:** `gs4d_ref::deform` in `golden_model/4dgs_ref.hpp` is bit-true to
  the RTL. `Accelerator::step` applies it first, and the conic cross-check still uses
  the independent `gs4d::conic_from_scale`. The Phase 3 testbench adds 800
  deformation vectors: directed rail times, ±full-scale and ±1-LSB coefficients, and
  random values. Vectors whose deformed `|ρ(t)| ≥ 1` or `|s| ≥ 11 px` (where s² saturates
  Q(7,F)) are not valid covariances. They are still checked bit-true but skip the
  conic cross-check.

## 2. FP32 reference renderer and validation

`golden_model/gs4d_frame.hpp` holds the scene, the FP32 renderer, the testbench-side
binning and compositing, PSNR, FP16 conversion, and a software model of the SSIM_v2
formula. There was no FP32 renderer in the repository before this.

- **Scene:** 256 Gaussians on a 64×64 frame, frames 0–7 at `t = k/8`. 60% are static
  background (all coefficients 0, σ 1.5–3.5 px). 40% are moving foreground (σ 0.8–2 px)
  on quadratic trajectories. Their motion amplitude is log-uniform from about 0.002 px
  to 12 px over the sequence (up to 1.5 px per frame), with proportional curvature,
  scale drift, and ρ drift.
- **Ground truth:** every Gaussian is evaluated at every pixel in float32 with the
  standard 3DGS `α = min(0.99, o·exp(-½ dᵀΣ⁻¹d))` and the `α < 1/255` cutoff. The conic
  is `(sy², -ρ·sx·sy, sx²)/det` from `conic_from_scale`.
- **Fixed-point RTL:** each Gaussian's 3.5σ box is binned into 4-pixel row spans,
  and each span is one accelerator transaction with the same id. The FP32 deformed
  parameters decide the binning, the quantized coefficients drive `in_t` / `in_coef`,
  and every output is checked bit-true against the golden model.
- **Compositing:** the testbench, not the RTL, composites with a sort-free normalized
  weighted sum that is order-independent like the accelerator's per-Gaussian alpha
  stream: `C(p) = (w_bg·c_bg + Σ αᵢ(p)·cᵢ) / (w_bg + Σ αᵢ(p))`, with `c_bg = 0` and
  `w_bg = 0.25`.

Validation at `MOTION_THRESH=0`, `Q_FRAC=12` (`make sim_4dgs_frame`). The tolerances
are enforced by the testbench:

| Check | Result | Tolerance |
|---|---:|---:|
| Directed `sx=sy=2, ρ=0, dx=1`: FP32 conic vs `conic_from_scale` | (0.25, 0, 0.25) = (0.25, 0, 0.25) | 1e-7 |
| Directed case, FP32 exact α | 0.882497 = exp(-0.125) | 1e-6 |
| Directed case, FP32 with the RTL's 2^-y model, ×256 | 233 (expected 233/256, not 35) | exact |
| FP32 conic vs `conic_from_scale`, 10,000 random (σ 0.3–6, \|ρ\| ≤ 0.9) | max rel. error 4.5e-7 | 1e-5 |
| Per-pixel α, fixed vs FP32 with the RTL exp model (585,638 px) | max 0.0207 | 0.025 |
| Per-pixel α, fixed vs FP32 exact exp | max 0.0409, mean 0.0021 | 0.05 |
| Composited frame, max \|RTL − FP32\| per channel | 0.0167 | (reported) |
| Bit-true RTL vs golden model (148,360 transactions, 6.23 M cycles) | 0 mismatches | 0 |

The first α row isolates quantization error. It is largest on near-singular conics,
where `|ρ(t)|` approaches 0.8. The exact-exp row adds the RTL lane's piecewise-linear
`2^-y ≈ (1 − f/2)·2^-k` error, which is at most 0.043·opacity. At Q_FRAC=8 the same
two deviations are 0.048 and 0.050 (reported, not enforced).

### SSIM_v2 adaptation and spot check

`SSIM_v2` (`rtl_baseline/ssim_eval/SSIM_v2.v`) scores one 16×17 image per run:
272 serpentine-addressed 48-bit words (FP16 R, G, B), 9-bit SRAM addresses, a 3×3
window with zero padding, a window mean of sum/8 (`fp16_div8`, not /9), `C1 = 1e-4`,
and `C2 = 9e-4`. It produces one packed FP16 RGB score per pixel. It cannot take a
64×64 frame directly, so `rtl_4dgs/sim/sim_main_ssim_frame.cpp` adapts the frame:

- Each frame is tiled into 4×4 tiles of 16 columns × 17 rows, at row offsets
  0/17/34/47. The last tile row overlaps rows 47–50.
- Rows 47–50 are covered twice, by the tiles at row offsets 34 and 47, so the frame
  score double-weights those rows. HW and SW use the same tiling, so their comparison
  is still valid.
- Each tile is loaded into the IP's input SRAMs through `ssim_sim_top` and scored
  cycle-accurately with the existing protocol: 400 cycles per tile, scores from tick
  38, and `out_valid` timing checked.
- The frame score is the mean over 16 × 272 pixel scores and 3 channels.

Spot checks on every point of the sweep:

- **Identical frames** (GT vs GT): hardware SSIM = 1.00000.
- **Hardware vs software:** each tile is compared with the double-precision
  model of the same formula. The maximum per-tile difference is 0.0064, and the test
  limit is 0.02. At Q_FRAC=12, T=0, the hardware score is 0.99748 and the software
  score is 0.99981.
- **Offset source:** the hardware score is consistently about 0.002 lower than the
  software score. The likely causes are the IP's FP16 variance arithmetic (`E[x²] − μ²`
  cancellation in half precision) and its serpentine row-turn windows; this is not
  isolated further. Use the
  hardware SSIM to compare design points, not as an absolute SSIM.

## 3. Quality vs skip

`make eval_quality_4dgs`: NUM_PE=1, CACHE_WAYS=2, CACHE_SETS=512. Cycles are the total
cycle-accurate RTL cycles for all 8 frames. Speedup is the unskipped cycle count
(`transactions × (3 + raster + projection)`) divided by the total cycles. PSNR is over
all 8 frames against FP32, and HW SSIM is the mean `SSIM_v2` score. All 18 points have
0 bit-true mismatches. The 12 original rows match a fresh run from Phase 4
(`e49676f`) exactly. Pareto is computed within each Q_FRAC on speedup and PSNR.

| Q_FRAC | MOTION_THRESH | Skip rate | Total cycles | Speedup | PSNR (dB) | HW SSIM | Pareto |
|---:|---:|---:|---:|---:|---:|---:|:---:|
| 8 | 0 | 0.0% | 5,044,240 | 1.000x | 44.73 | 0.99724 |  |
| 8 | 1 | 84.2% | 1,670,590 | 3.019x | 44.73 | 0.99724 |  |
| 8 | 2 | 86.0% | 1,600,903 | 3.151x | 44.74 | 0.99721 |  |
| 8 | 3 | 86.8% | 1,569,070 | 3.215x | 44.74 | 0.99721 | yes |
| 8 | 4 | 87.3% | 1,547,227 | 3.260x | 44.74 | 0.99723 | yes |
| 8 | 8 | 88.9% | 1,484,776 | 3.397x | 44.73 | 0.99725 | yes |
| 8 | 16 | 90.4% | 1,421,488 | 3.549x | 44.71 | 0.99726 | yes |
| 8 | 32 | 91.9% | 1,362,358 | 3.703x | 44.62 | 0.99724 | yes |
| 8 | 64 | 92.7% | 1,329,121 | 3.795x | 44.48 | 0.99724 | yes |
| 12 | 0 | 0.0% | 6,231,120 | 1.000x | 47.74 | 0.99748 |  |
| 12 | 1 | 82.6% | 1,941,555 | 3.209x | 47.74 | 0.99748 |  |
| 12 | 2 | 82.7% | 1,938,825 | 3.214x | 47.74 | 0.99748 |  |
| 12 | 3 | 82.9% | 1,925,840 | 3.236x | 47.74 | 0.99748 |  |
| 12 | 4 | 83.0% | 1,919,400 | 3.246x | 47.74 | 0.99747 |  |
| 12 | 8 | 83.8% | 1,879,535 | 3.315x | 47.74 | 0.99748 |  |
| 12 | 16 | 85.2% | 1,804,565 | 3.453x | 47.74 | 0.99746 |  |
| 12 | 32 | 86.4% | 1,746,815 | 3.567x | 47.75 | 0.99747 | yes |
| 12 | 64 | 86.8% | 1,725,780 | 3.611x | 47.74 | 0.99749 | yes |

Observations:

- **What the skip rate counts:** the skip rate includes the intended lossless
  reuse. Static background never changes, and every span after the first span of a
  Gaussian in a frame hits the cache with the same delta. Both are skipped at any
  `MOTION_THRESH > 0` when the delta is zero, which accounts for the large jump at T=1.
  The thresholds then trade quality only on the moving Gaussians whose `|Δμ|₁ + |Δs|₁`
  falls below them.
- **Thresholds are in LSBs:** `MOTION_THRESH` is in raw LSBs, so the same value means
  16× less motion at Q_FRAC=12 than at Q_FRAC=8. At Q_FRAC=12, T=64 is 0.0156 px
  (L1). PSNR stays nearly flat (47.74 dB) up to 3.61×, so the sweep does not reach a
  high-threshold quality-loss knee at Q12.
- **Q8 trade-off:** at Q_FRAC=8, PSNR falls by 0.02 dB at T=16, 0.11 dB at T=32, and
  0.25 dB at T=64. Hardware SSIM moves by at most 0.00005, which is at the resolution
  of the FP16 SSIM IP, so PSNR is the more sensitive metric at these quality levels.
- **Q8 is faster:** Q_FRAC=8 has fewer projection cycles (11 + 2F = 27 vs 35), so
  its unskipped run is 5.04 M vs 6.23 M cycles.

### Phase 5a fine-sweep knee

The first threshold with skip rate above 50% and PSNR loss below 0.5 dB
relative to its own T=0 baseline is **MOTION_THRESH=1 at both precisions**.

| Q_FRAC | T=0 PSNR (dB) | T=1 PSNR (dB) | PSNR loss (dB) | T=1 skip rate | T=1 cycles | T=1 speedup |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 44.7331 | 44.7331 | 0.0000 | 84.22% | 1,670,590 | 3.019× |
| 12 | 47.7441 | 47.7441 | 0.0000 | 82.61% | 1,941,555 | 3.209× |

The skip test is strict (`delta < MOTION_THRESH`). T=1 admits only zero
quantized delta, subject to the cache-hit and zero-rho-change requirements.
This early crossing is distinct from the higher-threshold operating points
below. See [Modeled External DRAM Traffic](SYSTEM_BANDWIDTH_4DGS.md) for the
separate cache-bypass comparison.

### Higher-threshold operating points

- **Q_FRAC=8, MOTION_THRESH=16 is a speed/quality trade-off.** It runs at 3.55× (1.42 M cycles) for
  −0.02 dB. Going to T=32 adds 4.3% speed for 5.5× the PSNR loss, and T=64 adds 6.9%
  speed for a 0.25 dB loss.
- **Q_FRAC=12, MOTION_THRESH=64 is the best Q12 point in the sweep.** It runs at 3.61×
  with no measurable loss (47.74 dB). T≈256 matches
  Q8 T=16 in pixel units and is the next value to sweep.
- **Across both Q_FRAC values:** compare absolute cycles, not speedups. Each speedup is
  relative to its own precision's unskipped baseline (Q8: 5,044,240 cycles; Q12:
  6,231,120 cycles), so speedups cannot be compared across Q_FRAC.
  - **Speed:** Q12 T=64 takes 1,725,780 cycles vs 1,421,488 for Q8 T=16, so it is 21%
    slower. Every Q8 point with T ≥ 4 is faster in absolute cycles (≤ 1,547,227).
  - **PSNR:** Q12 T=64 beats Q8 T≤16 on PSNR only (47.74 dB, about +3 dB over Q8 T=16).
    The two fastest Q8 points, T=32 (1,362,358 cycles) and T=64 (1,329,121 cycles),
    trail it by 3.1 and 3.3 dB.
  - **Pick:** Q12 T=64 when quality matters most, Q8 T=16 when cycles or area matter
    (see §4).

## 4. Tier 2: Yosys/ABC generic-cell synthesis

`make synth_4dgs` runs sv2v, then Yosys with generic cells (SRAM ways kept opaque), then
ABC for AIG metrics, then ABC CEC. The config is `NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2,
CACHE_SETS=512`. These are technology-independent generic-cell counts, **not** 130 nm
area or timing.

| Metric | Phase 3 | Phase 4 | Change |
|---|---:|---:|---:|
| Generic gates | 28,509 | 51,110 | +22,601 (+79.3%) |
| Flip-flops | 2,275 | 2,275 | 0 |
| AIG AND nodes | 33,384 | 61,252 | +27,868 (+83.5%) |
| AIG levels | 335 | 334 | −1 |
| ABC CEC (RTL vs. optimized) | PASS | PASS | |

- **Gates:** almost all of the added logic is `deform_engine`. It has eleven W×W
  products (t² plus ten coefficient×basis terms) and five 2W+2-bit accumulators.
- **Flip-flops:** unchanged. The engine is combinational and feeds the existing
  accept-stage registers.
- **Depth:** the deepest path is still the projection/divider path. The engine sits in
  parallel with input capture, so it does not add AIG levels.
- **Runtime:** CEC on this netlist takes about 9 minutes per comparison locally, so the
  CI job timeout was raised from 30 to 120 minutes.

## 5. Tier 3: Z3 proofs

`make formal_4dgs` netlists each target with sv2v and Yosys, then checks it with Z3
through `eda_core/`. Each query has a 30 s timeout and 3 attempts. A query that times out
falls back to per-output-bit COI slices, then to BMC-20. Result: **43/43 obligations as
expected** (43 Z3 queries).

| Target | Netlist params | Obligations | Result |
|---|---|---:|---|
| `gs4d_skip_unit` | W=16 | 10 | pass |
| `gs4d_cache` | 4 sets × 2 ways, 8-bit data | 7 | pass |
| `gs4d_deform` (new) | Q_FRAC=4 (W=12, AW=26) | 7 | pass |
| `gs4d_top` (now includes `deform_engine`) | Q_FRAC=8, 4 sets × 2 ways | 19 | pass |

**`gs4d_deform` obligations** (all UNSAT, except the two witnesses, which are SAT):

- `basis_t2 == fxmul(t, t)`, bit-true.
- **Accumulator adders never wrap:** `acc[o]` equals `(c0 << F) + prod[o][0] + prod[o][1]`
  evaluated in AW+4 bits.
- **Accumulator headroom:** the top 2 bits of `acc[o]` are sign copies, so every reachable
  sum fits in 2W+1 bits.
- **Saturation safety:** `out[o] == sat_W(round_half_up(acc[o] >>> F))`.
- **Order-0 pass-through:** when `t == 0`, each output equals its order-0 term. This is
  the Phase 3 precomputed-delta interface.
- **Witnesses:** positive saturation (`dmu_x == FX_MAX`) and negative saturation
  (`dr == FX_MIN`) are both reachable.

**Multiplier products.** A direct Z3 miter `prod == coef × basis` on the bit-blasted
multipliers returned UNKNOWN at both W=12 and W=10, even after COI slicing and BMC-20.
The no-wrap proof is therefore stated over the netlist's own product nets. Each product
is a signed W×W product held in 2W bits, so it cannot overflow by width. Product
bit-exactness is checked by Tier 1: zero bit-true mismatches against the golden model's
exact `coef * t` and `coef * t2` over all 12 sweep points and the `sim_4dgs` vectors.

**Lossless and skip equivalence** (`gs4d_skip_unit`, `gs4d_top`):

- `motion_thresh == 0` implies `skip_deform == 0`.
- `skip_q == 0` and `out_skipped == 0` hold at reset and are inductive.
- Every LOOKUP enters full recompute.
- Under `motion_thresh == 0`, every datapath and control register is independent of cache
  contents.
- These proofs cover the new `dr(t) == 0` skip gate, which can only remove skips.
