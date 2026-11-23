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
make stress_4dgs         # Phase 5b: benign/pan/fast stress sweep -> build/4dgs/stress/ (section 6)
make conic_cliff_4dgs    # Phase 5b: small-determinant conic characterization -> build/4dgs/conic/ (section 7)
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
0 bit-true mismatches. Pareto is computed within each Q_FRAC on speedup and PSNR.
Phase 5a added MOTION_THRESH 1, 2 and 3 (six new points). The 12 Phase 4 points reproduce
exactly: every CSV field matches the e49676f run, and their Pareto marks are unchanged.

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
  `MOTION_THRESH > 0`, which is where most of the jump from 0% to 83–87% comes from.
  The thresholds then trade quality only on the moving Gaussians whose `|Δμ|₁ + |Δs|₁`
  falls below them.
- **Thresholds are in LSBs:** `MOTION_THRESH` is in raw LSBs, so the same value means
  16× less motion at Q_FRAC=12 than at Q_FRAC=8. At Q_FRAC=12, T=64 is 0.0156 px
  (L1). PSNR stays flat (47.74 dB) up to 3.61×, so the Q12 sweep never reaches its knee.
- **Q8 trade-off:** at Q_FRAC=8, PSNR falls by 0.02 dB at T=16, 0.11 dB at T=32, and
  0.25 dB at T=64. Hardware SSIM moves by at most 0.00005, which is at the resolution
  of the FP16 SSIM IP, so PSNR is the more sensitive metric at these quality levels.
- **Q8 is faster:** Q_FRAC=8 has fewer projection cycles (11 + 2F = 27 vs 35), so
  its unskipped run is 5.04 M vs 6.23 M cycles.

### Fine sweep: where skipping starts (Phase 5a)

MOTION_THRESH 1, 2 and 3 locate the knee between T=0 and T=4. The criterion is the first
threshold where the skip rate crosses 50% with a PSNR drop under 0.5 dB vs the same
Q_FRAC's T=0 baseline.

| Q_FRAC | T | Skip rate | Total cycles | Speedup | PSNR (dB) | ΔPSNR vs T=0 (dB) | HW SSIM |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0 | 0.0% | 5,044,240 | 1.000x | 44.7331 | — | 0.99724 |
| 8 | 1 | 84.2% | 1,670,590 | 3.019x | 44.7331 | 0.0000 | 0.99724 |
| 8 | 2 | 86.0% | 1,600,903 | 3.151x | 44.7358 | +0.0027 | 0.99721 |
| 8 | 3 | 86.8% | 1,569,070 | 3.215x | 44.7359 | +0.0028 | 0.99721 |
| 8 | 4 | 87.3% | 1,547,227 | 3.260x | 44.7354 | +0.0023 | 0.99723 |
| 12 | 0 | 0.0% | 6,231,120 | 1.000x | 47.7441 | — | 0.99748 |
| 12 | 1 | 82.6% | 1,941,555 | 3.209x | 47.7441 | 0.0000 | 0.99748 |
| 12 | 2 | 82.7% | 1,938,825 | 3.214x | 47.7441 | 0.0000 | 0.99748 |
| 12 | 3 | 82.9% | 1,925,840 | 3.236x | 47.7441 | 0.0000 | 0.99748 |
| 12 | 4 | 83.0% | 1,919,400 | 3.246x | 47.7441 | 0.0000 | 0.99747 |

(PSNR to 4 decimals from `quality_vs_skip.csv`.)

- **The knee is MOTION_THRESH=1 at both Q_FRAC values.** The skip rate goes from 0% to
  84.2% (Q8) and 82.6% (Q12) in one LSB, with a PSNR change of 0.0000 dB.
- **T=1 is lossless:** `delta < 1` means `delta == 0`, so T=1 only skips Gaussians whose
  deformed Δμ and Δs are exactly zero (static background, and repeat spans of the same
  Gaussian in a frame, which reuse attributes computed from the same deltas). The PSNR and
  HW SSIM at T=1 equal T=0 to every printed digit. This confirms the Phase 4
  observation that most of the 0% → 83–87% jump is intended lossless reuse.
- **No knee inside 0 < T < 4 beyond T=1:** T=2..4 add 1.8–3.1 points of skip rate at Q8
  and 0.1–0.4 points at Q12. At Q8, PSNR moves by +0.002–0.003 dB, which is noise from
  reusing a slightly stale conic rather than a quality gain. At Q12, PSNR is unchanged.
- **Pareto with the new points (within each Q_FRAC):** at Q8, T=3 joins the front.
  T=1 and T=2 are dominated by T=3, which is faster with PSNR at least as high. At Q12,
  T=1..3 are dominated by T=32 (faster, higher PSNR), like T=4..16 in Phase 4. The
  Phase 4 Pareto marks are unchanged.
- **Bandwidth at the knee:** `docs/SYSTEM_BANDWIDTH_4DGS.md` profiles Full Co-Design
  (`CACHE_WAYS=4`, `cache_bypass=0`) at T=1 and T=3 as well as T=16. At the lossless
  T=1, Modeled External DRAM Traffic drops 84.2% (Q8) and 82.6% (Q12) vs Baseline over
  8 frames, and 82.2% / 80.3% in steady state (frames 1–7, excluding the cold-cache
  frame 0). Conservative figures are in the same document.

### Knee-point recommendations

- **Q_FRAC=8, MOTION_THRESH=16 is the knee.** It runs at 3.55× (1.42 M cycles) for
  −0.02 dB. Going to T=32 adds 4.3% speed for 5.5× the PSNR loss, and T=64 adds 6.9%
  speed for a 0.25 dB loss.
- **Q_FRAC=12, MOTION_THRESH=64 is the best Q12 point in the sweep.** It runs at 3.61×
  with no measurable loss (47.74 dB). The Q12 knee lies above T=64. T≈256 matches
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
expected** (43 Z3 queries). Phase 5a adds the `cache_bypass` input and two `gs4d_top`
obligations, for 45/45; see `docs/FORMAL_VERIFICATION.md`.

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

## 6. Phase 5b: stress scenes (pan, fast)

Phase 5a measured skipping on the benign scene only. Phase 5b adds two scene modes to
`golden_model/gs4d_frame.hpp` (`SceneMode`, selected with `+scene=<benign|pan|fast>` on the
frame testbench). `benign` stays the default and draws the same PRNG sequence, so every
earlier sweep reproduces bit for bit (`quality_vs_skip.csv`, `bandwidth_profile.csv/.md`,
`golden_sweep.csv`, `sim_4dgs` RESULT lines: byte-identical to c0a9b14). Golden model and
RTL get the same transaction list, and the rendering math is unchanged.

| Scene | Definition | Measured median step |
|---|---|---:|
| benign | Phase 4/5a scene: 60% static; moving amplitude log-uniform 0.002–12 px over the sequence | 0.0139 px/frame |
| pan | benign + uniform camera pan on **every** Gaussian along (0.8, 0.6): step ramps 2 → 4 px/frame (frame 0→1: 2 px, frame 6→7: 4 px; 21 px total). Implemented as extra `c1, c2` terms in mu(t), so the deformation engine sees it | 3.00 px/frame |
| fast | benign split (60% static), but the moving linear term is log-uniform **1.5–8 px/frame**; curvature scales with speed, so individual frame steps span 0.82–11.2 px (91.6% inside 1.5–8; 5th–95th percentile 1.57–8.01); scale/rho drift capped at the benign maximum | 3.69 px/frame |

Median step = median |mu(t<sub>k+1</sub>) − mu(t<sub>k</sub>)| over the Gaussians that move
(`median_step_px`). The benign value is lower than the ~0.019 px/frame linear-term figure
because it uses the full curved trajectory.

**Off-frame handling: clipped, not wrapped.** Pan spawns mu0 over the frame extended by
the 21 px pan, upstream of the pan direction, so the 64×64 frame stays covered. `bin_spans`
clips each Gaussian's 3.5-sigma box to [0, 63]. A Gaussian whose box is fully outside the
frame produces no spans in that frame, so it is dropped for that frame. The FP32 reference
also ignores off-frame pixels. Golden and RTL get the same span list. This is why the
transaction counts differ: 148,360 (benign), 115,528 (pan), 143,367 (fast).

Sweep: CACHE_WAYS=4 profiler build, Q_FRAC {8, 12}. Each scene gets Baseline
(`cache_bypass=1`, T=0), Skip-Only (`cache_bypass=1`, T=16) and Full Co-Design
(`cache_bypass=0`) at T {0, 1, 2, 3, 4, 8, 16, 32, 64}. Fast also gets a beyond-grid probe at
T = 0.5–8 px. 76 runs: 66 grid runs (3 scenes x 2 Q_FRAC x 11 modes) + 10 fast-scene probe
runs (5 thresholds x 2 Q_FRAC); **0 mismatches** vs golden. CSVs (Phase 5a profiler columns, plus
`scene`, `hit_rate`, `psnr_drop_db` and conic stats):
[`phase5b/stress_benign.csv`](phase5b/stress_benign.csv),
[`phase5b/stress_pan.csv`](phase5b/stress_pan.csv),
[`phase5b/stress_fast.csv`](phase5b/stress_fast.csv).

Full Co-Design, Modeled External DRAM Traffic (hit rate = `cache_served / transactions`):

| Scene | Q_FRAC | T | Skip rate | Total cycles | Speedup vs scene Baseline | PSNR (dB) | HW SSIM | fetch_count | Hit rate | dram_read_bytes/frame | Reduction vs scene Baseline |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| benign | 8 | 0 | 0.0% | 5,044,240 | 1.000x | 44.73 | 0.99724 | 148,360 | 0.0% | 890,160 | 0.0% |
| benign | 8 | 1 | 84.2% | 1,670,590 | 3.019x | 44.73 | 0.99724 | 23,410 | 84.2% | 140,460 | 84.2% |
| benign | 8 | 16 | 90.4% | 1,421,488 | 3.549x | 44.71 | 0.99726 | 14,184 | 90.4% | 85,104 | 90.4% |
| benign | 8 | 64 | 92.7% | 1,329,121 | 3.795x | 44.48 | 0.99724 | 10,763 | 92.7% | 64,578 | 92.7% |
| pan | 8 | 0 | 0.0% | 3,927,952 | 1.000x | 44.08 | 0.99630 | 115,528 | 0.0% | 693,168 | 0.0% |
| pan | 8 | 1–64 | 12.0% | 3,554,488 | 1.105x | 44.08 | 0.99630 | 101,696 | 12.0% | 610,176 | 12.0% |
| fast | 8 | 0 | 0.0% | 4,874,478 | 1.000x | 44.55 | 0.99715 | 143,367 | 0.0% | 860,202 | 0.0% |
| fast | 8 | 1–64 | 85.5% | 1,565,385 | 3.114x | 44.55 | 0.99715 | 20,808 | 85.5% | 124,848 | 85.5% |
| benign | 12 | 1 | 82.6% | 1,941,555 | 3.209x | 47.74 | 0.99748 | 25,801 | 82.6% | 190,282 | 82.6% |
| benign | 12 | 64 | 86.8% | 1,725,780 | 3.611x | 47.74 | 0.99749 | 19,636 | 86.8% | 144,816 | 86.8% |
| pan | 12 | 1–64 | 12.0% | 4,368,056 | 1.111x | 46.91 | 0.99714 | 101,696 | 12.0% | 750,008 | 12.0% |
| fast | 12 | 1–64 | 85.5% | 1,731,849 | 3.477x | 47.60 | 0.99750 | 20,808 | 85.5% | 153,459 | 85.5% |

The pan and fast rows are identical for every T from 1 to 64. In every scene, Baseline and
Skip-Only equal Full Co-Design T=0, as in Phase 5a. All rows are in the CSVs.

### Pan: cache thrashing

- **The pan defeats the cache.** The skip metric is the deformation offset from the
  canonical pose (|dmu| + |ds|, LSB), not the frame-to-frame motion. A pan gives every
  Gaussian, static ones included, an offset of at least 2 px from frame 1 onward.
  That is ≥ 512 LSB at Q8, far above T=64. Only frame 0 (t=0, offset 0) is reused.
- **Thrashing vs benign at the same T.** At T=1, Q8: hit rate **12.0% vs 84.2%**, and
  fetch_count 101,696 vs 23,410 (4.3x, on 22% fewer transactions). At T=16 the
  comparison is 12.0% vs 90.4%, and fetch_count 101,696 vs 14,184 (7.2x).
- **Traffic stays near the no-cache Baseline at every threshold.** Pan Full Co-Design
  reads 610,176 B/frame (Q8) and 750,008 B/frame (Q12) at T=1 through T=64. That is
  **88.0% of the pan Baseline** at every T. Raising T does not help, because the 2–4
  px/frame offsets sit far above the grid. The benign scene's 84–93% reduction becomes
  12%, and the speedup drops from 3.0–3.8x to 1.1x. Under a global pan the accelerator is
  bandwidth-bound again (see
  [SYSTEM_BANDWIDTH_4DGS.md](SYSTEM_BANDWIDTH_4DGS.md#phase-5b-stress-scenes-and-miss-penalty-latency)).
- PSNR and HW SSIM are identical at every T: the misses are recomputed, never reused.

### Fast: ghosting boundary

- **No PSNR cliff in the grid at either Q_FRAC.** The largest drop vs the scene's T=0 is
  **0.00 dB** at Q8 and at Q12 (T up to 64). The fast scene's skip rate (85.5%) comes
  entirely from static Gaussians and frame 0. No fast mover is ever served from cache.
- **Why: two gates keep fast movers out.**
  - (1) Their offset is ≥ 1.5 px after one frame: ≥ 384 LSB at Q8 and ≥ 6,144 LSB at
    Q12, far above T=64.
  - (2) The skip rule also needs `drho == 0`. Fast movers have nonzero rho drift.
    Counting quantized (Gaussian, frame ≥ 1) pairs of moving Gaussians, `drho == 0`
    holds for 6 of 714 at Q8 and 0 of 714 at Q12. Benign: 464 and 226.
- **Beyond-grid probe** (T = 0.5, 1, 2, 4, 8 px of L1 offset, i.e. 128–2,048 LSB at Q8 and
  2,048–32,768 LSB at Q12):
  - Q12: still **no cliff**; PSNR is unchanged up to 8 px.
  - Q8: the first loss appears at T = 8 px (2,048 LSB): **−0.08 dB**, with 10 fewer
    fetches. Those are the 6 `drho == 0` movers.
- **Ghosting boundary per Q_FRAC: none below 8 px (Q8) and none up to 8 px (Q12).** The
  `drho == 0` guard, not the threshold, is what prevents ghosting on fast motion. The
  benign scene is still the only one in which T trades quality: Q8 −0.25 dB at T=64.

## 7. Phase 5b: small-determinant conic characterization

The existing `ConicCheck` covers s ∈ [0.3, 1.0] and rho ∈ [−0.4, 0.4], and leaves its
pass criteria unchanged. Phase 5b adds a characterization grid (`gs4d_ref::small_det_grid`
+ `probe_conic`, run through `sim_main_4dgs +smalldet=<csv>`):

- Grid: sx, sy ∈ {0.10, 0.12, …, 0.30} ∪ {0.35, …, 1.00} and rho ∈ {−0.90, −0.85, …, 0.90}.
  That is 23,125 points per Q_FRAC.
- Every point is streamed through the default-config RTL and checked bit-true against
  golden: **0 mismatches** at Q8 and at Q12.
- Each point is compared against the exact double `conic_from_scale`. The grid points are
  kept out of `ConicCheck`, so they never gate PASS. `ConicCheck` itself still reports
  `failures=0`, with the same `inv_saturated` count as before.

Metrics:

- rel err = max<sub>i</sub>|conic<sub>i</sub> − ref<sub>i</sub>| / max<sub>j</sub>|ref<sub>j</sub>|.
- flicker = the largest change in that normalized conic after ±1 LSB on sx, sy or rho.
- "flicker exact" applies the same input step to the exact conic. It is the part of the
  flicker the input LSB itself causes.

Data: [`phase5b/small_det_cliff_summary.csv`](phase5b/small_det_cliff_summary.csv), per
(Q_FRAC, min(sx, sy), |rho|). The full per-point grid is
`build/4dgs/conic/small_det_cliff.csv`.

| Q_FRAC | Points | inv_saturated | Saturation boundary | Smallest unsaturated det | Max rel err (unsaturated) | Points > 10% | Worst flicker (unsaturated) | Worst flicker, exact |
|---:|---:|---:|---|---:|---:|---:|---:|---:|
| 8 | 23,125 | 9,833 | det ≤ **2 LSB** (2<sup>2F</sup>/det > FX_MAX) | 3 LSB | **36.6%** | 10,965 | **52.6%** | 8.2% |
| 12 | 23,125 | 9,172 | det ≤ **32 LSB** | 33 LSB | 2.9% | 8,670 | 3.1% | 0.5% |

- **Cliff location.**
  - Q8: error passes 10% **before** saturation. Unsaturated points with det of 3–10 LSB
    reach 36.6% error and 52.6% flicker. Example: s = 0.5, rho = 0.8, det = 6 LSB gives
    4.3% error but 20.9% flicker.
  - Q12: the cliff **is** the saturation boundary. All unsaturated points stay ≤ 2.9%
    error and ≤ 3.1% flicker. Every point over 10% is a saturated reciprocal.
- **Determinant envelope** (every grid point at or above it keeps < 10% error and no
  saturation; det = sx² sy² (1 − rho²)):
  - Q8: det ≥ **0.0416 px⁴** (√det ≥ 0.204 px²).
  - Q12: det ≥ **0.0079 px⁴** (√det ≥ 0.089 px²).

Operating envelope: the smallest min(sx, sy) such that every grid point at or above it has
< 10% error and no saturation. The table also gives the worst error and flicker inside
that region.

| abs(rho) | Q8 min(sx, sy) | Q8 max err | Q8 max flicker | Q12 min(sx, sy) | Q12 max err | Q12 max flicker |
|---:|---:|---:|---:|---:|---:|---:|
| 0.0 | 0.35 | 8.2% | 15.5% | 0.30 | 0.8% | 1.6% |
| 0.4 | 0.40 | 8.8% | 18.3% | 0.35 | 0.7% | 2.0% |
| 0.5 | 0.35 | 9.6% | 24.4% | 0.35 | 1.8% | 2.1% |
| 0.6 | 0.45 | 4.3% | 16.0% | 0.35 | 1.6% | 2.5% |
| 0.7 | 0.50 | 6.5% | 9.1% | 0.40 | 1.1% | 1.5% |
| 0.8 | 0.45 | 7.3% | 31.4% | 0.40 | 1.0% | 2.1% |
| 0.9 | 0.55 | 9.8% | 22.8% | 0.50 | 1.4% | 2.2% |

The Q8 boundary is not monotonic in |rho|, because sx, sy and rho are quantized to 1/256.
All 19 |rho| rows are generated by `make conic_cliff_4dgs`.

**Does the scene reach the cliff? No.** The frame testbench probes every in-frame
(Gaussian, frame) pair of each scene with the same `probe_conic` (SCENE line):

| Scene (Q8) | min s (px) | max abs(rho) | min det (LSB) | inv_saturated | > 10% pairs with min(s) < 1 px |
|---|---:|---:|---:|---:|---:|
| benign | 0.766 | 0.441 | 155 | 0 | 0 |
| pan | 0.777 | 0.441 | 155 | 0 | 0 |
| fast | 0.629 | 0.617 | 86 | 0 | 0 |

- Even the fast scene's deform-pushed |rho| = 0.62 with s = 0.63 sits inside the Q8
  envelope: min(s) 0.45–0.50 for |rho| 0.6–0.7.
- Its smallest det (86 LSB) is ~40x the Q8 saturation boundary.
- The ~±0.7 rho named in the task is not reached by these generators. At |rho| = 0.7 the
  envelope is s ≥ 0.50 (Q8) or ≥ 0.40 (Q12).
- The scene's worst relative error, 18.1% at Q8, does not come from the small-det cliff.
  It occurs at s ≈ 3.3 px, where the whole conic is only ~24 LSB at Q8 and 1 LSB is ~4%.
  It is a resolution limit for large Gaussians, inside the existing ConicCheck tolerance.

**Recommendation.**
- Q12 is safe down to the saturation boundary.
- Q8 needs min(sx, sy) ≥ 0.5 px for |rho| ≤ 0.9, or the det envelope above. Below that,
  1-LSB input changes can move the conic by up to ~50%, which shows as frame-to-frame
  flicker.
- Neither stress scene nor the benign scene goes below this envelope.
