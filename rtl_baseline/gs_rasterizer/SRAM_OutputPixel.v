`define BIT_99   99   // 24+32+32+11=99
`define WORD_2550  2550  // 75*34=2550

module SRAM_2550x99(clk, din, a, we, dout);
    input                        clk;   // posedge clk triggered
    input                        we;    // write enable (active high)
    input  [$clog2(`WORD_2550)-1:0]   a;     // address
    input  [`BIT_99-1:0]            din;   // data input
    output [`BIT_99-1:0]            dout;  // data output

    reg [`BIT_99-1:0] MEM[`WORD_2550-1:0];
    reg [`BIT_99-1:0] dout_temp;

    assign dout = dout_temp;

    always @(negedge clk) begin
        dout_temp <= MEM[a];

        if (we) begin
            MEM[a] <= din;
        end
    end

endmodule