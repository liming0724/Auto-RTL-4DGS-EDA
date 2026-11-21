// SRAM macro boundaries; do not read simulation images during synthesis.
module SRAM_1089x11 (input wire clk, input wire [10:0] a, output wire [10:0] dout);
endmodule

module SRAM_1089x384 (input wire clk, input wire [10:0] a, output wire [383:0] dout);
endmodule

module SRAM_2550x99 (
    input wire clk, we, input wire [11:0] a, input wire [98:0] din,
    output wire [98:0] dout
);
endmodule
