// Independent golden formulation of fx_round_sat for equivalence checking:
// rounding by bias-then-arithmetic-shift and saturation by signed compare.
module fx_round_sat_spec #(
    parameter WP = 49, parameter SH = 10, parameter WO = 25,
    parameter ROUND = 2, parameter SAT = 1
) (
    input  wire signed [WP-1:0] p,
    output wire signed [WO-1:0] y
);
    localparam WE = WP + 2;

    wire signed [WE-1:0] pe = p;
    wire signed [WE-1:0] half_m1 = (64'sd1 <<< (SH - 1)) - 64'sd1;
    wire signed [WE-1:0] bias = (ROUND == 0) ? {WE{1'b0}} :
                                (ROUND == 1) ? half_m1 + 1 :
                                               half_m1 + {{(WE-1){1'b0}}, pe[SH]};
    wire signed [WE-1:0] s = (pe + bias) >>> SH;

    wire signed [WE-1:0] maxv = (64'sd1 <<< (WO - 1)) - 64'sd1;
    wire signed [WE-1:0] minv = -(64'sd1 <<< (WO - 1));
    wire signed [WE-1:0] c = (SAT == 0) ? s : (s > maxv) ? maxv : (s < minv) ? minv : s;
    assign y = c[WO-1:0];
endmodule

module fx_mul_rs_spec #(
    parameter WA = 20, parameter FA = 16,
    parameter WB = 29, parameter FB = 8,
    parameter WO = 25, parameter FO = 14,
    parameter ROUND = 2, parameter SAT = 1
) (
    input  wire signed [WA-1:0] a,
    input  wire signed [WB-1:0] b,
    output wire signed [WO-1:0] y
);
    wire signed [WA+WB+1:0] p = a * b;
    fx_round_sat_spec #(.WP(WA + WB + 2), .SH(FA + FB - FO), .WO(WO), .ROUND(ROUND), .SAT(SAT)) u (.p(p), .y(y));
endmodule
