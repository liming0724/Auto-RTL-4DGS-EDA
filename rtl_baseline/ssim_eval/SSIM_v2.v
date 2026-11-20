`timescale 1ns/1ps
module SSIM_v2 #(
    parameter int DATAW = 48
)(
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     in_valid,
    output logic                     out_valid,
    output logic [DATAW-1:0]         ssim_score,
    // SRAM 接口
    output logic [8:0]               sram_img_addr,
    output logic [8:0]               sram_gt_addr,
    output logic                     sram_reb,       // Read Enable Bar (0=Read)
    input  logic [DATAW-1:0]         sram_img_data,
    input  logic [DATAW-1:0]         sram_gt_data
);
localparam [15:0] FP16_C1   = 16'h068E;  // 0.0001
localparam [15:0] FP16_C2   = 16'h135F;  // 0.0009
logic [8:0] addr1_counter;
assign sram_img_addr = addr1_counter;
assign sram_gt_addr  = addr1_counter;
assign sram_reb     = 0; // Always read

addr_counter u_addr1_counter (
    .clk(clk),
    .rst_n(rst_n),
    .en(in_valid),
    .addr_counter(addr1_counter)
);
//////////////////////////////
// Stage 1
//////////////////////////////

// Register SRAM read data
logic [DATAW-1:0] in_pixel_img;
logic [DATAW-1:0] in_pixel_gt;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        in_pixel_img <= '0;
        in_pixel_gt  <= '0;
    end else begin
        in_pixel_img <= sram_img_data;
        in_pixel_gt  <= sram_gt_data;
    end
end

// Stage 1 valid signals
logic s1_valid;
logic s1_conv_valid;
logic dumped1;

logic s1_img_conv_valid;
logic s1_gt_conv_valid;

// Stage 1 IMG window outputs
logic [DATAW-1:0] img0;
logic [DATAW-1:0] img1;
logic [DATAW-1:0] img2;
logic [DATAW-1:0] img3;
logic [DATAW-1:0] img4;
logic [DATAW-1:0] img5;
logic [DATAW-1:0] img6;
logic [DATAW-1:0] img7;
logic [DATAW-1:0] img8;

// Stage 1 GT window outputs
logic [DATAW-1:0] gt0;
logic [DATAW-1:0] gt1;
logic [DATAW-1:0] gt2;
logic [DATAW-1:0] gt3;
logic [DATAW-1:0] gt4;
logic [DATAW-1:0] gt5;
logic [DATAW-1:0] gt6;
logic [DATAW-1:0] gt7;
logic [DATAW-1:0] gt8;

// Generate IMG 3x3 window
ssim_custom_shift_safe #(
    .DATAW(DATAW)
) s1_shift_img (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_pixel  (sram_img_data),
    .in_valid  (s1_valid),
    .out_valid (s1_conv_valid),
    .out_p00   (img0),
    .out_p01   (img1),
    .out_p02   (img2),
    .out_p10   (img3),
    .out_p11   (img4),
    .out_p12   (img5),
    .out_p20   (img6),
    .out_p21   (img7),
    .out_p22   (img8)
);

// Generate GT 3x3 window
ssim_custom_shift_safe #(
    .DATAW(DATAW)
) s1_shift_gt (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_pixel  (sram_gt_data),
    .in_valid  (s1_valid),
    .out_valid (dumped1),
    .out_p00   (gt0),
    .out_p01   (gt1),
    .out_p02   (gt2),
    .out_p10   (gt3),
    .out_p11   (gt4),
    .out_p12   (gt5),
    .out_p20   (gt6),
    .out_p21   (gt7),
    .out_p22   (gt8)
);

// IMG 3x3 window array
logic [47:0] img_window [0:2][0:2];

assign img_window[0][0] = img0;
assign img_window[0][1] = img1;
assign img_window[0][2] = img2;
assign img_window[1][0] = img3;
assign img_window[1][1] = img4;
assign img_window[1][2] = img5;
assign img_window[2][0] = img6;
assign img_window[2][1] = img7;
assign img_window[2][2] = img8;

// GT 3x3 window array
logic [47:0] gt_window [0:2][0:2];

assign gt_window[0][0] = gt0;
assign gt_window[0][1] = gt1;
assign gt_window[0][2] = gt2;
assign gt_window[1][0] = gt3;
assign gt_window[1][1] = gt4;
assign gt_window[1][2] = gt5;
assign gt_window[2][0] = gt6;
assign gt_window[2][1] = gt7;
assign gt_window[2][2] = gt8;

// Stage 1 convolution outputs
logic [DATAW-1:0] s1_img_conv;
logic [DATAW-1:0] s1_gt_conv;

// Pipelined IMG convolution
conv3x3_rgb u_conv_img (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (s1_conv_valid),
    .window    (img_window),
    .out_valid (s1_img_conv_valid),
    .result    (s1_img_conv)
);

// Pipelined GT convolution
conv3x3_rgb u_conv_gt (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (s1_conv_valid),
    .window    (gt_window),
    .out_valid (s1_gt_conv_valid),
    .result    (s1_gt_conv)
);



// ============================================================================
// Per-pixel products
// ============================================================================
logic [DATAW-1:0] s1_img_gt;
logic [DATAW-1:0] s1_img2;
logic [DATAW-1:0] s1_gt2;

fp_mult_rgb u_fp_mult_img_gt (
    .a (in_pixel_img),
    .b (in_pixel_gt),
    .z (s1_img_gt)
);

fp_mult_rgb u_fp_mult_img2 (
    .a (in_pixel_img),
    .b (in_pixel_img),
    .z (s1_img2)
);

fp_mult_rgb u_fp_mult_gt2 (
    .a (in_pixel_gt),
    .b (in_pixel_gt),
    .z (s1_gt2)
);


// ============================================================================
// Stage 2 input valid pipeline
// ============================================================================
logic s2_valid;
logic in_valid_1;

logic s3_valid_1;
logic dumped2;
logic dumped3;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        in_valid_1 <= 1'b0;
        s1_valid   <= 1'b0;
        s2_valid   <= 1'b0;
    end else begin
        in_valid_1 <= in_valid;
        s1_valid   <= in_valid_1;
        s2_valid   <= s1_valid;
    end
end


// ============================================================================
// Stage 2 shift buffers
// ============================================================================
logic [DATAW-1:0] img_gt0;
logic [DATAW-1:0] img_gt1;
logic [DATAW-1:0] img_gt2;
logic [DATAW-1:0] img_gt3;
logic [DATAW-1:0] img_gt4;
logic [DATAW-1:0] img_gt5;
logic [DATAW-1:0] img_gt6;
logic [DATAW-1:0] img_gt7;
logic [DATAW-1:0] img_gt8;

logic [DATAW-1:0] gt20;
logic [DATAW-1:0] gt21;
logic [DATAW-1:0] gt22;
logic [DATAW-1:0] gt23;
logic [DATAW-1:0] gt24;
logic [DATAW-1:0] gt25;
logic [DATAW-1:0] gt26;
logic [DATAW-1:0] gt27;
logic [DATAW-1:0] gt28;

logic [DATAW-1:0] img20;
logic [DATAW-1:0] img21;
logic [DATAW-1:0] img22;
logic [DATAW-1:0] img23;
logic [DATAW-1:0] img24;
logic [DATAW-1:0] img25;
logic [DATAW-1:0] img26;
logic [DATAW-1:0] img27;
logic [DATAW-1:0] img28;

ssim_custom_shift_safe #(
    .DATAW (DATAW)
) s2_shift_img_gt (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_pixel  (s1_img_gt),
    .in_valid  (s2_valid),
    .out_valid (s3_valid_1),
    .out_p00   (img_gt0),
    .out_p01   (img_gt1),
    .out_p02   (img_gt2),
    .out_p10   (img_gt3),
    .out_p11   (img_gt4),
    .out_p12   (img_gt5),
    .out_p20   (img_gt6),
    .out_p21   (img_gt7),
    .out_p22   (img_gt8)
);

ssim_custom_shift_safe #(
    .DATAW (DATAW)
) s2_shift_gt2 (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_pixel  (s1_gt2),
    .in_valid  (s2_valid),
    .out_valid (dumped2),
    .out_p00   (gt20),
    .out_p01   (gt21),
    .out_p02   (gt22),
    .out_p10   (gt23),
    .out_p11   (gt24),
    .out_p12   (gt25),
    .out_p20   (gt26),
    .out_p21   (gt27),
    .out_p22   (gt28)
);

ssim_custom_shift_safe #(
    .DATAW (DATAW)
) s2_shift_img2 (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_pixel  (s1_img2),
    .in_valid  (s2_valid),
    .out_valid (dumped3),
    .out_p00   (img20),
    .out_p01   (img21),
    .out_p02   (img22),
    .out_p10   (img23),
    .out_p11   (img24),
    .out_p12   (img25),
    .out_p20   (img26),
    .out_p21   (img27),
    .out_p22   (img28)
);


// ============================================================================
// Stage 2 windows
// ============================================================================
logic [47:0] img_gt_window [0:2][0:2];
logic [47:0] gt2_window    [0:2][0:2];
logic [47:0] img2_window   [0:2][0:2];

assign img_gt_window[0][0] = img_gt0;
assign img_gt_window[0][1] = img_gt1;
assign img_gt_window[0][2] = img_gt2;
assign img_gt_window[1][0] = img_gt3;
assign img_gt_window[1][1] = img_gt4;
assign img_gt_window[1][2] = img_gt5;
assign img_gt_window[2][0] = img_gt6;
assign img_gt_window[2][1] = img_gt7;
assign img_gt_window[2][2] = img_gt8;

assign gt2_window[0][0] = gt20;
assign gt2_window[0][1] = gt21;
assign gt2_window[0][2] = gt22;
assign gt2_window[1][0] = gt23;
assign gt2_window[1][1] = gt24;
assign gt2_window[1][2] = gt25;
assign gt2_window[2][0] = gt26;
assign gt2_window[2][1] = gt27;
assign gt2_window[2][2] = gt28;

assign img2_window[0][0] = img20;
assign img2_window[0][1] = img21;
assign img2_window[0][2] = img22;
assign img2_window[1][0] = img23;
assign img2_window[1][1] = img24;
assign img2_window[1][2] = img25;
assign img2_window[2][0] = img26;
assign img2_window[2][1] = img27;
assign img2_window[2][2] = img28;


// ============================================================================
// Stage 2 pipelined convolution
// ============================================================================
logic [DATAW-1:0] s2_img_gt_conv;
logic [DATAW-1:0] s2_img2_conv;
logic [DATAW-1:0] s2_gt2_conv;

logic s2_img_gt_conv_valid;
logic s2_img2_conv_valid;
logic s2_gt2_conv_valid;

conv3x3_rgb u_conv_img_gt (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (s3_valid_1),
    .window    (img_gt_window),
    .out_valid (s2_img_gt_conv_valid),
    .result    (s2_img_gt_conv)
);

conv3x3_rgb u_conv_img2 (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (s3_valid_1),
    .window    (img2_window),
    .out_valid (s2_img2_conv_valid),
    .result    (s2_img2_conv)
);

conv3x3_rgb u_conv_gt2 (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (s3_valid_1),
    .window    (gt2_window),
    .out_valid (s2_gt2_conv_valid),
    .result    (s2_gt2_conv)
);


// ============================================================================
// Mean convolution register
// ============================================================================
logic [DATAW-1:0] s2_img_conv;
logic [DATAW-1:0] s2_gt_conv;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        s2_img_conv <= '0;
        s2_gt_conv  <= '0;
    end else if (s1_img_conv_valid) begin
        s2_img_conv <= s1_img_conv;
        s2_gt_conv  <= s1_gt_conv;
    end
end


// ============================================================================
// Mean product terms
// ============================================================================
logic [DATAW-1:0] s2_mu1_mu2;
logic [DATAW-1:0] s2_mu12;
logic [DATAW-1:0] s2_mu22;

fp_mult_rgb u_fp_mult_mu1_mu2 (
    .a (s2_img_conv),
    .b (s2_gt_conv),
    .z (s2_mu1_mu2)
);

fp_mult_rgb u_fp_mult_mu12 (
    .a (s2_img_conv),
    .b (s2_img_conv),
    .z (s2_mu12)
);

fp_mult_rgb u_fp_mult_mu22 (
    .a (s2_gt_conv),
    .b (s2_gt_conv),
    .z (s2_mu22)
);


// ============================================================================
// Mean SRAM
// ============================================================================
logic             wen2;
logic [DATAW-1:0] dout2_mu1;
logic [DATAW-1:0] dout2_mu2;
logic [8:0]       addr2_counter;

addr_counter_reverse u_addr2_counter (
    .clk          (clk),
    .rst_n        (rst_n),
    .en           (s1_img_conv_valid),
    .addr_counter (addr2_counter)
);

assign wen2 = s1_img_conv_valid;

SRAM_MU1 u_sram_mu1 (
    .clk  (clk),
    .wen  (wen2),
    .addr (addr2_counter),
    .din  (s2_img_conv),
    .dout (dout2_mu1)
);

SRAM_MU2 u_sram_mu2 (
    .clk  (clk),
    .wen  (wen2),
    .addr (addr2_counter),
    .din  (s2_gt_conv),
    .dout (dout2_mu2)
);


// ============================================================================
// Stage 3 register
// ============================================================================
logic [DATAW-1:0] s3_mu1_mu2;
logic [DATAW-1:0] s3_img_gt_conv;

logic [DATAW-1:0] s3_mu12;
logic [DATAW-1:0] s3_img2_conv;

logic [DATAW-1:0] s3_mu22;
logic [DATAW-1:0] s3_gt2_conv;

logic s3_valid;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        s3_mu12        <= '0;
        s3_mu1_mu2     <= '0;
        s3_mu22        <= '0;
        s3_img_gt_conv <= '0;
        s3_img2_conv   <= '0;
        s3_gt2_conv    <= '0;
        s3_valid       <= 1'b0;
    end else begin
        s3_mu12        <= s2_mu12;
        s3_mu1_mu2     <= s2_mu1_mu2;
        s3_mu22        <= s2_mu22;

        s3_img_gt_conv <= s2_img_gt_conv;
        s3_img2_conv   <= s2_img2_conv;
        s3_gt2_conv    <= s2_gt2_conv;

        s3_valid       <= s2_img_gt_conv_valid;
    end
end


// ============================================================================
// Stage 3 variance and covariance
// ============================================================================
logic [DATAW-1:0] s3_sigma1_sigma2;
logic [DATAW-1:0] s3_sigma12;
logic [DATAW-1:0] s3_sigma22;

fp_sub_rgb u_fp_sub_num1 (
    .a (s3_img_gt_conv),
    .b (s3_mu1_mu2),
    .z (s3_sigma1_sigma2)
);

fp_sub_rgb u_fp_sub_num2 (
    .a (s3_img2_conv),
    .b (s3_mu12),
    .z (s3_sigma12)
);

fp_sub_rgb u_fp_sub_num3 (
    .a (s3_gt2_conv),
    .b (s3_mu22),
    .z (s3_sigma22)
);
//////////////////////////////
// Stage 4
//////////////////////////////
logic [DATAW-1:0] s4_sigma1_sigma2, s4_sigma12, s4_sigma22;
logic [DATAW-1:0] s4_mu1_mu2, s4_mu12, s4_mu22;
logic             s4_valid;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        s4_valid <= 0;
        s4_sigma1_sigma2 <= 0;
        s4_sigma12 <= 0;
        s4_sigma22 <= 0;
        s4_mu1_mu2 <= 0;
        s4_mu12 <= 0;
        s4_mu22 <= 0;
    end else begin
        s4_valid <= s3_valid;
        s4_sigma1_sigma2 <= s3_sigma1_sigma2;
        s4_sigma12 <= s3_sigma12;
        s4_sigma22 <= s3_sigma22;
        s4_mu1_mu2 <= s3_mu1_mu2;
        s4_mu12 <= s3_mu12;
        s4_mu22 <= s3_mu22;
    end
end


// ============================================================================
// Stage 4: Build SSIM terms using DW_fp_sum3
//
// A = 2 * mu1_mu2 + C1
// B = 2 * sigma12 + C2
// C = mu1^2 + mu2^2 + C1
// D = sigma1^2 + sigma2^2 + C2
// ============================================================================
logic [DATAW-1:0] s4_A, s4_B, s4_C, s4_D;

fp_sum3_rgb u_s4_A (
    .a (s4_mu1_mu2),
    .b (s4_mu1_mu2),
    .c ({FP16_C1, FP16_C1, FP16_C1}),
    .z (s4_A)
);

fp_sum3_rgb u_s4_B (
    .a (s4_sigma1_sigma2),
    .b (s4_sigma1_sigma2),
    .c ({FP16_C2, FP16_C2, FP16_C2}),
    .z (s4_B)
);

fp_sum3_rgb u_s4_C (
    .a (s4_mu12),
    .b (s4_mu22),
    .c ({FP16_C1, FP16_C1, FP16_C1}),
    .z (s4_C)
);

fp_sum3_rgb u_s4_D (
    .a (s4_sigma12),
    .b (s4_sigma22),
    .c ({FP16_C2, FP16_C2, FP16_C2}),
    .z (s4_D)
);
//////////////////////////////
// Stage 5
//////////////////////////////
logic             s5_valid;
logic [DATAW-1:0] s5_A, s5_B, s5_C, s5_D;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        s5_valid <= 0;
        s5_A <= 0;
        s5_B <= 0;
        s5_C <= 0;
        s5_D <= 0;
    end else begin
        s5_valid <= s4_valid;
        s5_A <= s4_A;
        s5_B <= s4_B;
        s5_C <= s4_C;
        s5_D <= s4_D;
    end
end
logic [DATAW-1:0] s5_A_B, s5_C_D;
fp_mult_rgb u_s5_A_B (
    .a(s5_A),
    .b(s5_B),
    .z(s5_A_B)
);
fp_mult_rgb u_s5_C_D (
    .a(s5_C),
    .b(s5_D),
    .z(s5_C_D)
);
//////////////////////////////
logic             wen3;
logic [DATAW-1:0] dout3_A, dout3_B, dout3_C, dout3_D;
logic [8:0]      addr3_counter;
addr_counter_reverse u_addr3_counter (
    .clk(clk),
    .rst_n(rst_n),
    .en(s5_valid),
    .addr_counter(addr3_counter)
);
assign wen3 = s5_valid;
SRAM_A u_sram_a (
    .clk  (clk),
    .wen  (wen3),
    .addr (addr3_counter),
    .din  (s5_A),
    .dout (dout3_A)
);
SRAM_B u_sram_b (
    .clk  (clk),
    .wen  (wen3),
    .addr (addr3_counter),
    .din  (s5_B),
    .dout (dout3_B)
);
SRAM_C u_sram_c (
    .clk  (clk),
    .wen  (wen3),
    .addr (addr3_counter),
    .din  (s5_C),
    .dout (dout3_C)
);
SRAM_D u_sram_d (
    .clk  (clk),
    .wen  (wen3),
    .addr (addr3_counter),
    .din  (s5_D),
    .dout (dout3_D)
);
//////////////////////////////
// STAGE 6
//////////////////////////////
logic             s6_valid;
logic [DATAW-1:0] s6_A_B, s6_C_D;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        s6_valid <= 1'b0;
        s6_A_B   <= '0;
        s6_C_D   <= '0;
    end else begin
        s6_valid <= s5_valid;
        s6_A_B   <= s5_A_B;
        s6_C_D   <= s5_C_D;
    end
end

// FP16 SSIM division result before saturation
logic [DATAW-1:0] s6_ssim_score_raw;

// FP16 SSIM result after saturation to [0, 1]
logic [DATAW-1:0] s6_ssim_score_sat;

fp_div_rgb u_s6_ssim (
    .a(s6_A_B),
    .b(s6_C_D),
    .z(s6_ssim_score_raw)
);

// Clamp each RGB FP16 channel to [0, 1]
fp16_clamp01_rgb u_s6_ssim_clamp (
    .fp_in (s6_ssim_score_raw),
    .fp_out(s6_ssim_score_sat)
);

//////////////////////////////
// OUTPUT
//////////////////////////////
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        out_valid  <= 1'b0;
        ssim_score <= '0;
    end else begin
        out_valid  <= s6_valid;
        ssim_score <= s6_ssim_score_sat;
    end
end

//////////////////////////////
// OUTPUT SRAM
//////////////////////////////
logic             wen4;
logic [DATAW-1:0] dout4_ssim_score;
logic [8:0]       addr4_counter;

addr_counter_reverse u_addr4_counter (
    .clk          (clk),
    .rst_n        (rst_n),
    .en           (out_valid),
    .addr_counter (addr4_counter)
);

assign wen4 = out_valid;

SRAM_SSIM_SCORE u_sram_ssim_score (
    .clk  (clk),
    .wen  (wen4),
    .addr (addr4_counter),
    .din  (ssim_score),
    .dout (dout4_ssim_score)
);

endmodule


// ============================================================================
// FP16 saturation to [0, 1]
//
// FP16 encoding:
//   0.0 = 16'h0000
//   1.0 = 16'h3C00
//
// Behavior:
//   Negative finite number -> 0.0
//   Positive number > 1.0 -> 1.0
//   NaN or Infinity        -> 0.0
//   Otherwise              -> unchanged
// ============================================================================
module fp16_clamp01 (
    input  logic [15:0] fp_in,
    output logic [15:0] fp_out
);

    localparam logic [15:0] FP16_ZERO = 16'h0000;
    localparam logic [15:0] FP16_ONE  = 16'h3C00;

    logic       sign_bit;
    logic [4:0] exponent;
    logic [9:0] fraction;

    logic is_nan;
    logic is_inf;
    logic is_invalid;
    logic is_negative;
    logic is_greater_than_one;

    always_comb begin
        sign_bit = fp_in[15];
        exponent = fp_in[14:10];
        fraction = fp_in[9:0];

        is_nan = (exponent == 5'h1F) &&
                 (fraction != 10'h000);

        is_inf = (exponent == 5'h1F) &&
                 (fraction == 10'h000);

        is_invalid = is_nan || is_inf;

        // Negative zero is treated as zero rather than a negative value.
        is_negative = sign_bit &&
                      (fp_in[14:0] != 15'h0000);

        // For nonnegative IEEE-754 FP16 values, unsigned bit comparison
        // preserves numerical ordering.
        is_greater_than_one =
            (!sign_bit) && (fp_in > FP16_ONE);

        if (is_invalid) begin
            fp_out = FP16_ZERO;
        end else if (is_negative) begin
            fp_out = FP16_ZERO;
        end else if (is_greater_than_one) begin
            fp_out = FP16_ONE;
        end else begin
            fp_out = fp_in;
        end
    end

endmodule


// ============================================================================
// RGB FP16 saturation to [0, 1]
//
// Input/output format:
//   [47:32] = R
//   [31:16] = G
//   [15:0]  = B
// ============================================================================
module fp16_clamp01_rgb (
    input  logic [47:0] fp_in,
    output logic [47:0] fp_out
);

    logic [15:0] in_r;
    logic [15:0] in_g;
    logic [15:0] in_b;

    logic [15:0] out_r;
    logic [15:0] out_g;
    logic [15:0] out_b;

    assign in_r = fp_in[47:32];
    assign in_g = fp_in[31:16];
    assign in_b = fp_in[15:0];

    fp16_clamp01 u_clamp_r (
        .fp_in (in_r),
        .fp_out(out_r)
    );

    fp16_clamp01 u_clamp_g (
        .fp_in (in_g),
        .fp_out(out_g)
    );

    fp16_clamp01 u_clamp_b (
        .fp_in (in_b),
        .fp_out(out_b)
    );

    assign fp_out = {out_r, out_g, out_b};

endmodule

/*
將原先由多個 FP16 加法器串接的 3×3 convolution 改為兩級 DW_fp_sum3 pipeline 後，
運算 latency 增加一個 clock cycle，但 throughput 仍維持每個 clock 處理一組資料。
正常數值區域的輸出誤差多落於數個 FP16 ULP 內，差異主要來自浮點加法順序與 rounding 行為。
分母接近零的位置則因除法敏感度而產生較大輸出差異，最終版本可透過飽和限制避免異常值傳遞。
*/