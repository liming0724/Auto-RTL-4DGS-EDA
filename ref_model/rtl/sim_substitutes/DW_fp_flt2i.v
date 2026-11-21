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
// Substitute for DW_fp_flt2i, unsigned result (isign = 0), round to nearest
// even, saturating at 2^isize - 1; NaN and values <= 0 give 0.
/* verilator lint_off UNUSED */
module DW_fp_flt2i #(
    parameter sig_width = 23, parameter exp_width = 8, parameter isize = 32, parameter isign = 1
) (
    input  wire [sig_width+exp_width:0] a,
    input  wire [2:0]                   rnd,
    output reg  [isize-1:0]             z,
    output wire [7:0]                   status
);
/* verilator lint_on UNUSED */
`include "dw_sub_fp_common.vh"
    localparam real MAXV = (2.0 ** isize) - 1.0;
    real r, fl;
    assign status = 8'b0;
    always @* begin
        r = dwsub_f2r(a);
        fl = $floor(r);
        if (dwsub_isnan(a) || !(r > 0.0)) z = {isize{1'b0}};
        else if (r >= MAXV)              z = {isize{1'b1}};
        else begin
            if ((r - fl) > 0.5 || ((r - fl) == 0.5 && ($rtoi(fl) % 2) == 1)) fl = fl + 1.0;
            z = isize'($rtoi(fl));
        end
    end
endmodule
