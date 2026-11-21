// Signed Q-format multiply with rounding and overflow handling.
// Bit-true RTL of gs4d::fx_mul (ref_model/include/gs4d/fixed_point.hpp).
//   y = fit_WO( round( a * b * 2^-(FA+FB-FO) ) )
// ROUND: 0 = truncate (floor), 1 = round half up, 2 = round half to even
// SAT:   1 = saturate, 0 = two's-complement wrap
// Requires FA + FB - FO >= 2.

module fx_mul_rs #(
    parameter WA = 20, parameter FA = 16,
    parameter WB = 29, parameter FB = 8,
    parameter WO = 25, parameter FO = 14,
    parameter ROUND = 2, parameter SAT = 1
) (
    input  wire signed [WA-1:0] a,
    input  wire signed [WB-1:0] b,
    output wire signed [WO-1:0] y
);
    wire signed [WA+WB-1:0] p = a * b;
    fx_round_sat #(.WP(WA + WB), .SH(FA + FB - FO), .WO(WO), .ROUND(ROUND), .SAT(SAT)) u_rs (.p(p), .y(y));
endmodule
