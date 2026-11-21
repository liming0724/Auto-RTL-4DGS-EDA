// Bit-true RTL of gs4d::requantize / shift_right_round + fit_width
// (ref_model/include/gs4d/fixed_point.hpp).
// Rounding + overflow stage shared by every fixed-point operator:
//   y = fit_WO( round( p * 2^-SH ) ), p a signed WP-bit full-precision value.
// ROUND: 0 = truncate (floor), 1 = round half up, 2 = round half to even
// SAT:   1 = saturate, 0 = two's-complement wrap.   Requires SH >= 2.
module fx_round_sat #(
    parameter WP = 49, parameter SH = 10, parameter WO = 25,
    parameter ROUND = 2, parameter SAT = 1
) (
    input  wire signed [WP-1:0] p,
    output wire signed [WO-1:0] y
);
    localparam WQ = WP - SH;      // width of floor(p / 2^SH)
    localparam WR = WQ + 1;       // one guard bit for the rounding increment

    wire signed [WQ-1:0] q = p[WP-1:SH];
    wire guard  = p[SH-1];
    wire sticky = |p[SH-2:0];

    wire inc = (ROUND == 0) ? 1'b0 :
               (ROUND == 1) ? guard :
                              guard & (sticky | q[0]);

    wire signed [WR-1:0] r = {q[WQ-1], q} + {{(WR-1){1'b0}}, inc};

    // In range iff bits [WR-1:WO-1] are all equal.
    wire [WR-WO:0] top = r[WR-1:WO-1];
    wire in_range = (top == {(WR-WO+1){1'b0}}) | (top == {(WR-WO+1){1'b1}});
    wire [WO-1:0] sat_val = r[WR-1] ? {1'b1, {(WO-1){1'b0}}} : {1'b0, {(WO-1){1'b1}}};

    assign y = (SAT != 0 && !in_range) ? sat_val : r[WO-1:0];
endmodule
