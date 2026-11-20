// Test Mode Settings:
// LVT
    // RTSEL = 2'b01
    // WTSEL = 2'b00
    // MTSEL = 2'b00

module SRAM_IMG(
    CLK,
    // READ PORT
    AB, REB, Q,
    // WRITE PORT
    AA, WEB, D,
    // TEST MODE
    RTSEL, WTSEL, MTSEL
);
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Parameter declarations
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    parameter  numIOBit    = 48;
    parameter  numWord     = 328;
    parameter  numWordAddr = $clog2(numWord);

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Input-Output declarations
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    input                       CLK;               // Clock
    input  [numWordAddr-1:0]    AB;                // Address read bus 
    input                       REB;               // Active-low Read enable
    output [numIOBit-1:0]       Q;                 // Data output bus
    input  [numWordAddr-1:0]    AA;                // Address write bus
    input                       WEB;               // Active-low Write enable
    input  [numIOBit-1:0]       D;                 // Date input bus
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Test Mode
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    input [1:0] RTSEL;  
    input [1:0] WTSEL;
    input [1:0] MTSEL;

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Signals
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    reg  [numIOBit-1:0]      mem[numWord-1:0];
    reg  [numIOBit-1:0]      dout_tmp;
    reg  [numIOBit-1:0]      din_tmp;
    wire [numWordAddr-1:0]   iAA         = AA;
    wire [numWordAddr-1:0]   iAB         = AB;
    wire [numIOBit-1:0]      iD          = D;
    wire                     iREB        = REB;
    wire                     iWEB        = WEB;
    wire                     pwr_mode    = 1'b0;
    wire [numIOBit-1:0]      Q_int;

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Initialization
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    integer k;
    initial begin
        dout_tmp = 0;
        for (k = 0; k < numWord; k = k + 1) begin
            mem[k] = 48'h0;
        end
    end
    
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Control Logic
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    assign Q_int    = dout_tmp;
    assign Q        = pwr_mode? {numIOBit{1'b0}} : Q_int;
    always @(posedge CLK) begin
        if (pwr_mode == 1'b0) begin
            if (iREB == 1'b0) begin
                dout_tmp <= mem[iAB];
            end
        end
    end
    genvar i;
    generate
        for (i=0; i<numIOBit; i=i+1) begin: BIT_WISE
            always @(posedge CLK) begin
                    if (iWEB == 1'b0) begin // write
                        mem[iAA][i] <= iD[i]; 
                    end
            end
    end // BIT_WISE
    endgenerate
    
endmodule



module SRAM_GT(
    CLK,
    // READ PORT
    AB, REB, Q,
    // WRITE PORT
    AA, WEB, D,
    // TEST MODE
    RTSEL, WTSEL, MTSEL
);
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Parameter declarations
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    parameter  numIOBit    = 48;
    parameter  numWord     = 328;
    parameter  numWordAddr = $clog2(numWord);

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Input-Output declarations
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    input                       CLK;               // Clock
    input  [numWordAddr-1:0]    AB;                // Address read bus 
    input                       REB;               // Active-low Read enable
    output [numIOBit-1:0]       Q;                 // Data output bus
    input  [numWordAddr-1:0]    AA;                // Address write bus
    input                       WEB;               // Active-low Write enable
    input  [numIOBit-1:0]       D;                 // Date input bus
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Test Mode
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    input [1:0] RTSEL;  
    input [1:0] WTSEL;
    input [1:0] MTSEL;

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Signals
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    reg  [numIOBit-1:0]      mem[numWord-1:0];
    reg  [numIOBit-1:0]      dout_tmp;
    reg  [numIOBit-1:0]      din_tmp;
    wire [numWordAddr-1:0]   iAA         = AA;
    wire [numWordAddr-1:0]   iAB         = AB;
    wire [numIOBit-1:0]      iD          = D;
    wire                     iREB        = REB;
    wire                     iWEB        = WEB;
    wire                     pwr_mode    = 1'b0;
    wire [numIOBit-1:0]      Q_int;

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Initialization
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    integer k;
    initial begin
        dout_tmp = 0;
        for (k = 0; k < numWord; k = k + 1) begin
            mem[k] = 48'h0;
        end
    end

// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
// Control Logic
// +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    assign Q_int    = dout_tmp;
    assign Q        = pwr_mode? {numIOBit{1'b0}} : Q_int;
    always @(posedge CLK) begin
        if (pwr_mode == 1'b0) begin
            if (iREB == 1'b0) begin
                dout_tmp <= mem[iAB];
            end
        end
    end
    genvar i;
    generate
        for (i=0; i<numIOBit; i=i+1) begin: BIT_WISE
            always @(posedge CLK) begin
                    if (iWEB == 1'b0) begin // write
                        mem[iAA][i] <= iD[i]; 
                    end
            end
    end // BIT_WISE
    endgenerate
    
endmodule