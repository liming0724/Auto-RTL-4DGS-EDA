`timescale 1ns/1ps
// Clean-room SSIM-only behavioral operators, not Synopsys originals.
// Binary16, gradual underflow, round-to-nearest-even; status is not modeled.
// Never read this file into synthesis. GS uses the separate binary32 models.

package ssim_fp16;
    function automatic real decode(input logic [15:0] h);
        int fraction;
        fraction = int'(h[9:0]);
        if (h[14:10] == 5'h1f)
            decode = $bitstoreal({h[15], 11'h7ff, h[9:0], 42'b0});
        else if (h[14:0] == 0)
            decode = $bitstoreal({h[15], 63'b0});
        else if (h[14:10] == 0)
            decode = (h[15] ? -1.0 : 1.0) * real'(fraction) * (2.0 ** -24);
        else
            decode = $bitstoreal({h[15], 11'd1008 + {6'b0, h[14:10]}, h[9:0], 42'b0});
    endfunction

    function automatic logic [15:0] encode(input real r);
        logic [63:0] bits;
        int exponent;
        int mantissa;
        real scaled, residue;
        bits = $realtobits(r);
        if (bits[62:52] == 11'h7ff)
            return (|bits[51:0]) ? 16'h7e00 : {bits[63], 15'h7c00};
        exponent = int'(bits[62:52]) - 1023;
        if (exponent < -25)
            return {bits[63], 15'b0};
        if (exponent > 15)
            return {bits[63], 15'h7c00};
        if (exponent < -14) begin
            scaled = $bitstoreal({1'b0, bits[62:0]}) * (2.0 ** 24);
            mantissa = $rtoi(scaled);
            residue = scaled - $itor(mantissa);
            if (residue > 0.5 || (residue == 0.5 && mantissa[0]))
                mantissa = mantissa + 1;
            return {bits[63], 4'b0, mantissa[10:0]};
        end
        mantissa = int'(bits[51:42]);
        if (bits[41] && ((|bits[40:0]) || mantissa[0]))
            mantissa = mantissa + 1;
        return {bits[63], 15'(((exponent + 15) << 10) + mantissa)};
    endfunction
endpackage

`define SSIM_DW_PARAMS parameter sig_width = 10, exp_width = 5, ieee_compliance = 1
`define SSIM_DW_AB input wire [sig_width+exp_width:0] a, b
`define SSIM_DW_OUT input wire [2:0] rnd, \
    output reg [sig_width+exp_width:0] z, output wire [7:0] status
`define SSIM_DW_CHECK \
    initial if (sig_width != 10 || exp_width != 5 || ieee_compliance != 1) \
        $fatal(1, "SSIM simulation operator requires IEEE binary16"); \
    assign status = 8'b0;

module DW_fp_add #(`SSIM_DW_PARAMS) (`SSIM_DW_AB, `SSIM_DW_OUT);
    `SSIM_DW_CHECK
    always @* z = ssim_fp16::encode(ssim_fp16::decode(a) + ssim_fp16::decode(b));
endmodule

module DW_fp_sum3 #(`SSIM_DW_PARAMS) (
    `SSIM_DW_AB, input wire [sig_width+exp_width:0] c, `SSIM_DW_OUT
);
    `SSIM_DW_CHECK
    // Binary64 can hold the exact sum of three finite binary16 operands.
    always @* z = ssim_fp16::encode(ssim_fp16::decode(a) + ssim_fp16::decode(b) + ssim_fp16::decode(c));
endmodule

module DW_fp_mult #(`SSIM_DW_PARAMS) (`SSIM_DW_AB, `SSIM_DW_OUT);
    `SSIM_DW_CHECK
    always @* z = ssim_fp16::encode(ssim_fp16::decode(a) * ssim_fp16::decode(b));
endmodule

module DW_fp_div #(`SSIM_DW_PARAMS, parameter faithful_round = 0) (`SSIM_DW_AB, `SSIM_DW_OUT);
    `SSIM_DW_CHECK
    always @* z = ssim_fp16::encode(ssim_fp16::decode(a) / ssim_fp16::decode(b));
endmodule

`undef SSIM_DW_PARAMS
`undef SSIM_DW_AB
`undef SSIM_DW_OUT
`undef SSIM_DW_CHECK
