# Auto-RTL-4DGS-EDA: Algorithm-Hardware Co-Design & Automated Formal Verification Framework

An open-source hardware Design Space Exploration (DSE) and automated formal verification framework targeting **4D Gaussian Splatting (4DGS)** hardware acceleration. This repository integrates high-speed C++/Python EDA automation (**Yosys**, **Berkeley ABC**, **Z3 SMT Solver**) with cycle-accurate **Verilator** simulation and synthesizable SystemVerilog/Verilog RTL baselines.

---

## Repository Architecture

```text
Auto-RTL-4DGS-EDA/
├── README.md                     # Project overview, architecture, and verification flow
├── DEVIN.md                      # Deterministic guardrails & CI rules for Devin engineering bots
├── LICENSE                       # MIT License
├── .gitignore                    # Ignores config.yaml, simulation dumps, and build artifacts
├── eda_core/                     # Automated netlist optimization & formal verification engine
│   ├── agent.py                  # CEGAR-driven optimization agent with circuit-breaker logic
│   ├── parser.py                 # Structural netlist parser & Cone-of-Influence (COI) extractor
│   └── fast_eda.cpp              # C++17 Pybind11 backend for fast graph traversal & solver calls
└── rtl_baseline/                 # Synthesizable 3DGS hardware accelerator RTL & testbenches
    ├── ssim_eval/                # Pipelined fixed-point SSIM hardware evaluation unit
    │   ├── SSIM_v2.v             # Top-level pipelined SSIM datapath
    │   ├── ssim_custom_shift_safe.v # Overflow-safe fixed-point bit-shift arithmetic
    │   ├── fp_operation.v        # Fixed-point arithmetic operators
    │   ├── SRAM_IMG_GT.v         # Ground-truth image SRAM behavioral model
    │   ├── SRAM_other.v          # Intermediate feature map SRAM behavioral model
    │   ├── TB.v                  # Cycle-accurate Verilog testbench
    │   ├── gt.txt                # Reference ground-truth stimulus vectors
    │   └── img.txt               # Input rendered image stimulus vectors
    └── gs_rasterizer/            # Sorted 3DGS rasterizer pipeline baseline
        ├── GS_RASTERIZER_pipeline.v # Multi-stage pipelined Gaussian rasterization core
        ├── SRAM_GaussianValueDataBase.v # Gaussian value database SRAM behavioral model
        ├── SRAM_OutputPixel.v    # Output pixel buffer SRAM behavioral model
        ├── SRAM_SortedGaussianBall.v # Sorted Gaussian primitive buffer model
        └── TB.sv                 # SystemVerilog testbench environment
```

---

## Core Capabilities

### 1. Automated EDA & Formal Verification (`eda_core/`)
* **High-Speed Netlist Manipulation (`fast_eda.cpp`)**: C++17 backend exposed via `pybind11` to accelerate AIG/netlist traversal and fanin/fanout cone extraction without Python interpreter overhead.
* **CEGAR Verification Loop (`agent.py`)**: Integrates **Yosys**, **Berkeley ABC**, and **Z3** to verify logic optimizations and RTL transformations. Automatically parses `SAT` counter-examples into cycle-level simulation stimuli.

### 2. Synthesizable Hardware Baselines (`rtl_baseline/`)
* **Pipelined Fixed-Point SSIM Evaluator (`ssim_eval/`)**: Hardware-efficient Structural Similarity Index (SSIM) engine featuring custom overflow-safe shifting (`ssim_custom_shift_safe.v`) and dual-SRAM buffering (`SRAM_IMG_GT.v`, `SRAM_other.v`) for real-time on-chip rendering loss evaluation.
* **Pipelined Gaussian Rasterizer (`gs_rasterizer/`)**: Multi-stage pipelined rasterization architecture (`GS_RASTERIZER_pipeline.v`) designed to be extended toward spatiotemporal 4DGS motion-aware caching and parallel PE array exploration.

---

## Three-Tier Verification Funnel

To prevent formal solver timeouts and state explosion during automated DSE, all RTL modifications pass through a strict deterministic verification funnel:

1. **Tier 1 — Fast Functional Simulation (Verilator)**
   * Runs constrained-random and directed stimulus vectors (`10,000+` cycles) comparing RTL outputs bit-for-bit against the C++ fixed-point golden model.
2. **Tier 2 — Structural Equivalence Checking (Berkeley ABC)**
   * Synthesizes pre- and post-optimization RTL via Yosys and executes Combinational Equivalence Checking (`cec`) in Berkeley ABC.
3. **Tier 3 — SMT Formal Proof & COI Slicing (Z3 Solver)**
   * Targets arithmetic corner cases (fixed-point rounding boundaries, shift overflows, FSM deadlock states) using Z3 with strict timeout circuit-breakers and Cone-of-Influence (COI) decomposition.

---

## Quick Start

### Build the C++ EDA Extension
```bash
cd eda_core
c++ -O3 -Wall -shared -std=c++17 -fPIC $(python3 -m pybind11 --includes) fast_eda.cpp -o fast_eda$(python3-config --extension-suffix)
```

### Run the RTL Baseline
```bash
make lint
make -j2 sim             # or sim_ssim / sim_gs
make -j2 cec_baseline    # runs simulation, generic synthesis, then ABC CEC
```

See [Baseline metrics](docs/BASELINE_METRICS.md) for setup, expected results, and
which IP and SRAM boundaries are excluded from the generic metrics.

### Configure the EDA Agent

`eda_core/agent.py` reads its Gemini / Vertex AI settings from a local `config.yaml` (project ID, location, model). This file is **not** included in the repository and is ignored by `.gitignore` — create your own locally and never commit it:

```yaml
gemini:
  project_id: "your-gcp-project-id"
  location: "us-central1"
  model: "gemini-2.5-flash"
generation:
  temperature: 0.2
```

---

## Roadmap for Autonomous DSE
- [x] Transpile 4DGS spatiotemporal deformation and rasterization kernels into a bit-true C++ fixed-point reference model. See [`ref_model/`](ref_model/README.md); plan for the next two items in [`docs/ROADMAP_PLAN.md`](docs/ROADMAP_PLAN.md).
- [ ] Parameterize `GS_RASTERIZER_pipeline.v` across PE array widths, Q-format bit-widths, and motion-aware cache associativity.
- [ ] Execute parallel Area-Delay Pareto frontier sweeps via Yosys + ABC + Z3 verification loops.
