# Roadmap plan for items (b) and (c)

Item (a) is in `ref_model/`. Items (b) and (c) are planned here and not implemented yet.
Every step follows `DEVIN.md`: Verilator first, then Yosys + ABC `cec`, then Z3 on isolated
sub-modules only (30 s timeout, max 3 retries, COI slicing / depth-20 BMC fallback, CEGAR
on any counterexample), existing port names kept, no waveform or binary artifacts committed.

## (b) Parameterize `GS_RASTERIZER_pipeline.v`

Goal: one RTL whose arithmetic, parallelism and memory system are parameters, each
configuration bit-true to `RtlRasterizer<FixedPolicy>` with the matching `FixedConfig`.

1. **Fixed-point datapath variant.** New `GS_RASTERIZER_fx.v` (the DW float baseline stays
   untouched as the reference). Each `DW_fp_*` operator becomes `fx_mul_rs` / an add-round-sat
   twin, with widths from one parameter per `Sig` class (coord, delta_sq, conic, power, depth,
   alpha, trans, color, pixel, time, vel). The exp stage becomes `fx_exp` with its table
   generated from `ExpUnit` (a `gen_exp_lut` tool writing a `.vh`). Ports unchanged
   (`RGB[23:0]`, `D`, `T`, `N[10:0]`, `Finish`, `en`); `D`/`T` carry raw Q values, plus a
   parameter to re-emit binary32 for the existing TB.
2. **Q-format bit-widths.** Parameters `Q_<SIG>_I` / `Q_<SIG>_F` plus `ROUND` and `SAT`.
   A Python generator emits the parameter set and the matching `FixedConfig` from one YAML
   so model and RTL can never disagree.
3. **PE array width.** Parameter `N_PE`: N pixels of a row processed in parallel. Each
   Gaussian word read from SRAM1 is broadcast to N per-pixel pipelines (stages 1-10),
   sharing the stage-1 conic/depth-gradient products (they do not depend on the pixel).
   FSM steps `px` by `N_PE`; writes to SRAM2 are serialized or banked. The C++ model gets a
   matching `RtlRasterizerPE<P, N>` for cycle counts.
4. **Motion-aware cache.** Between the Gaussian SRAM and the PEs, an `ASSOC`-way,
   `SETS`-set cache of deformed splats keyed by Gaussian index and indexed by a motion bucket
   (|v| / time slice), so slowly moving Gaussians are not re-deformed every frame. Parameters
   `CACHE_SETS`, `CACHE_ASSOC`, `REPL` (LRU / pseudo-LRU). Bit-true behaviour is independent of
   the cache (hit or miss returns the same splat); only cycle count and SRAM traffic change, and
   the C++ model reports hit rate.
5. **The three RTL pipeline issues** found in (a) (`RtlQuirks`) become parameters defaulting to
   the fixed behaviour, so the variant can be compared with and without the fixes.
6. **Verification per configuration.** Tier 1: Verilator lint + `run_rtl_sim.sh`-style
   pixel-exact comparison against `RtlRasterizer<FixedPolicy>` on seeded scenes (>= 10k cycles).
   Tier 2: Yosys + ABC `cec` of each generated arithmetic unit against its spec, and of the
   parameterized core at the default configuration against a hand-flattened copy. Tier 3: Z3 on
   `fx_round_sat`, `fx_exp` index/shift logic and the cache tag-compare / replacement logic only.

## (c) Parallel Area-Delay Pareto sweeps

1. **Design space.** Cartesian product (pruned) of: per-`Sig` fractional widths, `ROUND`,
   `SAT`, `N_PE` in {1, 2, 4, 8}, exp table segments in {16, 32, 64, 128}, cache
   `{SETS, ASSOC}`. Start with a Latin-hypercube sample, then refine around the frontier.
2. **Quality gate first (cheap).** For each point run the C++ model (`FixedPolicy` + the
   config) on a fixed scene set and measure PSNR / max |dT| / max |dD| against
   `render_ideal<F32Policy>`. Points below a PSNR threshold (e.g. 40 dB) are dropped before
   any synthesis. This is milliseconds per point.
3. **Synthesis (parallel).** For surviving points: generate RTL parameters, then
   `yosys synth -flatten; abc -D <period>` (with a Liberty file when available, else
   `abc -g AND` + logic depth as a proxy). Area = cell area / AND count, delay = ABC
   critical path. Jobs run in a process pool (`concurrent.futures`) with per-job build dirs
   under an ignored `build/dse/`, results appended to a CSV.
4. **Verification loop per point.** Each kept point must pass the funnel: Verilator
   pixel-exact vs the C++ model (tier 1), ABC `cec` of the synthesized netlist against the
   pre-optimization RTL (tier 2), Z3 on the isolated arithmetic sub-modules (tier 3, 30 s,
   3 retries, COI / BMC fallback). Failures trigger CEGAR: counterexample into the Verilator
   TB, fix, restart at tier 1. A point that still fails is marked invalid, never plotted.
5. **Pareto extraction.** Non-dominated filter over (area, delay, 1/PSNR), with a plot and a
   CSV of the frontier written to `docs/`; `eda_core` (`netlist_optimization_engine`, COI
   helpers) can be reused for netlist-level optimization of frontier points.
6. **Reproducibility.** Seeds, tool versions and the YAML of every point are stored with the
   results; the sweep driver is deterministic and resumable.

Open questions for review: target technology library (none in the repo; AND count / depth
is a proxy until one is provided), PSNR threshold, and whether the binary32 output format
must be kept on the fixed-point variant.
