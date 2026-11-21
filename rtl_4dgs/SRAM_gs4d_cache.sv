// Behavioral 1R1W synchronous SRAM for one way of motion_aware_cache.
// Simulation / formal model only: synthesis metrics treat SRAM_* as an opaque macro
// (scripts/dse/gs4d_sram_interfaces.v), as the rtl_baseline flow does.
// Read-first: a read and a write to the same row in one cycle return the old word.
// Rows are explicit registers (not a memory array) so formal netlists keep their names.
module SRAM_gs4d_cache #(
    parameter int AW = 8,
    parameter int DW = 100
) (
    input  logic          clk,
    input  logic          we,
    input  logic [AW-1:0] waddr,
    input  logic [DW-1:0] wdata,
    input  logic          re,
    input  logic [AW-1:0] raddr,
    output logic [DW-1:0] rdata
);
    localparam int DEPTH = 1 << AW;
    logic [DEPTH*DW-1:0] rows;

    for (genvar a = 0; a < DEPTH; a++) begin : g_row
        always_ff @(posedge clk) begin
            if (we && waddr == AW'(a)) rows[a*DW +: DW] <= wdata;
        end
    end

    always_ff @(posedge clk) begin
        if (re) rdata <= rows[raddr*DW +: DW];
    end
endmodule
