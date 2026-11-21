`timescale 1ns/1ps
module ssim_sim_top (
    input  logic        clk, rst_n, in_valid, write,
    input  logic [8:0]  write_addr,
    input  logic [47:0] img, gt,
    output logic        out_valid,
    output logic [47:0] score,
    output logic [8:0]  img_addr, gt_addr,
    input  logic        shift_valid,
    input  logic [47:0] pixel,
    output logic        window_valid,
    output logic [47:0] p00, p01, p02, p10, p11, p12, p20, p21, p22,
    input  logic [15:0] div8_in,
    output logic [15:0] div8_out
);
    logic reb;
    logic [47:0] img_data, gt_data;
    SRAM_IMG img_sram (.CLK(clk), .AB(img_addr), .REB(reb), .Q(img_data),
        .AA(write_addr), .WEB(!write), .D(img), .RTSEL(2'b01), .WTSEL(2'b00), .MTSEL(2'b00));
    SRAM_GT gt_sram (.CLK(clk), .AB(gt_addr), .REB(reb), .Q(gt_data),
        .AA(write_addr), .WEB(!write), .D(gt), .RTSEL(2'b01), .WTSEL(2'b00), .MTSEL(2'b00));
    SSIM_v2 ssim (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .out_valid(out_valid),
        .ssim_score(score), .sram_img_addr(img_addr), .sram_gt_addr(gt_addr), .sram_reb(reb),
        .sram_img_data(img_data), .sram_gt_data(gt_data));
    ssim_custom_shift_safe shift (.clk(clk), .rst_n(rst_n), .in_pixel(pixel),
        .in_valid(shift_valid), .out_valid(window_valid),
        .out_p00(p00), .out_p01(p01), .out_p02(p02), .out_p10(p10), .out_p11(p11), .out_p12(p12),
        .out_p20(p20), .out_p21(p21), .out_p22(p22));
    fp16_div8 div8 (.fp_in(div8_in), .fp_out(div8_out));
endmodule
