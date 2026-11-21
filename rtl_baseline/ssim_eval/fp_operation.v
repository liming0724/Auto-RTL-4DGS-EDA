`timescale 1ns/1ps

// ============================================================================
// Floating Point Operations for SSIM
//
// FP16 format:
//   Significand width = 10
//   Exponent width    = 5
//
// RGB format:
//   [47:32] = R
//   [31:16] = G
//   [15:0]  = B
// ============================================================================


// ============================================================================
// Single-Channel FP16 Addition
// ============================================================================
module fp_add (
    input  logic [15:0] a,
    input  logic [15:0] b,
    output logic [15:0] z
);

    logic [7:0]  status;
    logic [15:0] z_raw;

    DW_fp_add #(
        .sig_width       (10),
        .exp_width       (5),
        .ieee_compliance (1)
    ) U_fp_add (
        .a      (a),
        .b      (b),
        .rnd    (3'b000),
        .z      (z_raw),
        .status (status)
    );

    always_comb begin
        if (z_raw[14:10] == 5'h1F)
            z = 16'h0000;
        else
            z = z_raw;
    end

endmodule


// ============================================================================
// Single-Channel FP16 Sum of Three Inputs
//
// z = a + b + c
//
// This module uses DW_fp_sum3 rather than two cascaded DW_fp_add blocks.
// ============================================================================
module fp_sum3 (
    input  logic [15:0] a,
    input  logic [15:0] b,
    input  logic [15:0] c,
    output logic [15:0] z
);

    logic [7:0]  status;
    logic [15:0] z_raw;

    DW_fp_sum3 #(
        .sig_width       (10),
        .exp_width       (5),
        .ieee_compliance (1)
    ) U_fp_sum3 (
        .a      (a),
        .b      (b),
        .c      (c),
        .rnd    (3'b000),
        .z      (z_raw),
        .status (status)
    );

    always_comb begin
        if (z_raw[14:10] == 5'h1F)
            z = 16'h0000;
        else
            z = z_raw;
    end

endmodule


// ============================================================================
// Single-Channel FP16 Subtraction
// ============================================================================
module fp_sub (
    input  logic [15:0] a,
    input  logic [15:0] b,
    output logic [15:0] z
);

    logic [7:0]  status;
    logic [15:0] b_neg;
    logic [15:0] z_raw;

    assign b_neg = {~b[15], b[14:0]};

    DW_fp_add #(
        .sig_width       (10),
        .exp_width       (5),
        .ieee_compliance (1)
    ) U_fp_sub (
        .a      (a),
        .b      (b_neg),
        .rnd    (3'b000),
        .z      (z_raw),
        .status (status)
    );

    always_comb begin
        if (z_raw[14:10] == 5'h1F)
            z = 16'h0000;
        else
            z = z_raw;
    end

endmodule


// ============================================================================
// Single-Channel FP16 Multiplication
// ============================================================================
module fp_mult (
    input  logic [15:0] a,
    input  logic [15:0] b,
    output logic [15:0] z
);

    logic [7:0]  status;
    logic [15:0] z_raw;

    DW_fp_mult #(
        .sig_width       (10),
        .exp_width       (5),
        .ieee_compliance (1)
    ) U_fp_mult (
        .a      (a),
        .b      (b),
        .rnd    (3'b000),
        .z      (z_raw),
        .status (status)
    );

    always_comb begin
        if (z_raw[14:10] == 5'h1F)
            z = 16'h0000;
        else
            z = z_raw;
    end

endmodule


// ============================================================================
// Single-Channel FP16 Division
// ============================================================================
module fp_div (
    input  logic [15:0] a,
    input  logic [15:0] b,
    output logic [15:0] z
);

    logic [7:0]  status;
    logic [15:0] z_raw;

    DW_fp_div #(
        .sig_width       (10),
        .exp_width       (5),
        .ieee_compliance (1),
        .faithful_round  (0)
    ) U_fp_div (
        .a      (a),
        .b      (b),
        .rnd    (3'b000),
        .z      (z_raw),
        .status (status)
    );

    always_comb begin
        if (z_raw[14:10] == 5'h1F)
            z = 16'h0000;
        else
            z = z_raw;
    end

endmodule


// ============================================================================
// Optimized FP16 Division by 8
// ============================================================================
module fp16_div8 (
    input  logic [15:0] fp_in,
    output logic [15:0] fp_out
);

    logic        sign_in;
    logic [4:0]  exp_in;
    logic [9:0]  man_in;
    logic [10:0] one_plus_man;

    logic is_zero;
    logic is_inf;
    logic is_nan;

    always_comb begin
        sign_in = fp_in[15];
        exp_in  = fp_in[14:10];
        man_in  = fp_in[9:0];

        is_zero = (exp_in == 5'd0) &&
                  (man_in == 10'd0);

        is_inf = (exp_in == 5'h1F) &&
                 (man_in == 10'd0);

        is_nan = (exp_in == 5'h1F) &&
                 (man_in != 10'd0);

        one_plus_man = {1'b1, man_in};

        if (is_nan || is_inf) begin
            fp_out = 16'h0000;
        end
        else if (is_zero) begin
            fp_out = {sign_in, 5'd0, 10'd0};
        end
        else if (exp_in == 5'd0) begin
            fp_out = {
                sign_in,
                5'd0,
                man_in >> 3
            };
        end
        else if (exp_in == 5'd1) begin
            // Preserve the legacy low 16 bits of the 17-bit concatenation.
            fp_out = 16'({
                sign_in,
                5'd0,
                one_plus_man >> 3
            });
        end
        else if (exp_in == 5'd2) begin
            fp_out = 16'({
                sign_in,
                5'd0,
                one_plus_man >> 2
            });
        end
        else if (exp_in == 5'd3) begin
            fp_out = 16'({
                sign_in,
                5'd0,
                one_plus_man >> 1
            });
        end
        else begin
            fp_out = {
                sign_in,
                exp_in - 5'd3,
                man_in
            };
        end
    end

endmodule


// ============================================================================
// Optimized FP16 Multiply by 2
// ============================================================================
module fp16_mult2 (
    input  logic [15:0] fp_in,
    output logic [15:0] fp_out
);

    logic        sign_in;
    logic [4:0]  exp_in;
    logic [9:0]  man_in;

    logic is_zero;
    logic is_inf;
    logic is_nan;

    always_comb begin
        sign_in = fp_in[15];
        exp_in  = fp_in[14:10];
        man_in  = fp_in[9:0];

        is_zero = (exp_in == 5'd0) &&
                  (man_in == 10'd0);

        is_inf = (exp_in == 5'h1F) &&
                 (man_in == 10'd0);

        is_nan = (exp_in == 5'h1F) &&
                 (man_in != 10'd0);

        if (is_nan || is_inf) begin
            fp_out = 16'h0000;
        end
        else if (is_zero) begin
            fp_out = {sign_in, 5'd0, 10'd0};
        end
        else if (exp_in == 5'd0) begin
            fp_out = {
                sign_in,
                5'd0,
                man_in << 1
            };
        end
        else if (exp_in == 5'h1E) begin
            fp_out = 16'h0000;
        end
        else begin
            fp_out = {
                sign_in,
                exp_in + 5'd1,
                man_in
            };
        end
    end

endmodule


// ============================================================================
// RGB FP16 Addition
// ============================================================================
module fp_add_rgb (
    input  logic [47:0] a,
    input  logic [47:0] b,
    output logic [47:0] z
);

    logic [15:0] z_r;
    logic [15:0] z_g;
    logic [15:0] z_b;

    fp_add U_ADD_R (
        .a (a[47:32]),
        .b (b[47:32]),
        .z (z_r)
    );

    fp_add U_ADD_G (
        .a (a[31:16]),
        .b (b[31:16]),
        .z (z_g)
    );

    fp_add U_ADD_B (
        .a (a[15:0]),
        .b (b[15:0]),
        .z (z_b)
    );

    assign z = {z_r, z_g, z_b};

endmodule


// ============================================================================
// RGB FP16 Subtraction
// ============================================================================
module fp_sub_rgb (
    input  logic [47:0] a,
    input  logic [47:0] b,
    output logic [47:0] z
);

    logic [15:0] z_r;
    logic [15:0] z_g;
    logic [15:0] z_b;

    fp_sub U_SUB_R (
        .a (a[47:32]),
        .b (b[47:32]),
        .z (z_r)
    );

    fp_sub U_SUB_G (
        .a (a[31:16]),
        .b (b[31:16]),
        .z (z_g)
    );

    fp_sub U_SUB_B (
        .a (a[15:0]),
        .b (b[15:0]),
        .z (z_b)
    );

    assign z = {z_r, z_g, z_b};

endmodule


// ============================================================================
// RGB FP16 Multiplication
// ============================================================================
module fp_mult_rgb (
    input  logic [47:0] a,
    input  logic [47:0] b,
    output logic [47:0] z
);

    logic [15:0] z_r;
    logic [15:0] z_g;
    logic [15:0] z_b;

    fp_mult U_MULT_R (
        .a (a[47:32]),
        .b (b[47:32]),
        .z (z_r)
    );

    fp_mult U_MULT_G (
        .a (a[31:16]),
        .b (b[31:16]),
        .z (z_g)
    );

    fp_mult U_MULT_B (
        .a (a[15:0]),
        .b (b[15:0]),
        .z (z_b)
    );

    assign z = {z_r, z_g, z_b};

endmodule


// ============================================================================
// RGB FP16 Division
// ============================================================================
module fp_div_rgb (
    input  logic [47:0] a,
    input  logic [47:0] b,
    output logic [47:0] z
);

    logic [15:0] z_r;
    logic [15:0] z_g;
    logic [15:0] z_b;

    fp_div U_DIV_R (
        .a (a[47:32]),
        .b (b[47:32]),
        .z (z_r)
    );

    fp_div U_DIV_G (
        .a (a[31:16]),
        .b (b[31:16]),
        .z (z_g)
    );

    fp_div U_DIV_B (
        .a (a[15:0]),
        .b (b[15:0]),
        .z (z_b)
    );

    assign z = {z_r, z_g, z_b};

endmodule


// ============================================================================
// RGB FP16 Division by 8
// ============================================================================
module fp16_div8_rgb (
    input  logic [47:0] fp_in,
    output logic [47:0] fp_out
);

    logic [15:0] out_r;
    logic [15:0] out_g;
    logic [15:0] out_b;

    fp16_div8 U_DIV8_R (
        .fp_in  (fp_in[47:32]),
        .fp_out (out_r)
    );

    fp16_div8 U_DIV8_G (
        .fp_in  (fp_in[31:16]),
        .fp_out (out_g)
    );

    fp16_div8 U_DIV8_B (
        .fp_in  (fp_in[15:0]),
        .fp_out (out_b)
    );

    assign fp_out = {out_r, out_g, out_b};

endmodule


// ============================================================================
// RGB FP16 Multiply by 2
// ============================================================================
module fp16_mult2_rgb (
    input  logic [47:0] fp_in,
    output logic [47:0] fp_out
);

    logic [15:0] out_r;
    logic [15:0] out_g;
    logic [15:0] out_b;

    fp16_mult2 U_MULT2_R (
        .fp_in  (fp_in[47:32]),
        .fp_out (out_r)
    );

    fp16_mult2 U_MULT2_G (
        .fp_in  (fp_in[31:16]),
        .fp_out (out_g)
    );

    fp16_mult2 U_MULT2_B (
        .fp_in  (fp_in[15:0]),
        .fp_out (out_b)
    );

    assign fp_out = {out_r, out_g, out_b};

endmodule


// ============================================================================
// Two-Stage Pipelined RGB 3x3 Convolution
//
// Stage 1:
//   Calculate three sums for each channel:
//     col0 = p00 + p10 + p20
//     col1 = p01 + p11 + p21
//     col2 = p02 + p12 + p22
//
// Pipeline register:
//   Register col0, col1, and col2.
//
// Stage 2:
//   total = col0 + col1 + col2
//   result = total / 8
//
// The new out_valid corresponds to the registered column sums.
// ============================================================================
module conv3x3_rgb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        in_valid,
    input  logic [47:0] window [0:2][0:2],
    output logic        out_valid,
    output logic [47:0] result
);

    logic [15:0] window_r [0:2][0:2];
    logic [15:0] window_g [0:2][0:2];
    logic [15:0] window_b [0:2][0:2];

    logic [15:0] result_r;
    logic [15:0] result_g;
    logic [15:0] result_b;

    genvar i;
    genvar j;

    generate
        for (i = 0; i < 3; i = i + 1) begin : ROW_GEN
            for (j = 0; j < 3; j = j + 1) begin : COL_GEN
                assign window_r[i][j] =
                    window[i][j][47:32];

                assign window_g[i][j] =
                    window[i][j][31:16];

                assign window_b[i][j] =
                    window[i][j][15:0];
            end
        end
    endgenerate

    // Register the valid signal at the same pipeline boundary
    // as the three column sums.
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            out_valid <= 1'b0;
        else
            out_valid <= in_valid;
    end

    conv3x3_single_pipe U_CONV_R (
        .clk    (clk),
        .rst_n  (rst_n),
        .window (window_r),
        .result (result_r)
    );

    conv3x3_single_pipe U_CONV_G (
        .clk    (clk),
        .rst_n  (rst_n),
        .window (window_g),
        .result (result_g)
    );

    conv3x3_single_pipe U_CONV_B (
        .clk    (clk),
        .rst_n  (rst_n),
        .window (window_b),
        .result (result_b)
    );

    assign result = {
        result_r,
        result_g,
        result_b
    };

endmodule

// ============================================================================
// RGB FP16 Sum of Three Inputs
//
// z = a + b + c
//   [47:32] = R
//   [31:16] = G
//   [15:0]  = B
// ============================================================================
module fp_sum3_rgb (
    input  logic [47:0] a,
    input  logic [47:0] b,
    input  logic [47:0] c,
    output logic [47:0] z
);

    logic [15:0] z_r;
    logic [15:0] z_g;
    logic [15:0] z_b;

    fp_sum3 U_SUM3_R (
        .a (a[47:32]),
        .b (b[47:32]),
        .c (c[47:32]),
        .z (z_r)
    );

    fp_sum3 U_SUM3_G (
        .a (a[31:16]),
        .b (b[31:16]),
        .c (c[31:16]),
        .z (z_g)
    );

    fp_sum3 U_SUM3_B (
        .a (a[15:0]),
        .b (b[15:0]),
        .c (c[15:0]),
        .z (z_b)
    );

    assign z = {z_r, z_g, z_b};

endmodule
// ============================================================================
// Two-Stage Pipelined Single-Channel 3x3 Convolution
//
// First combinational section:
//   Three DW_fp_sum3 modules operate in parallel.
//
// Pipeline boundary:
//   col_sum_reg[0:2]
//
// Second combinational section:
//   One DW_fp_sum3 followed by exponent-based division by 8.
// ============================================================================
module conv3x3_single_pipe (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [15:0] window [0:2][0:2],
    output logic [15:0] result
);

    // First-stage combinational output
    logic [15:0] col_sum_comb [0:2];

    // Pipeline registers
    logic [15:0] col_sum_reg [0:2];

    // Second-stage combinational signals
    logic [15:0] window_sum;
    logic [15:0] result_comb;

    // ------------------------------------------------------------------------
    // First sum3 level
    //
    // Three independent groups of three pixels are added in parallel.
    // ------------------------------------------------------------------------
    fp_sum3 U_COL0_SUM3 (
        .a (window[0][0]),
        .b (window[1][0]),
        .c (window[2][0]),
        .z (col_sum_comb[0])
    );

    fp_sum3 U_COL1_SUM3 (
        .a (window[0][1]),
        .b (window[1][1]),
        .c (window[2][1]),
        .z (col_sum_comb[1])
    );

    fp_sum3 U_COL2_SUM3 (
        .a (window[0][2]),
        .b (window[1][2]),
        .c (window[2][2]),
        .z (col_sum_comb[2])
    );

    // ------------------------------------------------------------------------
    // Pipeline registers between the two DW_fp_sum3 levels
    // ------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            col_sum_reg[0] <= 16'h0000;
            col_sum_reg[1] <= 16'h0000;
            col_sum_reg[2] <= 16'h0000;
        end
        else begin
            col_sum_reg[0] <= col_sum_comb[0];
            col_sum_reg[1] <= col_sum_comb[1];
            col_sum_reg[2] <= col_sum_comb[2];
        end
    end

    // ------------------------------------------------------------------------
    // Second sum3 level
    //
    // Add the three registered partial sums.
    // ------------------------------------------------------------------------
    fp_sum3 U_TOTAL_SUM3 (
        .a (col_sum_reg[0]),
        .b (col_sum_reg[1]),
        .c (col_sum_reg[2]),
        .z (window_sum)
    );

    // Divide by 8 using exponent adjustment
    fp16_div8 U_DIV8 (
        .fp_in  (window_sum),
        .fp_out (result_comb)
    );

    assign result = result_comb;

endmodule


// ============================================================================
// Helper: Add Constant
// ============================================================================
module fp_add_const (
    input  logic [15:0] a,
    input  logic [15:0] constant,
    output logic [15:0] z
);

    fp_add U_ADD (
        .a (a),
        .b (constant),
        .z (z)
    );

endmodule