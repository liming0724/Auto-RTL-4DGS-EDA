`define BIT_W     48      // 16-bit per channel × 3 channels = 48 bits
`define WORD_CNT  272     // 16 * 17 = 272 pixels

// ============================================================================
// SRAM for IMG (Input Image)
// ============================================================================
// module SRAM_IMG (
//     input                              clk,
//     input                              wen,     // Write enable
//     input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
//     input  [`BIT_W-1:0]                din,     // Data input (48 bits)
//     output [`BIT_W-1:0]                dout     // Data output (48 bits)
// );
//     reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
//     reg [`BIT_W-1:0] dout_reg;

//     assign dout = dout_reg;

//     always @(posedge clk) begin
//         if (wen) begin
//             MEM[addr] <= din;
//         end
//         dout_reg <= MEM[addr];
//     end
// endmodule

// // ============================================================================
// // SRAM for GT (Ground Truth)
// // ============================================================================
// module SRAM_GT (
//     input                              clk,
//     input                              wen,     // Write enable
//     input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
//     input  [`BIT_W-1:0]                din,     // Data input (48 bits)
//     output [`BIT_W-1:0]                dout     // Data output (48 bits)
// );
//     reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
//     reg [`BIT_W-1:0] dout_reg;

//     assign dout = dout_reg;

//     always @(posedge clk) begin
//         if (wen) begin
//             MEM[addr] <= din;
//         end
//         dout_reg <= MEM[addr];
//     end
// endmodule

// ============================================================================
// SRAM for μ₁ (Mean of IMG)
// ============================================================================
module SRAM_MU1 (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for μ₂ (Mean of GT)
// ============================================================================
module SRAM_MU2 (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for Intermediate Result A
// ============================================================================
module SRAM_A (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for Intermediate Result B
// ============================================================================
module SRAM_B (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for Intermediate Result C
// ============================================================================
module SRAM_C (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for Intermediate Result D
// ============================================================================
module SRAM_D (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule

// ============================================================================
// SRAM for final result SSIM_SCORE
// ============================================================================
module SRAM_SSIM_SCORE (
    input                              clk,
    input                              wen,     // Write enable
    input  [$clog2(`WORD_CNT)-1:0]     addr,    // Address (0 to 271)
    input  [`BIT_W-1:0]                din,     // Data input (48 bits)
    output [`BIT_W-1:0]                dout     // Data output (48 bits)
);
    reg [`BIT_W-1:0] MEM [`WORD_CNT-1:0];
    reg [`BIT_W-1:0] dout_reg;

    assign dout = dout_reg;

    always @(posedge clk) begin
        if (wen) begin
            MEM[addr] <= din;
        end
        dout_reg <= MEM[addr];
    end
endmodule