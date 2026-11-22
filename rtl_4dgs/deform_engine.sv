// Swappable spatiotemporal deformation front-end for gs4d_accelerator_top.
//
// Interface contract (any replacement front-end, e.g. an MLP or HexPlane engine, keeps
// these ports and the combinational timing; only COEF_W and the basis math change):
//   in : Gaussian base parameters mu0_x/mu0_y/s0_x/s0_y/rho0, normalized time t, and the
//        per-Gaussian basis coefficients (c0_* order-0 terms + packed coef vector)
//   out: dmu_x/dmu_y/ds_x/ds_y/dr = delta-mu(t), delta-s(t), delta-rho(t), all Q(7,F)
//
// This front-end is a polynomial temporal basis b = [1, t, t^2] with a dot-product
// accumulator per output o (o = 0..4: mu_x, mu_y, s_x, s_y, rho):
//   acc_o = (c0_o << F) + c1_o * t + c2_o * t^2        (exact, AW = 2W+2 bits)
//   out_o = sat_W((acc_o + 2^(F-1)) >>> F)              (one half-up rounding)
// t^2 = fxmul(t, t). c0 for rho is 0. coef[(2*o + k-1)*W +: W] = c{k}_o (k = 1, 2).
// |c0 << F| <= 2^(W-1+F) and |c_k * b_k| <= 2^(2W-2), so the 3-term sum (plus the
// rounding half) fits in 2W+1 bits; AW = 2W+2 cannot wrap (proved in formal_4dgs).
// The base parameters are part of the swappable interface but unused by this
// temporal-only basis. The engine is never gated by the skip decision it feeds.
module deform_engine #(
    parameter int Q_FRAC = 8,
    parameter int W      = 8 + Q_FRAC,
    parameter int COEF_W = 5 * 2 * W
) (
    /* verilator lint_off UNUSED */
    input  logic [W-1:0]      mu0_x,
    input  logic [W-1:0]      mu0_y,
    input  logic [W-1:0]      s0_x,
    input  logic [W-1:0]      s0_y,
    input  logic [W-1:0]      rho0,
    /* verilator lint_on UNUSED */
    input  logic [W-1:0]      t,
    input  logic [W-1:0]      c0_mu_x,
    input  logic [W-1:0]      c0_mu_y,
    input  logic [W-1:0]      c0_s_x,
    input  logic [W-1:0]      c0_s_y,
    input  logic [COEF_W-1:0] coef,
    output logic [W-1:0]      dmu_x,
    output logic [W-1:0]      dmu_y,
    output logic [W-1:0]      ds_x,
    output logic [W-1:0]      ds_y,
    output logic [W-1:0]      dr
);
    localparam int F    = Q_FRAC;
    localparam int NOUT = 5;
    localparam int K    = 2;
    localparam int AW = 2 * W + 2;
    localparam int PW = 2 * W;

    `include "gs4d_fx.svh"

    // Kept nets: formal proves basis, products, accumulators and saturation stage-wise.
    (* keep *) logic [W-1:0]         basis_t2;
    (* keep *) logic [NOUT*K*PW-1:0] prod;
    (* keep *) logic [NOUT*AW-1:0]   acc;
    logic [NOUT*W-1:0] c0, res;
    logic [K*W-1:0]    basis;

    assign basis_t2 = fxmul(t, t);
    assign basis    = {basis_t2, t};
    assign c0       = {{W{1'b0}}, c0_s_y, c0_s_x, c0_mu_y, c0_mu_x};

    function automatic logic [W-1:0] round_sat(input logic [AW-1:0] a);
        logic signed [AW-1:0] q;
        q = ($signed(a) + $signed(AW'(1) << (F - 1))) >>> F;
        if (&q[AW-1:W-1] || ~|q[AW-1:W-1]) return q[W-1:0];
        return q[AW-1] ? FX_MIN : FX_MAX;
    endfunction

    genvar o, k;
    generate
        for (o = 0; o < NOUT; o++) begin : g_out
            for (k = 0; k < K; k++) begin : g_term
                assign prod[(o*K + k)*PW +: PW] =
                    $signed({{W{coef[(o*K + k)*W + W-1]}}, coef[(o*K + k)*W +: W]}) *
                    $signed({{W{basis[k*W + W-1]}}, basis[k*W +: W]});
            end
            assign acc[o*AW +: AW] =
                ({{(AW-W){c0[o*W + W-1]}}, c0[o*W +: W]} << F)
                + {{2{prod[(o*K)*PW + PW-1]}}, prod[(o*K)*PW +: PW]}
                + {{2{prod[(o*K + 1)*PW + PW-1]}}, prod[(o*K + 1)*PW +: PW]};
            assign res[o*W +: W] = round_sat(acc[o*AW +: AW]);
        end
    endgenerate

    assign dmu_x = res[0*W +: W];
    assign dmu_y = res[1*W +: W];
    assign ds_x  = res[2*W +: W];
    assign ds_y  = res[3*W +: W];
    assign dr    = res[4*W +: W];
endmodule
