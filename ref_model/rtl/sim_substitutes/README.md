# Simulation-only DesignWare substitutes

**These files are NOT Synopsys DesignWare originals.** They are clean-room,
behavioral stand-ins for the `DW_fp_*` cells instantiated by
`rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v`, written from the public
IEEE-754 binary32 definition so the baseline can be simulated with Verilator
without licensed IP. Do not request, upload or commit the real DW models here.

| File | Behaviour |
| --- | --- |
| `DW_fp_add.v`, `DW_fp_sub.v`, `DW_fp_mult.v` | binary32, round-to-nearest-even, subnormal inputs/outputs flushed to signed zero |
| `DW_fp_exp.v` | `exp` in binary64, rounded to binary32 (DW accuracy not modelled) |
| `DW_fp_cmp.v` | `agtb/altb/aeqb/unordered` only |
| `DW_fp_i2flt.v`, `DW_fp_flt2i.v` | exact int->float (`isize <= 24`); unsigned float->int, RNE, saturating |

Scope and limits:

* Only `ieee_compliance = 0` and `rnd = 3'b000` (what the baseline uses).
  `status` outputs are tied to 0.
* Not synthesizable (`real` arithmetic). Never add this directory to a
  synthesis/Yosys file list; `ref_model/verif/funnel.py` does not read it.
* **Bit-exactness against the real DesignWare models is not verified.** The
  same testbench (`ref_model/verif/tb_gs_rasterizer.cpp` / `run_rtl_sim.sh`)
  must be re-run against the real DW simulation models in the lab Synopsys
environment to close that gap.

`ssim_fp16.v` is a separate clean-room binary16 model for SSIM's add, sum3,
multiply, and divide instances. It uses round-to-nearest-even and gradual
underflow; operator status is not modeled. It defines some of the same module
names as the GS models and must be compiled in a separate Verilator invocation.
It likewise must not be read by synthesis tools.
