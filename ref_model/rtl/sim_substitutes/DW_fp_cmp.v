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
// Substitute for DW_fp_cmp (only the agtb/altb/aeqb/unordered outputs).
/* verilator lint_off UNUSED */
module DW_fp_cmp #(
    parameter sig_width = 23, parameter exp_width = 8, parameter ieee_compliance = 0
) (
    input  wire [sig_width+exp_width:0] a,
    input  wire [sig_width+exp_width:0] b,
    output wire                         agtb,
    output wire                         altb,
    output wire                         aeqb,
    output wire                         unordered
);
/* verilator lint_on UNUSED */
`include "dw_sub_fp_common.vh"
    wire un = dwsub_isnan(a) || dwsub_isnan(b);
    real ra, rb;
    always @* begin
        ra = dwsub_f2r(a);
        rb = dwsub_f2r(b);
    end
    assign unordered = un;
    assign agtb = !un && (ra > rb);
    assign altb = !un && (ra < rb);
    assign aeqb = !un && (ra == rb);
endmodule
