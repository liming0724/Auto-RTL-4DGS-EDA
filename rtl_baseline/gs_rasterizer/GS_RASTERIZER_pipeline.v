`define px_offset 975
`define py_offset 34

module GS_RASTERIZER_pipeline (
    input  logic        clk,
    input  logic        rst_n,
    output logic [23:0] RGB,
    output logic [31:0] D,
    output logic [31:0] T,
    output logic [10:0] N,
    output logic        Finish,
    output logic        en
);

    // ============ fp parameterization ============
    parameter inst_sig_width       = 23;
    parameter inst_exp_width       = 8;
    parameter inst_ieee_compliance = 0;
    parameter inst_arch            = 2;
    parameter int_width_px_py      = 11;
    parameter int_width_rgb        = 9;
    parameter int_sign             = 0;

    // ============ top FSM ===============
    typedef enum logic [2:0] {
        IDLE,
        INIT_PIXEL,
        PROCESS,
        FINALIZE,
        WRITE_RESULT,
        COMPLETE
    } top_state_t;

    top_state_t state, state_next;

    // ============ SRAM interfaces ================
    logic [10:0]  addr0, addr0_next;
    logic [10:0]  dout0;
    logic [10:0]  addr1;
    logic [383:0] dout1;
    logic [11:0]  addr2, addr2_next;
    logic [98:0]  din2, dout2;
    logic         we2;

    assign addr1 = dout0;

    // Instantiate SRAMs
    SRAM_1089x11 sram0  (.clk(clk), .a(addr0),  .dout(dout0));
    SRAM_1089x384 sram1 (.clk(clk), .a(addr1),  .dout(dout1));
    SRAM_2550x99 sram2  (.clk(clk), .din(din2), .a(addr2), .we(we2), .dout(dout2));

    // ============ pixel counters ================
    logic [10:0] px, px_next;
    logic [6:0]  py, py_next;
    
    // ============ structs ================
    typedef struct packed {
        logic [31:0] ux;
        logic [31:0] uy;
        logic [31:0] red;
        logic [31:0] green;
        logic [31:0] blue;
        logic [31:0] depth;
        logic [31:0] conx;
        logic [31:0] cony;
        logic [31:0] conz;
        logic [31:0] conw;
        logic [31:0] coni;
        logic [31:0] conj;
    } GaussianBall_t;

    typedef struct packed {
        logic [31:0] red;
        logic [31:0] green;
        logic [31:0] blue;
        logic [31:0] depth;
        logic [31:0] transmittance;
        logic [10:0] num_of_gaussian;
    } Pixel_t;

    // ============ pipeline valid bits ============
    logic stage_valid_0, stage_valid_0_next;
    logic stage_valid_1, stage_valid_1_next;
    logic stage_valid_2, stage_valid_2_next;
    logic stage_valid_3, stage_valid_3_next;
    logic stage_valid_4, stage_valid_4_next;
    logic stage_valid_5, stage_valid_5_next;
    logic stage_valid_6, stage_valid_6_next;
    logic stage_valid_7, stage_valid_7_next;
    logic stage_valid_8, stage_valid_8_next;
    logic stage_valid_9, stage_valid_9_next;
    logic stage_valid_10, stage_valid_10_next;

    // ============ pipeline registers ============
    // stage0 不需要保存 addr0，因為直接從 addr0 讀取
    // stage1 不需要保存 dout0，因為直接連到 addr1

    GaussianBall_t g_s2, g_s2_next;
    GaussianBall_t g_s3, g_s3_next;
    GaussianBall_t g_s4, g_s4_next;
    GaussianBall_t g_s5, g_s5_next;
    GaussianBall_t g_s6, g_s6_next;
    GaussianBall_t g_s7, g_s7_next;
    GaussianBall_t g_s8, g_s8_next;
    GaussianBall_t g_s9, g_s9_next;

    Pixel_t current_pixel, current_pixel_next;

    // arithmetic pipeline regs
    logic [31:0] dx_s2, dx_s2_next;
    logic [31:0] dy_s2, dy_s2_next;
    logic [31:0] term1_s2, term1_s2_next;
    logic [31:0] term2_s2, term2_s2_next;
    logic [31:0] term3_s2, term3_s2_next;
    logic [31:0] term4_s2, term4_s2_next;
    
    logic [31:0] dx_sq_s3, dx_sq_s3_next;
    logic [31:0] dy_sq_s3, dy_sq_s3_next;
    logic [31:0] dx_dy_s3, dx_dy_s3_next;
    logic [31:0] depth_sum1_s3, depth_sum1_s3_next;
    logic [31:0] depth_sum2_s3, depth_sum2_s3_next;
    
    logic [31:0] beta_term1_s4, beta_term1_s4_next;
    logic [31:0] beta_term2_s4, beta_term2_s4_next;
    logic [31:0] beta_term3_s4, beta_term3_s4_next;
    logic [31:0] dx_depth_s4, dx_depth_s4_next;
    logic [31:0] dy_depth_s4, dy_depth_s4_next;
    
    logic [31:0] beta_term12_s5, beta_term12_s5_next;   // (term1 + term2) * -1/2
    logic [31:0] depth_sum_s5, depth_sum_s5_next;       // depth_sum1 + depth_sum2
    logic [31:0] beta_term3_1_s5, beta_term3_1_s5_next; // beta_term3_s4
    
    logic [31:0] beta_s6, beta_s6_next;                 // beta_term12_s5 - beta_term3_1_s5
    logic [31:0] depth_i_s6, depth_i_s6_next;
    
    logic [31:0] alpha_term_s7, alpha_term_s7_next;     // exp(beta)
    logic        beta_positive_s7, beta_positive_s7_next;
    logic [31:0] depth_i_1_s7, depth_i_1_s7_next;
    
    logic [31:0] alpha_i_s8, alpha_i_s8_next;           // opcacity * alpha_term
    logic [31:0] depth_i_2_s8, depth_i_2_s8_next;
    logic        beta_positive_1_s8, beta_positive_1_s8_next;

    logic [31:0] one_alpha_i_s9, one_alpha_i_s9_next;
    logic [31:0] T_accum_s9, T_accum_s9_next;
    logic [31:0] T_test_s9, T_test_s9_next;  // 新增
    logic        alpha_too_small_s9, alpha_too_small_s9_next;
    logic [31:0] depth_i_3_s9, depth_i_3_s9_next;
    logic        beta_positive_2_s9, beta_positive_2_s9_next;
    
    logic [31:0] T_test_s10, T_test_s10_next;
    logic [31:0] r_term_s10, r_term_s10_next; // T_accum * ci
    logic [31:0] g_term_s10, g_term_s10_next; // T_accum * ci
    logic [31:0] b_term_s10, b_term_s10_next; // T_accum * ci
    logic [31:0] d_term_s10, d_term_s10_next; // T_accum * depth_i
    logic        beta_positive_3_s10, beta_positive_3_s10_next;
    logic        alpha_too_small_1_s10, alpha_too_small_1_s10_next;


    // for debug
    logic [31:0] debug_red, debug_green, debug_blue, debug_depth, debug_transmittance;
    logic [10:0] debug_num_of_gaussian;
    assign debug_red           = current_pixel.red;
    assign debug_green         = current_pixel.green;
    assign debug_blue          = current_pixel.blue;
    assign debug_depth         = current_pixel.depth;
    assign debug_transmittance = current_pixel.transmittance;
    assign debug_num_of_gaussian = current_pixel.num_of_gaussian;
    
    logic        early_termination, early_termination_next;
    logic [31:0] T_testx15, T_testx15_next;                      // 等等
    
    // termination flags
    logic all_gaussians_done;
    logic all_pixels_done;

    // ============ Px Py  ============

    // float pixel coordinates
    logic [31:0] float_px, float_py;
    logic [31:0] float_px_s0, float_px_s0_next;
    logic [31:0] float_py_s0, float_py_s0_next;
    logic [31:0] float_px_s1, float_px_s1_next;
    logic [31:0] float_py_s1, float_py_s1_next;

    // I2F for px and py
    logic [10:0] px_offset_sum, py_offset_sum;
    logic [7:0] status_i2f_px, status_i2f_py;
    
    assign px_offset_sum = px + `px_offset;
    assign py_offset_sum = {4'b0, py} + `py_offset;

    DW_fp_i2flt #(inst_sig_width, inst_exp_width, int_width_px_py, int_sign) I2F_PX (
        .a(px_offset_sum), 
        .rnd(3'b0), 
        .z(float_px), 
        .status(status_i2f_px)
    );
    
    DW_fp_i2flt #(inst_sig_width, inst_exp_width, int_width_px_py, int_sign) I2F_PY (
        .a(py_offset_sum), 
        .rnd(3'b0), 
        .z(float_py), 
        .status(status_i2f_py)
    );

    always_comb begin                                         // 等等
        if (state == INIT_PIXEL) begin
            float_px_s0_next = float_px;
            float_py_s0_next = float_py;
            float_px_s1_next = float_px_s1;
            float_py_s1_next = float_py_s1;
        end else begin
            float_px_s0_next = float_px_s0;
            float_py_s0_next = float_py_s0;
            float_px_s1_next = float_px_s0;
            float_py_s1_next = float_py_s0;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            float_px_s0 <= 32'h0;
            float_py_s0 <= 32'h0;
            float_px_s1 <= 32'h0;
            float_py_s1 <= 32'h0;
        end else begin
            float_px_s0 <= float_px_s0_next;
            float_py_s0 <= float_py_s0_next;
            float_px_s1 <= float_px_s1_next;
            float_py_s1 <= float_py_s1_next;
        end
    end

    // ============ FSM next state logic (combinational) ============
    always_comb begin
        state_next = state;
        case (state)
            IDLE:       state_next = INIT_PIXEL;
            INIT_PIXEL: state_next = PROCESS;
            PROCESS:    begin
                if ((current_pixel.num_of_gaussian >= 11'd1089) || early_termination || all_gaussians_done) 
                    state_next = FINALIZE;
                else 
                    state_next = PROCESS;
            end
            FINALIZE:   state_next = WRITE_RESULT;
            WRITE_RESULT: state_next = (all_pixels_done) ? COMPLETE : INIT_PIXEL;
            COMPLETE:   state_next = COMPLETE;
        endcase
    end

    // ============ FSM sequential ============
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= IDLE;
        else state <= state_next;
    end

    // ============ addr next logic (combinational) ============
    always_comb begin
        addr0_next = addr0;
        addr2_next = addr2;

        if (state == INIT_PIXEL) begin
            addr0_next = 11'd0;
        end
        
        // Stage0: 發出新的 addr0 請求
        if (state == PROCESS && stage_valid_0) begin
            addr0_next = addr0 + 11'd1;
        end

        if (state == WRITE_RESULT) addr2_next = addr2 + 12'd1;
    end

    // ============ addr registers (sequential) ============
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            addr0 <= 11'd0;
            addr2 <= 12'd0;
        end else if (state == INIT_PIXEL) begin
            addr0 <= 11'd0;
        end else begin
            addr0 <= addr0_next;
            addr2 <= addr2_next;
        end
    end

    // ============ px/py next logic (combinational) ============
    always_comb begin
        px_next = px;
        py_next = py;
        
        if (state == WRITE_RESULT) begin
            if (px < 11'd74) begin
                px_next = px + 11'd1;
            end else begin
                px_next = 11'd0;
                if (py < 7'd33) py_next = py + 7'd1;
            end
        end
    end

    // ============ px/py counters (sequential) ============
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            px <= 11'd0;
            py <= 7'd0;
        end else begin
            px <= px_next;
            py <= py_next;
        end
    end

    // ============ termination flags (combinational) ============
    always_comb begin
        all_pixels_done = (addr2 == 12'd2550);
        all_gaussians_done = (addr0 == 11'd1099);
    end

    // ============ INIT_PIXEL and pixel update logic (combinational) ============
    logic cd_zero_s9, cd_zero_s10;
    logic [31:0] s10_r, s10_g, s10_b, s10_d;

    always_comb begin
        current_pixel_next = current_pixel;
        
        if (state == INIT_PIXEL) begin
            current_pixel_next.red           = 32'h0;
            current_pixel_next.green         = 32'h0;
            current_pixel_next.blue          = 32'h0;
            current_pixel_next.depth         = 32'h0;
            current_pixel_next.transmittance = 32'h3F800000;
            current_pixel_next.num_of_gaussian = 11'd0;
        end else if (state == PROCESS) begin
            // Stage 9: 更新 transmittance (提前更新!)
            if (stage_valid_9) begin
                cd_zero_s9 = (alpha_too_small_s9 || beta_positive_2_s9);
                if (!cd_zero_s9) begin
                    current_pixel_next.transmittance = T_test_s9;
                end
            end
            
            // Stage 10: 更新 RGB, depth, num_of_gaussian
            if (stage_valid_10) begin
                cd_zero_s10 = (alpha_too_small_1_s10 || beta_positive_3_s10);
                if (!cd_zero_s10) begin
                    current_pixel_next.red   = s10_r;
                    current_pixel_next.green = s10_g;
                    current_pixel_next.blue  = s10_b;
                    current_pixel_next.depth = s10_d;
                end
                current_pixel_next.num_of_gaussian = current_pixel.num_of_gaussian + 11'd1;
            end
        end
        
    end

    // ============ INIT_PIXEL and pixel update (sequential) ============
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            current_pixel.red           <= 32'h0;
            current_pixel.green         <= 32'h0;
            current_pixel.blue          <= 32'h0;
            current_pixel.depth         <= 32'h0;
            current_pixel.transmittance <= 32'h3F800000;
            current_pixel.num_of_gaussian <= 11'd0;
        end else if (state == INIT_PIXEL) begin
            current_pixel.red           <= 32'h0;
            current_pixel.green         <= 32'h0;
            current_pixel.blue          <= 32'h0;
            current_pixel.depth         <= 32'h0;
            current_pixel.transmittance <= 32'h3F800000;
            current_pixel.num_of_gaussian <= 11'd0;
        end else begin
            current_pixel <= current_pixel_next;
        end
    end

    // ============ pipeline valid shift logic (combinational) ============
    always_comb begin
        if (state == INIT_PIXEL || state == PROCESS) begin
            stage_valid_0_next  = 1'b1;
            stage_valid_1_next  = stage_valid_0;
            stage_valid_2_next  = stage_valid_1;
            stage_valid_3_next  = stage_valid_2;
            stage_valid_4_next  = stage_valid_3;
            stage_valid_5_next  = stage_valid_4;
            stage_valid_6_next  = stage_valid_5;
            stage_valid_7_next  = stage_valid_6;
            stage_valid_8_next  = stage_valid_7;
            stage_valid_9_next  = stage_valid_8;
            stage_valid_10_next = stage_valid_9;
        end else begin
            stage_valid_0_next  = 1'b0;
            stage_valid_1_next  = 1'b0;
            stage_valid_2_next  = 1'b0;
            stage_valid_3_next  = 1'b0;
            stage_valid_4_next  = 1'b0;
            stage_valid_5_next  = 1'b0;
            stage_valid_6_next  = 1'b0;
            stage_valid_7_next  = 1'b0;
            stage_valid_8_next  = 1'b0;
            stage_valid_9_next  = 1'b0;
            stage_valid_10_next = 1'b0;
        end
    end

    // ============ pipeline valid shift (sequential) ============
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            stage_valid_0  <= 1'b0;
            stage_valid_1  <= 1'b0;
            stage_valid_2  <= 1'b0;
            stage_valid_3  <= 1'b0;
            stage_valid_4  <= 1'b0;
            stage_valid_5  <= 1'b0;
            stage_valid_6  <= 1'b0;
            stage_valid_7  <= 1'b0;
            stage_valid_8  <= 1'b0;
            stage_valid_9  <= 1'b0;
            stage_valid_10 <= 1'b0;
        end else begin
            stage_valid_0  <= stage_valid_0_next;
            stage_valid_1  <= stage_valid_1_next;
            stage_valid_2  <= stage_valid_2_next;
            stage_valid_3  <= stage_valid_3_next;
            stage_valid_4  <= stage_valid_4_next;
            stage_valid_5  <= stage_valid_5_next;
            stage_valid_6  <= stage_valid_6_next;
            stage_valid_7  <= stage_valid_7_next;
            stage_valid_8  <= stage_valid_8_next;
            stage_valid_9  <= stage_valid_9_next;
            stage_valid_10 <= stage_valid_10_next;
        end
    end

    // ---------------- CALC_STEP1: 使用 dout1 (combinational) ----------------
    logic [31:0] s1_sub0_z, s1_sub1_z;
    logic [7:0]  s1_sub0_status, s1_sub1_status;
    logic [31:0] s1_mult0_z, s1_mult1_z, s1_mult2_z, s1_mult3_z;
    logic [7:0]  s1_mult0_stat, s1_mult1_stat, s1_mult2_stat, s1_mult3_stat;

    DW_fp_sub #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_SUB0 (
        .a(dout1[383:352]), .b(float_px_s1), .rnd(3'b0), .z(s1_sub0_z), .status(s1_sub0_status)
    );
    DW_fp_sub #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_SUB1 (
        .a(dout1[351:320]), .b(float_py_s1), .rnd(3'b0), .z(s1_sub1_z), .status(s1_sub1_status)
    );

    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_MULT0 (
        .a(dout1[63:32]), .b(dout1[191:160]), .rnd(3'b0), .z(s1_mult0_z), .status(s1_mult0_stat)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_MULT1 (
        .a(dout1[31:0]), .b(dout1[159:128]), .rnd(3'b0), .z(s1_mult1_z), .status(s1_mult1_stat)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_MULT2 (
        .a(dout1[63:32]), .b(dout1[159:128]), .rnd(3'b0), .z(s1_mult2_z), .status(s1_mult2_stat)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S1_MULT3 (
        .a(dout1[31:0]), .b(dout1[127:96]), .rnd(3'b0), .z(s1_mult3_z), .status(s1_mult3_stat)
    );

    // ---------------- Stage2 registers (combinational) ----------------
    always_comb begin
        dx_s2_next = dx_s2;
        dy_s2_next = dy_s2;
        term1_s2_next = term1_s2;
        term2_s2_next = term2_s2;
        term3_s2_next = term3_s2;
        term4_s2_next = term4_s2;
        g_s2_next = g_s2;
        
        if (stage_valid_1) begin
            // 運算結果
            dx_s2_next = s1_sub0_z;
            dy_s2_next = s1_sub1_z;
            term1_s2_next = s1_mult0_z;
            term2_s2_next = s1_mult1_z;
            term3_s2_next = s1_mult2_z;
            term4_s2_next = s1_mult3_z;

            // 分 bit 後的 gaussian 資料
            g_s2_next.ux    = dout1[383:352];
            g_s2_next.uy    = dout1[351:320];
            g_s2_next.red   = dout1[319:288];
            g_s2_next.green = dout1[287:256];
            g_s2_next.blue  = dout1[255:224];
            g_s2_next.depth = dout1[223:192];
            g_s2_next.conx  = dout1[191:160];
            g_s2_next.cony  = dout1[159:128];
            g_s2_next.conz  = dout1[127:96];
            g_s2_next.conw  = dout1[95:64];
            g_s2_next.coni  = dout1[63:32];
            g_s2_next.conj  = dout1[31:0];
        end
    end

    // ---------------- Stage2 registers (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dx_s2 <= 32'h0; 
            dy_s2 <= 32'h0;
            term1_s2 <= 32'h0; 
            term2_s2 <= 32'h0; 
            term3_s2 <= 32'h0; 
            term4_s2 <= 32'h0;
            g_s2 <= '0;
        end else begin
            dx_s2 <= dx_s2_next;
            dy_s2 <= dy_s2_next;
            term1_s2 <= term1_s2_next;
            term2_s2 <= term2_s2_next;
            term3_s2 <= term3_s2_next;
            term4_s2 <= term4_s2_next;
            g_s2 <= g_s2_next;
        end
    end

    // ---------------- CALC_STEP2 (combinational) ----------------
    logic [31:0] s2_mult0_z, s2_mult1_z, s2_mult2_z;
    logic [7:0]  s2_mstat0, s2_mstat1, s2_mstat2;
    logic [31:0] s2_add0_z, s2_add1_z;
    logic [7:0]  s2_ast0, s2_ast1;

    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S2_MULT0 (
        .a(dx_s2), .b(dx_s2), .rnd(3'b0), .z(s2_mult0_z), .status(s2_mstat0)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S2_MULT1 (
        .a(dy_s2), .b(dy_s2), .rnd(3'b0), .z(s2_mult1_z), .status(s2_mstat1)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S2_MULT2 (
        .a(dx_s2), .b(dy_s2), .rnd(3'b0), .z(s2_mult2_z), .status(s2_mstat2)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S2_ADD0 (
        .a(term1_s2), .b(term2_s2), .rnd(3'b0), .z(s2_add0_z), .status(s2_ast0)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S2_ADD1 (
        .a(term3_s2), .b(term4_s2), .rnd(3'b0), .z(s2_add1_z), .status(s2_ast1)
    );

    always_comb begin
        dx_sq_s3_next = dx_sq_s3;
        dy_sq_s3_next = dy_sq_s3;
        dx_dy_s3_next = dx_dy_s3;
        depth_sum1_s3_next = depth_sum1_s3;
        depth_sum2_s3_next = depth_sum2_s3;
        g_s3_next = g_s3;
        
        if (stage_valid_2) begin
            dx_sq_s3_next = s2_mult0_z;
            dy_sq_s3_next = s2_mult1_z;
            dx_dy_s3_next = s2_mult2_z;
            depth_sum1_s3_next = s2_add0_z;
            depth_sum2_s3_next = s2_add1_z;
            g_s3_next = g_s2;
        end
    end

    // ---------------- CALC_STEP2 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dx_sq_s3 <= 32'h0; 
            dy_sq_s3 <= 32'h0; 
            dx_dy_s3 <= 32'h0;
            depth_sum1_s3 <= 32'h0; 
            depth_sum2_s3 <= 32'h0;
            g_s3 <= '0;
        end else begin
            dx_sq_s3 <= dx_sq_s3_next;
            dy_sq_s3 <= dy_sq_s3_next;
            dx_dy_s3 <= dx_dy_s3_next;
            depth_sum1_s3 <= depth_sum1_s3_next;
            depth_sum2_s3 <= depth_sum2_s3_next;
            g_s3 <= g_s3_next;
        end
    end

    // ---------------- CALC_STEP3 (combinational) ----------------
    logic [31:0] s3_mult0_z, s3_mult1_z, s3_mult2_z, s3_mult3_z, s3_mult4_z;
    logic [7:0]  s3_mstat0,s3_mstat1,s3_mstat2,s3_mstat3,s3_mstat4;

    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S3_MULT0 (
        .a(g_s3.conx), .b(dx_sq_s3), .rnd(3'b0), .z(s3_mult0_z), .status(s3_mstat0)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S3_MULT1 (
        .a(g_s3.conz), .b(dy_sq_s3), .rnd(3'b0), .z(s3_mult1_z), .status(s3_mstat1)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S3_MULT2 (
        .a(g_s3.cony), .b(dx_dy_s3), .rnd(3'b0), .z(s3_mult2_z), .status(s3_mstat2)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S3_MULT3 (
        .a(dx_s2), .b(depth_sum1_s3), .rnd(3'b0), .z(s3_mult3_z), .status(s3_mstat3)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S3_MULT4 (.a(dy_s2), .b(depth_sum2_s3), .rnd(3'b0), .z(s3_mult4_z), .status(s3_mstat4)
    );

    always_comb begin
        beta_term1_s4_next = beta_term1_s4;
        beta_term2_s4_next = beta_term2_s4;
        beta_term3_s4_next = beta_term3_s4;
        dx_depth_s4_next = dx_depth_s4;
        dy_depth_s4_next = dy_depth_s4;
        g_s4_next = g_s4;
        
        if (stage_valid_3) begin
            beta_term1_s4_next = s3_mult0_z;
            beta_term2_s4_next = s3_mult1_z;
            beta_term3_s4_next = s3_mult2_z;
            dx_depth_s4_next   = s3_mult3_z;
            dy_depth_s4_next   = s3_mult4_z;
            g_s4_next = g_s3;
        end
    end

    // ---------------- CALC_STEP3 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            beta_term1_s4 <= 32'h0; 
            beta_term2_s4 <= 32'h0; 
            beta_term3_s4 <= 32'h0;
            dx_depth_s4 <= 32'h0; 
            dy_depth_s4 <= 32'h0;
            g_s4 <= '0;
        end else begin
            beta_term1_s4 <= beta_term1_s4_next;
            beta_term2_s4 <= beta_term2_s4_next;
            beta_term3_s4 <= beta_term3_s4_next;
            dx_depth_s4 <= dx_depth_s4_next;
            dy_depth_s4 <= dy_depth_s4_next;
            g_s4 <= g_s4_next;
        end
    end

    // ---------------- CALC_STEP4 (combinational) ----------------
    logic [31:0] s4_add0_z; logic [7:0] s4_ast0;
    logic [31:0] s4_mult0_z; logic [7:0] s4_mst0;
    logic [31:0] s4_add1_z; logic [7:0] s4_ast1;

    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S4_ADD0 (
        .a(beta_term1_s4), .b(beta_term2_s4), .rnd(3'b0), .z(s4_add0_z), .status(s4_ast0)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S4_MULT0 (
        .a(s4_add0_z), .b(32'hBF000000), .rnd(3'b0), .z(s4_mult0_z), .status(s4_mst0)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S4_ADD1 (
        .a(dx_depth_s4), .b(dy_depth_s4), .rnd(3'b0), .z(s4_add1_z), .status(s4_ast1)
    );

    always_comb begin
        beta_term12_s5_next = beta_term12_s5;
        depth_sum_s5_next = depth_sum_s5;
        beta_term3_1_s5_next = beta_term3_1_s5;
        g_s5_next = g_s5;
        
        if (stage_valid_4) begin
            beta_term12_s5_next = s4_mult0_z;
            depth_sum_s5_next   = s4_add1_z;
            beta_term3_1_s5_next = beta_term3_s4;
            g_s5_next = g_s4;
        end
    end

    // ---------------- CALC_STEP4 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            beta_term12_s5 <= 32'h0;
            depth_sum_s5 <= 32'h0;
            beta_term3_1_s5 <= 32'h0;
            g_s5 <= '0;
        end else begin
            beta_term12_s5 <= beta_term12_s5_next;
            depth_sum_s5 <= depth_sum_s5_next;
            beta_term3_1_s5 <= beta_term3_1_s5_next;
            g_s5 <= g_s5_next;
        end
    end

    // ---------------- CALC_STEP5 (combinational) ----------------
    logic [31:0] s5_sub0_z, s5_sub1_z; 
    logic [7:0] s5_sub0_stat, s5_sub1_stat;
    
    DW_fp_sub #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S5_SUB0 (
        .a(beta_term12_s5), .b(beta_term3_1_s5), .rnd(3'b0), .z(s5_sub0_z), .status(s5_sub0_stat)
    );
    DW_fp_sub #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S5_SUB1 (
        .a(g_s5.depth), .b(depth_sum_s5), .rnd(3'b0), .z(s5_sub1_z), .status(s5_sub1_stat)
    );

    always_comb begin
        beta_s6_next = beta_s6;
        depth_i_s6_next = depth_i_s6;
        g_s6_next = g_s6;
        
        if (stage_valid_5) begin
            beta_s6_next = s5_sub0_z;
            depth_i_s6_next = s5_sub1_z;
            g_s6_next = g_s5;
        end
    end

    // ---------------- CALC_STEP5 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            beta_s6 <= 32'h0;
            depth_i_s6 <= 32'h0;
            g_s6 <= '0;
        end else begin
            beta_s6 <= beta_s6_next;
            depth_i_s6 <= depth_i_s6_next;
            g_s6 <= g_s6_next;
        end
    end

    // ---------------- CALC_STEP6 (combinational) ----------------
    logic [31:0] s6_exp0_z; 
    logic [7:0] s6_exp0_stat;
    
    DW_fp_exp #(inst_sig_width, inst_exp_width, inst_ieee_compliance, inst_arch) S6_EXP0 (
        .a(beta_s6), .z(s6_exp0_z), .status(s6_exp0_stat)
    );

    always_comb begin
        alpha_term_s7_next = alpha_term_s7;
        beta_positive_s7_next = beta_positive_s7;             
        depth_i_1_s7_next = depth_i_1_s7;
        g_s7_next = g_s7;
        
        if (stage_valid_6) begin
            alpha_term_s7_next = s6_exp0_z;
            beta_positive_s7_next = ~beta_s6[31];
            depth_i_1_s7_next = depth_i_s6;
            g_s7_next = g_s6;
        end
    end

    // ---------------- CALC_STEP6 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            alpha_term_s7 <= 32'h0;
            beta_positive_s7 <= 1'b0;
            depth_i_1_s7 <= 32'h0;
            g_s7 <= '0;
        end else begin
            alpha_term_s7 <= alpha_term_s7_next;
            beta_positive_s7 <= beta_positive_s7_next;
            depth_i_1_s7 <= depth_i_1_s7_next;
            g_s7 <= g_s7_next;
        end
    end

    // ---------------- CALC_STEP7 (combinational) ----------------
    logic [31:0] s7_mult0_z; 
    logic [7:0] s7_mst0;
    
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S7_MULT0 (
        .a(g_s7.conw), .b(alpha_term_s7), .rnd(3'b0), .z(s7_mult0_z), .status(s7_mst0)
    );

    always_comb begin
        alpha_i_s8_next = alpha_i_s8;
        depth_i_2_s8_next = depth_i_2_s8;
        beta_positive_1_s8_next = beta_positive_1_s8;
        g_s8_next = g_s8;
        
        if (stage_valid_7) begin
            alpha_i_s8_next = s7_mult0_z;
            depth_i_2_s8_next = depth_i_1_s7;
            beta_positive_1_s8_next = beta_positive_s7;
            g_s8_next = g_s7;
        end
    end

    // ---------------- CALC_STEP7 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            alpha_i_s8 <= 32'h0;
            depth_i_2_s8 <= 32'h0;
            beta_positive_1_s8 <= 1'b0;
            g_s8 <= '0;
        end else begin
            alpha_i_s8 <= alpha_i_s8_next;
            depth_i_2_s8 <= depth_i_2_s8_next;
            beta_positive_1_s8 <= beta_positive_1_s8_next;
            g_s8 <= g_s8_next;
        end
    end

    // ---------------- CALC_STEP8 (combinational) ----------------
    logic [31:0] s8_sub0_z; 
    logic [7:0] s8_sub0_stat;
    logic [31:0] s8_mult0_z, s8_mult1_z;  // 新增 s8_mult1_z
    logic [7:0] s8_mst0, s8_mst1;         // 新增 s8_mst1
    logic s8_agtb, s8_altb, s8_aeqb, s8_unordered;
    
    // 計算 (1 - alpha_i)
    DW_fp_sub #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S8_SUB0 (
        .a(32'h3F800000), .b(alpha_i_s8), .rnd(3'b0), .z(s8_sub0_z), .status(s8_sub0_stat)
    );
    
    // 計算 T_accum = alpha_i * T_i
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S8_MULT0 (
        .a(alpha_i_s8), .b(current_pixel.transmittance), .rnd(3'b0), .z(s8_mult0_z), .status(s8_mst0)
    );
    
    // 新增: 計算 T_test = (1 - alpha_i) * T_i
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S8_MULT1 (
        .a(s8_sub0_z), .b(current_pixel.transmittance), .rnd(3'b0), .z(s8_mult1_z), .status(s8_mst1)
    );
    
    // alpha 的大小比較
    DW_fp_cmp #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S8_CMP0 (
        .a(alpha_i_s8), .b(32'h3B808081), .agtb(s8_agtb), .altb(s8_altb), .aeqb(s8_aeqb), .unordered(s8_unordered)
    );

    always_comb begin
        one_alpha_i_s9_next = one_alpha_i_s9;
        T_accum_s9_next = T_accum_s9;
        T_test_s9_next = T_test_s9;  // 新增
        alpha_too_small_s9_next = alpha_too_small_s9;
        depth_i_3_s9_next = depth_i_3_s9;
        beta_positive_2_s9_next = beta_positive_2_s9;
        g_s9_next = g_s9;
        
        if (stage_valid_8) begin
            one_alpha_i_s9_next = s8_sub0_z;
            T_accum_s9_next = s8_mult0_z;
            T_test_s9_next = s8_mult1_z;  // 新增: 從 Stage 8 傳來的 T_test
            alpha_too_small_s9_next = s8_altb;
            depth_i_3_s9_next = depth_i_2_s8;
            beta_positive_2_s9_next = beta_positive_1_s8;
            g_s9_next = g_s8;
        end
    end

    // ---------------- CALC_STEP8 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            one_alpha_i_s9 <= 32'h0;
            T_accum_s9 <= 32'h0;
            T_test_s9 <= 32'h3F800000;  // 新增
            alpha_too_small_s9 <= 1'b0;
            depth_i_3_s9 <= 32'h0;
            beta_positive_2_s9 <= 1'b0;
            g_s9 <= '0;
        end else begin
            one_alpha_i_s9 <= one_alpha_i_s9_next;
            T_accum_s9 <= T_accum_s9_next;
            T_test_s9 <= T_test_s9_next;  // 新增
            alpha_too_small_s9 <= alpha_too_small_s9_next;
            depth_i_3_s9 <= depth_i_3_s9_next;
            beta_positive_2_s9 <= beta_positive_2_s9_next;
            g_s9 <= g_s9_next;
        end
    end

    // ---------------- CALC_STEP9 (combinational) ----------------
// 移除原本的 S9_MULT0 (計算 T_test)
// 其他 multiplier 重新編號為 MULT0~3

logic [31:0] s9_mult0_z, s9_mult1_z, s9_mult2_z, s9_mult3_z;
logic [7:0]  s9_mst0, s9_mst1, s9_mst2, s9_mst3;

// 計算 r_term = T_accum * red
DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S9_MULT0 (
    .a(T_accum_s9), .b(g_s9.red), .rnd(3'b0), .z(s9_mult0_z), .status(s9_mst0)
);

// 計算 g_term = T_accum * green
DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S9_MULT1 (
    .a(T_accum_s9), .b(g_s9.green), .rnd(3'b0), .z(s9_mult1_z), .status(s9_mst1)
);

// 計算 b_term = T_accum * blue
DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S9_MULT2 (
    .a(T_accum_s9), .b(g_s9.blue), .rnd(3'b0), .z(s9_mult2_z), .status(s9_mst2)
);

// 計算 d_term = T_accum * depth
DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S9_MULT3 (
    .a(T_accum_s9), .b(depth_i_3_s9), .rnd(3'b0), .z(s9_mult3_z), .status(s9_mst3)
);

always_comb begin
    T_test_s10_next = T_test_s10;
    r_term_s10_next = r_term_s10;
    g_term_s10_next = g_term_s10;
    b_term_s10_next = b_term_s10;
    d_term_s10_next = d_term_s10;
    beta_positive_3_s10_next = beta_positive_3_s10;
    alpha_too_small_1_s10_next = alpha_too_small_1_s10;
    
    if (stage_valid_9) begin
        T_test_s10_next = T_test_s9;  // 修改: 從 s9 傳來，而非運算結果
        r_term_s10_next = s9_mult0_z;  // 注意: multiplier 編號改變了
        g_term_s10_next = s9_mult1_z;
        b_term_s10_next = s9_mult2_z;
        d_term_s10_next = s9_mult3_z;
        beta_positive_3_s10_next = beta_positive_2_s9;
        alpha_too_small_1_s10_next = alpha_too_small_s9;
    end
end

    // ---------------- CALC_STEP9 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            T_test_s10 <= 32'h3F800000;
            r_term_s10 <= 32'h0; 
            g_term_s10 <= 32'h0; 
            b_term_s10 <= 32'h0; 
            d_term_s10 <= 32'h0;
            beta_positive_3_s10 <= 1'b0;
            alpha_too_small_1_s10 <= 1'b0;
        end else begin
            T_test_s10 <= T_test_s10_next;
            r_term_s10 <= r_term_s10_next;
            g_term_s10 <= g_term_s10_next;
            b_term_s10 <= b_term_s10_next;
            d_term_s10 <= d_term_s10_next;
            beta_positive_3_s10 <= beta_positive_3_s10_next;
            alpha_too_small_1_s10 <= alpha_too_small_1_s10_next;
        end
    end

    // ---------------- CALC_STEP10 (combinational) ----------------
    logic s10_agtb, s10_altb, s10_aeqb, s10_unordered;
    DW_fp_cmp #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_CMP0 (
        .a(T_test_s10), .b(32'h38D1B717), .agtb(s10_agtb), .altb(s10_altb), .aeqb(s10_aeqb), .unordered(s10_unordered)
    );
    
    logic [31:0] s10_mult0_z; 
    logic [7:0] s10_mst0;
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_MULT0 (
        .a(T_test_s10), .b(32'h41700000), .rnd(3'b0), .z(s10_mult0_z), .status(s10_mst0)
    );

    logic [31:0] s10_add0_z, s10_add1_z, s10_add2_z, s10_add3_z;
    logic [7:0]  s10_ast0, s10_ast1, s10_ast2, s10_ast3;
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_ADD0 (
        .a(current_pixel.red), .b(r_term_s10), .rnd(3'b0), .z(s10_add0_z), .status(s10_ast0)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_ADD1 (
        .a(current_pixel.green), .b(g_term_s10), .rnd(3'b0), .z(s10_add1_z), .status(s10_ast1)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_ADD2 (
        .a(current_pixel.blue), .b(b_term_s10), .rnd(3'b0), .z(s10_add2_z), .status(s10_ast2)
    );
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) S10_ADD3 (
        .a(current_pixel.depth), .b(d_term_s10), .rnd(3'b0), .z(s10_add3_z), .status(s10_ast3)
    );

    assign s10_r = s10_add0_z;
    assign s10_g = s10_add1_z;
    assign s10_b = s10_add2_z;
    assign s10_d = s10_add3_z;

    always_comb begin
        early_termination_next = early_termination;
        T_testx15_next = T_testx15;
        
        if (stage_valid_10) begin
            early_termination_next = (alpha_too_small_1_s10 || beta_positive_3_s10) ? 1'b0 : s10_altb;  // 等等
            T_testx15_next = s10_mult0_z;
        end
    end

    // ---------------- CALC_STEP10 (sequential) ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            early_termination <= 1'b0;
            T_testx15 <= 32'h0;
        end else begin
            early_termination <= early_termination_next;
            T_testx15 <= T_testx15_next;
        end
    end

    // ============ FINALIZE: RGB conversion (combinational) ============
    logic [31:0] rgb_mult_r, rgb_mult_g, rgb_mult_b;
    logic [7:0] rgb_mult_stat_r, rgb_mult_stat_g, rgb_mult_stat_b;
    logic [31:0] f2i_in_r, f2i_in_g, f2i_in_b;
    logic [8:0]  int_r, int_g, int_b;
    logic [7:0]  status_f2i_r, status_f2i_g, status_f2i_b;
    
    // Multiply by 32'h437F0000 (255.0)
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) FINALIZE_MULT_R (
        .a(current_pixel.red), .b(32'h437F0000), .rnd(3'b0), .z(rgb_mult_r), .status(rgb_mult_stat_r)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) FINALIZE_MULT_G (
        .a(current_pixel.green), .b(32'h437F0000), .rnd(3'b0), .z(rgb_mult_g), .status(rgb_mult_stat_g)
    );
    DW_fp_mult #(inst_sig_width, inst_exp_width, inst_ieee_compliance) FINALIZE_MULT_B (
        .a(current_pixel.blue), .b(32'h437F0000), .rnd(3'b0), .z(rgb_mult_b), .status(rgb_mult_stat_b)
    );
    
    assign f2i_in_r = rgb_mult_r;
    assign f2i_in_g = rgb_mult_g;
    assign f2i_in_b = rgb_mult_b;
    
    DW_fp_flt2i #(inst_sig_width, inst_exp_width, int_width_rgb, int_sign) F2I_R (
        .a(f2i_in_r), .rnd(3'b0), .z(int_r), .status(status_f2i_r)
    );
    DW_fp_flt2i #(inst_sig_width, inst_exp_width, int_width_rgb, int_sign) F2I_G (
        .a(f2i_in_g), .rnd(3'b0), .z(int_g), .status(status_f2i_g)
    );
    DW_fp_flt2i #(inst_sig_width, inst_exp_width, int_width_rgb, int_sign) F2I_B (
        .a(f2i_in_b), .rnd(3'b0), .z(int_b), .status(status_f2i_b)
    );

    // Depth final calculation
    logic [31:0] depth_final_sum;
    logic [7:0] depth_add_stat;
    DW_fp_add #(inst_sig_width, inst_exp_width, inst_ieee_compliance) FINALIZE_DEPTH_ADD (
        .a(current_pixel.depth), .b(T_testx15), .rnd(3'b0), .z(depth_final_sum), .status(depth_add_stat)
    );

    // ============ WRITE_RESULT (combinational) ============
    always_comb begin
        din2 = '0;
        we2  = 1'b0;
        en   = 1'b0;
        if (state == WRITE_RESULT) begin
            din2 = {int_r[7:0], int_g[7:0], int_b[7:0], depth_final_sum, current_pixel.transmittance, current_pixel.num_of_gaussian};
            we2  = 1'b1;
            en   = 1'b1;
        end
    end

    // ============ outputs ============
    assign RGB = {int_r[7:0], int_g[7:0], int_b[7:0]};
    assign D   = depth_final_sum;
    assign T   = current_pixel.transmittance;
    assign N   = current_pixel.num_of_gaussian;
    assign Finish = (state == COMPLETE);

endmodule