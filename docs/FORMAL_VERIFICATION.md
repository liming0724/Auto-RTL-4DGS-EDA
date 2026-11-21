# Formal Verification (Tier 3): Z3, COI Slicing and CEGAR

Phase 2 connects `eda_core/` to the `rtl_baseline/` netlists. Tier 3 runs only after
tiers 1 and 2: netlisting (`formal_netlists`) depends on `cec_baseline`, also under `-j`, which runs lint,
Verilator simulation, Yosys/ABC synthesis and ABC CEC first (`DEVIN.md`).

```bash
make build_eda     # fast_eda pybind11 extension -> build/eda/ (-O3 -std=c++17 -fPIC)
make formal_z3     # netlists + Z3 obligations + circuit-breaker drill
make test_cegar    # injected faults -> Z3 SAT -> cegar_trace.hex -> Verilator FAIL
```

Requirements: Yosys >= 0.23 (Ubuntu 24.04 ships 0.33; 22.04's 0.9 lacks `dffunmap`),
sv2v 0.0.12, Berkeley ABC or `yosys-abc`, Verilator, and Python with `z3-solver` and
`pybind11`. Pass tool paths as in the baseline, e.g. `make formal_z3 YOSYS=... SV2V=...`.
All output goes to the ignored `build/eda/` and `build/formal/`.

## Data flow

```
rtl_baseline RTL --sv2v/yosys--> primitive-gate .v + .blif + manifest.json   (scripts/formal/rtl_to_netlist.py)
                                   |
             eda_core/parser.py VerilogParser -> Design -> LogicAnalyzer.build_z3_expr  (Z3 Bool per net)
             eda_core/fast_eda FastGraph.cone_of_influence(roots) -> cone nets + leaves (COI slices)
                                   |
             scripts/formal/z3_engine.py   Breaker: 30 s, 3 attempts, COI slicing, BMC k=20
                     |                                   |
   scripts/formal/verify_z3.py                scripts/formal/cegar_to_verilator.py
   (property/equivalence proofs)              (fault -> SAT -> trace -> Verilator replay)
```

**Netlist bridge.** `rtl_to_netlist.py` runs `synth -flatten -noabc -nofsm`,
`setundef -zero`, `dffunmap` and `abc -g AND,NAND,OR,NOR,XOR,XNOR`, then rewrites the
Yosys JSON as one scalar net per bit using only `and/or/nand/nor/xor/xnor/not/buf`,
`assign` and `dff (.C, .R, .D, .Q)`, which `VerilogParser` reads without
pre-processing (`--check` loads every netlist into the parser and `FastGraph`).
`-nofsm` keeps register names, so properties refer to RTL names (`state`,
`stage_valid_3`, `row1_14`); the manifest maps them to bits and records each
register's D net and async reset value. DesignWare and SRAM cells stay opaque, as in
the Tier 2 flow (`scripts/synth/dw_interfaces.v`, `gs_sram_interfaces.v`): their
outputs become free inputs, so GS control proofs hold for any datapath value.
`FastGraph.cone_of_influence` is the one new method in `fast_eda.cpp`.

| Netlist (generic cells, Yosys 0.23) | Gates | DFFs | Opaque cells |
|---|---:|---:|---:|
| `fp16_div8` | 108 | 0 | 0 |
| `fp16_mult2` | 74 | 0 | 0 |
| `ssim_custom_shift_safe` | 12,593 | 2,269 | 0 |
| `GS_RASTERIZER_pipeline` | 8,286 | 2,729 | 48 |

These are generic two-input gate counts for formal work, not the project's 130 nm
figures.

## Circuit breakers (`z3_engine.Breaker`)

1. Every obligation is first one **monolithic** query: the OR of all its per-output-bit
   violations.
2. Each query has `solver.set("timeout", 30000)`. An `unknown` is retried with a
   different seed/solver, **at most 3 consecutive attempts** per cone.
3. After 3 unknowns, **COI slicing**: per output bit, `FastGraph` slices the
   backward cone (logged as nets/leaves) and that bit's violation is rebuilt from
   only that cone and solved alone.
4. A slice that is still unknown after 3 attempts is logged as `BOTTLENECK` and
   **downgraded to BMC, k = 20**: the design is unrolled from reset over the slice's
   sequential COI. A clean BMC result is reported as `BOUNDED`, not as a proof.

None of the real obligations needs the fallback today, so `make formal_z3` also runs
`verify_z3.py --drill`. The drill sets a Z3 resource limit that forces every
monolithic GS query and the `stage_valid_5_next` slice to `unknown`, so the whole
path runs on each build (log: `build/formal/breaker_drill.log`):

```
    query monolithic attempt 3/3: unknown (max. resource limit exceeded) in 0.00s
  monolithic query unknown after 3 attempts -> COI slicing per output bit
  slice stage_valid_5_next[0]: ... nets, ... leaves (fast_eda COI)
  BOTTLENECK slice stage_valid_5_next[0] (... nets) still unknown -> BMC k=20
    query bmc-20 stage_valid_5_next[0] attempt 1/3: unsat
  pass (bounded)  gs_rasterizer: stage-valid propagation ... -> BOUNDED via COI slices + BMC-20
```

## Obligations and results (`build/formal/formal_z3.log`, `.json`)

Each obligation states its expected result. `known deviation` is a SAT the harness
expects because it matches documented legacy RTL behaviour; the RTL was not
changed. If someone fixes that behaviour, the result stops matching and the run fails
until the expectation is updated.

| Target | Obligation | Result |
|---|---|---|
| `fp16_div8` | equals `reference_div8` (sim_main_ssim.cpp) on all 2^16 inputs, 16 bit cones | UNSAT (proved) |
| `fp16_div8` | no magnitude wrap: \|x/8\| <= \|x\| for finite x | UNSAT (proved) |
| `fp16_div8` | sign kept for finite non-zero results | SAT, known deviation: exponents 1-3 drop the sign (e.g. `8a30`) |
| `fp16_mult2` | equals bit-true reference of the RTL | UNSAT (proved) |
| `fp16_mult2` | no silent wrap: \|2x\| > \|x\| for finite non-zero x | SAT, known deviation: exp 30 (incl. `MAX_VAL 7bff`, `MIN_VAL fbff`) flushes to +0; a subnormal with mantissa MSB set loses it (e.g. `79fe`) |
| `fp16_mult2` | sign kept for finite non-zero results | UNSAT (proved) |
| `fp16_*` | 15 corner words: +/-0, min/max subnormal, exp 1/2/3/4, -min normal, `MAX_VAL`, `MIN_VAL`, exp 30, +/-Inf, NaN | all match the reference |
| `ssim_custom_shift_safe` | every next-state bit of all 2,269 registers (counter, window valid, taps, row buffers) equals the bit-true Python spec `shift_spec` | UNSAT (proved), about 3 s |
| GS | `state_next` equals the RTL case statement | UNSAT (proved) |
| GS | FSM closed over legal states 0..5 | UNSAT (proved) |
| GS | `stage_valid_i' = active & stage_valid_{i-1}`, `stage_valid_0' = active` | UNSAT (proved) |
| GS | inductive: `state' == PROCESS -> stage_valid_0'` | UNSAT (proved) |
| GS | inductive: valid bits form a bubble-free prefix | UNSAT (proved) |
| GS | deadlock-free PROCESS: with `stage_valid_0`, staying in PROCESS means `addr0 != 1099` and `addr0' = addr0 + 1` | UNSAT (proved) |
| GS | deadlock-free frame: `addr2` advances only in WRITE_RESULT, by one, and 2550 goes to COMPLETE | UNSAT (proved) |
| GS | COMPLETE absorbing and `Finish == (state == COMPLETE)` | UNSAT (proved) |
| GS | BMC-20 from reset: `Finish` low, state legal | UNSAT for 20 cycles |

The whole run is 16 obligations, 16 Z3 queries and about 8 s wall time on the
development VM (Z3 4.x/5.x Python bindings). The drill adds about 1 s.

**GS deadlock freedom.** The GS top has no ready/valid handshake. Its control
protocol is the FSM, the 11 `stage_valid` bits and `Finish`. Reset puts the FSM in
IDLE. The two inductive invariants hold at reset and are preserved each cycle, so
PROCESS always has `stage_valid_0`. Then `(1099 - addr0) mod 2048` drops by one each
PROCESS cycle, so PROCESS exits within 2048 cycles. Every other state moves on in one
cycle, and `(2550 - addr2) mod 4096` drops by one per pixel, so COMPLETE (`Finish`)
is always reached. The obligations are not vacuous: a deliberately false variant
(for example `state' == PROCESS -> stage_valid_1'`) returns SAT.

## CEGAR loop (`scripts/formal/cegar_to_verilator.py`)

`--inject-fault` copies `ssim_custom_shift_safe.v` to `build/formal/cegar/<fault>/rtl/`
and injects one off-by-one boundary bug (default: both):

| Fault | Mutation | Z3 (next-state proof) | BMC miter port | Verilator replay |
|---|---|---|---|---|
| `valid_boundary` | `counter > 17` -> `counter >= 17` | SAT, `out_valid_next` at counter 17 | `out_valid` (22 regs in COI) | golden PASS, faulty FAIL at cycle 19 |
| `shift_boundary` | `counter < 14` -> `counter <= 14` | SAT, `row1_*_next` at counter 14 | `out_p10` (2,136 regs in COI) | golden PASS, faulty FAIL at cycle 15 |

Steps:

1. Netlist the faulty copy and run the same next-state proof under the breakers. It
   must be SAT, and the failing cones are logged.
2. Build a golden-vs-faulty miter, unroll it from reset for k = 20, and run one Z3
   query per output port, cut to that port's sequential COI. Ports in the failing
   cones go first. The SAT model gives `shift_valid` and the 48-bit pixel for each
   cycle.
3. Write `cegar_trace.hex`, one line per cycle:
   `cycle shift_valid pixel window_valid p00 .. p22` (hex). The expected outputs come
   from a cycle-accurate simulation of the golden netlist, which also predicts the
   first failing cycle.
4. Replay with the existing testbench, `Vssim_sim_top --cegar cegar_trace.hex`
   (`rtl_baseline/sim/sim_main_ssim.cpp`). The golden binary from `make sim` must
   print `CEGAR PASS`. A Verilator build with the faulty copy must print
   `CEGAR FAIL: cycle N`, with N equal to the predicted cycle. The faulty build uses
   `--trace` and writes `build/formal/cegar/<fault>/cegar_faulty.vcd` up to the failing
   cycle, so the divergence can be checked at the signal level (`DEVIN.md` CEGAR step 3).

`make test_cegar` takes about 3 minutes, most of it the faulty Verilator build.

## Limits

* Proofs are against the netlist from Yosys, not the RTL text. Tier 2 CEC and Tier 1
  simulation cover that step, and the CEGAR replay cross-checks it in Verilator.
* DesignWare and SRAM behaviour is out of scope (opaque cuts). Licensed DesignWare
  models are never used; simulation uses `ref_model/rtl/sim_substitutes/`.
* `eda_core/agent.py` is not imported by this flow. Importing it needs the `httpx`
  and `google-genai` packages, which the formal flow does not install; it creates its
  Gemini client only at run time, not at import. No cloud credentials are used.
