# 4DGS bit-true reference model (roadmap item a)

Header-only C++17 golden model of the 4DGS spatiotemporal deformation and the
`rtl_baseline/gs_rasterizer` rasterization kernel, plus the verification funnel
required by `DEVIN.md`.

## Layout

| Path | Contents |
| --- | --- |
| `include/gs4d/fixed_point.hpp` | Signed Q(I,F) arithmetic on integers: truncate / round-half-up / round-half-even, saturate / wrap, `__int128` intermediates |
| `include/gs4d/exp_unit.hpp` | Hardware `exp(x<=0)`: `x*log2e`, integer shift + piecewise-linear `2^f` table (segment count configurable) |
| `include/gs4d/arith.hpp` | Arithmetic policies: `F32Policy` (binary32 RNE, flush-to-zero, as the DW-based RTL) and `FixedPolicy` (one Q-format per signal class `Sig`, the knobs for item b) |
| `include/gs4d/gaussian.hpp` | Splat record (SRAM word layout) and the 4D deformation kernel |
| `include/gs4d/rasterizer.hpp` | `RtlRasterizer<P>`: register/cycle-accurate model of `GS_RASTERIZER_pipeline.v`; `render_ideal<P>`: hazard-free sequential reference |
| `include/gs4d/scene.hpp` | Seeded synthetic 4DGS scene, depth sort, `ram_file.txt` / `gs_value.txt` / `TB.sv` golden writers |
| `tools/gs4d_ref.cpp` | CLI: render a frame, fixed-vs-float error report, write RTL memory images and golden files |
| `tests/test_fixed_point.cpp` | Rounding/overflow (incl. exhaustive Q2.3 multiply), exp accuracy, FTZ, deformation |
| `rtl/fx_round_sat.v`, `rtl/fx_mul_rs.v` | Synthesizable RTL of the fixed-point multiply-round-saturate contract |
| `rtl/sim_substitutes/` | **Sim-only clean-room stand-ins for the DW_fp_* cells, not Synopsys originals** (see its README) |
| `verif/funnel.py` | Tier 0-3 verification funnel (below) |
| `verif/run_rtl_sim.sh`, `verif/tb_gs_rasterizer.cpp` | Baseline RTL vs C++ model, pixel by pixel |

## Kernels

Deformation (per Gaussian, frame time `t`; `ha = a/2`, `lam_h = 1/(2 sigma_t^2)`):

```
dt = t - t0;  mu(t) = mu + v*dt + ha*dt^2;  depth(t) = depth + vd*dt
opacity(t) = opacity * exp(-lam_h * dt^2)
```

Rasterization is the baseline's per-pixel loop: `beta = -0.5(a dx^2 + c dy^2) - b dx dy`,
`alpha = o*exp(beta)`, skip if `beta >= 0` or `alpha < 1/255`, front-to-back
accumulation of RGB and interpolated depth, early stop at `T < 1e-4`, `D = depth + 15T`.

`RtlRasterizer` reproduces the RTL's pipeline behaviour exactly; each of these is a
`RtlQuirks` flag (default on = bit-true to the current RTL, off = the fix):

* `stale_dxdy_depth`: stage 3 uses `dx_s2/dy_s2` (the next Gaussian's) in the depth term.
* `stale_trans`: stage 8 reads `T` before stage 9 writes the previous Gaussian's update.
* `sticky_early`: `early_termination` is never cleared in `INIT_PIXEL`, so after the first
  early-terminated pixel every later pixel ends immediately with `N=0`, `T=1`
  (2148 of 2550 pixels in scene seed 1).

Four more flags describe where the RTL differs from the lab golden_ans (on = pre-Phase 5e
RTL, off = golden behaviour):

* `stop_on_crossing`: the RTL leaves PROCESS after the Gaussian that drives `T` below
  `1e-4` (plus the stage-10 entry in flight); the golden blends one more `alpha >= 1/255`
  Gaussian, counting the skipped ones in between, then stops.
* `n_overshoot`: a pixel that walks the whole 1089-entry list reports `N=1090` (the stage-10
  counter increments once more after the `N >= 1089` stop); the golden reports 1089.
* `rgb_wrap`: the RTL keeps the low 8 bits of the 9-bit colour, so a sum above 255 wraps;
  the golden saturates at 255.
* `depth_stop_tail`: the pre-Phase 5e RTL added every committed Gaussian to depth; the
  golden's depth skips the Gaussian that drives `T` below `1e-4` and every one after it (`T`
  and N still include them). Found by fitting the golden, not from a known design.
* `rgb_stop_tail`: the same rule for RGB (`docs/BACKLOG_INVESTIGATION.md` item 2): the golden
  does not blend the crossing Gaussian or any later one into RGB either.
* `alpha_clamp` (on = golden): the 3DGS clamp `alpha = min(0.99, conw * exp)`
  (`docs/BACKLOG_INVESTIGATION.md` item 1); off = the pre-Phase 5e unclamped RTL.

Phase 5e implemented the last three rules in the RTL (stage-7 clamp, stage-10 RGB/depth gate
on `s10_altb` plus a per-pixel `tail_crossed` flag), so the defaults are now
`depth_stop_tail = rgb_stop_tail = false`, `alpha_clamp = true` and `q111` is still the
current RTL.

## Golden-answer check

`verif/run_golden.sh [DIR]` renders the scene in `DIR` (default
`verif/golden/gs_rasterizer`, see its README) with every quirk combination
`q<dxdy><trans><early>` (q111 = current RTL), `q000` with the pre-Phase 5e tail/clamp
behaviour plus each golden rule (`q000_s/n/r/d/t/c`, see `tools/gs4d_render.cpp`), the PR #1
"all off" set (`q000_snrd`), every golden rule (`q000_snrdtc`, 0 tolerance mismatches on the
lab scene) and the ideal algorithm, simulates the RTL,
and prints exact and TB.sv-tolerance mismatch counts per field (N, T, D, RGB) against
`DIR/golden_ans`, with per-pixel lists in `build/golden/mismatch/*.csv`. Only RTL vs q111 is
gated; golden mismatches are reported. A scene written by `gs4d_ref --n 1089` works as a
self-test (its golden_ans is the q111 model).

## Build and run

```
g++ -std=c++17 -O2 -Iref_model/include ref_model/tools/gs4d_ref.cpp -o gs4d_ref
./gs4d_ref --seed 1 --n 600 --t 0.5 [--out DIR]
python3 ref_model/verif/funnel.py          # tiers 0-3, artifacts in ref_model/build/ (ignored)
```

Requires g++ >= 9, Verilator, Yosys, `yosys-abc`, Z3 (`yosys-smtbmc -s z3`).

## Verification funnel (DEVIN.md)

* **Tier 0**: C++ unit tests; deterministic synthetic render (hash checked twice).
* **Tier 1**: Verilator `-Wall` lint; `fx_mul_rs` vs `gs4d::fx_mul` for 3 operator
  instances x 3 rounding x 2 overflow modes (>= 32k directed + constrained-random vectors
  each); full `GS_RASTERIZER_pipeline.v` + sim substitutes vs `RtlRasterizer<F32Policy>` on
  3 synthetic scenes (0.4M-2.8M cycles each): every pixel's `RGB/D/T/N` and the cycle count match.
* **Tier 2**: Yosys synth + ABC `cec` of `fx_round_sat` against an independent formulation
  (`verif/fx_mul_rs_spec.v`) for all 18 configurations; full multiplier cec for one mode per
  instance (120 s budget). Reports AIG AND count and logic depth.
* **Tier 3**: Z3 (via `yosys-smtbmc`) on the isolated `fx_round_sat` miter only, 30 s
  per query, at most 3 retries, then COI slicing per output bit, then depth-20 BMC.

## Not verified here

* **Bit-exactness against the real Synopsys DesignWare models.** The baseline was simulated
  with the clean-room substitutes in `rtl/sim_substitutes/`; re-run
  `verif/run_rtl_sim.sh` with the real DW simulation models in the lab Synopsys
  environment to close this. `DW_fp_exp` in particular is modelled as binary64 `exp`
  rounded to binary32.
* The original `Algo/pattern/golden_ans` vectors (not in the repo); seeded synthetic
  scenes are used instead.
* There is no deformation RTL yet; `deform()` is the specification for it.
