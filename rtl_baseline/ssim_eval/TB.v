`timescale 1ns/1ps

`define BIT_W   48
`define WORD_W  272

module SSIM_tb;

    // ========================================================
    // Signal declarations
    // ========================================================
    reg clk;
    reg rst_n;
    reg in_valid;

    wire out_valid;
    wire [47:0] ssim_score;

    // TB writes input data into SRAM
    reg        tb_web;        // Write Enable Bar: 0 = Write
    reg [8:0]  tb_waddr;      // Write address
    reg [47:0] tb_wdata_img;  // IMG write data
    reg [47:0] tb_wdata_gt;   // GT write data

    // SSIM reads input data from SRAM
    wire [8:0] ssim_raddr_img;
    wire [8:0] ssim_raddr_gt;
    wire       ssim_reb;      // Read Enable Bar: 0 = Read

    // SRAM read data
    wire [47:0] sram_rdata_img;
    wire [47:0] sram_rdata_gt;

    // Input data storage
    reg [47:0] img_mem [0:323];
    reg [47:0] gt_mem  [0:323];

    integer i;
    integer out_count;


    // ========================================================
    // FP16 to real conversion
    // ========================================================
    function real fp16_to_real;
        input [15:0] fp16;

        integer sign_bit;
        integer exponent;
        integer fraction;
        real mantissa;
        real converted_value;

        begin
            sign_bit = fp16[15];
            exponent = fp16[14:10];
            fraction = fp16[9:0];

            if ((exponent == 0) && (fraction == 0)) begin
                // Positive or negative zero
                converted_value = 0.0;
            end
            else if (exponent == 0) begin
                // Subnormal number:
                // (-1)^sign * 2^(-14) * (fraction / 1024)
                mantissa = fraction / 1024.0;
                converted_value = mantissa * (2.0 ** (-14));
            end
            else if (exponent == 31) begin
                // Infinity or NaN
                // The DUT already converts invalid results to zero
                converted_value = 0.0;
            end
            else begin
                // Normalized number:
                // (-1)^sign * 2^(exponent-15) * (1 + fraction/1024)
                mantissa = 1.0 + (fraction / 1024.0);
                converted_value =
                    mantissa * (2.0 ** (exponent - 15));
            end

            if (sign_bit == 1)
                converted_value = -converted_value;

            fp16_to_real = converted_value;
        end
    endfunction

    // ========================================================
    // Clock generation
    // ========================================================
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // ========================================================
    // SRAM instantiation
    // ========================================================
    SRAM_IMG u_sram_img (
        .CLK   (clk),

        // Read port for SSIM
        .AB    (ssim_raddr_img),
        .REB   (ssim_reb),
        .Q     (sram_rdata_img),

        // Write port for TB
        .AA    (tb_waddr),
        .WEB   (tb_web),
        .D     (tb_wdata_img),

        // Test mode pins
        .RTSEL (2'b01),
        .WTSEL (2'b00),
        .MTSEL (2'b00)
    );

    SRAM_GT u_sram_gt (
        .CLK   (clk),

        // Read port for SSIM
        .AB    (ssim_raddr_gt),
        .REB   (ssim_reb),
        .Q     (sram_rdata_gt),

        // Write port for TB
        .AA    (tb_waddr),
        .WEB   (tb_web),
        .D     (tb_wdata_gt),

        // Test mode pins
        .RTSEL (2'b01),
        .WTSEL (2'b00),
        .MTSEL (2'b00)
    );

    // ========================================================
    // DUT instantiation
    // ========================================================
    SSIM_v2 u_dut (
        .clk            (clk),
        .rst_n          (rst_n),
        .in_valid       (in_valid),

        .out_valid      (out_valid),
        .ssim_score     (ssim_score),

        // SRAM interface
        .sram_img_addr  (ssim_raddr_img),
        .sram_gt_addr   (ssim_raddr_gt),
        .sram_reb       (ssim_reb),
        .sram_img_data  (sram_rdata_img),
        .sram_gt_data   (sram_rdata_gt)
    );

    // ========================================================
    // Test pattern
    // ========================================================
    initial begin
        // Read input files
        $readmemh("img.txt", img_mem);
        $readmemh("gt.txt", gt_mem);

        // Initialize signals
        rst_n       = 0;
        in_valid    = 0;

        tb_web      = 1;   // Disable SRAM write
        tb_waddr    = 0;
        tb_wdata_img = 0;
        tb_wdata_gt  = 0;

        out_count = 0;

        $display("");
        $display("============================================================");
        $display("                 SSIM Simulation Start");
        $display("============================================================");

        // Reset for five cycles
        repeat (5) @(posedge clk);

        rst_n = 1;

        $display("[%0t] Reset released", $time);

        // Wait for reset stabilization
        repeat (5) @(posedge clk);

        // ----------------------------------------------------
        // Write the first input at address 0
        // ----------------------------------------------------
        @(negedge clk);

        tb_web       = 0;
        tb_waddr     = 0;
        tb_wdata_img = img_mem[0];
        tb_wdata_gt  = gt_mem[0];

        $display(
            "[%0t] Write Addr %0d: IMG=%h, GT=%h",
            $time,
            tb_waddr,
            tb_wdata_img,
            tb_wdata_gt
        );

        // SRAM captures write data on the following positive edge
        @(posedge clk);

        // ----------------------------------------------------
        // Stream remaining input data
        // ----------------------------------------------------
        @(negedge clk);

        in_valid = 1;

        $display(
            "[%0t] Start streaming data and set in_valid=1",
            $time
        );

        for (i = 1; i < `WORD_W; i = i + 1) begin
            // Serpentine address order
            tb_waddr =
                ((i / 16) % 2)
                ? ((i / 16) * 32 - i + 15)
                : i;

            tb_wdata_img = img_mem[tb_waddr];
            tb_wdata_gt  = gt_mem[tb_waddr];

            // $display(
            //     "[%0t] Write Addr %0d: IMG=%h, GT=%h",
            //     $time,
            //     tb_waddr,
            //     tb_wdata_img,
            //     tb_wdata_gt
            // );

            // Write occurs at this positive edge
            @(posedge clk);

            // Update the next input away from the active edge
            @(negedge clk);
        end

        // ----------------------------------------------------
        // End input streaming
        // ----------------------------------------------------
        tb_web  = 1;
        in_valid = 0;

        $display("[%0t] Input data complete", $time);

        // Wait for the pipeline to flush
        repeat (100) @(posedge clk);

        $display("");
        $display("============================================================");
        $display("                 Testbench Complete");
        $display("Total valid SSIM outputs = %0d", out_count);
        $display("============================================================");
        $display("");

        $finish;
    end

    // ========================================================
    // Print SSIM result
    //
    // Use negedge so out_valid, ssim_score, and addr4_counter
    // have already stabilized after the preceding posedge.
    // ========================================================
    reg [47:0] aligned_s6_ab;
    reg [47:0] aligned_s6_cd;
    initial begin
        aligned_s6_ab = 48'h0;
        aligned_s6_cd = 48'h0;
    end
    always @(negedge clk) begin
        if (!rst_n) begin
            aligned_s6_ab = 48'h0;
            aligned_s6_cd = 48'h0;
        end
        else begin
            if (out_valid) begin
                $display("[SSIM OUTPUT] time=%0t output_index=%0d addr=%0d R=%.8f G=%.8f B=%.8f raw=%h",
                        $time,
                        out_count,
                        u_dut.addr4_counter,
                        fp16_to_real(ssim_score[47:32]),
                        fp16_to_real(ssim_score[31:16]),
                        fp16_to_real(ssim_score[15:0]),
                        ssim_score);

                // 檢查任一通道明顯離開正常範圍
                if ((fp16_to_real(ssim_score[47:32]) > 1.5) ||
                    (fp16_to_real(ssim_score[47:32]) < -0.5) ||
                    (fp16_to_real(ssim_score[31:16]) > 1.5) ||
                    (fp16_to_real(ssim_score[31:16]) < -0.5) ||
                    (fp16_to_real(ssim_score[15:0]) > 1.5) ||
                    (fp16_to_real(ssim_score[15:0]) < -0.5)) begin

                    $display("[ABNORMAL ALIGNED] addr=%0d", 
                            u_dut.addr4_counter);

                    $display("  AB: R=%.8f G=%.8f B=%.8f",
                            fp16_to_real(aligned_s6_ab[47:32]),
                            fp16_to_real(aligned_s6_ab[31:16]),
                            fp16_to_real(aligned_s6_ab[15:0]));

                    $display("  CD: R=%.8f G=%.8f B=%.8f",
                            fp16_to_real(aligned_s6_cd[47:32]),
                            fp16_to_real(aligned_s6_cd[31:16]),
                            fp16_to_real(aligned_s6_cd[15:0]));

                    $display("  SSIM: R=%.8f G=%.8f B=%.8f",
                            fp16_to_real(ssim_score[47:32]),
                            fp16_to_real(ssim_score[31:16]),
                            fp16_to_real(ssim_score[15:0]));
                end

                out_count = out_count + 1;
            end

            // 放在 display 後面，保存目前 s6 資料，
            // 供下一個 clock 產生的 ssim_score 對照
            aligned_s6_ab = u_dut.s6_A_B;
            aligned_s6_cd = u_dut.s6_C_D;
        end
    end

    always @(negedge clk) begin
        if (rst_n && out_valid) begin
            if ((fp16_to_real(ssim_score[31:16]) > 1.5) ||
                (fp16_to_real(ssim_score[31:16]) < -0.5)) begin

                $display("[ABNORMAL] addr=%0d", u_dut.addr4_counter);

                $display("  sigma_xy = %.8f",
                        fp16_to_real(u_dut.s4_sigma1_sigma2[31:16]));

                $display("  sigma_x2 = %.8f",
                        fp16_to_real(u_dut.s4_sigma12[31:16]));

                $display("  sigma_y2 = %.8f",
                        fp16_to_real(u_dut.s4_sigma22[31:16]));

                $display("  A = %.8f  B = %.8f",
                        fp16_to_real(u_dut.s5_A[31:16]),
                        fp16_to_real(u_dut.s5_B[31:16]));

                $display("  C = %.8f  D = %.8f",
                        fp16_to_real(u_dut.s5_C[31:16]),
                        fp16_to_real(u_dut.s5_D[31:16]));

                $display("  AB = %.8f  CD = %.8f  SSIM = %.8f",
                        fp16_to_real(u_dut.s6_A_B[31:16]),
                        fp16_to_real(u_dut.s6_C_D[31:16]),
                        fp16_to_real(ssim_score[31:16]));
            end
        end
    end
    // ========================================================
    // Waveform dump
    // ========================================================
    initial begin
        $fsdbDumpfile("ssim.fsdb");
        $fsdbDumpvars(0, SSIM_tb);
        $fsdbDumpMDA();
    end

    // ========================================================
    // Timeout watchdog
    // ========================================================
    initial begin
        #100000;

        $display("");
        $display("ERROR: Testbench timeout!");
        $display("Valid SSIM outputs before timeout = %0d", out_count);

        $finish;
    end

endmodule