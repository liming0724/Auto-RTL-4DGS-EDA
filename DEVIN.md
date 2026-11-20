# Devin Engineering Team Guardrails & Operating Protocol

This document defines mandatory engineering rules, verification pipelines, and safety circuit-breakers for all Devin Bot instances operating in `Auto-RTL-4DGS-EDA`. **Every bot must read and follow these rules before modifying RTL or EDA scripts.**

---

## 1. Mandatory Tiered Verification Funnel

Never invoke the Z3 SMT solver directly on newly generated or unverified SystemVerilog/Verilog code. You MUST execute verification in strict ascending order of computational cost:

1. **Step 1: Lint & Cycle-Accurate Functional Simulation (Verilator)**
   * Compile modified RTL with `verilator --lint-only -Wall` first; resolve all width mismatch and latch warnings.
   * Run at least `10,000` cycles of constrained-random + directed edge-case vectors comparing RTL signals bit-true against the C++ golden model.
   * **Gate**: Do NOT proceed to Step 2 until functional simulation passes with `0` mismatches.

2. **Step 2: Logic Synthesis & Structural Equivalence (Yosys + Berkeley ABC)**
   * Synthesize both the reference baseline RTL and the modified/optimized RTL using `yosys`.
   * Run Combinational Equivalence Checking (`cec`) via `berkeley-abc` to verify structural functional equivalence and extract gate area / critical path delay metrics.

3. **Step 3: Corner-Case Formal Proof (Z3 SMT Solver via `eda_core/`)**
   * Invoke Z3 **only** on isolated critical sub-modules: fixed-point rounding/saturation boundaries (`fp_operation.v`, `ssim_custom_shift_safe.v`), cache tag comparators, and control FSM transitions.

---

## 2. Counter-Example Guided Repair (CEGAR) Protocol

When Z3 returns `SAT` (indicating a property violation or non-equivalence) or ABC `cec` reports a mismatch:
1. **Do NOT guess or randomly perturb RTL logic.**
2. Extract the concrete failing input vector (counter-example witness) produced by the solver.
3. Inject that exact input vector into the Verilator testbench (`TB.v` / `TB.sv`) and dump waveform traces (`.vcd` / `.fst`) to pinpoint the exact clock cycle and bit index where divergence occurs.
4. Patch the specific datapath or control condition responsible for the divergence, then restart from **Step 1**.

---

## 3. Solver Timeout & Retry Circuit-Breakers

To prevent infinite debug loops and runaway compute consumption during Design Space Exploration (DSE):
* **Per-Query Solver Timeout**: Enforce a hard `30-second` timeout on every Z3 SMT invocation.
* **Max Consecutive Retries**: Allow at most **`3` consecutive automated repair attempts** on the same sub-module.
* **Mandatory Fallback Actions (Upon Hitting Timeout or 3 Failures)**:
  1. **Cone-of-Influence (COI) Slicing**: Use `eda_core/parser.py` to slice the failing output bit backward through its fanin cone and verify smaller combinational sub-cones independently.
  2. **Bounded Model Checking (BMC)**: If full unbounded proof still times out after COI slicing, downgrade verification to Bounded Model Checking with depth `k = 20` cycles, document the solver bottleneck in the PR description, and flag the module for human review.

---

## 4. Repository Hygiene Rules

* **Never commit binary simulation dumps or tool artifacts**: Do not `git add` `*.fsdb`, `*.vcd`, `*.fst`, `simv*`, `csrc/`, `nWaveLog/`, `novas*`, or compiled `.so` shared libraries. Keep `.gitignore` clean.
* **Never commit `config.yaml`**: It holds your GCP project ID and local Gemini / Vertex AI settings. Create it locally from the template in `README.md`.
* **Preserve Bit-True Interfaces**: When parameterizing `rtl_baseline/` modules for DSE sweeps, maintain backward compatibility with existing top-level port names and testbench stimulus files (`gt.txt`, `img.txt`).
