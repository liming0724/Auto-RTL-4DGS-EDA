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
GS_RTL := $(GS)/GS_RASTERIZER_pipeline.v $(wildcard $(GS)/SRAM_*.v) $(wildcard $(DW)/DW_fp_*.v)
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

.PHONY: lint lint_ssim lint_gs sim sim_ssim sim_gs synth_baseline cec_baseline
lint: lint_ssim lint_gs

lint_ssim:
	$(VERILATOR) --lint-only $(SSIM_LINT) --top-module ssim_sim_top $(SSIM_RTL)

lint_gs:
	$(VERILATOR) --lint-only $(GS_LINT) $(GS_INC) --top-module GS_RASTERIZER_pipeline $(GS_RTL)

sim: sim_ssim sim_gs

$(BUILD)/ssim/Vssim_sim_top: $(SSIM_RTL) $(SIM)/sim_main_ssim.cpp Makefile
	mkdir -p $(@D)
	+$(VERILATOR) $(VERILATOR_BUILD) $(SSIM_LINT) --top-module ssim_sim_top --Mdir $(@D) $(SSIM_RTL) $(abspath $(SIM)/sim_main_ssim.cpp)

$(BUILD)/gs/VGS_RASTERIZER_pipeline: $(GS_RTL) $(DW)/dw_sub_fp_common.vh $(SIM)/sim_main_gs.cpp $(GS_REF) Makefile
	mkdir -p $(@D)
	+$(VERILATOR) $(VERILATOR_BUILD) $(GS_LINT) $(GS_INC) --top-module GS_RASTERIZER_pipeline --Mdir $(@D) -CFLAGS "-std=c++17 -I$(abspath ref_model/include)" $(GS_RTL) $(abspath $(SIM)/sim_main_gs.cpp)

sim_ssim: lint_ssim $(BUILD)/ssim/Vssim_sim_top
	$(BUILD)/ssim/Vssim_sim_top $(SSIM)/gt.txt $(SSIM)/img.txt $(SIM)/ssim_golden.txt

sim_gs: lint_gs $(BUILD)/gs/VGS_RASTERIZER_pipeline
	cd $(BUILD)/gs && ./VGS_RASTERIZER_pipeline

synth_baseline: sim
	$(PYTHON) scripts/synth/baseline.py synth --yosys "$(YOSYS)" --abc "$(ABC)" --sv2v "$(SV2V)"

cec_baseline: synth_baseline
	$(PYTHON) scripts/synth/baseline.py cec --abc "$(ABC)"
