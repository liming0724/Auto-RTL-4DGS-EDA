# Phase 3 — 4DGS Spatiotemporal Accelerator: Verification and Pareto DSE

> **Metric labels.** Every area / gate / AIG / logic-level number in this document is a
> **generic-cell Yosys 0.23 + Berkeley ABC proxy** (`$_AND_/$_OR_/$_XOR_/$_MUX_` cells and
> structurally hashed AIG nodes, SRAM ways kept opaque). They are **not** 130nm standard-cell
> results and must not be compared with the existing 130nm figures (SSIM 66.7 MHz, area −19.0%,
> see [BASELINE_METRICS.md](BASELINE_METRICS.md)). No DesignWare model (licensed or clean-room) is
> instantiated by `rtl_4dgs/`; the cache SRAM `SRAM_gs4d_cache.sv` is a behavioral model used only
> for simulation and formal, replaced by an opaque macro boundary
> (`scripts/dse/gs4d_sram_interfaces.v`) for synthesis.

## 1. What was built

The frame tables, §6 DSE tables and original synthesis/proof tables below are historical
pre-conic-inversion measurements; §7 reports the corrected targeted rerun. The corrected projection uses inverse covariance
in the historically named `cov_a/b/c` fields. Its FSM is
`ACCEPT -> LOOKUP -> PROJ(phase 0..5) -> DIV(17 cycles at F=8) -> INV ->
PROJ(phase 7..9) -> RASTER -> OUT`, with the existing cache-hit skip bypass.
Projection costs `11+2*Q_FRAC` cycles; at F=8 the default transaction costs 34 active
cycles including acceptance and output consumption (33 edges to `out_valid`).

`gs4d_raster_lane` is a clean-room fixed-point per-pixel alpha stage corresponding to the original `rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v` per-pixel dx/dy -> quadratic form (conic) -> exp -> alpha stages.
It is parameterized by `NUM_PE` (lanes per cycle) and `Q_FRAC` (fraction bits).

| Item | File(s) | Notes |
|---|---|---|
| Bit-true golden model | `golden_model/4dgs_ref.hpp`, `golden_model/4dgs_ref.cpp` | Q(7,`Q_FRAC`) signed fixed point (half-up rounding, saturation, same API as `ref_model/include/gs4d/fixed_point.hpp`); deformation `mu(t)=mu0+dmu(t)`, `s(t)=s0+ds(t)`; 2D covariance projection; L1 skip test; set-associative cache; CLI stimulus/trace generator (`--trace-dir`, `--sweep`) |
| Temporal skip unit | `rtl_4dgs/temporal_skip_unit.sv` | `delta = |dmu_x|+|dmu_y|+|ds_x|+|ds_y|`, `skip_deform = delta < motion_thresh` (strict, so `motion_thresh = 0` never skips) |
| Motion-aware cache | `rtl_4dgs/motion_aware_cache.sv`, `rtl_4dgs/SRAM_gs4d_cache.sv` | `CACHE_SETS` × `CACHE_WAYS`, one 1R1W SRAM per way storing `{tag, mean_x, mean_y, cov_a, cov_b, cov_c, opacity}`; valid bits + round-robin victim; synchronous lookup, non-blocking write-back to the way selected at lookup, write→read forwarding |
| Accelerator top | `rtl_4dgs/gs4d_accelerator_top.sv`, `rtl_4dgs/gs4d_raster_lane.sv`, `rtl_4dgs/gs4d_fx.svh` | FSM `ACCEPT → LOOKUP → (PROJ ×4) → RASTER ×(TILE/NUM_PE) → OUT`; skip **and** cache hit bypasses PROJ (projection operand registers are not loaded, so the covariance multipliers see no new operands); `NUM_PE` parallel lanes extend the baseline rasterizer's per-pixel alpha stage over a `TILE`-pixel span (default 4) |
| Testbench | `rtl_4dgs/sim/sim_main_4dgs.cpp` | Drives the 4-frame scene with input bubbles and random `out_ready` back-pressure, checks every transaction bit-for-bit against the golden model, output stability under back-pressure, and projection operand isolation on skipped transactions |
| Flow / DSE | `scripts/dse/gs4d_flow.py`, `scripts/dse/run_dse_sweep.py` | per-configuration lint → sim → synth → CEC; 36-point sweep + Pareto extraction |
| Formal | `scripts/formal/rtl_to_netlist.py` (targets `gs4d_skip_unit`, `gs4d_cache`, `gs4d_top`), `scripts/formal/verify_z3_4dgs.py` | eda_core netlists, 30 s / 3-retry breaker, COI slicing, BMC-20 |

Scene: 1,024 Gaussians × `frame_0..frame_3`; 70% static background (zero deformation), 30%
dynamic foreground (moving and micro-motion groups). `frame_0` is a cold cache, so no skips occur
there by construction.

## 2. Reproduce

```bash
T=$PWD/build/baseline/tools   # prepared as in BASELINE_METRICS.md (Yosys 0.23, sv2v 0.0.12)
TOOLS="PYTHON=$PWD/build/venv/bin/python YOSYS=$T/yosys/usr/bin/yosys SV2V=$T/sv2v-Linux/sv2v"
make $TOOLS sim_4dgs                 # Tier 1
make $TOOLS formal_4dgs              # Tier 2 (synth_4dgs) then Tier 3
make $TOOLS DSE_JOBS=6 dse_4dgs      # sweep, results in build/dse/
```

Outputs stay under `build/` (ignored); no waveform, `.so`, `obj_dir/` or `config.yaml` is committed.

## 3. Tier 1 — Verilator

* `verilator --lint-only -Wall` is clean (zero warnings) for all 18 hardware configurations.
* Every configuration simulates **> 20,000 cycles** (4 frames × 1,024 Gaussians with bubbles and
  back-pressure) for both thresholds with **0 bit-true mismatches**, 0 output-stability errors and
  0 operand-isolation errors (full table in §6).

Golden-model per-frame breakdown, `NUM_PE=1, CACHE_WAYS=2, Q_FRAC=8, MOTION_THRESH=16`:

| Frame | skip-eligible | skipped (hit) | cycles | no-skip cycles | reduction | lossy alpha px (max err, LSB) |
|---|---|---|---|---|---|---|
| frame_0 | 1024 | 0 | 11264 | 11264 | 0.0% | 0 (0) |
| frame_1 | 820 | 354 | 9848 | 11264 | 12.6% | 43 (3) |
| frame_2 | 820 | 354 | 9848 | 11264 | 12.6% | 70 (4) |
| frame_3 | 798 | 348 | 9872 | 11264 | 12.4% | 74 (6) |
| total | — | 1056 / 4096 (25.8%) | 40832 | 45056 | 9.4% (1.103×) | 187 (6) |

Skip-eligible Gaussians only skip when their previous attributes are still in the cache, so the
skip rate is bounded by cache capacity/conflicts (512 sets): 1-way 9.0%, 2-way 25.8%, 4-way 55.7%
at Q_FRAC=8. `MOTION_THRESH=16` is a raw LSB threshold, so at Q_FRAC=12 it is 16× tighter in real
units: slightly fewer skips and zero alpha error in this scene.

## 4. Tier 2 — Yosys / ABC (generic-cell)

Default configuration (`NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2, CACHE_SETS=512`), **generic-cell**:

| Metric | Value |
|---|---|
| Generic gates after `abc -g AND,OR,XOR,MUX` | 27,571 |
| Flip-flops | 2,118 |
| Optimized AIG AND nodes | 32,540 |
| AIG logic levels (critical-path depth) | 339 |
| Opaque SRAM ways / bits | 2 / 101,376 |
| ABC CEC (pre-map vs mapped, mapped vs optimized) | PASS |

All 18 configurations are listed in §6. The datapath has single-cycle `fxmul` stages (multiply,
half-up rounding and saturation in one combinational stage), so logic depth is expected to track
the operand width (`Q_FRAC`) rather than `NUM_PE` or `CACHE_WAYS`; §6 shows the measured levels.

### Corrected conic projection: default configuration

Fresh Yosys 0.23 / Berkeley ABC **generic-cell** synthesis and both ABC CEC checks
pass with the sequential restoring divider. These are area/depth proxies, not
physical cell area, nanosecond delay or 130nm measurements. The historical sweep
in §6 has not been rerun.

| Metric | Pre-fix default above | Corrected default |
|---|---:|---:|
| Generic gates | 27,571 | 28,509 |
| Flip-flops | 2,118 | 2,275 |
| Optimized AIG AND nodes | 32,540 | 33,384 |
| Optimized AIG logic levels | 339 | 335 |
| Opaque SRAM ways / bits | 2 / 101,376 | 2 / 101,376 |
| ABC CEC (both comparisons) | PASS | PASS |

The pre-fix baseline is measured on merged main `1a30afc` (which includes the five review-fix commits after the PR #4 review: phase-counter sizing, round-robin victim wrap, `--resume` SHA256 check, per-pixel multiply count, and docs refresh), not on the PR #4 review head `4b0eabf`.

## 5. Tier 3 — Z3 proofs (eda_core, 30 s breaker, COI slicing, BMC-20)

Netlists are produced by `scripts/formal/rtl_to_netlist.py` in small formal configurations
(`gs4d_skip_unit`: W=16; `gs4d_cache`: 4 sets × 2 ways, 8-bit data, 2-bit tag; `gs4d_top`:
`NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2, CACHE_SETS=4, ID_W=4`) and loaded through `eda_core`
(`parser.py` + `fast_eda`). Historical pre-conic result of `make formal_4dgs`
(the corrected control proof is described after this table):

| Target | Obligation | Expect | Result | Method | Time (s) | Verdict |
|---|---|---|---|---|---|---|
| `gs4d_skip_unit` | mag_dmu_x == \|dmu_x\| (bit-true) | UNSAT | UNSAT | monolithic | 0.04 | pass |
| `gs4d_skip_unit` | mag_dmu_y == \|dmu_y\| (bit-true) | UNSAT | UNSAT | monolithic | 0.03 | pass |
| `gs4d_skip_unit` | mag_ds_x == \|ds_x\| (bit-true) | UNSAT | UNSAT | monolithic | 0.02 | pass |
| `gs4d_skip_unit` | mag_ds_y == \|ds_y\| (bit-true) | UNSAT | UNSAT | monolithic | 0.02 | pass |
| `gs4d_skip_unit` | sum_mu == sum of its two operands (bit-true) | UNSAT | UNSAT | monolithic | 0.03 | pass |
| `gs4d_skip_unit` | sum_s == sum of its two operands (bit-true) | UNSAT | UNSAT | monolithic | 0.04 | pass |
| `gs4d_skip_unit` | delta == sum of its two operands (bit-true) | UNSAT | UNSAT | monolithic | 0.75 | pass |
| `gs4d_skip_unit` | skip_deform == (delta < motion_thresh) | UNSAT | UNSAT | monolithic | 0.01 | pass |
| `gs4d_skip_unit` | lossless: motion_thresh == 0 -> skip_deform == 0 | UNSAT | UNSAT | monolithic | 0.0 | pass |
| `gs4d_skip_unit` | non-vacuity witness: motion_thresh == 16 can skip | SAT | SAT | monolithic | 0.01 | pass |
| `gs4d_cache` | base: invariants I, J, K hold in the reset state | UNSAT | UNSAT | monolithic | 0.05 | pass |
| `gs4d_cache` | inductive I: no tag stored twice in a set | UNSAT | UNSAT | monolithic | 0.11 | pass |
| `gs4d_cache` | inductive J: write selection is the unique way for its tag | UNSAT | UNSAT | monolithic | 0.09 | pass |
| `gs4d_cache` | inductive K: lookup response equals the array (forwarding) | UNSAT | UNSAT | monolithic | 0.09 | pass |
| `gs4d_cache` | coherency: hit data / miss == post-write array under simultaneous lookup + write | UNSAT | UNSAT | monolithic | 0.18 | pass |
| `gs4d_cache` | write locality: only (sel_set, sel_way) changes, with {sel_tag, wr_data} | UNSAT | UNSAT | monolithic | 0.05 | pass |
| `gs4d_cache` | non-vacuity witness: a hit coincident with a write-back is reachable under I, J, K | SAT | SAT | monolithic | 0.09 | pass |
| `gs4d_top` | lossless: motion_thresh == 0 -> skip_deform == 0 | UNSAT | UNSAT | monolithic | 0.02 | pass |
| `gs4d_top` | lossless: skip_q == 0 and out_skipped == 0 are inductive under motion_thresh == 0 | UNSAT | UNSAT | monolithic | 0.01 | pass |
| `gs4d_top` | skip equivalence: under motion_thresh == 0 every datapath/control register is independent of cache contents (== full evaluation) | UNSAT | UNSAT | monolithic | 0.82 | pass |
| `gs4d_top` | FSM closed over legal states (0..4) | UNSAT | UNSAT | monolithic | 0.01 | pass |
| `gs4d_top` | backpressure: out_ready == 0 in OUT freezes state, outputs and cache arrays/valid/LRU; no accept | UNSAT | UNSAT | monolithic | 0.26 | pass |
| `gs4d_top` | handshakes: OUT & out_ready -> ACCEPT, ACCEPT & in_valid -> LOOKUP | UNSAT | UNSAT | monolithic | 0.0 | pass |
| `gs4d_top` | non-vacuity witness: with motion_thresh != 0 the result does depend on cache contents | SAT | SAT | monolithic | 0.5 | pass |
| `gs4d_top` | deadlock-free: rank(LOOKUP/PROJ/RASTER) strictly decreases every cycle | UNSAT | UNSAT | monolithic | 0.01 | pass |
| `gs4d_top` | BMC-20 from reset: out_valid within 11 cycles, state legal | UNSAT | UNSAT | BMC-20 | 0.08 | pass |

**26/26 obligations as expected, 26 Z3 queries** (5.4.1, timeout 30 s, 3.89 s total). No obligation needed COI slicing or the BMC-20 fallback; BMC-20 is used deliberately for the reset-reachability check.

**Corrected conic run:** `make formal_4dgs` passes end-to-end using Verilator
4.038, sv2v 0.0.12, Yosys 0.23, Berkeley ABC and Z3 5.4.1: **36/36 obligations,
36 queries**, approximately 5.52 s in the Z3 stage on the Devin machine;
timing is machine-dependent. All unbounded obligations close
monolithically; the explicit BMC-20 control-safety check also passes. No timeout,
COI fallback or bounded downgrade was needed. Lossless cache non-interference
takes 1.21 s, cache coherency 0.24 s, control-invariant induction 0.01 s,
unbounded progress 0.02 s, and the 52-output-cone restoring-step check 0.19 s.
Per-obligation times and results are written to `build/formal/formal_4dgs.json`
and `build/formal/formal_4dgs.log` (not committed).

Proof structure:

* **Skip equivalence (lossless mode).** Under `motion_thresh == 0`, `skip_deform` is never 1 and
  `skip_q == 0 ∧ out_skipped == 0` is inductive. Non-interference: with two arbitrary copies of the
  entire cache state (arrays, valid bits, round-robin pointers, read registers), the next value of
  every non-cache register (FSM, counters, projection, raster and all output registers) is
  identical. Hence the datapath output in lossless mode is a function of the input transaction only,
  i.e. bit-identical to full (unskipped) evaluation. The SAT witness shows that with
  `motion_thresh != 0` the cache *does* reach the datapath, so the property is not vacuous.
* **Cache coherency.** Invariants I (no tag stored twice in a set), J (latched write selection is
  the unique way holding its tag), K (response registers equal the array, including forwarding)
  hold at reset and are inductive; under them, a lookup response coincident with a write-back
  reports exactly the post-write array (hit data or miss), and a write changes only
  `(sel_set, sel_way)` with `{sel_tag, wr_data}`.
* **Back-pressure / deadlock freedom (corrected conic FSM).** In `OUT` with `out_ready == 0`, the FSM, all output
  registers and the whole cache (arrays, valid, round-robin) are frozen and `in_ready == 0`. The FSM
  stays in its 7 legal states; `OUT ∧ out_ready → ACCEPT`, `ACCEPT ∧ in_valid → LOOKUP`.
  A reset-established, inductive control invariant constrains PROJ phases to 0..5 or
  7..9, DIV to phase 6 and `div_count < 17`, INV to phase 6 and `div_count == 17`,
  and RASTER to `cnt < 4`. Under it, the unsigned 8-bit rank is:

  | State | Rank |
  |---|---|
  | LOOKUP | 32 |
  | PROJ, phase 0..5 | 31 - phase |
  | DIV | 25 - div_count |
  | INV | 8 |
  | PROJ, phase 7..9 | 14 - phase |
  | RASTER | 4 - cnt |
  | ACCEPT / OUT | 0 |

  Every busy step has positive rank, strictly decreases it, and cannot return to
  ACCEPT. Thus every accepted transaction reaches OUT, independently of cache and
  arithmetic data; only the two handshake states can wait. This is an **unbounded
  inductive progress proof**, not bounded liveness. Recompute needs 33 edges to
  `out_valid`, exceeding 20, so BMC remains at **k=20** and checks reset-reachable
  control safety, not an impossible 20-cycle completion bound.
* **Divider.** Additional gate-netlist obligations check rounded-numerator
  initialization from the latched determinant, zero initial quotient/remainder, nonpositive-det
  degeneracy, the saturation mux, each bit of the restoring quotient/remainder/
  numerator recurrence, and INV loading. These are compositional local-step
  checks, not a monolithic endpoint division miter.
* **Proof acceptance.** The 30-second per-query, three-attempt, COI-slicing and
  BMC-20 fallback policy is unchanged. A bounded fallback is diagnostic only for
  these unbounded obligations and fails this gate rather than counting as proof.

**Breaker / bottleneck status.** The first attempt to prove `delta` against the L1 specification
as one 4-operand adder miter exhausted the 30 s × 3 budget, fell back to per-bit COI slicing and
hit `BOTTLENECK slice delta[14] … BMC k=20` (a combinational cone, so BMC could not close it). The
RTL now keeps the intermediate nets (`(* keep *) mag_*`, `sum_mu`, `sum_s`) and the proof is
staged; every obligation closes monolithically well under 1 s, with no slicing or BMC fallback.
A CEGAR iteration also found a tool issue: public netnames whose bit lost its driver after
flatten/ABC (e.g. `cache_wr`) were exposed as free variables, producing a spurious back-pressure
counterexample; `rtl_to_netlist.py` now exposes such bits as `None`. The Phase 2 baseline flow
(`make formal_z3`, 16/16 + breaker drill 9/9) is unchanged.

## 6. Historical pre-fix DSE sweep (36 points; covariance, not conic)

Columns: `skip_rate`, `speedup` (vs the same hardware with no skipping) and `throughput`
(shaded pixels per active cycle) come from the Verilator runs; `gates`, `ffs`, `aig_and`, `levels`
are **generic-cell** Yosys/ABC numbers; `area = aig_and + 6 × ffs` (generic-cell AIG-equivalent,
SRAM excluded and given separately as `sram_bits`); `adp = area × levels`;
`eff_thr = 1000 × throughput / levels` (pixels per 1000 level-delays).
`pareto2`: non-dominated in (min `adp`, max `eff_thr`); `pareto3`: additionally max `skip_rate`.

| num_pe | cache_ways | q_frac | motion_thresh | sim_cycles | mismatches | skip_rate | speedup | throughput | lossy_alpha_px | max_alpha_err_lsb | gates | ffs | aig_and | levels | sram_bits | area | adp | eff_thr | pareto2 | pareto3 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 8 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 21960 | 1606 | 26211 | 342 | 50688 | 35847 | 12259674 | 1.0633 |  |  |
| 1 | 1 | 8 | 16 | 45393 | 0 | 0.0898 | 1.0338 | 0.3759 | 54 | 6 | 21960 | 1606 | 26211 | 342 | 50688 | 35847 | 12259674 | 1.0992 | **yes** | **yes** |
| 1 | 1 | 12 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 31439 | 1738 | 37455 | 419 | 62976 | 47883 | 20062977 | 0.8679 |  |  |
| 1 | 1 | 12 | 16 | 45437 | 0 | 0.082 | 1.0307 | 0.3748 | 0 | 0 | 31439 | 1738 | 37455 | 419 | 62976 | 47883 | 20062977 | 0.8946 |  |  |
| 1 | 2 | 8 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 27571 | 2118 | 32540 | 339 | 101376 | 45248 | 15339072 | 1.0727 |  |  |
| 1 | 2 | 8 | 16 | 42698 | 0 | 0.2578 | 1.1034 | 0.4013 | 187 | 6 | 27571 | 2118 | 32540 | 339 | 101376 | 45248 | 15339072 | 1.1836 | **yes** | **yes** |
| 1 | 2 | 12 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 36801 | 2250 | 43477 | 413 | 125952 | 56977 | 23531501 | 0.8805 |  |  |
| 1 | 2 | 12 | 16 | 43062 | 0 | 0.2314 | 1.0919 | 0.3971 | 0 | 0 | 36801 | 2250 | 43477 | 413 | 125952 | 56977 | 23531501 | 0.9614 |  |  |
| 1 | 4 | 8 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 37938 | 3656 | 44817 | 337 | 202752 | 66753 | 22495761 | 1.079 |  |  |
| 1 | 4 | 8 | 16 | 38032 | 0 | 0.5574 | 1.2542 | 0.4561 | 451 | 7 | 37938 | 3656 | 44817 | 337 | 202752 | 66753 | 22495761 | 1.3533 |  | **yes** |
| 1 | 4 | 12 | 0 | 46689 | 0 | 0.0 | 1.0 | 0.3636 | 0 | 0 | 47470 | 3788 | 56162 | 413 | 251904 | 78890 | 32581570 | 0.8805 |  |  |
| 1 | 4 | 12 | 16 | 38864 | 0 | 0.4929 | 1.2184 | 0.443 | 0 | 0 | 47470 | 3788 | 56162 | 413 | 251904 | 78890 | 32581570 | 1.0728 |  |  |
| 2 | 1 | 8 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 32266 | 1606 | 37293 | 335 | 50688 | 46929 | 15721215 | 1.3267 |  |  |
| 2 | 1 | 8 | 16 | 37499 | 0 | 0.0898 | 1.0416 | 0.4629 | 54 | 6 | 32266 | 1606 | 37293 | 335 | 50688 | 46929 | 15721215 | 1.3819 | **yes** | **yes** |
| 2 | 1 | 12 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 46531 | 1738 | 55161 | 415 | 62976 | 65589 | 27219435 | 1.0709 |  |  |
| 2 | 1 | 12 | 16 | 37607 | 0 | 0.082 | 1.0378 | 0.4613 | 0 | 0 | 46531 | 1738 | 55161 | 415 | 62976 | 65589 | 27219435 | 1.1115 |  |  |
| 2 | 2 | 8 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 37644 | 2118 | 43538 | 338 | 101376 | 56246 | 19011148 | 1.3149 |  |  |
| 2 | 2 | 8 | 16 | 35003 | 0 | 0.2578 | 1.1294 | 0.502 | 187 | 6 | 37644 | 2118 | 43538 | 338 | 101376 | 56246 | 19011148 | 1.4851 | **yes** | **yes** |
| 2 | 2 | 12 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 51866 | 2250 | 61639 | 422 | 125952 | 75139 | 31708658 | 1.0532 |  |  |
| 2 | 2 | 12 | 16 | 35420 | 0 | 0.2314 | 1.1147 | 0.4954 | 0 | 0 | 51866 | 2250 | 61639 | 422 | 125952 | 75139 | 31708658 | 1.1739 |  |  |
| 2 | 4 | 8 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 47828 | 3656 | 55992 | 336 | 202752 | 77928 | 26183808 | 1.3228 |  |  |
| 2 | 4 | 8 | 16 | 30543 | 0 | 0.5574 | 1.3293 | 0.5908 | 451 | 7 | 47828 | 3656 | 55992 | 336 | 202752 | 77928 | 26183808 | 1.7583 | **yes** | **yes** |
| 2 | 4 | 12 | 0 | 38817 | 0 | 0.0 | 1.0 | 0.4444 | 0 | 0 | 63259 | 3788 | 74216 | 419 | 251904 | 96944 | 40619536 | 1.0607 |  |  |
| 2 | 4 | 12 | 16 | 31430 | 0 | 0.4929 | 1.2805 | 0.5691 | 0 | 0 | 63259 | 3788 | 74216 | 419 | 251904 | 96944 | 40619536 | 1.3583 |  |  |
| 4 | 1 | 8 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 51345 | 1606 | 58792 | 337 | 50688 | 68428 | 23060236 | 1.4837 |  |  |
| 4 | 1 | 8 | 16 | 33480 | 0 | 0.0898 | 1.047 | 0.5235 | 54 | 6 | 51345 | 1606 | 58792 | 337 | 50688 | 68428 | 23060236 | 1.5535 | **yes** | **yes** |
| 4 | 1 | 12 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 77419 | 1738 | 89668 | 415 | 62976 | 100096 | 41539840 | 1.2048 |  |  |
| 4 | 1 | 12 | 16 | 33759 | 0 | 0.082 | 1.0428 | 0.5214 | 0 | 0 | 77419 | 1738 | 89668 | 415 | 62976 | 100096 | 41539840 | 1.2563 |  |  |
| 4 | 2 | 8 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 56535 | 2118 | 65057 | 337 | 101376 | 77765 | 26206805 | 1.4837 |  |  |
| 4 | 2 | 8 | 16 | 31129 | 0 | 0.2578 | 1.148 | 0.574 | 187 | 6 | 56535 | 2118 | 65057 | 337 | 101376 | 77765 | 26206805 | 1.7032 |  |  |
| 4 | 2 | 12 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 83172 | 2250 | 95902 | 416 | 125952 | 109402 | 45511232 | 1.2019 |  |  |
| 4 | 2 | 12 | 16 | 31613 | 0 | 0.2314 | 1.1309 | 0.5654 | 0 | 0 | 83172 | 2250 | 95902 | 416 | 125952 | 109402 | 45511232 | 1.3592 |  |  |
| 4 | 4 | 8 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 66847 | 3656 | 77062 | 338 | 202752 | 98998 | 33461324 | 1.4793 |  |  |
| 4 | 4 | 8 | 16 | 27126 | 0 | 0.5574 | 1.3864 | 0.6932 | 451 | 7 | 66847 | 3656 | 77062 | 338 | 202752 | 98998 | 33461324 | 2.0508 | **yes** | **yes** |
| 4 | 4 | 12 | 0 | 34863 | 0 | 0.0 | 1.0 | 0.5 | 0 | 0 | 93728 | 3788 | 108848 | 423 | 251904 | 131576 | 55656648 | 1.182 |  |  |
| 4 | 4 | 12 | 16 | 27942 | 0 | 0.4929 | 1.3271 | 0.6635 | 0 | 0 | 93728 | 3788 | 108848 | 423 | 251904 | 131576 | 55656648 | 1.5686 |  |  |

### Pareto-optimal configurations

| Configuration | area (generic-cell) | levels | adp | throughput (px/cycle) | eff_thr | skip_rate | speedup | front |
|---|---|---|---|---|---|---|---|---|
| PE=1, WAYS=1, Q=8, T=16 | 35847 | 342 | 12259674 | 0.3759 | 1.0992 | 0.0898 | 1.0338x | 2-obj + 3-obj |
| PE=1, WAYS=2, Q=8, T=16 | 45248 | 339 | 15339072 | 0.4013 | 1.1836 | 0.2578 | 1.1034x | 2-obj + 3-obj |
| PE=2, WAYS=1, Q=8, T=16 | 46929 | 335 | 15721215 | 0.4629 | 1.3819 | 0.0898 | 1.0416x | 2-obj + 3-obj |
| PE=2, WAYS=2, Q=8, T=16 | 56246 | 338 | 19011148 | 0.502 | 1.4851 | 0.2578 | 1.1294x | 2-obj + 3-obj |
| PE=1, WAYS=4, Q=8, T=16 | 66753 | 337 | 22495761 | 0.4561 | 1.3533 | 0.5574 | 1.2542x | 3-obj |
| PE=4, WAYS=1, Q=8, T=16 | 68428 | 337 | 23060236 | 0.5235 | 1.5535 | 0.0898 | 1.047x | 2-obj + 3-obj |
| PE=2, WAYS=4, Q=8, T=16 | 77928 | 336 | 26183808 | 0.5908 | 1.7583 | 0.5574 | 1.3293x | 2-obj + 3-obj |
| PE=4, WAYS=4, Q=8, T=16 | 98998 | 338 | 33461324 | 0.6932 | 2.0508 | 0.5574 | 1.3864x | 2-obj + 3-obj |

### Skip-rate / speedup analysis (MOTION_THRESH = 16 vs 0, same hardware)

| NUM_PE | CACHE_WAYS | Q_FRAC | skip_rate | cycle reduction | speedup | lossy alpha px (max err LSB) |
|---|---|---|---|---|---|---|
| 1 | 1 | 8 | 9.0% | 3.3% | 1.0338x | 54 (6) |
| 1 | 1 | 12 | 8.2% | 3.0% | 1.0307x | 0 (0) |
| 1 | 2 | 8 | 25.8% | 9.4% | 1.1034x | 187 (6) |
| 1 | 2 | 12 | 23.1% | 8.4% | 1.0919x | 0 (0) |
| 1 | 4 | 8 | 55.7% | 20.3% | 1.2542x | 451 (7) |
| 1 | 4 | 12 | 49.3% | 17.9% | 1.2184x | 0 (0) |
| 2 | 1 | 8 | 9.0% | 4.0% | 1.0416x | 54 (6) |
| 2 | 1 | 12 | 8.2% | 3.6% | 1.0378x | 0 (0) |
| 2 | 2 | 8 | 25.8% | 11.5% | 1.1294x | 187 (6) |
| 2 | 2 | 12 | 23.1% | 10.3% | 1.1147x | 0 (0) |
| 2 | 4 | 8 | 55.7% | 24.8% | 1.3293x | 451 (7) |
| 2 | 4 | 12 | 49.3% | 21.9% | 1.2805x | 0 (0) |
| 4 | 1 | 8 | 9.0% | 4.5% | 1.047x | 54 (6) |
| 4 | 1 | 12 | 8.2% | 4.1% | 1.0428x | 0 (0) |
| 4 | 2 | 8 | 25.8% | 12.9% | 1.148x | 187 (6) |
| 4 | 2 | 12 | 23.1% | 11.6% | 1.1309x | 0 (0) |
| 4 | 4 | 8 | 55.7% | 27.9% | 1.3864x | 451 (7) |
| 4 | 4 | 12 | 49.3% | 24.6% | 1.3271x | 0 (0) |

### Observations

* **All 36 points verified.** Every configuration: Verilator `-Wall` clean, > 20,000 simulated
  cycles (46,689 / 38,817 / 34,863 cycles at `NUM_PE` = 1 / 2 / 4 in lossless mode), 0 bit-true
  mismatches, and ABC CEC PASS for the generic-cell netlists.
* **Lossless mode is exact.** `MOTION_THRESH = 0` gives 0 skips, speedup 1.000× and 0 alpha error
  everywhere, matching the Z3 skip-equivalence proof in §5.
* **Skip rate is set by the cache, not by `NUM_PE`.** At `MOTION_THRESH = 16` it is 9.0% / 25.8% /
  55.7% (Q_FRAC=8) and 8.2% / 23.1% / 49.3% (Q_FRAC=12) for 1 / 2 / 4 ways. Skips remove the
  4-cycle projection, so the cycle reduction grows with `NUM_PE`, where projection is a larger share
  of each transaction (4-way, Q_FRAC=8: 1.254× → 1.329× → 1.386× for `NUM_PE` 1 → 2 → 4).
* **Pareto front.** All 8 non-dominated points use `Q_FRAC = 8, MOTION_THRESH = 16`: the skip path
  adds throughput on the same hardware, and Q_FRAC=12 costs 18-46% more generic-cell area and
  74-85 more AIG levels (335-342 → 413-423). The minimum-ADP point is `NUM_PE=1, CACHE_WAYS=1`; the
  highest effective throughput is `NUM_PE=4, CACHE_WAYS=4` (2.05 px per 1000 level-delays, 2.7× ADP).
  `NUM_PE=2, CACHE_WAYS=4` reaches 86% of that throughput at 78% of its ADP.
* **Quality.** Q_FRAC=8 skipping changes ≤ 451 of 16,384 shaded alpha values by ≤ 7 LSB in this
  scene. `Q_FRAC=12, MOTION_THRESH=16` skips 8-49% with **0 alpha error**; for a lossless-quality
  budget, `NUM_PE=4, CACHE_WAYS=4, Q_FRAC=12` gives 1.327× (not on the generic-cell ADP front).
* **What the metrics leave out.** SRAM area (`sram_bits`, 50,688-251,904 bits) is outside the
  generic-cell area and scales linearly with `CACHE_WAYS`. A 130nm or other library mapping would be
  needed before comparing with the 130nm SSIM numbers.

### Bottlenecks and fallbacks

* Tier 2 run time: the default Yosys `abc` script (`&fraig -x; scorr; dc2; dretime; &dch -f; &nf`)
  took 76-97 minutes per `NUM_PE=4, Q_FRAC=12` configuration (6 parallel jobs), against 3-39
  minutes for the others; the full sweep took 133 minutes. All 18 completed with the default script.
  `gs4d_flow.py synth --fast-abc` is available as a labelled fallback (`abc -fast`). It was measured
  on `NUM_PE=4, CACHE_WAYS=1, Q_FRAC=8` (RTL revision before the review fixes): 59,128 gates /
  270 levels against 51,268 / 335 with the default script. Because the numbers differ, fallback results must not be mixed into this table.
  `run_dse_sweep.py --resume` reuses a finished `metrics.json` only when its `source_sha256` (RTL, scripts, parameters) still matches.
* Tier 3: the bottleneck and the CEGAR finding are described in §5. No obligation in the final
  run used slicing or BMC fallback.

## 7. Corrected conic projection: targeted rerun

This is **not** a new full-grid sweep. The historical table actually lists eight
3-objective Pareto points, not nine. We rerun those eight plus the remaining Q8
combination (`PE=4, WAYS=2`) and three Q12 controls paired with `PE=1, WAYS=1/2/4`.
All configurations use 512 sets. The 12 rows below use `MOTION_THRESH=16`; each
hardware configuration is also simulated at threshold 0 as a lossless check and
to measure speedup. No other hardware configurations are synthesized.
The default sweep remains 18 hardware configurations / 36 threshold rows.

Reproduction (system Verilator 4.038, sv2v 0.0.12, Yosys 0.23, default ABC mapping;
no `abc -fast` results are mixed in):

```sh
python3 scripts/dse/run_dse_sweep.py --out build/dse_conic_targeted --jobs 4 \
  --verilator /usr/bin/verilator --yosys "$HOME/tools/yosys-0.23/usr/bin/yosys" \
  --sv2v "$HOME/tools/sv2v-Linux/sv2v" --abc /usr/bin/berkeley-abc \
  --configs pe1_q8_w1_s512 pe1_q8_w2_s512 pe1_q8_w4_s512 \
    pe2_q8_w1_s512 pe2_q8_w2_s512 pe2_q8_w4_s512 \
    pe4_q8_w1_s512 pe4_q8_w2_s512 pe4_q8_w4_s512 \
    pe1_q12_w1_s512 pe1_q12_w2_s512 pe1_q12_w4_s512 --report-thresholds 16
python3 -m unittest discover -s scripts/dse -p 'test_*.py' -v
```

`--configs` rejects unknown/empty selections and deduplicates tags in grid order;
`--report-thresholds` filters reported rows, not the two verification simulations.
Generated CSV/JSON, synthesis netlists and simulation objects remain under ignored
`build/`; only the documented results are committed, as in the original sweep.

### Corrected targeted results (12 rows)

All 12 configurations: warning-free Verilator `-Wall`, both simulations exceed
20,000 cycles with zero bit-true mismatches, and ABC CEC PASS. The run took
2,230 seconds with four jobs. Gates, FFs, area, ADP and levels below are
**generic-cell Yosys/ABC proxies**, with the same area formula as §6; SRAM is
excluded. SRAM bits remain 50,688/101,376/202,752 at Q8 and
62,976/125,952/251,904 at Q12 for 1/2/4 ways.
`front` means both the 2- and 3-objective fronts **among these 12 rows only**.
`skip alpha px/max LSB` is error versus fixed-point full recomputation.

| PE | ways | F | generic gates | FFs | area | levels | ADP | throughput | eff_thr | skip_rate | speedup | skip alpha px/max LSB | front |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 8 | 23048 | 1763 | 37638 | 334 | 12571092 | 0.1267 | 0.3793 | 0.0898 | 1.0767x | 84/14 | yes |
| 1 | 1 | 12 | 32067 | 1943 | 49821 | 408 | 20326968 | 0.1022 | 0.2505 | 0.0820 | 1.0733x | 0/0 | no |
| 1 | 2 | 8 | 28509 | 2275 | 47034 | 335 | 15756390 | 0.1479 | 0.4415 | 0.2576 | 1.2571x | 246/14 | yes |
| 1 | 2 | 12 | 37579 | 2455 | 59382 | 410 | 24346620 | 0.1180 | 0.2877 | 0.2312 | 1.2387x | 0/0 | no |
| 1 | 4 | 8 | 38957 | 3813 | 68373 | 337 | 23041701 | 0.2109 | 0.6258 | 0.5568 | 1.7927x | 572/14 | yes |
| 1 | 4 | 12 | 48522 | 3993 | 80950 | 412 | 33351400 | 0.1615 | 0.3920 | 0.4924 | 1.6960x | 0/0 | no |
| 2 | 1 | 8 | 33084 | 1763 | 48584 | 334 | 16227056 | 0.1352 | 0.4049 | 0.0898 | 1.0819x | 84/14 | no |
| 2 | 2 | 8 | 38453 | 2275 | 58217 | 337 | 19619129 | 0.1597 | 0.4739 | 0.2576 | 1.2777x | 246/14 | yes |
| 2 | 4 | 8 | 48214 | 3813 | 79360 | 340 | 26982400 | 0.2358 | 0.6934 | 0.5568 | 1.8862x | 572/14 | yes |
| 4 | 1 | 8 | 52008 | 1763 | 70282 | 337 | 23685034 | 0.1400 | 0.4154 | 0.0898 | 1.0848x | 84/14 | no |
| 4 | 2 | 8 | 57909 | 2275 | 79654 | 334 | 26604436 | 0.1663 | 0.4981 | 0.2576 | 1.2892x | 246/14 | no |
| 4 | 4 | 8 | 68150 | 3813 | 101121 | 334 | 33774414 | 0.2505 | 0.7501 | 0.5568 | 1.9417x | 572/14 | yes |

**Pareto-change verdict:** the historical eight-point 3-objective set shrinks to
six in this targeted cohort: Q8 `(PE,ways) = (1,1), (1,2), (1,4), (2,2), (2,4),
(4,4)`. The historical `(2,1)` and `(4,1)` points are now dominated by `(1,2)`
(lower ADP, higher effective throughput and skip rate). `(1,4)` was previously
3-objective-only and is now also on the 2-objective front. The additional Q8
`(4,2)` remains dominated, e.g. by `(1,4)`. All three Q12 controls are dominated
by their Q8 counterparts on these cost/performance objectives; precision is not
an objective in this front. This does **not** establish the corrected full-grid
front: the other six Q12 hardware configurations were deliberately not rerun.

The divider makes recompute much longer, so cache capacity now matters more than
adding raster lanes: e.g. `(1,4,Q8)` beats `(4,2,Q8)` on ADP, effective throughput
and skip rate. Compared with the old covariance-only model, the longer projection
reduces absolute throughput but increases skip-path speedup (up to 1.9417x here).
Corrected Q8 skipping changes at most 572/16,384 scene alpha values by at most
14 LSB, versus the historical 451 values / 7 LSB; the three paired Q12 controls
show zero skip-induced alpha error, not zero error versus floating point.

### Q8 versus Q12 precision after covariance inversion

The independent `scene.hpp::conic_from_scale` cross-check on the same 4,096 scene
transactions gives Q8: 12,264 coefficients checked, 24 excluded for reciprocal
saturation (eight transactions), zero out-of-range references, zero failures.
Q12: all 12,288 coefficients checked, zero reciprocal saturations, zero out-of-range
references, zero failures. Saturation counts are coefficients, not Gaussians;
the fixed-point determinant/reciprocal makes low-scale Q8 cases more sensitive.

For an additional full-recompute audit, compare `lane_alpha / 2^F` on all 16,384
scene pixels against double-precision `opacity * exp(-0.5*(a*dx*dx + 2*b*dx*dy +
c*dy*dy))`, using `conic_from_scale`, the same quantized deformed scales, rho,
means, opacity and pixel coordinates. Include saturated cases and use no cache
skips. This isolates arithmetic/exp approximation from input quantization and
cache reuse; it is not an end-to-end image-quality measurement:

| Q_FRAC | alpha MAE | alpha RMSE | max absolute alpha error |
|---|---|---|---|
| 8 | 0.00949220 | 0.01430091 | 0.05904488 |
| 12 | 0.00860526 | 0.01307396 | 0.04375759 |

Q12 improves precision and eliminates the observed reciprocal saturations, but
does not eliminate the lane's piecewise exp approximation or its alpha cutoff.
These errors are versus exact floating-point exp, **not** the DSE
`max_alpha_err_lsb`/`lossy_alpha_px` fields, which compare skipping with fixed-point
full recomputation. Equal raw threshold 16 also means different physical motion
tolerances (16/256 versus 16/4096), so Q8/Q12 skip rates are not equal-quality budgets.
The restoring divider costs `2F+1` cycles (17 versus 25), and total projection
cost is `11+2F` (27 versus 35): Q12 adds eight cycles on every recompute.

### Formal observation portability (Yosys 0.23 / Noble 0.33)

Yosys 0.33 trims the public `remainder_q` alias to 12 bits even though five higher
register bits remain live under `trial` aliases. The old proof zero-extended the
shortened word, causing a spurious restoring-step SAT result. Formal extraction
now preserves all four divider observation words **after hierarchy specialization**
and rejects missing/trimmed words (17-bit numerator/quotient, 18-bit remainder/
denominator). This changes only formal observability, not RTL or production
synthesis, and leaves the recurrence assertion and solver guardrails unchanged.
Unexpected SAT results also print control/divider values and save the complete
model under the formal output directory for diagnosis.
