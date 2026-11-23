// Phase 5c split-fetch AXI4-Stream wrapper around gs4d_accelerator_top (SPLIT_FETCH = 1).
//
// Channel A (s_axis_meta_*, always fetched, one beat per Gaussian), LSB first:
//   [W-1:0] pix_y, [2W-1:W] pix_x0, [12W-1:2W] coef, then ds_y, ds_x, dmu_y, dmu_x, t (W each),
//   [ID_W+17W-1:17W] gauss_id. These are every input of the skip decision (deform_engine on
//   the order-0 terms + coef + t, temporal_skip_unit), of the cache tag lookup (id) and of
//   the raster lanes (pix_x0, pix_y).
// Channel B (s_axis_attr_*, demand-fetched on a miss only), LSB first:
//   [W-1:0] opacity, rho, s0_y, s0_x, mu0_y, [6W-1:5W] mu0_x; s_axis_attr_tuser carries the
//   beat's gauss_id (sideband, checked against the pending meta beat).
//
// One Gaussian at a time. FSM:
//   W_IDLE    s_axis_meta_tready = 1; a meta beat is latched -> W_PRESENT
//   W_PRESENT top in_valid = 1 with the latched Channel A (Channel B top inputs = 0) until
//             the top accepts -> W_DECIDE
//   W_DECIDE  the top is in LOOKUP: needs_attr == 0 (skip + hit) -> W_IDLE (done),
//             needs_attr == 1 (miss) -> W_ATTR
//   W_ATTR    s_axis_attr_tready = 1 (the only state that requests Channel B). A beat whose
//             tuser matches the pending gauss_id is passed to the top as attr_valid/attr_data
//             in the same cycle -> W_IDLE; a mismatching beat is consumed and sets the sticky
//             axi_err (reset-only clear), and the wrapper keeps waiting.
// W_PRESENT + W_DECIDE together are the DECIDE phase of docs/AXI_SPLIT_FETCH_4DGS.md.
// The attr channel is reactive: the source sends a beat only after s_axis_attr_tready.
// The top's result interface (out_*) passes through unchanged.
module gs4d_axi_wrapper #(
    parameter int NUM_PE     = 1,
    parameter int Q_FRAC     = 8,
    parameter int CACHE_WAYS = 2,
    parameter int CACHE_SETS = 512,
    parameter int ID_W       = 12,
    parameter int TILE       = 4,
    parameter int W          = 8 + Q_FRAC,
    parameter int COEF_W     = 10 * W,
    parameter int TDATA_A    = ID_W + 7 * W + COEF_W,
    parameter int TDATA_B    = 6 * W
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic [W+1:0]       motion_thresh,
    input  logic               cache_bypass,
    input  logic               s_axis_meta_tvalid,
    output logic               s_axis_meta_tready,
    input  logic [TDATA_A-1:0] s_axis_meta_tdata,
    input  logic               s_axis_attr_tvalid,
    output logic               s_axis_attr_tready,
    input  logic [TDATA_B-1:0] s_axis_attr_tdata,
    input  logic [ID_W-1:0]    s_axis_attr_tuser,
    output logic               out_valid,
    input  logic               out_ready,
    output logic [ID_W-1:0]    out_id,
    output logic               out_skipped,
    output logic [W+1:0]       out_delta,
    output logic [W-1:0]       out_mean_x,
    output logic [W-1:0]       out_mean_y,
    output logic [W-1:0]       out_cov_a,
    output logic [W-1:0]       out_cov_b,
    output logic [W-1:0]       out_cov_c,
    output logic [W-1:0]       out_opacity,
    output logic [TILE*W-1:0]  out_alpha,
    output logic               axi_err
);
    localparam logic [1:0] W_IDLE    = 2'd0;
    localparam logic [1:0] W_PRESENT = 2'd1;
    localparam logic [1:0] W_DECIDE  = 2'd2;
    localparam logic [1:0] W_ATTR    = 2'd3;

    logic [1:0]         wstate;
    logic [TDATA_A-1:0] meta_q;
    logic [ID_W-1:0]    meta_id;
    logic               top_in_ready, needs_attr, attr_fire, attr_id_ok, top_attr_valid;
    logic [6*W-1:0]     top_attr_data;

    assign meta_id            = meta_q[TDATA_A-1 -: ID_W];
    assign s_axis_meta_tready = wstate == W_IDLE;
    assign s_axis_attr_tready = wstate == W_ATTR;
    assign attr_fire          = s_axis_attr_tvalid && s_axis_attr_tready;
    assign attr_id_ok         = s_axis_attr_tuser == meta_id;
    assign top_attr_valid     = attr_fire && attr_id_ok;
    assign top_attr_data      = top_attr_valid ? s_axis_attr_tdata : '0;

    gs4d_accelerator_top #(
        .NUM_PE(NUM_PE), .Q_FRAC(Q_FRAC), .CACHE_WAYS(CACHE_WAYS), .CACHE_SETS(CACHE_SETS),
        .ID_W(ID_W), .TILE(TILE), .W(W), .COEF_W(COEF_W), .SPLIT_FETCH(1)
    ) u_top (
        .clk(clk),
        .rst_n(rst_n),
        .motion_thresh(motion_thresh),
        .cache_bypass(cache_bypass),
        .in_valid(wstate == W_PRESENT),
        .in_ready(top_in_ready),
        .in_id(meta_id),
        .in_mu0_x('0),
        .in_mu0_y('0),
        .in_dmu_x(meta_q[15*W +: W]),
        .in_dmu_y(meta_q[14*W +: W]),
        .in_s0_x('0),
        .in_s0_y('0),
        .in_ds_x(meta_q[13*W +: W]),
        .in_ds_y(meta_q[12*W +: W]),
        .in_rho('0),
        .in_opacity('0),
        .in_pix_x0(meta_q[W +: W]),
        .in_pix_y(meta_q[0 +: W]),
        .in_t(meta_q[16*W +: W]),
        .in_coef(meta_q[2*W +: COEF_W]),
        .out_valid(out_valid),
        .out_ready(out_ready),
        .out_id(out_id),
        .out_skipped(out_skipped),
        .out_delta(out_delta),
        .out_mean_x(out_mean_x),
        .out_mean_y(out_mean_y),
        .out_cov_a(out_cov_a),
        .out_cov_b(out_cov_b),
        .out_cov_c(out_cov_c),
        .out_opacity(out_opacity),
        .out_alpha(out_alpha),
        .attr_valid(top_attr_valid),
        .attr_data(top_attr_data),
        .needs_attr(needs_attr)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wstate  <= W_IDLE;
            meta_q  <= '0;
            axi_err <= 1'b0;
        end else begin
            case (wstate)
                W_IDLE: if (s_axis_meta_tvalid) begin
                    meta_q <= s_axis_meta_tdata;
                    wstate <= W_PRESENT;
                end
                W_PRESENT: if (top_in_ready) wstate <= W_DECIDE;
                W_DECIDE: wstate <= needs_attr ? W_ATTR : W_IDLE;
                default: if (attr_fire) begin
                    if (attr_id_ok) wstate <= W_IDLE;
                    else axi_err <= 1'b1;
                end
            endcase
        end
    end
endmodule
