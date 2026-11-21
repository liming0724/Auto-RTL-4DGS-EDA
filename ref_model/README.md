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
  (2148 of 2550 pixels in scene seed 1). A pixel that walks the whole 1089-entry list reports
  `N=1090`.

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
