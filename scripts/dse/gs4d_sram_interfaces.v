// Opaque SRAM macro boundary for rtl_4dgs synthesis metrics (one cache way); the
// behavioral model rtl_4dgs/SRAM_gs4d_cache.sv is used for simulation and formal only.
module SRAM_gs4d_cache #(parameter AW = 8, parameter DW = 100) (
    input wire clk, we, input wire [AW-1:0] waddr, input wire [DW-1:0] wdata,
    input wire re, input wire [AW-1:0] raddr, output wire [DW-1:0] rdata
);
endmodule
