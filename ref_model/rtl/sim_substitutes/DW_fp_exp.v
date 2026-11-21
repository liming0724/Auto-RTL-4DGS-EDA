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
// Substitute for DW_fp_exp: z = exp(a), evaluated in binary64 and rounded
// to binary32 (the real DW_fp_exp accuracy/rounding is not modelled).
/* verilator lint_off UNUSED */
module DW_fp_exp #(
    parameter sig_width = 23, parameter exp_width = 8, parameter ieee_compliance = 0, parameter arch = 2
) (
    input  wire [sig_width+exp_width:0] a,
    output reg  [sig_width+exp_width:0] z,
    output wire [7:0]                   status
);
/* verilator lint_on UNUSED */
`include "dw_sub_fp_common.vh"
    assign status = 8'b0;
    always @* z = dwsub_r2f($exp(dwsub_f2r(a)));
endmodule
