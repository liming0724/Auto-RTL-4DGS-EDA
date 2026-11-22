// 4DGS accelerator top: swappable deformation front-end (deform_engine) + motion-aware
// skip + temporal attribute cache + NUM_PE-lane fixed-point rasterizer (Q(7,Q_FRAC),
// W = 8 + Q_FRAC bits).
//
// One Gaussian per transaction (in_valid/in_ready), one result per transaction
// (out_valid/out_ready, held stable under backpressure). FSM:
//   ACCEPT : deform_engine evaluates dmu(t), ds(t), drho(t) from in_t and the basis
//            coefficients (in_dmu_*/in_ds_* are its order-0 terms, so in_coef == 0 is the
//            Phase 3 precomputed-delta interface); temporal_skip_unit decides skip_deform
//            on the generated deltas; capture inputs, rho(t) = rho + drho; cache lookup.
//            The engine runs for every Gaussian (never gated by its own skip decision).
//   LOOKUP : cache response. skip_deform && dr(t) == 0 && hit -> reuse cached 2D
//            attributes -> RASTER (no rho term in the skip metric, so a rotating
//            Gaussian always recomputes its conic).
//            Otherwise deform mu(t) = mu0 + dmu, s(t) = s0 + ds -> PROJ.
//   PROJ   : six covariance/determinant products on one shared fxmul, det subtract,
//            2*F+1 restoring divide cycles, reciprocal setup, three conic products.
//            Total 11+2*F cycles; final cycle writes the cache. out_cov_* and cache
//            cov_* fields hold inverse covariance (conic a/b/c), not covariance.
//   RASTER : TILE/NUM_PE cycles, NUM_PE gs4d_raster_lane instances per cycle.
//   OUT    : hold result until out_ready.
// Operand isolation: the covariance multiplier operands (proj_a/proj_b) and the
// deformed-scale registers only load on the recompute path, so a skipped Gaussian
// leaves the projection datapath with zero toggling.
// With motion_thresh == 0, skip_deform is constantly 0 (delta < 0 is false), so every
// Gaussian takes the full recompute path (lossless mode).
module gs4d_accelerator_top #(
    parameter int NUM_PE     = 1,
    parameter int Q_FRAC     = 8,
    parameter int CACHE_WAYS = 2,
    parameter int CACHE_SETS = 512,
    parameter int ID_W       = 12,
    parameter int TILE       = 4,
    parameter int W          = 8 + Q_FRAC,
    parameter int COEF_W     = 10 * W
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic [W+1:0]      motion_thresh,
    input  logic              cache_bypass,
    input  logic              in_valid,
    output logic              in_ready,
    input  logic [ID_W-1:0]   in_id,
    input  logic [W-1:0]      in_mu0_x,
    input  logic [W-1:0]      in_mu0_y,
    input  logic [W-1:0]      in_dmu_x,
    input  logic [W-1:0]      in_dmu_y,
    input  logic [W-1:0]      in_s0_x,
    input  logic [W-1:0]      in_s0_y,
    input  logic [W-1:0]      in_ds_x,
    input  logic [W-1:0]      in_ds_y,
    input  logic [W-1:0]      in_rho,
    input  logic [W-1:0]      in_opacity,
    input  logic [W-1:0]      in_pix_x0,
    input  logic [W-1:0]      in_pix_y,
    input  logic [W-1:0]      in_t,
    input  logic [COEF_W-1:0] in_coef,
    output logic              out_valid,
    input  logic              out_ready,
    output logic [ID_W-1:0]   out_id,
    output logic              out_skipped,
    output logic [W+1:0]      out_delta,
    output logic [W-1:0]      out_mean_x,
    output logic [W-1:0]      out_mean_y,
    output logic [W-1:0]      out_cov_a,
    output logic [W-1:0]      out_cov_b,
    output logic [W-1:0]      out_cov_c,
    output logic [W-1:0]      out_opacity,
    output logic [TILE*W-1:0] out_alpha
);
    localparam int F     = Q_FRAC;
    localparam int R     = TILE / NUM_PE;
    localparam int SET_W = $clog2(CACHE_SETS);
    localparam int TAG_W = ID_W - SET_W;
    localparam int DW    = 6 * W;
    localparam int CNT_W = R > 4 ? $clog2(R) : 2;

    `include "gs4d_fx.svh"

    localparam logic [2:0] S_ACCEPT = 3'd0;
    localparam logic [2:0] S_LOOKUP = 3'd1;
    localparam logic [2:0] S_PROJ   = 3'd2;
    localparam logic [2:0] S_RASTER = 3'd3;
    localparam logic [2:0] S_OUT    = 3'd4;
    localparam logic [2:0] S_DIV    = 3'd5;
    localparam logic [2:0] S_INV    = 3'd6;
    localparam int N = 2*F+1;
    localparam int DIV_CW = $clog2(N);

    logic [2:0] state;
    logic [CNT_W-1:0] cnt;

    // Captured transaction.
    logic [W-1:0] mu0x_q, mu0y_q, dmux_q, dmuy_q, s0x_q, s0y_q, dsx_q, dsy_q;
    logic [W-1:0] rho_q, opa_q, pix_x0_q, pix_y_q;
    logic         skip_q, cache_bypass_q;

    // Projection datapath (operand-isolated).
    logic [W-1:0] sx_q, sy_q, proj_a, proj_b, proj_p;
    logic [3:0] phase;
    logic [W-1:0] ca_q, cb_q, cc_q, ac_q, det;
    logic [W-1:0] inv_q, inv_next;
    logic [N-1:0] numerator, quotient;
    logic [N:0] remainder_q, denominator, trial, remainder_next;
    logic [N-1:0] quotient_next;
    logic [DIV_CW-1:0] div_count;
    logic degenerate;

    (* keep *) logic skip_deform;
    logic [W+1:0] skip_delta;

    logic         accept;
    logic         lk_resp, lk_hit, cache_wr;
    logic [DW-1:0] lk_rdata, wr_data;

    assign in_ready  = state == S_ACCEPT;
    assign out_valid = state == S_OUT;
    assign accept    = in_valid && in_ready;

    // Deformation front-end: all basis-specific math lives in deform_engine.sv.
    logic [W-1:0] def_dmu_x, def_dmu_y, def_ds_x, def_ds_y, def_dr;
    deform_engine #(.Q_FRAC(Q_FRAC), .W(W), .COEF_W(COEF_W)) u_deform (
        .mu0_x(in_mu0_x),
        .mu0_y(in_mu0_y),
        .s0_x(in_s0_x),
        .s0_y(in_s0_y),
        .rho0(in_rho),
        .t(in_t),
        .c0_mu_x(in_dmu_x),
        .c0_mu_y(in_dmu_y),
        .c0_s_x(in_ds_x),
        .c0_s_y(in_ds_y),
        .coef(in_coef),
        .dmu_x(def_dmu_x),
        .dmu_y(def_dmu_y),
        .ds_x(def_ds_x),
        .ds_y(def_ds_y),
        .dr(def_dr)
    );

    temporal_skip_unit #(.W(W)) u_skip (
        .dmu_x(def_dmu_x),
        .dmu_y(def_dmu_y),
        .ds_x(def_ds_x),
        .ds_y(def_ds_y),
        .motion_thresh(motion_thresh),
        .delta(skip_delta),
        .skip_deform(skip_deform)
    );

    assign proj_p   = fxmul(proj_a, proj_b);
    assign cache_wr = state == S_PROJ && phase == 9 && !cache_bypass_q;
    assign wr_data  = {out_mean_x, out_mean_y, out_cov_a, fx_sub('0, proj_p), out_cov_c, out_opacity};
    assign det = fx_sub(ac_q, proj_p);
    assign trial = (remainder_q << 1) | (N+1)'(numerator[N-1]);
    assign remainder_next = trial >= denominator ? trial - denominator : trial;
    assign quotient_next = {quotient[N-2:0], trial >= denominator};
    assign inv_next = degenerate || quotient > N'(FX_MAX) ? FX_MAX : W'(quotient);

    motion_aware_cache #(
        .CACHE_SETS(CACHE_SETS),
        .CACHE_WAYS(CACHE_WAYS),
        .DATA_WIDTH(DW),
        .TAG_WIDTH(TAG_W)
    ) u_cache (
        .clk(clk),
        .rst_n(rst_n),
        .lk_valid(accept && !cache_bypass),
        .lk_set(in_id[SET_W-1:0]),
        .lk_tag(in_id[ID_W-1:SET_W]),
        .lk_resp(lk_resp),
        .lk_hit(lk_hit),
        .lk_rdata(lk_rdata),
        .wr_en(cache_wr),
        .wr_data(wr_data)
    );

    // NUM_PE rasterizer lanes; lane l in raster cycle cnt shades tile pixel cnt*NUM_PE+l.
    logic [W-1:0] lane_alpha [NUM_PE];
    for (genvar l = 0; l < NUM_PE; l++) begin : g_pe
        logic [W-1:0] px_off;
        assign px_off = W'(32'(cnt) * NUM_PE + l) << F;
        gs4d_raster_lane #(.Q_FRAC(Q_FRAC), .W(W)) u_lane (
            .px(fx_add(pix_x0_q, px_off)),
            .py(pix_y_q),
            .mean_x(out_mean_x),
            .mean_y(out_mean_y),
            .cov_a(out_cov_a),
            .cov_b(out_cov_b),
            .cov_c(out_cov_c),
            .opacity(out_opacity),
            .alpha(lane_alpha[l])
        );
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= S_ACCEPT;
            cnt         <= '0;
            out_id      <= '0;
            skip_q      <= 1'b0;
            cache_bypass_q <= 1'b0;
            out_skipped <= 1'b0;
            out_delta   <= '0;
            mu0x_q <= '0; mu0y_q <= '0; dmux_q <= '0; dmuy_q <= '0;
            s0x_q  <= '0; s0y_q  <= '0; dsx_q  <= '0; dsy_q  <= '0;
            rho_q  <= '0; opa_q  <= '0; pix_x0_q <= '0; pix_y_q <= '0;
            sx_q   <= '0; sy_q   <= '0; proj_a <= '0; proj_b <= '0;
            phase <= '0; ca_q <= '0; cb_q <= '0; cc_q <= '0; ac_q <= '0;
            inv_q <= '0; numerator <= '0; quotient <= '0; remainder_q <= '0;
            denominator <= '0; div_count <= '0; degenerate <= 1'b0;
            out_mean_x <= '0; out_mean_y <= '0; out_cov_a <= '0;
            out_cov_b  <= '0; out_cov_c  <= '0; out_opacity <= '0;
            out_alpha  <= '0;
        end else begin
            case (state)
                S_ACCEPT: if (accept) begin
                    out_id   <= in_id;
                    skip_q   <= skip_deform && (def_dr == '0);
                    cache_bypass_q <= cache_bypass;
                    out_delta <= skip_delta;
                    mu0x_q   <= in_mu0_x;  mu0y_q <= in_mu0_y;
                    dmux_q   <= def_dmu_x; dmuy_q <= def_dmu_y;
                    s0x_q    <= in_s0_x;   s0y_q  <= in_s0_y;
                    dsx_q    <= def_ds_x;  dsy_q  <= def_ds_y;
                    rho_q    <= fx_add(in_rho, def_dr);
                    opa_q    <= in_opacity;
                    pix_x0_q <= in_pix_x0; pix_y_q <= in_pix_y;
                    state    <= S_LOOKUP;
                end
                S_LOOKUP: begin
                    cnt <= '0;
                    if (skip_q && lk_resp && lk_hit) begin
                        out_skipped <= 1'b1;
                        {out_mean_x, out_mean_y, out_cov_a, out_cov_b, out_cov_c, out_opacity} <= lk_rdata;
                        state <= S_RASTER;
                    end else begin
                        out_skipped <= 1'b0;
                        out_mean_x  <= fx_add(mu0x_q, dmux_q);
                        out_mean_y  <= fx_add(mu0y_q, dmuy_q);
                        out_opacity <= opa_q;
                        sx_q   <= fx_add(s0x_q, dsx_q);
                        sy_q   <= fx_add(s0y_q, dsy_q);
                        proj_a <= fx_add(s0x_q, dsx_q);
                        proj_b <= fx_add(s0x_q, dsx_q);
                        phase <= '0;
                        state  <= S_PROJ;
                    end
                end
                S_PROJ: begin
                    phase <= phase + 1'b1;
                    case (phase)
                        0: begin ca_q <= proj_p; proj_a <= sy_q; proj_b <= sy_q; end
                        1: begin cc_q <= proj_p; proj_a <= sx_q; proj_b <= sy_q; end
                        2: begin proj_a <= rho_q; proj_b <= proj_p; end
                        3: begin cb_q <= proj_p; proj_a <= ca_q; proj_b <= cc_q; end
                        4: begin ac_q <= proj_p; proj_a <= cb_q; proj_b <= cb_q; end
                        5: begin
                            numerator <= (N'(1) << (2*F)) + (N'(det) >> 1);
                            denominator <= (N+1)'(det);
                            degenerate <= $signed(det) <= 0;
                            remainder_q <= '0; quotient <= '0; div_count <= '0;
                            state <= S_DIV;
                        end
                        7: begin out_cov_a <= proj_p; proj_a <= ca_q; proj_b <= inv_q; end
                        8: begin out_cov_c <= proj_p; proj_a <= cb_q; proj_b <= inv_q; end
                        default: begin
                            out_cov_b <= fx_sub('0, proj_p);
                            cnt   <= '0;
                            state <= S_RASTER;
                        end
                    endcase
                end
                S_DIV: begin
                    numerator <= numerator << 1;
                    remainder_q <= remainder_next;
                    quotient <= quotient_next;
                    div_count <= div_count + 1'b1;
                    if (div_count == DIV_CW'(N-1)) state <= S_INV;
                end
                S_INV: begin
                    inv_q <= inv_next;
                    proj_a <= cc_q; proj_b <= inv_next;
                    phase <= 7;
                    state <= S_PROJ;
                end
                S_RASTER: begin
                    for (int l = 0; l < NUM_PE; l++)
                        out_alpha[(32'(cnt) * NUM_PE + l) * W +: W] <= lane_alpha[l];
                    if (32'(cnt) == R - 1) begin
                        cnt   <= '0;
                        state <= S_OUT;
                    end else begin
                        cnt <= cnt + 1'b1;
                    end
                end
                S_OUT: if (out_ready) state <= S_ACCEPT;
                default: state <= S_ACCEPT;
            endcase
        end
    end
endmodule
