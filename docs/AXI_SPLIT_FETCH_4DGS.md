# Phase 5c: Split-Fetch AXI4-Stream Wrapper (measured bandwidth)

`rtl_4dgs/gs4d_axi_wrapper.sv` wraps `gs4d_accelerator_top` (`SPLIT_FETCH=1`) with two
AXI4-Stream-style input channels. Channel A (metadata) is fetched for every Gaussian.
Channel B (base attributes) is requested only for recompute misses. A skip + hit transaction
never asserts `s_axis_attr_tready`. The result interface (`out_*`, `out_ready`) is the top's,
unchanged.

**Labels.** Every bandwidth number here is **MEASURED** or **MODELED**:

* **MEASURED**: AXI handshakes (`tvalid && tready`) counted in the cycle-accurate Verilator
  simulation of the wrapper, multiplied by the beat size.
* **MODELED**: the Part D prediction `B_meta + (1 - skip_rate) * B_attr` per Gaussian.

The Phase 5a headline (-90.4 % at Q8 / T=16) is still the **MODELED zero-cost skip-decision**
number. It assumes a served Gaussian costs no input bytes at all. The split fetch delivers
the **MEASURED** -21 % to -23 % (benign/fast) and -3 % (adversarial pan) below.

## Field mapping

LSB first. `W = 8 + Q_FRAC` (16 / 20), `ID_W = 12`, `COEF_W = 10W`.

| Channel | TDATA bits | Fields (LSB to MSB) | Bytes Q8 / Q12 |
|---|---|---|---|
| A `s_axis_meta_*` | `12 + 17W` | `pix_y [W-1:0]`, `pix_x0 [2W-1:W]`, `coef [12W-1:2W]`, `ds_y`, `ds_x`, `dmu_y`, `dmu_x`, `t` (W each), `gauss_id [12+17W-1:17W]` | 36 / 44 |
| B `s_axis_attr_*` | `6W` | `opacity [W-1:0]`, `rho`, `s0_y`, `s0_x`, `mu0_y`, `mu0_x [6W-1:5W]` | 12 / 15 |
| Baseline (Phase 5a) | `12 + 23W` | every top input field | 48 / 59 |

* **Channel A contents.** Channel A carries every input of the skip decision: `deform_engine`
  on the order-0 terms plus `coef` and `t`, and `temporal_skip_unit`. It also carries the
  cache tag (`gauss_id`) and the raster-lane pixel origin.
* **`s_axis_attr_tuser`.** This `ID_W`-bit sideband carries the Channel B beat's `gauss_id`.
  The wrapper checks it against the pending metadata id. It is not counted in `B_attr`; it
  would add 1.5 B per attribute beat if it were sent in-band.
* **Unused top inputs.** The wrapper drives the top's Channel B inputs (`in_mu0_*`, `in_s0_*`,
  `in_rho`, `in_opacity`) to 0. In split mode the top takes those fields from `attr_data`.
* **No `tlast`.** One beat is one Gaussian.

## Wrapper FSM

| State | Spec phase | Behaviour |
|---|---|---|
| `W_IDLE` | META | `s_axis_meta_tready = 1`. A beat is latched into `meta_q`, then go to `W_PRESENT`. |
| `W_PRESENT` | DECIDE (before top accept) | `in_valid = 1` with the latched Channel A held stable until `in_ready`, then go to `W_DECIDE`. |
| `W_DECIDE` | DECIDE (top in LOOKUP) | Exactly one cycle. Samples `needs_attr`: 0 (skip + hit) goes to `W_IDLE`; 1 (miss) goes to `W_ATTR`. |
| `W_ATTR` | ATTR | `s_axis_attr_tready = 1`. A beat whose `tuser` matches the id goes to the top as `attr_valid`/`attr_data` in the same cycle, then `W_IDLE`. A mismatching beat is consumed and sets sticky `axi_err`; the wrapper keeps waiting. |

* **Reset.** `W_IDLE` and `axi_err = 0`. `axi_err` is cleared only by reset.
* **Attribute source.** It is reactive: it sends a beat only after it sees
  `s_axis_attr_tready`, at most one per metadata beat, in metadata order.
* **Overlap.** The wrapper accepts the next metadata beat while the top is still computing the
  previous Gaussian. `W_PRESENT` then waits for `in_ready`.

### Top changes (`gs4d_accelerator_top.sv`)

* **New interface.** Parameter `SPLIT_FETCH` (default 0), inputs `attr_valid` and
  `attr_data[6W-1:0]`, output `needs_attr`.
* **`needs_attr`.** It equals `SPLIT_FETCH && state == LOOKUP && !(skip_q && lk_serve)`. It is
  valid for the whole LOOKUP cycle.
* **Miss path.** In split mode a miss goes LOOKUP -> `S_ATTR` (`3'd7`) and waits for
  `attr_valid`. It then latches the Channel B fields, sets
  `rho_q <= fx_add(attr_rho, dr_q)` (`dr_q <= def_dr` is captured at ACCEPT), and loads the
  same miss-path operands LOOKUP loads in legacy mode, now from `attr_data`. It then enters
  PROJ phase 0. The datapath (deform, skip, cache, projection, raster math) is unchanged.
* **Legacy mode.** With `SPLIT_FETCH=0`, LOOKUP goes directly to PROJ, `attr_valid`/`attr_data`
  are ignored and `needs_attr = 0`. Yosys removes `dr_q`.
* **Dead latches in split mode.** The `mu0x_q`/`mu0y_q`/`s0x_q`/`s0y_q`/`opa_q` latches are
  written in `S_ATTR` but never read afterwards, so synthesis removes them. Obligation 4c
  checks their consumers instead (`out_mean_*`, `out_opacity`, `sx_q`, `sy_q`, `proj_a/b`,
  `rho_q`).

## Tier 1: wrapper-driven simulation (`make sim_4dgs_axi`)

The testbench is `rtl_4dgs/sim/sim_main_4dgs_axi.cpp`, driven by `scripts/dse/axi_flow.py sim`.

* **Workload.** The 64 x 64 x 8-frame scenes of `golden_model/gs4d_frame.hpp`, one transaction
  per (Gaussian, 4-pixel span), as in the frame testbench.
* **Configuration.** `CACHE_WAYS=4`, the Phase 5a profiling configuration.
* **Runs.** `{benign, pan, fast} x Q_FRAC {8, 12} x T {0, 1, 16}` (18 runs), plus benign Q8 T=3
  (a Part D row) and a `cache_bypass=1` smoke (Q8 T=16).
* **Stalls.** LFSR-random stalls on metadata `tvalid`, on attribute `tvalid` (reactive, 0-3
  cycles after `tready`), and on `out_ready` (random single-cycle stalls plus bursts of up to
  20 cycles).
* **Checks per run.**
  * Every output is bit-true against `gs4d_ref::Accelerator`.
  * Exactly one metadata beat per Gaussian.
  * Attribute beats equal the golden fetch count (tolerance ±1), at most one per Gaussian.
  * `s_axis_attr_tready` is never high while the pending Gaussian is a golden skip + hit
    (an independent prediction from the golden model).
  * MEASURED bytes equal the MODELED bytes within `B_meta + B_attr`.
  * `axi_err` stays 0. The run lasts at least 10 000 cycles.
* **Result.** All 20 runs pass with 0 mismatches and `axi_err = 0`. MEASURED equals MODELED
  bytes exactly in every run.

### Part D: MODELED prediction vs MEASURED

Benign/fast/pan, `CACHE_WAYS=4`. The MODELED skip rate is the Phase 5a/5b value from the task
table. The MEASURED skip rate is the golden skip fraction of this run, which equals the RTL
`out_skipped` fraction (0 mismatches).

| Scene | Q | T | MODELED skip | MODELED avg B | MODELED reduction | MEASURED skip | MEASURED avg B | MEASURED reduction | Baseline B |
|---|---|---|---|---|---|---|---|---|---|
| benign | Q8 | 1 | 0.842 | 37.89 | 21.1 % | 0.8422 | 37.89 | 21.1 % | 48 |
| benign | Q8 | 3 | 0.868 | 37.59 | 21.7 % | 0.8676 | 37.59 | 21.7 % | 48 |
| benign | Q8 | 16 | 0.904 | 37.15 | 22.6 % | 0.9044 | 37.15 | 22.6 % | 48 |
| benign | Q12 | 1 | 0.826 | 46.61 | 21.0 % | 0.8261 | 46.61 | 21.0 % | 59 |
| benign | Q12 | 16 | 0.852 | 46.22 | 21.7 % | 0.8525 | 46.21 | 21.7 % | 59 |
| pan | Q8 | 1 | 0.120 | 46.56 | 3.0 % | 0.1197 | 46.56 | 3.0 % | 48 |
| fast | Q8 | 1 | 0.855 | 37.74 | 21.4 % | 0.8549 | 37.74 | 21.4 % | 48 |

### All runs (MEASURED AXI beats and bytes)

| Scene | Q | T | Bypass | Gaussians | Meta beats | Attr beats | MEASURED bytes | MEASURED avg B | MEASURED reduction | Cycles | Mismatches |
|---|---|---|---|---|---|---|---|---|---|---|---|
| benign | Q8 | 0 | 0 | 148360 | 148360 | 148360 | 7121280 | 48.00 | 0.00 % | 5639139 | 0 |
| benign | Q8 | 1 | 0 | 148360 | 148360 | 23410 | 5621880 | 37.89 | 21.06 % | 1880412 | 0 |
| benign | Q8 | 16 | 0 | 148360 | 148360 | 14184 | 5511168 | 37.15 | 22.61 % | 1601723 | 0 |
| benign | Q12 | 0 | 0 | 148360 | 148360 | 148360 | 8753240 | 59.00 | 0.00 % | 6829857 | 0 |
| benign | Q12 | 1 | 0 | 148360 | 148360 | 25801 | 6914855 | 46.61 | 21.00 % | 2158563 | 0 |
| benign | Q12 | 16 | 0 | 148360 | 148360 | 21887 | 6856145 | 46.21 | 21.67 % | 2008325 | 0 |
| pan | Q8 | 0 | 0 | 115528 | 115528 | 115528 | 5545344 | 48.00 | 0.00 % | 4393776 | 0 |
| pan | Q8 | 1 | 0 | 115528 | 115528 | 101696 | 5379360 | 46.56 | 2.99 % | 3975878 | 0 |
| pan | Q8 | 16 | 0 | 115528 | 115528 | 101696 | 5379360 | 46.56 | 2.99 % | 3978947 | 0 |
| pan | Q12 | 0 | 0 | 115528 | 115528 | 115528 | 6816152 | 59.00 | 0.00 % | 5316615 | 0 |
| pan | Q12 | 1 | 0 | 115528 | 115528 | 101696 | 6608672 | 57.20 | 3.04 % | 4791449 | 0 |
| pan | Q12 | 16 | 0 | 115528 | 115528 | 101696 | 6608672 | 57.20 | 3.04 % | 4788853 | 0 |
| fast | Q8 | 0 | 0 | 143367 | 143367 | 143367 | 6881616 | 48.00 | 0.00 % | 5452678 | 0 |
| fast | Q8 | 1 | 0 | 143367 | 143367 | 20808 | 5410908 | 37.74 | 21.37 % | 1761969 | 0 |
| fast | Q8 | 16 | 0 | 143367 | 143367 | 20808 | 5410908 | 37.74 | 21.37 % | 1762901 | 0 |
| fast | Q12 | 0 | 0 | 143367 | 143367 | 143367 | 8458653 | 59.00 | 0.00 % | 6597350 | 0 |
| fast | Q12 | 1 | 0 | 143367 | 143367 | 20808 | 6620268 | 46.18 | 21.73 % | 1931313 | 0 |
| fast | Q12 | 16 | 0 | 143367 | 143367 | 20808 | 6620268 | 46.18 | 21.73 % | 1929245 | 0 |
| benign | Q8 | 3 | 0 | 148360 | 148360 | 19650 | 5576760 | 37.59 | 21.69 % | 1767502 | 0 |
| benign | Q8 | 16 | 1 | 148360 | 148360 | 148360 | 7121280 | 48.00 | 0.00 % | 5638864 | 0 |

* **T=0 and bypass.** Every Gaussian fetches both beats: `B_meta + B_attr` = the baseline
  48 / 59 B, a 0 % reduction. Split fetch costs no extra bytes.
* **Cycles.** The cycle counts include the random stalls and are not a throughput figure.

## Tier 2: synthesis and CEC (`make synth_4dgs_axi`)

`scripts/dse/axi_flow.py synth` runs the Phase 5a `gs4d_flow.synth` flow on both designs at
the identical configuration:

* `gs4d_axi_wrapper`: the wrapper plus the top with `SPLIT_FETCH=1`.
* `gs4d_accelerator_top`: the bare top, `SPLIT_FETCH=0`.

The flow is sv2v, Yosys `synth` with the SRAM macro opaque, then ABC `-g AND,OR,XOR,MUX`. CEC
checks `pre.blif` against the mapped and the optimized netlists.

* **Metrics.** All numbers are **generic-cell** Yosys/ABC metrics, not a 130 nm library
  mapping. Logic levels are ABC AIG levels after `aig.abc`.
* **Overhead.** Overhead = (wrapper + top) - bare top. It includes the wrapper FSM and
  `meta_q`, plus the top's `S_ATTR`, `needs_attr` and `dr_q`.
* **What it nets against.** The split mode also removes logic: in split mode the top's
  Channel B input ports are unused (tied 0 by the wrapper) and the dead `mu0x_q`/`s0x_q`/...
  latches are removed. The overhead is net of that.
* **Not comparable to Phase 5a.** Do not compare against the Phase 5a 50 910-gate figure.

**Required configuration: NUM_PE=2, CACHE_WAYS=4, Q_FRAC=12 (local Yosys 0.23).**

| Design | Generic gates | Flip-flops | AIG ANDs | AIG levels | CEC |
|---|---|---|---|---|---|
| `gs4d_accelerator_top` (bare) | 98 643 | 3 997 | 118 508 | 425 | PASS / PASS |
| `gs4d_axi_wrapper` (wrapper + top) | 98 334 | 4 334 | 117 611 | 420 | PASS / PASS |
| Overhead | -309 (-0.31 %) | +337 | -897 | -5 | |

* **Negative gate overhead.** The +337 flip-flops are mostly `meta_q`, plus `dr_q`, `axi_err`
  and the wrapper state. They cost fewer gates than the split mode removes from the top: the
  unused Channel B input ports and their capture logic, the dead `mu0x_q`/`s0x_q`/... latches,
  and the generic-cell mapping effects of that smaller cone.
* **CEC runtime.** At this configuration CEC takes more than the 7 200 s limit of the
  `gs4d_flow` run per design. The ABC mapping takes about 35 min. CEC was therefore rerun
  with ABC `&cec`, on the same `pre.blif`, `mapped.blif` and `optimized.blif`
  converted to AIGER (`read; strash; write_aiger`, then `&r pre.aig; &cec <other>.aig`).
  Both comparisons report "Networks are equivalent" for both designs: 29 min and 104 min for
  the bare top, 102 min and 158 min for the wrapper.

**CI configuration: NUM_PE=1, CACHE_WAYS=2, Q_FRAC=8.**

| Toolchain | Bare top gates / FFs / AIG / levels | Wrapper + top gates / FFs / AIG / levels | Overhead | CEC |
|---|---|---|---|---|
| Local (Yosys 0.23) | 50 639 / 2 279 / 60 769 / 335 | 51 661 / 2 552 / 61 344 / 345 | +1 022 gates (+2.02 %), +273 FFs | PASS / PASS |
| CI (ubuntu-24.04 apt Yosys) | 49 019 / 2 279 / 61 029 / 348 | 49 911 / 2 552 / 61 812 / 351 | +892 gates (+1.82 %), +273 FFs | PASS / PASS |

**Legacy (`SPLIT_FETCH=0`) netlist vs base `af43f4c`.** Same configuration and toolchain:

| | Generic gates | Flip-flops | AIG ANDs | AIG levels |
|---|---|---|---|---|
| Base `af43f4c` | 50 910 | 2 275 | 61 125 | 336 |
| This PR, `SPLIT_FETCH=0` | 50 639 | 2 279 | 60 769 | 335 |

* **Cause of the difference.** The explicit `S_ATTR` case item restructures Yosys's decode of
  the state register. The +4 flip-flops are one extra `state` bit, registered
  `in_ready`/`out_valid` decodes and one internal adder bit. The −271 gates
  (−0.53 %) are mapping noise from that change.
* **Behaviour is unchanged.** `make sim_4dgs` (bit-true, 0 mismatches) and the 45/45
  `formal_4dgs` obligations both pass on this netlist.

## Tier 3: formal (`make formal_4dgs_axi`, plus the `make formal_4dgs` regression)

**Phase 5a regression.** All 45 existing obligations of `scripts/formal/verify_z3_4dgs.py`
were rerun on `SPLIT_FETCH=0`: 45/45 pass, and no obligation was changed or weakened. In
legacy mode the only new state, the `S_ATTR` encoding `3'd7`, is unreachable. The
"FSM closed over 0..6" obligation therefore still holds. `dr_q` has no fanout and is removed,
so the netlist register groups of the non-interference proofs are unchanged.

**New obligations.** `scripts/formal/verify_z3_4dgs_axi.py` runs them on the `gs4d_axi`
netlist: the wrapper composed with the top, `NUM_PE=1, Q_FRAC=4, CACHE_WAYS=2, CACHE_SETS=4,
ID_W=4`.

* **Query form.** Every query is single-step over free inputs and free register values,
  constrained only by the proved inductive invariant J.
* **Circuit breaker.** The same 30 s / 3-retry / COI-slicing / BMC-20 breaker as Phase 5a.
* **Result.** 25/25 pass, every query monolithic and under 1 s.

| # | Obligation | Expect | Result |
|---|---|---|---|
| J | Base at reset, and inductive: `W_DECIDE` <-> LOOKUP, `W_ATTR` <-> ATTR; `W_IDLE`/`W_PRESENT` -> top not in LOOKUP/ATTR; ATTR -> `out_skipped == 0`; LOOKUP/ATTR -> `out_id ==` pending meta id | unsat x2 | pass |
| 1a | `s_axis_attr_tready` -> top in ATTR with `out_skipped == 0` (never on a cache-served transaction) | unsat | pass |
| 1b | DECIDE requests Channel B iff `needs_attr` iff not cache-served (next `out_skipped == 0`, next state != RASTER) | unsat | pass |
| 1c | `s_axis_attr_tready` rises only from DECIDE with `needs_attr == 1` | unsat | pass |
| 2a | `attr_valid` to the top -> `tuser ==` pending meta id `==` top `out_id`; `attr_data ==` the beat | unsat | pass |
| 2b | A mismatching beat is consumed and not forwarded; `axi_err` is set; wrapper and top keep waiting. No beat -> `attr_data == 0` | unsat | pass |
| 2c | `axi_err` is sticky | unsat | pass |
| 3a | A meta beat is latched only in `W_IDLE` (`tready == W_IDLE`), `meta_q == tdata`; otherwise held | unsat | pass |
| 3b | The top accepts only in `W_PRESENT` with `in_id ==` latched id, captured into `out_id`; `W_PRESENT` leaves exactly on that accept | unsat | pass |
| 3c | `out_id` changes only on a top accept. ACCEPT is re-entered only via OUT & `out_ready` (one output per accept); accept -> LOOKUP; `out_valid` iff OUT | unsat | pass |
| 3d | `W_IDLE` is re-entered only on completion (DECIDE & `!needs_attr`, or a matching attr beat) | unsat | pass |
| 4a | `needs_attr` is 0 outside LOOKUP; in LOOKUP it is `!`(cache-served), with next state ATTR or RASTER accordingly | unsat | pass |
| 4b | ATTR without `attr_valid` holds the state and every datapath register | unsat | pass |
| 4c | ATTR & `attr_valid` -> PROJ phase 0, with `rho_q = fx_add(rho, dr_q)`, `out_mean_x/y = fx_add(mu0, dmu)`, `out_opacity = opacity`, `sx_q`/`sy_q`/`proj_a`/`proj_b = fx_add(s0, ds)`, all from the beat | unsat | pass |
| 4d | A cache-served transaction never enters ATTR; ATTR is entered only from LOOKUP with `needs_attr` | unsat | pass |
| 5a | Rank R is non-increasing, equal on stalls, and 0 exactly on completion | unsat | pass |
| 5b | R strictly decreases on meta accept, top accept, the DECIDE branch and attr accept | unsat | pass |
| 5c | Enabledness: `W_IDLE` & meta `tvalid` -> accept; `W_PRESENT` & `in_ready` -> accept; `W_DECIDE` never stalls; `W_ATTR` & `tvalid` & matching `tuser` -> accept | unsat | pass |
| 5d/e | Top control invariant (ATTR legal, Q_FRAC=4 divider): base and inductive | unsat x2 | pass |
| 5f | Top rank: strict decrease in LOOKUP/PROJ/DIV/INV/RASTER; ATTR non-increasing, strict on `attr_valid` | unsat | pass |
| 6a | Witness: a skip + hit DECIDE completes without an attribute request (`cache_bypass = 0`) | sat | pass |
| 6b | Witness: a miss DECIDE requests attributes | sat | pass |
| 6c | Witness: a miss DECIDE is reachable from reset (BMC-3) | sat | pass |

* **Ranking function (deviation, justified).** The task specifies
  `R = 4*(3 - phase) + attr_owed` (12 / 8 / 5 / 0). As written, it is equal on both sides of
  the top-accept handshake inside DECIDE (`W_PRESENT` -> `W_DECIDE`), so "strict decrease on
  every handshake" would fail. The proof therefore adds the analogous unit term `top_owed`
  (1 in `W_PRESENT`): R = 12 (META), 9 (DECIDE, top not yet accepted), 8 (DECIDE, LOOKUP),
  5 (ATTR), 0 (DONE). The phase term still dominates.
* **Liveness argument.** Assume metadata eventually arrives, the top eventually raises
  `in_ready`, and a matching attribute beat eventually arrives when requested. Then 5c gives
  each handshake, and 5a/5b bound the steps. The top-side rank (5f) shows the top itself
  never deadlocks in ATTR, PROJ, DIV, INV or RASTER.
* **Exactly-once, in-order composition argument.** There are two slots: the wrapper's
  `meta_q` and the top's in-flight transaction.
  * 3a/3b: a slot is filled only from the previous slot, in order.
  * 3b/3c: the id is carried unchanged into `out_id`, and each top accept yields exactly one
    output handshake.
  * 3d: the wrapper accepts the next beat only after the current one completed its fetch.
  * Hence the output id sequence equals the accepted metadata id sequence. Tier 1 checks the
    same property concretely (one beat per Gaussian, outputs in scene order, 0 mismatches).
* **Witness scope.** 6a is SAT under J, an inductive over-approximation of the reachable
  states. A skip + hit from reset needs a full recompute first (more than 20 cycles), beyond
  BMC-20. Tier 1 reaches it concretely: 134 176 skip + hit transactions in benign Q8 T=16.
  6c is a true reset-reachability witness.
* **Non-vacuity (mutation check, not in CI).** Two mutants make the obligations fail:
  * Asserting `s_axis_attr_tready` also in `W_DECIDE` fails 1a and 2b.
  * Dropping the tuser check fails 2a and 2b.

## CI

The `axi_split_fetch` job in `.github/workflows/rtl-baseline.yml` has its own 120-minute
timeout. It runs, with `ABC=yosys-abc`:

* `make sim_4dgs_axi`.
* `make formal_4dgs_axi AXI_SYNTH="--num-pe 1 --q-frac 8 --cache-ways 2"`, in funnel order:
  * Tier 2: `synth_4dgs_axi`, wrapper-vs-bare synthesis and CEC at the `synth_4dgs`
    configuration.
  * Tier 3: netlist + Z3.

The baseline job keeps the `make formal_4dgs` 45-obligation regression.

* **Why a reduced CI configuration.** Locally the `NUM_PE=2, CACHE_WAYS=4, Q_FRAC=12`
  Tier 2 takes about 35 min of ABC mapping plus more than 85 min of CEC per design, with the
  two designs in parallel. A first CI run with it in this job would have exceeded the
  120-minute limit, so that configuration is a local `make synth_4dgs_axi` run (the default
  `AXI_SYNTH`).
* **Local runtime of the reduced configuration.** 16 min wall-clock for both designs, CEC
  included.
* **CI runtime.** The `axi_split_fetch` job takes 27 min: tool install about 6 min,
  `sim_4dgs_axi` 2 min, `formal_4dgs_axi` (Tier 2 + Tier 3) 18 min. The baseline job is
  unchanged.

## Out of scope (Phase 5d, deferred)

No ping-pong tile buffer, tile pixel renderer, pixel accumulator, colour path, `m_axis`
pixel output or depth sorter. The golden model and the rendering math are unchanged.
