`define BIT_GVDS   384   // 32*12=384
`define WORD_GVDS  1089  // 1089

module SRAM_1089x384(clk, a, dout);
    input                        clk;   // posedge clk triggered
    input  [$clog2(`WORD_GVDS)-1:0]   a;     // address
    output [`BIT_GVDS-1:0]            dout;  // data output

    reg [`BIT_GVDS-1:0] MEM[`WORD_GVDS-1:0];
    reg [`BIT_GVDS-1:0] dout_temp;

    initial begin
        $readmemh("ram_file.txt", MEM);
    end

    assign dout = dout_temp;

    always @(negedge clk) begin
        dout_temp <= MEM[a];
    end

endmodule