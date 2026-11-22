// 4DGS accelerator top: temporal deformation + motion-aware skip + temporal attribute
// cache + NUM_PE-lane fixed-point rasterizer (Q(7,Q_FRAC), W = 8 + Q_FRAC bits).
//
// One Gaussian per transaction (in_valid/in_ready), one result per transaction
// (out_valid/out_ready, held stable under backpressure). FSM:
//   ACCEPT : capture inputs, temporal_skip_unit decides skip_deform, issue cache lookup
//   LOOKUP : cache response. skip_deform && hit -> reuse cached 2D attributes -> RASTER.
//            Otherwise deform mu(t) = mu0 + dmu, s(t) = s0 + ds -> PROJ.
//   PROJ   : 4 cycles on one shared fxmul (cov_a = sx^2, cov_c = sy^2, t = sx*sy,
//            cov_b = rho*t); last cycle issues the non-blocking cache write-back.
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
    parameter int W          = 8 + Q_FRAC
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic [W+1:0]      motion_thresh,
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

    logic [2:0] state;
    logic [CNT_W-1:0] cnt;

    // Captured transaction.
    logic [W-1:0] mu0x_q, mu0y_q, dmux_q, dmuy_q, s0x_q, s0y_q, dsx_q, dsy_q;
    logic [W-1:0] rho_q, opa_q, pix_x0_q, pix_y_q;
    logic         skip_q;

    // Projection datapath (operand-isolated).
    logic [W-1:0] sx_q, sy_q, proj_a, proj_b, proj_p;

    (* keep *) logic skip_deform;
    logic [W+1:0] skip_delta;

    logic         accept;
    logic         lk_resp, lk_hit, cache_wr;
    logic [DW-1:0] lk_rdata, wr_data;

    assign in_ready  = state == S_ACCEPT;
    assign out_valid = state == S_OUT;
    assign accept    = in_valid && in_ready;

    temporal_skip_unit #(.W(W)) u_skip (
        .dmu_x(in_dmu_x),
        .dmu_y(in_dmu_y),
        .ds_x(in_ds_x),
        .ds_y(in_ds_y),
        .motion_thresh(motion_thresh),
        .delta(skip_delta),
        .skip_deform(skip_deform)
    );

    assign proj_p   = fxmul(proj_a, proj_b);
    assign cache_wr = state == S_PROJ && cnt == CNT_W'(3);
    assign wr_data  = {out_mean_x, out_mean_y, out_cov_a, proj_p, out_cov_c, out_opacity};

    motion_aware_cache #(
        .CACHE_SETS(CACHE_SETS),
        .CACHE_WAYS(CACHE_WAYS),
        .DATA_WIDTH(DW),
        .TAG_WIDTH(TAG_W)
    ) u_cache (
        .clk(clk),
        .rst_n(rst_n),
        .lk_valid(accept),
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
            out_skipped <= 1'b0;
            out_delta   <= '0;
            mu0x_q <= '0; mu0y_q <= '0; dmux_q <= '0; dmuy_q <= '0;
            s0x_q  <= '0; s0y_q  <= '0; dsx_q  <= '0; dsy_q  <= '0;
            rho_q  <= '0; opa_q  <= '0; pix_x0_q <= '0; pix_y_q <= '0;
            sx_q   <= '0; sy_q   <= '0; proj_a <= '0; proj_b <= '0;
            out_mean_x <= '0; out_mean_y <= '0; out_cov_a <= '0;
            out_cov_b  <= '0; out_cov_c  <= '0; out_opacity <= '0;
            out_alpha  <= '0;
        end else begin
            case (state)
                S_ACCEPT: if (accept) begin
                    out_id   <= in_id;
                    skip_q   <= skip_deform;
                    out_delta <= skip_delta;
                    mu0x_q   <= in_mu0_x;  mu0y_q <= in_mu0_y;
                    dmux_q   <= in_dmu_x;  dmuy_q <= in_dmu_y;
                    s0x_q    <= in_s0_x;   s0y_q  <= in_s0_y;
                    dsx_q    <= in_ds_x;   dsy_q  <= in_ds_y;
                    rho_q    <= in_rho;    opa_q  <= in_opacity;
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
                        state  <= S_PROJ;
                    end
                end
                S_PROJ: begin
                    cnt <= cnt + 1'b1;
                    case (cnt)
                        CNT_W'(0): begin out_cov_a <= proj_p; proj_a <= sy_q;  proj_b <= sy_q;   end
                        CNT_W'(1): begin out_cov_c <= proj_p; proj_a <= sx_q;  proj_b <= sy_q;   end
                        CNT_W'(2): begin                      proj_a <= rho_q; proj_b <= proj_p; end
                        default: begin
                            out_cov_b <= proj_p;
                            cnt   <= '0;
                            state <= S_RASTER;
                        end
                    endcase
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
