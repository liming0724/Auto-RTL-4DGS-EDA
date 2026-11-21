// Interface-only cut points for synthesis/CEC. No licensed IP or simulation
// behavior is included. Do not use these declarations in a simulation.
`define FP_PARAMS parameter sig_width = 23, exp_width = 8, ieee_compliance = 0
`define FP_A input wire [sig_width+exp_width:0] a
`define FP_B input wire [sig_width+exp_width:0] b
`define FP_RND input wire [2:0] rnd
`define FP_OUT output wire [sig_width+exp_width:0] z, output wire [7:0] status

module DW_fp_add #(`FP_PARAMS) (`FP_A, `FP_B, `FP_RND, `FP_OUT);
endmodule
module DW_fp_sub #(`FP_PARAMS) (`FP_A, `FP_B, `FP_RND, `FP_OUT);
endmodule
module DW_fp_mult #(`FP_PARAMS) (`FP_A, `FP_B, `FP_RND, `FP_OUT);
endmodule
module DW_fp_sum3 #(`FP_PARAMS) (
    `FP_A, `FP_B, input wire [sig_width+exp_width:0] c, `FP_RND, `FP_OUT
);
endmodule
module DW_fp_div #(`FP_PARAMS, parameter faithful_round = 0) (`FP_A, `FP_B, `FP_RND, `FP_OUT);
endmodule
module DW_fp_cmp #(`FP_PARAMS) (
    `FP_A, `FP_B, output wire agtb, altb, aeqb, unordered
);
endmodule
module DW_fp_exp #(`FP_PARAMS, parameter arch = 2) (`FP_A, `FP_OUT);
endmodule
module DW_fp_i2flt #(parameter sig_width = 23, exp_width = 8, isize = 32, isign = 1) (
    input wire [isize-1:0] a, `FP_RND, `FP_OUT
);
endmodule
module DW_fp_flt2i #(parameter sig_width = 23, exp_width = 8, isize = 32, isign = 1) (
    `FP_A, `FP_RND, output wire [isize-1:0] z, output wire [7:0] status
);
endmodule

`undef FP_PARAMS
`undef FP_A
`undef FP_B
`undef FP_RND
`undef FP_OUT
