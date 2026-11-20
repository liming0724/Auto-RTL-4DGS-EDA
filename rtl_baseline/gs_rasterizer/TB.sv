`timescale 1ns / 1ps
`define CYCLE_TIME 15
`define MAX_CYCLES 10000 // Maximum number of cycles to run the simulation, to prevent infinite loops
`define SDFFILE  "./GS_RASTERIZER_syn.sdf"	// Modify your sdf file name

module tb;
//======================================================================
//   PARAMETER & INTEGER DECLARATION
//======================================================================
parameter INST_SIG_WIDTH       = 23;
parameter INST_EXP_WIDTH       = 8;
parameter INST_IEEE_COMPLIANCE = 0;
parameter FP_WIDTH             = INST_SIG_WIDTH + INST_EXP_WIDTH + 1;
parameter LENGTH               = 1089;
parameter LEN_WIDTH            = $clog2(LENGTH);
parameter LEN_BUFFER           = 2550; // 75 * 34

integer   total_latency;
integer   scan_result;
integer   f_read;
integer   i;
integer   _;
//======================================================================
//   PORT DECLARATION
//======================================================================
logic                          clk, rst_n;
logic [2*FP_WIDTH-1:0]         mean2d;
logic [3*FP_WIDTH-1:0]         rgb2d;
logic [FP_WIDTH-1:0]           depth2d;
logic [6*FP_WIDTH-1:0]         conic2d;
logic [$clog2(LENGTH)-1:0]     gs_value;
logic [$clog2(LENGTH)-1:0]     length;
logic [$clog2(LENGTH)-1:0]     idx_2dgs;
logic [$clog2(LENGTH)-1:0]     idx_value;
logic                          web;
logic [23:0]                   rgb;
logic [FP_WIDTH-1:0]           depth;
logic [FP_WIDTH-1:0]           t;
logic [$clog2(LENGTH)-1:0]     n;
logic                          finish;
//======================================================================
//   Internal Signal Declaration
//======================================================================
reg   [8*64-1:0]               line;
logic [2*FP_WIDTH-1:0]         mean2ds   [0:LENGTH-1];
logic [3*FP_WIDTH-1:0]         rgbs      [0:LENGTH-1];
logic [FP_WIDTH-1:0]           depths    [0:LENGTH-1];
logic [6*FP_WIDTH-1:0]         conic2ds  [0:LENGTH-1];
logic [$clog2(LENGTH)-1:0]     gs_values [0:LENGTH-1];
logic [2*FP_WIDTH-1:0]         mean2d_tmp;  
logic [3*FP_WIDTH-1:0]         rgb_tmp;     
logic [FP_WIDTH-1:0]           depth_tmp;   
logic [6*FP_WIDTH-1:0]         conic2d_tmp;
logic [$clog2(LENGTH)-1:0]     gs_value_tmp;
logic [23:0]                   rgb_buffer   [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]           depth_buffer [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]           t_buffer     [0:LEN_BUFFER-1];
logic [$clog2(LENGTH)-1:0]     n_buffer     [0:LEN_BUFFER-1];
//======================================================================
//   MODULE INSTANTIATION
//======================================================================
GS_RASTERIZER_pipeline U_GS_RASTERIZER_pipeline ( // 高斯球排序索引
    .clk(clk),
    .rst_n(rst_n),
    .RGB(rgb),
    .D(depth),
    .T(t),
    .N(n),
    .Finish(finish),
    .en(check_time)
);

`ifdef SDF
	initial $sdf_annotate(`SDFFILE, U_GS_RASTERIZER_pipeline);
`endif

//======================================================================
//   Assignments
//======================================================================
assign length   = LENGTH;
// 2DGS DATA 
assign #0.2 mean2d   = mean2d_tmp;
assign #0.2 rgb2d    = rgb_tmp;
assign #0.2 depth2d  = depth_tmp;
assign #0.2 conic2d  = conic2d_tmp;
// GS_VALUE DATA
assign #0.2 gs_value = gs_value_tmp;
// assign finish = 0;
// assign idx_2dgs = 0; // This should be set by the GS_RASTERIZER module
// assign idx_value = 100; // This should be set by the GS_RASTERIZER module

//======================================================================
//   CLOCK GENERATION
//======================================================================
real    CYCLE = `CYCLE_TIME;
initial begin
    clk = 0;
    forever #(CYCLE / 2.0) clk = ~clk;
end
//======================================================================
//   SRAM READ OUT
//======================================================================
// 2DGS DATA
always_ff @(posedge clk) begin
    mean2d_tmp  <= mean2ds[idx_2dgs];
    rgb_tmp     <= rgbs[idx_2dgs]; 
    depth_tmp   <= depths[idx_2dgs]; 
    conic2d_tmp <= conic2ds[idx_2dgs];
end
// GS_VALUE DATA
always_ff @(posedge clk) begin
    gs_value_tmp <= gs_values[idx_value];
end
//======================================================================
//   SRAM WRITE IN
//======================================================================
integer waddr;
always_ff @(posedge clk) begin
    rgb_buffer[waddr]   <= rgb;
    depth_buffer[waddr] <= depth;
    t_buffer[waddr]     <= t;
    n_buffer[waddr]     <= n;
end


//======================================================================
//   Pattern_Design
//======================================================================
initial begin
    load_data_task;
    reset_task;
    wait_finish_task;
    // check_answer_task;
    $finish;
end


//======================================================================
//   Task
//======================================================================
// task laod_data_task; begin
//     // load Mean2Ds
//     f_read = $fopen("../00_Testbed/pattern/mean2Ds29.txt", "r");
//     if (f_read == 0) begin
//         $display("Error: Cannot open ../00_Testbed/pattern/mean2Ds29.txt");
//         $finish;
//     end
//     for (i = 0; i < LENGTH; i = i + 1) begin
//         scan_result = $fscanf(f_read, "%h %h\n", mean2ds[i][2*FP_WIDTH-1:FP_WIDTH], mean2ds[i][FP_WIDTH-1:0]);
//     end
//     $fclose(f_read);
//     // load RGBs
//     f_read = $fopen("../00_Testbed/pattern/rgbs29.txt", "r");
//     if (f_read == 0) begin
//         $display("Error: Cannot open ../00_Testbed/pattern/rgbs29.txt");
//         $finish;
//     end
//     _ = $fgets(line, f_read); // Skip the first line
//     for (i = 0; i < LENGTH; i = i + 1) begin
//         scan_result = $fscanf(f_read, "%h %h %h\n", rgbs[i][3*FP_WIDTH-1:2*FP_WIDTH], rgbs[i][2*FP_WIDTH-1:FP_WIDTH], rgbs[i][FP_WIDTH-1:0]);
//     end
//     $fclose(f_read);
//     // load Depths
//     f_read = $fopen("../00_Testbed/pattern/depths29.txt", "r");
//     if (f_read == 0) begin
//         $display("Error: Cannot open ../00_Testbed/pattern/depths29.txt");
//         $finish;
//     end
//     for (i = 0; i < LENGTH; i = i + 1) begin
//         scan_result = $fscanf(f_read, "%h\n", depths[i][FP_WIDTH-1:0]);
//     end
//     $fclose(f_read);
//     // load Conic2Ds
//     f_read = $fopen("../00_Testbed/pattern/conics29.txt", "r");
//     if (f_read == 0) begin
//         $display("Error: Cannot open ../00_Testbed/pattern/conics29.txt");
//         $finish;
//     end
//     _ = $fgets(line, f_read); // Skip the first line
//     for (i = 0; i < LENGTH; i = i + 1) begin
//         scan_result = $fscanf(f_read, "%h %h %h %h %h %h\n", 
//                               conic2ds[i][6*FP_WIDTH-1:5*FP_WIDTH], conic2ds[i][5*FP_WIDTH-1:4*FP_WIDTH], conic2ds[i][4*FP_WIDTH-1:3*FP_WIDTH], 
//                               conic2ds[i][3*FP_WIDTH-1:2*FP_WIDTH], conic2ds[i][2*FP_WIDTH-1:FP_WIDTH],   conic2ds[i][FP_WIDTH-1:0]);
//     end
//     $fclose(f_read);
//     // load GS_Values
//     f_read = $fopen("../00_Testbed/pattern/gs_value29.txt", "r");
//     if (f_read == 0) begin
//         $display("Error: Cannot open ../00_Testbed/pattern/gs_value29.txt");
//         $finish;
//     end
//     for (i = 0; i < LENGTH; i = i + 1) begin
//         scan_result = $fscanf(f_read, "%d\n", gs_values[i][$clog2(LENGTH)-1:0]);
//     end
//     $fclose(f_read);
// end endtask
parameter color_len = 8;
logic [$clog2(LENGTH)-1:0] N_ans_next      [0:LEN_BUFFER-1];
logic [$clog2(LENGTH)-1:0] N_ans [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]       T_ans_next      [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]       T_ans [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]       D_ans_next      [0:LEN_BUFFER-1];
logic [FP_WIDTH-1:0]       D_ans [0:LEN_BUFFER-1];
logic [color_len-1:0]      R_ans_next      [0:LEN_BUFFER-1];
logic [color_len-1:0]      R_ans [0:LEN_BUFFER-1];
logic [color_len-1:0]      G_ans_next      [0:LEN_BUFFER-1];
logic [color_len-1:0]      G_ans [0:LEN_BUFFER-1];
logic [color_len-1:0]      B_ans_next      [0:LEN_BUFFER-1];
logic [color_len-1:0]      B_ans [0:LEN_BUFFER-1];

always_ff @(posedge clk) begin
    N_ans  <= N_ans_next;
    T_ans  <= T_ans_next;
    D_ans  <= D_ans_next;
    R_ans  <= R_ans_next;
    G_ans  <= G_ans_next;
    B_ans  <= B_ans_next;
end

task load_data_task; begin
    // load Golden answer
    // load Ns
    f_read = $fopen("../Algo/pattern/golden_ans/N_img.txt", "r");
    if (f_read == 0) begin
        $display("Error: Cannot open ../Algo/pattern/golden_ans/N_img.txt");
        $finish;
    end
    for (i = 0; i < LEN_BUFFER; i = i + 1) begin
        scan_result = $fscanf(f_read, "%d\n", N_ans_next[i]);
    end
    $fclose(f_read);
    // load Ts
    f_read = $fopen("../Algo/pattern/golden_ans/T_img.txt", "r");
    if (f_read == 0) begin
        $display("Error: Cannot open ../Algo/pattern/golden_ans/T_img.txt");
        $finish;
    end
    for (i = 0; i < LEN_BUFFER; i = i + 1) begin
        scan_result = $fscanf(f_read, "%h\n", T_ans_next[i]);
    end
    $fclose(f_read);
    // load RGBs
    f_read = $fopen("../Algo/pattern/golden_ans/rendered_img.txt", "r");
    if (f_read == 0) begin
        $display("Error: Cannot open ../Algo/pattern/golden_ans/rendered_img.txt");
        $finish;
    end
    for (i = 0; i < LEN_BUFFER; i = i + 1) begin
        scan_result = $fscanf(f_read, "%d %d %d\n", R_ans_next[i], G_ans_next[i], B_ans_next[i]);
    end
    $fclose(f_read);
    // load Ds
    f_read = $fopen("../Algo/pattern/golden_ans/depth_img.txt", "r");
    if (f_read == 0) begin
        $display("Error: Cannot open ../Algo/pattern/golden_ans/depth_img.txt");
        $finish;
    end
    for (i = 0; i < LEN_BUFFER; i = i + 1) begin
        scan_result = $fscanf(f_read, "%h\n", D_ans_next[i]);
    end
end endtask

task reset_task; begin
    force clk     = 1'b0;
    rst_n         = 1'b0;
    #100;
    rst_n         = 1'b1;
    if (finish !== 1'b0) begin
        $display("**************************************************");
        $display("Error: finish signal is not reset to 0");
        $display("**************************************************");
        repeat (2) #CYCLE;
        $finish;
    end
    release clk;
end endtask

// logic check_time;
task wait_finish_task; begin
    total_latency = 0;
    waddr = 0;
    while (finish !== 1'b1) begin
        if (total_latency > `MAX_CYCLES) begin
            $display("********************************************************");
            $display("Error: Simulation exceeded maximum cycles (%d)", `MAX_CYCLES);
            $display("********************************************************");
            repeat (2) #CYCLE;
            $finish;
        end else if (check_time) begin
            check_answer_task;
            waddr = waddr + 1;
        end
        total_latency = total_latency + 1;
        @(negedge clk);
    end
    YOU_PASS_task;

end endtask

task check_answer_task; begin
    real diff;
    integer diff_color;
    // Check if the output matches the golden answer
    if (n_buffer[waddr] !== N_ans[waddr]) begin
        $display("******************************************************************");
        $display("Error: Mismatch in N at index %0d: expected %0d, got %0d", waddr, N_ans[waddr], n_buffer[waddr]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end
    
    diff = $bitstoshortreal(t_buffer[waddr]) - $bitstoshortreal(T_ans[waddr]);
    if (diff < 0) diff = -diff;
    if (diff > 1e-4) begin
        $display("******************************************************************");
        $display("Error: Mismatch in T at index %0d: expected %h, got %h", waddr, T_ans[waddr], t_buffer[waddr]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end

    diff = $bitstoshortreal(depth_buffer[waddr]) - $bitstoshortreal(D_ans[waddr]);
    if (diff < 0) diff = -diff;
    if (diff > 1e-4) begin
        $display("******************************************************************");
        $display("Error: Mismatch in D at index %0d: expected %h, got %h", waddr, D_ans[waddr], depth_buffer[waddr]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end
    diff_color = rgb_buffer[waddr][23:16] - R_ans[waddr];
    if (diff_color < 0) diff_color = -diff_color;
    if (diff_color > 1) begin
        $display("******************************************************************");
        $display("Error: Mismatch in R at index %0d: expected %h, got %h", waddr, R_ans[waddr], rgb_buffer[waddr][23:16]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end

    diff_color = rgb_buffer[waddr][15:8] - G_ans[waddr];
    if (diff_color < 0) diff_color = -diff_color;
    if (diff_color > 1) begin
        $display("******************************************************************");
        $display("Error: Mismatch in G at index %0d: expected %h, got %h", waddr, G_ans[waddr], rgb_buffer[waddr][15:8]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end

    diff_color = rgb_buffer[waddr][7:0] - B_ans[waddr];
    if (diff_color < 0) diff_color = -diff_color;
    if (diff_color > 1) begin
        $display("******************************************************************");
        $display("Error: Mismatch in B at index %0d: expected %h, got %h", waddr, B_ans[waddr], rgb_buffer[waddr][7:0]);
        $display("******************************************************************");
        repeat (2) #CYCLE;
        $finish;
    end
    $display("pass at %0d", waddr);
end endtask

task YOU_PASS_task;begin
$display("\033[37m                                                                                                                                          ");        
$display("\033[37m                                                                                \033[32m      :BBQvi.                                              ");        
$display("\033[37m                                                              .i7ssrvs7         \033[32m     BBBBBBBBQi                                           ");        
$display("\033[37m                        .:r7rrrr:::.        .::::::...   .i7vr:.      .B:       \033[32m    :BBBP :7BBBB.                                         ");        
$display("\033[37m                      .Kv.........:rrvYr7v7rr:.....:rrirJr.   .rgBBBBg  Bi      \033[32m    BBBB     BBBB                                         ");        
$display("\033[37m                     7Q  :rubEPUri:.       ..:irrii:..    :bBBBBBBBBBBB  B      \033[32m   iBBBv     BBBB       vBr                               ");        
$display("\033[37m                    7B  BBBBBBBBBBBBBBB::BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB :R     \033[32m   BBBBBKrirBBBB.     :BBBBBB:                            ");        
$display("\033[37m                   Jd .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: Bi    \033[32m  rBBBBBBBBBBBR.    .BBBM:BBB                             ");        
$display("\033[37m                  uZ .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB .B    \033[32m  BBBB   .::.      EBBBi :BBU                             ");        
$display("\033[37m                 7B .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  B    \033[32m MBBBr           vBBBu   BBB.                             ");        
$display("\033[37m                .B  BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: JJ   \033[32m i7PB          iBBBBB.  iBBB                              ");        
$display("\033[37m                B. BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  Lu             \033[32m  vBBBBPBBBBPBBB7       .7QBB5i                ");        
$display("\033[37m               Y1 KBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBi XBBBBBBBi :B            \033[32m :RBBB.  .rBBBBB.      rBBBBBBBB7              ");        
$display("\033[37m              :B .BBBBBBBBBBBBBsRBBBBBBBBBBBrQBBBBB. UBBBRrBBBBBBr 1BBBBBBBBB  B.          \033[32m    .       BBBB       BBBB  :BBBB             ");        
$display("\033[37m              Bi BBBBBBBBBBBBBi :BBBBBBBBBBE .BBK.  .  .   QBBBBBBBBBBBBBBBBBB  Bi         \033[32m           rBBBr       BBBB    BBBU            ");        
$display("\033[37m             .B .BBBBBBBBBBBBBBQBBBBBBBBBBBB       \033[38;2;242;172;172mBBv \033[37m.LBBBBBBBBBBBBBBBBBBBBBB. B7.:ii:   \033[32m           vBBB        .BBBB   :7i.            ");        
$display("\033[37m            .B  PBBBBBBBBBBBBBBBBBBBBBBBBBBBBbYQB. \033[38;2;242;172;172mBB: \033[37mBBBBBBBBBBBBBBBBBBBBBBBBB  Jr:::rK7 \033[32m             .7  BBB7   iBBBg                  ");        
$display("\033[37m           7M  PBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  \033[38;2;242;172;172mBB. \033[37mBBBBBBBBBBBBBBBBBBBBBBB..i   .   v1                  \033[32mdBBB.   5BBBr                 ");        
$display("\033[37m          sZ .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  \033[38;2;242;172;172mBB. \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBB iD2BBQL.                 \033[32m ZBBBr  EBBBv     YBBBBQi     ");        
$display("\033[37m  .7YYUSIX5 .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  \033[38;2;242;172;172mBB. \033[37mBBBBBBBBBBBBBBBBBBBBBBBBY.:.      :B                 \033[32m  iBBBBBBBBD     BBBBBBBBB.   ");        
$display("\033[37m LB.        ..BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB. \033[38;2;242;172;172mBB: \033[37mBBBBBBBBBBBBBBBBBBBBBBBBMBBB. BP17si                 \033[32m    :LBBBr      vBBBi  5BBB   ");        
$display("\033[37m  KvJPBBB :BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: \033[38;2;242;172;172mZB: \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBsiJr .i7ssr:                \033[32m          ...   :BBB:   BBBu  ");        
$display("\033[37m i7ii:.   ::BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBj \033[38;2;242;172;172muBi \033[37mQBBBBBBBBBBBBBBBBBBBBBBBBi.ir      iB                \033[32m         .BBBi   BBBB   iMBu  ");        
$display("\033[37mDB    .  vBdBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBg \033[38;2;242;172;172m7Bi \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBBBB rBrXPv.                \033[32m          BBBX   :BBBr        ");        
$display("\033[37m :vQBBB. BQBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBQ \033[38;2;242;172;172miB: \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBBBB .L:ii::irrrrrrrr7jIr   \033[32m          .BBBv  :BBBQ        ");        
$display("\033[37m :7:.   .. 5BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  \033[38;2;242;172;172mBr \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBBB:            ..... ..YB. \033[32m           .BBBBBBBBB:        ");        
$display("\033[37mBU  .:. BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  \033[38;2;242;172;172mB7 \033[37mgBBBBBBBBBBBBBBBBBBBBBBBBBB. gBBBBBBBBBBBBBBBBBB. BL \033[32m             rBBBBB1.         ");        
$display("\033[37m rY7iB: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: \033[38;2;242;172;172mB7 \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBB. QBBBBBBBBBBBBBBBBBi  v5                                ");        
$display("\033[37m     us EBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB \033[38;2;242;172;172mIr \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBgu7i.:BBBBBBBr Bu                                 ");        
$display("\033[37m      B  7BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB.\033[38;2;242;172;172m:i \033[37mBBBBBBBBBBBBBBBBBBBBBBBBBBBv:.  .. :::  .rr    rB                                  ");        
$display("\033[37m      us  .BBBBBBBBBBBBBQLXBBBBBBBBBBBBBBBBBBBBBBBBq  .BBBBBBBBBBBBBBBBBBBBBBBBBv  :iJ7vri:::1Jr..isJYr                                   ");        
$display("\033[37m      B  BBBBBBB  MBBBM      qBBBBBBBBBBBBBBBBBBBBBB: BBBBBBBBBBBBBBBBBBBBBBBBBB  B:           iir:                                       ");        
$display("\033[37m     iB iBBBBBBBL       BBBP. :BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  B.                                                       ");        
$display("\033[37m     P: BBBBBBBBBBB5v7gBBBBBB  BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: Br                                                        ");        
$display("\033[37m     B  BBBs 7BBBBBBBBBBBBBB7 :BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB .B                                                         ");        
$display("\033[37m    .B :BBBB.  EBBBBBQBBBBBJ .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB. B.                                                         ");        
$display("\033[37m    ij qBBBBBg          ..  .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB .B                                                          ");        
$display("\033[37m    UY QBBBBBBBBSUSPDQL...iBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBK EL                                                          ");        
$display("\033[37m    B7 BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB: B:                                                          ");        
$display("\033[37m    B  BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBYrBB vBBBBBBBBBBBBBBBBBBBBBBBB. Ls                                                          ");        
$display("\033[37m    B  BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBi_  /UBBBBBBBBBBBBBBBBBBBBBBBBB. :B:                                                        ");        
$display("\033[37m   rM .BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB  ..IBBBBBBBBBBBBBBBBQBBBBBBBBBB  B                                                        ");        
$display("\033[37m   B  BBBBBBBBBdZBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBPBBBBBBBBBBBBEji:..     sBBBBBBBr Br                                                       ");        
$display("\033[37m  7B 7BBBBBBBr     .:vXQBBBBBBBBBBBBBBBBBBBBBBBBBQqui::..  ...i:i7777vi  BBBBBBr Bi                                                       ");        
$display("\033[37m  Ki BBBBBBB  rY7vr:i....  .............:.....  ...:rii7vrr7r:..      7B  BBBBB  Bi                                                       ");        
$display("\033[37m  B. BBBBBB  B:    .::ir77rrYLvvriiiiiiirvvY7rr77ri:..                 bU  iQBB:..rI                                                      ");        
$display("\033[37m.S: 7BBBBP  B.                                                          vI7.  .:.  B.                                                     ");        
$display("\033[37mB: ir:.   :B.                                                             :rvsUjUgU.                                                      ");        
$display("\033[37mrMvrrirJKur                                                                                                                               \033[m");
$display ("----------------------------------------------------------------------------------------------------------------------");
$display ("                                                  Congratulations!                						             ");
$display ("                                           You have passed all patterns!          						             ");
$display ("                                        Your design's latency is %d cycles.   	        				             ", total_latency);
$display ("----------------------------------------------------------------------------------------------------------------------");



$finish;	
end endtask

endmodule

