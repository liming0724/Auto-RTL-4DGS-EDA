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
    output logic        write_enable,
    // Directed-vector probes: stage-7 alpha clamp, committed stage-10 entry in PROCESS,
    // and that entry being the T < 1e-4 crossing (s10_altb).
    output logic        probe_clamp7,
    output logic        probe_commit10,
    output logic        probe_altb10
);
    GS_RASTERIZER_pipeline dut (
        .clk(clk), .rst_n(rst_n), .RGB(RGB), .D(D), .T(T), .N(N),
        .Finish(Finish), .en(en)
    );

    assign write_addr = dut.sram2.a;
    assign write_enable = dut.sram2.we;
    assign probe_clamp7 = dut.stage_valid_7 && dut.s7_alpha_clamp;
    assign probe_commit10 = dut.state == 3'd2 /* PROCESS */ && dut.stage_valid_10 &&
                            !(dut.alpha_too_small_1_s10 || dut.beta_positive_3_s10);
    assign probe_altb10 = probe_commit10 && dut.s10_altb;
endmodule
