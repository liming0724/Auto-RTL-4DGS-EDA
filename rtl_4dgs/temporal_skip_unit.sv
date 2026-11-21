// Motion-aware temporal skip decision for one 4DGS Gaussian.
// delta = |dmu_x| + |dmu_y| + |ds_x| + |ds_y| (L1 norm of the temporal deformation
// deltas, raw Q(7,F) LSBs). skip_deform = delta < motion_thresh, so motion_thresh == 0
// never skips (lossless mode). gs4d_accelerator_top uses skip_deform (with the cache
// hit) as the bypass / operand-isolation control of its covariance datapath.
module temporal_skip_unit #(
    parameter int W = 16
) (
    input  logic [W-1:0] dmu_x,
    input  logic [W-1:0] dmu_y,
    input  logic [W-1:0] ds_x,
    input  logic [W-1:0] ds_y,
    input  logic [W+1:0] motion_thresh,
    output logic [W+1:0] delta,
    output logic         skip_deform
);
    // Two's-complement magnitude; -2^(W-1) maps to 2^(W-1), which fits in W unsigned bits.
    function automatic logic [W-1:0] mag(input logic [W-1:0] x);
        return x[W-1] ? (~x + 1'b1) : x;
    endfunction

    // Kept intermediate nets: formal equivalence is proven per stage (magnitudes, two
    // partial sums, final sum) instead of on one 4-operand adder miter.
    (* keep *) logic [W-1:0] mag_dmu_x, mag_dmu_y, mag_ds_x, mag_ds_y;
    (* keep *) logic [W:0]   sum_mu, sum_s;

    assign mag_dmu_x = mag(dmu_x);
    assign mag_dmu_y = mag(dmu_y);
    assign mag_ds_x  = mag(ds_x);
    assign mag_ds_y  = mag(ds_y);
    assign sum_mu = {1'b0, mag_dmu_x} + {1'b0, mag_dmu_y};
    assign sum_s  = {1'b0, mag_ds_x} + {1'b0, mag_ds_y};
    assign delta  = {1'b0, sum_mu} + {1'b0, sum_s};
    assign skip_deform = delta < motion_thresh;
endmodule
