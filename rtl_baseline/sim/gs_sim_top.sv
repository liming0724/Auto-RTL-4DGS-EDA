module gs_sim_top (
    input  logic        clk,
    input  logic        rst_n,
    output logic [23:0] RGB,
    output logic [31:0] D,
    output logic [31:0] T,
    output logic [10:0] N,
    output logic        Finish,
    output logic        en,
    output logic [11:0] write_addr,
    output logic        write_enable
);
    GS_RASTERIZER_pipeline dut (
        .clk(clk), .rst_n(rst_n), .RGB(RGB), .D(D), .T(T), .N(N),
        .Finish(Finish), .en(en)
    );

    assign write_addr = dut.sram2.a;
    assign write_enable = dut.sram2.we;
endmodule
