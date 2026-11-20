`timescale 1ns/1ps
module ssim_custom_shift_safe #(
    parameter int DATAW = 48
)(
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic [DATAW-1:0]         in_pixel,
    input  logic                     in_valid,
    output logic                     out_valid,
    output logic [DATAW-1:0]         out_p00, out_p01, out_p02,
    output logic [DATAW-1:0]         out_p10, out_p11, out_p12,
    output logic [DATAW-1:0]         out_p20, out_p21, out_p22
);

    logic [DATAW-1:0] row0 [0:16];
    logic [DATAW-1:0] row1 [0:16];
    logic [DATAW-1:0] row2 [0:2];
    logic [DATAW-1:0] row3;

    logic            dir;
    logic [8:0]      counter;
    logic            is_t1_reg;

    logic [DATAW-1:0] row0_next [0:16];
    logic [DATAW-1:0] row1_next [0:16];
    logic [DATAW-1:0] row2_next [0:2];
    logic [DATAW-1:0] row3_next;

    logic            dir_next;
    logic [8:0]      counter_next;
    logic            is_t1_reg_next;

    logic            out_valid_next;
    logic [DATAW-1:0] out_p00_next, out_p01_next, out_p02_next;
    logic [DATAW-1:0] out_p10_next, out_p11_next, out_p12_next;
    logic [DATAW-1:0] out_p20_next, out_p21_next, out_p22_next;

    logic is_t1;
    logic is_t2;
    logic is_valid;

    always_comb begin
        for (int i = 0; i <= 16; i++) begin
            row0_next[i] = row0[i];
            row1_next[i] = row1[i];
        end
        for (int i = 0; i <= 2; i++) begin
            row2_next[i] = row2[i];
        end
        row3_next       = row3;

        dir_next        = dir;
        counter_next    = counter;
        is_t1_reg_next  = is_t1_reg;

        out_valid_next  = out_valid;

        out_p00_next = row0[16];
        out_p01_next = row0[15];
        out_p02_next = row0[14];
        out_p10_next = row1[16];
        out_p11_next = row1[15];
        out_p12_next = row1[14];
        out_p20_next = row2[2];
        out_p21_next = row2[1];
        out_p22_next = row2[0];

        is_t1 = (counter[3:0] == 4'd0);
        is_t2 = is_t1_reg;

        if (in_valid || is_valid) begin
            counter_next = counter + 9'd1;
            is_t1_reg_next = is_t1;

            if (counter > 9'd17 && counter < 9'd290)
                out_valid_next = 1'b1;
            else
                out_valid_next = 1'b0;

            if (counter < 9'd14) begin
                for (int i = 0; i < 14; i++) begin
                    row1_next[i] = row1[i+1];
                end
                row1_next[14] = in_pixel;
            end
            else if (counter == 9'd14 || counter == 9'd15) begin
                row2_next[0] = row2[1];
                row2_next[1] = row2[2];
                row2_next[2] = in_pixel;
            end
            else if (counter >= 9'd16 && counter <= 9'd273) begin
                if (is_t2) begin
                    dir_next = ~dir;
                    if (counter != 9'd273) begin
                        row2_next[2] = in_pixel;
                        row2_next[1] = row3;
                    end
                    else begin
                        row2_next[1] = '0;
                        row2_next[2] = '0;
                    end

                    if (dir == 1'b1) begin
                        row1_next[14] = row2[1];
                        row0_next[14] = row1[14];
                        row1_next[15] = row2[2];
                        row0_next[15] = row1[15];
                    end
                    else begin
                        row1_next[16] = row2[1];
                        row0_next[16] = row1[16];
                        row1_next[15] = row2[2];
                        row0_next[15] = row1[15];
                    end
                end
                else begin
                    if (is_t1) begin
                        row3_next = in_pixel;
                        row2_next[0] = '0;
                    end
                    else begin
                        row2_next[0] = row2[1];
                        row2_next[1] = row2[2];
                        row2_next[2] = in_pixel;
                    end

                    if (dir == 1'b0) begin
                        for (int i = 16; i > 0; i--) begin
                            row0_next[i] = row0[i-1];
                            row1_next[i] = row1[i-1];
                        end
                        row0_next[0] = row1[16];
                        row1_next[0] = row2[0];
                    end
                    else begin
                        for (int i = 0; i < 16; i++) begin
                            row0_next[i] = row0[i+1];
                            row1_next[i] = row1[i+1];
                        end
                        row0_next[16] = row0[0];
                        row1_next[16] = row1[0];
                        if (counter != 9'd16) begin
                            row1_next[13] = row2[0];
                            row0_next[13] = row1[14];
                        end
                    end
                end
            end
            else begin
                for (int i = 16; i > 0; i--) begin
                    row0_next[i] = row0[i-1];
                    row1_next[i] = row1[i-1];
                end
                row0_next[0] = '0;
                row1_next[0] = '0;
            end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            is_valid <= 1'b0;
        end
        else begin
            if (in_valid) begin
                is_valid <= 1'b1;
            end else if (counter >= 9'd290) begin
                is_valid <= 1'b0;
            end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i <= 16; i++) begin
                row0[i]    <= '0;
                row1[i]    <= '0;
            end
            for (int i = 0; i <= 2; i++) begin
                row2[i] <= '0;
            end
            row3       <= '0;
            dir        <= 1'b1;
            counter    <= '0;
            is_t1_reg  <= 1'b0;
            out_valid  <= 1'b0;

            out_p00 <= '0; out_p01 <= '0; out_p02 <= '0;
            out_p10 <= '0; out_p11 <= '0; out_p12 <= '0;
            out_p20 <= '0; out_p21 <= '0; out_p22 <= '0;
        end
        else begin
            for (int i = 0; i <= 16; i++) begin
                row0[i] <= row0_next[i];
                row1[i] <= row1_next[i];
            end
            for (int i = 0; i <= 2; i++) begin
                row2[i] <= row2_next[i];
            end
            row3      <= row3_next;
            dir       <= dir_next;
            counter   <= counter_next;
            is_t1_reg <= is_t1_reg_next;

            out_valid <= out_valid_next;

            out_p00 <= out_p00_next;
            out_p01 <= out_p01_next;
            out_p02 <= out_p02_next;
            out_p10 <= out_p10_next;
            out_p11 <= out_p11_next;
            out_p12 <= out_p12_next;
            out_p20 <= out_p20_next;
            out_p21 <= out_p21_next;
            out_p22 <= out_p22_next;
        end
    end
endmodule

module addr_counter (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         en,
    output logic [8:0]   addr_counter
);
    logic [8:0] addr_counter_next;
    logic       dir, dir_next, k, k_next;

    always_comb begin
        addr_counter_next = addr_counter;
        dir_next = dir;
        k_next = k;
        if (en && addr_counter < 271) begin
            if (!k && (addr_counter[3:0] == 15 || (addr_counter != 0 && addr_counter[3:0] == 0))) begin
                addr_counter_next[8:4] = addr_counter[8:4] + 1;
                dir_next = ~dir;
                k_next = 1;
            end else begin
                addr_counter_next = dir ? addr_counter - 1 : addr_counter + 1;
                k_next = 0;
            end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            addr_counter <= 0;
            dir <= 0;
            k <= 0;
        end else begin
            addr_counter <= addr_counter_next;
            dir <= dir_next;
            k <= k_next;
        end
    end
endmodule

module addr_counter_reverse (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         en,
    output logic [8:0]   addr_counter
);
    logic [8:0] addr_counter_next;
    logic       dir, dir_next, k, k_next;

    always_comb begin
        addr_counter_next = addr_counter;
        dir_next = dir;
        k_next = k;
        if (en && addr_counter < 272) begin
            if (!k && ((addr_counter != 15 && addr_counter[3:0] == 15) || addr_counter[3:0] == 0)) begin
                addr_counter_next[8:4] = addr_counter[8:4] + 1;
                dir_next = ~dir;
                k_next = 1;
            end else begin
                addr_counter_next = dir ? addr_counter + 1 : addr_counter - 1;
                k_next = 0;
            end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            addr_counter <= 15;
            dir <= 0;
            k <= 0;
        end else begin
            addr_counter <= addr_counter_next;
            dir <= dir_next;
            k <= k_next;
        end
    end
endmodule