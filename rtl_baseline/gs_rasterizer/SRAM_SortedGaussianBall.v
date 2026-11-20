`define BIT_11   11    // 2**11>1089
`define WORD_1089  1089  // 1089

module SRAM_1089x11(clk, a, dout);
    input                        clk;   // posedge clk triggered
    input  [$clog2(`WORD_1089)-1:0]   a;     // address
    output [`BIT_11-1:0]            dout;  // data output

    reg [`BIT_11-1:0] MEM[`WORD_1089-1:0];
    reg [`BIT_11-1:0] dout_temp;

    initial begin
        $readmemb("gs_value.txt", MEM);
    end

    assign dout = dout_temp;

    always @(negedge clk) begin
        dout_temp <= MEM[a];
    end

endmodule