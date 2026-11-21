// ============================================================================
// SIMULATION-ONLY SUBSTITUTE -- NOT A SYNOPSYS DESIGNWARE ORIGINAL.
// Clean-room behavioral model written from the public IEEE-754 binary32
// definition so GS_RASTERIZER_pipeline.v can be simulated without licensed IP.
// Only the parameter values / ports used by rtl_baseline/gs_rasterizer are
// supported (inst_ieee_compliance = 0: subnormals flushed to zero, rnd = 0:
// round to nearest even). `status` is driven to 0. Not synthesizable (uses
// `real`); never add this directory to a synthesis file list.
// Bit-exactness against the real DesignWare models is NOT verified.
// ============================================================================
// Substitute for DW_fp_i2flt (isize <= 24 so the conversion is exact).
/* verilator lint_off UNUSED */
module DW_fp_i2flt #(
    parameter sig_width = 23, parameter exp_width = 8, parameter isize = 32, parameter isign = 1
) (
    input  wire [isize-1:0]             a,
    input  wire [2:0]                   rnd,
    output reg  [sig_width+exp_width:0] z,
    output wire [7:0]                   status
);
/* verilator lint_on UNUSED */
`include "dw_sub_fp_common.vh"
    assign status = 8'b0;
    always @* begin
        if (isign != 0) z = dwsub_r2f($itor($signed(a)));
        else            z = dwsub_r2f($itor({1'b0, a}));
    end
endmodule
