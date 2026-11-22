// One rasterization PE lane: fixed-point Q(7,F) form of the per-pixel stages of
// rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v (dx/dy, quadratic form, exp,
// alpha) for one pixel of one Gaussian. Purely combinational; bit-true to
// golden_model/4dgs_ref.hpp (Lane::alpha).
//   e     = sat((a*dx^2 + c*dy^2) >>> 1 + b*dx*dy)          (beta = -e)
//   alpha = 0 if e < 0, else opacity * 2^-(e*log2e) with 2^-y ~ (1 - frac(y)/2) >> int(y),
//           flushed to 0 below 2^-8.
// Every multiply is fxmul: (a*b + 2^(F-1)) >>> F, saturated to W bits.
// Historical cov_a/b/c input names carry inverse covariance (conic a/b/c).
module gs4d_raster_lane #(
    parameter int Q_FRAC = 8,
    parameter int W = 8 + Q_FRAC
) (
    input  logic [W-1:0] px,
    input  logic [W-1:0] py,
    input  logic [W-1:0] mean_x,
    input  logic [W-1:0] mean_y,
    input  logic [W-1:0] cov_a,
    input  logic [W-1:0] cov_b,
    input  logic [W-1:0] cov_c,
    input  logic [W-1:0] opacity,
    output logic [W-1:0] alpha
);
    localparam int F = Q_FRAC;
    localparam logic [63:0] LOG2E_64 = ((64'd94548 << F) + 64'd32768) >> 16;
    localparam logic [W-1:0] LOG2E = LOG2E_64[W-1:0];
    localparam logic [W-1:0] ONE = W'(1) << F;
    localparam logic [W-1:0] ALPHA_MIN = W'(1) << (F - 8);
    localparam int K_W = W - 1 - F;

    `include "gs4d_fx.svh"

    logic [W-1:0] dx, dy, dxx, dyy, dxy, t1, t2, t3, e, y, m, ex, a_raw;
    logic [W:0]   t12;
    logic [K_W-1:0] k;
    logic [F-1:0]   f;

    assign dx  = fx_sub(px, mean_x);
    assign dy  = fx_sub(py, mean_y);
    assign dxx = fxmul(dx, dx);
    assign dyy = fxmul(dy, dy);
    assign dxy = fxmul(dx, dy);
    assign t1  = fxmul(cov_a, dxx);
    assign t2  = fxmul(cov_c, dyy);
    assign t3  = fxmul(cov_b, dxy);
    assign t12 = {t1[W-1], t1} + {t2[W-1], t2};
    assign e   = fx_add_w1(t12[W:1], t12[W], t3);
    assign y   = fxmul(e, LOG2E);
    assign k   = y[W-2:F];
    assign f   = y[F-1:0];
    assign m   = ONE - (W'(f) >> 1);
    assign ex  = (32'(k) > F) ? '0 : (m >> k);
    assign a_raw = fxmul(opacity, ex);
    assign alpha = (e[W-1] || y[W-1] || $signed(a_raw) < $signed(ALPHA_MIN)) ? '0 : a_raw;
endmodule
