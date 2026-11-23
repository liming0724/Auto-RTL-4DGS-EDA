VERILATOR ?= verilator
YOSYS ?= yosys
SV2V ?= sv2v
ABC ?= $(or $(shell command -v berkeley-abc),$(shell command -v abc),yosys-abc)
PYTHON ?= python3

BUILD := $(abspath build/baseline)
SIM := rtl_baseline/sim
SSIM := rtl_baseline/ssim_eval
GS := rtl_baseline/gs_rasterizer
DW := ref_model/rtl/sim_substitutes
SSIM_RTL := $(DW)/ssim_fp16.v $(addprefix $(SSIM)/,SSIM_v2.v ssim_custom_shift_safe.v fp_operation.v SRAM_IMG_GT.v SRAM_other.v) $(SIM)/ssim_sim_top.sv
GS_RTL := $(GS)/GS_RASTERIZER_pipeline.v $(wildcard $(GS)/SRAM_*.v) $(wildcard $(DW)/DW_fp_*.v) $(SIM)/gs_sim_top.sv
GS_REF := $(wildcard ref_model/include/gs4d/*.hpp)
GS_INC := -I$(DW)
VERILATOR_VERSION := $(word 2,$(shell $(VERILATOR) --version))
LINT := -Wall -Wno-DECLFILENAME -Wno-UNUSED
ifeq ($(filter 4.%,$(VERILATOR_VERSION)),)
LINT += -Wno-EOFNEWLINE
endif
SSIM_LINT := $(LINT)
GS_LINT := $(LINT) -Wno-CASEINCOMPLETE
VERILATOR_BUILD := --cc --exe --build -O2

FORMAL_TOOLS = --yosys "$(YOSYS)" --sv2v "$(SV2V)" --abc "$(ABC)"

.PHONY: lint lint_ssim lint_gs sim sim_ssim sim_gs synth_baseline cec_baseline build_eda formal_netlists formal_z3 test_cegar \
	sim_4dgs synth_4dgs formal_4dgs dse_4dgs sim_4dgs_frame eval_quality_4dgs profile_bw_4dgs stress_4dgs conic_cliff_4dgs \
	sim_4dgs_axi synth_4dgs_axi formal_4dgs_axi
lint: lint_ssim lint_gs

lint_ssim:
	$(VERILATOR) --lint-only $(SSIM_LINT) --top-module ssim_sim_top $(SSIM_RTL)

lint_gs:
	$(VERILATOR) --lint-only $(GS_LINT) $(GS_INC) --top-module gs_sim_top $(GS_RTL)

sim: sim_ssim sim_gs

$(BUILD)/ssim/Vssim_sim_top: $(SSIM_RTL) $(SIM)/sim_main_ssim.cpp Makefile
	mkdir -p $(@D)
	+$(VERILATOR) $(VERILATOR_BUILD) $(SSIM_LINT) --top-module ssim_sim_top --Mdir $(@D) $(SSIM_RTL) $(abspath $(SIM)/sim_main_ssim.cpp)

$(BUILD)/gs/Vgs_sim_top: $(GS_RTL) $(DW)/dw_sub_fp_common.vh $(SIM)/sim_main_gs.cpp $(GS_REF) Makefile
	mkdir -p $(@D)
	+$(VERILATOR) $(VERILATOR_BUILD) $(GS_LINT) $(GS_INC) --top-module gs_sim_top --Mdir $(@D) -CFLAGS "-std=c++17 -I$(abspath ref_model/include)" $(GS_RTL) $(abspath $(SIM)/sim_main_gs.cpp)

sim_ssim: lint_ssim $(BUILD)/ssim/Vssim_sim_top
	$(BUILD)/ssim/Vssim_sim_top $(SSIM)/gt.txt $(SSIM)/img.txt $(SIM)/ssim_golden.txt

sim_gs: lint_gs $(BUILD)/gs/Vgs_sim_top
	cd $(BUILD)/gs && ./Vgs_sim_top

synth_baseline: sim
	$(PYTHON) scripts/synth/baseline.py synth --yosys "$(YOSYS)" --abc "$(ABC)" --sv2v "$(SV2V)"

cec_baseline: synth_baseline
	$(PYTHON) scripts/synth/baseline.py cec --abc "$(ABC)"

build_eda:
	$(MAKE) -C eda_core PYTHON="$(PYTHON)" OUT="$(abspath build/eda)"

# Netlisting waits for the tier 1-2 funnel (lint, simulation, synthesis, CEC), also under -j.
formal_netlists: cec_baseline build_eda
	$(PYTHON) scripts/formal/rtl_to_netlist.py --check $(FORMAL_TOOLS)

formal_z3: formal_netlists
	$(PYTHON) scripts/formal/verify_z3.py
	$(PYTHON) scripts/formal/verify_z3.py --drill

test_cegar: formal_z3 $(BUILD)/ssim/Vssim_sim_top
	$(PYTHON) scripts/formal/cegar_to_verilator.py $(FORMAL_TOOLS) --verilator "$(VERILATOR)" \
		--lint-flags "$(SSIM_LINT)" --golden-bin $(BUILD)/ssim/Vssim_sim_top

# ---------------------------------------------------------------- Phase 3: rtl_4dgs
G4_BUILD := $(abspath build/4dgs)
G4_TOOLS = --verilator "$(VERILATOR)" --yosys "$(YOSYS)" --sv2v "$(SV2V)" --abc "$(ABC)"
DSE_JOBS ?= 4

$(G4_BUILD)/4dgs_ref: golden_model/4dgs_ref.cpp golden_model/4dgs_ref.hpp $(GS_REF)
	mkdir -p $(@D)
	$(CXX) -std=c++17 -O2 -Wall -Wextra -Iref_model/include -Igolden_model -o $@ $<

# Tier 1: golden-model self-check, Verilator -Wall lint, >= 20k-cycle bit-true simulation.
sim_4dgs: $(G4_BUILD)/4dgs_ref
	$(G4_BUILD)/4dgs_ref --sweep > $(G4_BUILD)/golden_sweep.csv
	$(G4_BUILD)/4dgs_ref --thresh 16 --trace-dir $(G4_BUILD)
	$(PYTHON) scripts/dse/gs4d_flow.py sim $(G4_TOOLS)

# Tier 2: Yosys/ABC generic-cell synthesis + ABC CEC of gs4d_accelerator_top.
synth_4dgs: sim_4dgs
	$(PYTHON) scripts/dse/gs4d_flow.py synth $(G4_TOOLS)

# Tier 3: Z3 proofs through eda_core (30 s breaker, COI slicing, BMC-20).
formal_4dgs: synth_4dgs build_eda
	$(PYTHON) scripts/formal/rtl_to_netlist.py gs4d_skip_unit gs4d_cache gs4d_deform gs4d_top --check $(FORMAL_TOOLS)
	$(PYTHON) scripts/formal/verify_z3_4dgs.py

dse_4dgs: $(G4_BUILD)/4dgs_ref
	$(PYTHON) scripts/dse/run_dse_sweep.py $(G4_TOOLS) --jobs $(DSE_JOBS)

# Phase 4: 64x64 x 8-frame FP32-vs-fixed validation and SSIM_v2 frame scoring.
sim_4dgs_frame:
	$(PYTHON) scripts/dse/quality_sweep.py frame --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Phase 4/5a: quality-vs-skip sweep, Q_FRAC {8,12} x MOTION_THRESH {0,1,2,3,4,8,16,32,64}.
eval_quality_4dgs:
	$(PYTHON) scripts/dse/quality_sweep.py sweep --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Phase 5a: bandwidth profiler (Modeled External DRAM Traffic), Q_FRAC {8,12} x
# {Baseline, Skip-Only, Full Co-Design T=1/3/16} -> build/4dgs/bandwidth/bandwidth_profile.{csv,json,md}.
profile_bw_4dgs:
	$(PYTHON) scripts/dse/quality_sweep.py profile --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Phase 5b: stress scenes (benign / pan / fast) x Q_FRAC {8,12} on the profiler build, with
# miss-penalty Pareto tables (effective cycles, no-overlap bound) -> build/4dgs/stress/.
stress_4dgs:
	$(PYTHON) scripts/dse/quality_sweep.py stress --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Phase 5b: small-determinant conic characterization through the RTL -> build/4dgs/conic/.
conic_cliff_4dgs:
	$(PYTHON) scripts/dse/quality_sweep.py conic --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Phase 5c: split-fetch AXI4-Stream wrapper. Tier 1: lint + 18 wrapper-driven runs (scene x
# Q_FRAC x T {0,1,16}) + Q8 T=3 + cache_bypass smoke with LFSR stalls; MEASURED AXI beats vs
# MODELED prediction -> build/4dgs/axi/axi_bandwidth.{csv,json,md}.
sim_4dgs_axi:
	$(PYTHON) scripts/dse/axi_flow.py sim --verilator "$(VERILATOR)" --jobs $(DSE_JOBS)

# Tier 2: generic-cell synthesis + CEC of the wrapper and the bare top at the same configuration,
# by default NUM_PE=2, CACHE_WAYS=4, Q_FRAC=12 (about 2.5 h of ABC + CEC; CI overrides AXI_SYNTH
# with the NUM_PE=1, CACHE_WAYS=2, Q_FRAC=8 synth_4dgs configuration to fit its 120-minute limit).
AXI_SYNTH ?= --num-pe 2 --q-frac 12 --cache-ways 4
synth_4dgs_axi:
	$(PYTHON) scripts/dse/axi_flow.py synth $(AXI_SYNTH) $(G4_TOOLS)

# Tier 3 (after Tier 2, as in DEVIN.md): composed wrapper + top (Q_FRAC=4) Z3 obligations
# (30 s breaker, COI slicing, BMC-20).
formal_4dgs_axi: synth_4dgs_axi build_eda
	$(PYTHON) scripts/formal/rtl_to_netlist.py gs4d_axi --check $(FORMAL_TOOLS)
	$(PYTHON) scripts/formal/verify_z3_4dgs_axi.py
