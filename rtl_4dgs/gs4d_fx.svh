// Q(7,F) fixed-point helpers shared by the rtl_4dgs modules (included inside a module
// that defines W and F). Bit-true to gs4d::fx_mul / fx_add with Round::kHalfUp and
// Overflow::kSaturate (ref_model/include/gs4d/fixed_point.hpp).
localparam logic [2*W-1:0] FX_HALF = (2*W)'(1) << (F - 1);
localparam logic [W-1:0] FX_MAX = {1'b0, {(W-1){1'b1}}};
localparam logic [W-1:0] FX_MIN = {1'b1, {(W-1){1'b0}}};

function automatic logic [W-1:0] fxmul(input logic [W-1:0] a, input logic [W-1:0] b);
    logic signed [2*W-1:0] p;
    logic signed [2*W-1:0] q;
    p = $signed({{W{a[W-1]}}, a}) * $signed({{W{b[W-1]}}, b});
    q = (p + $signed(FX_HALF)) >>> F;
    if (&q[2*W-1:W-1] || ~|q[2*W-1:W-1]) return q[W-1:0];
    return q[2*W-1] ? FX_MIN : FX_MAX;
endfunction

// Saturating W-bit add of a (W+1)-bit signed value {sx, x} and a W-bit value.
function automatic logic [W-1:0] fx_add_w1(input logic [W-1:0] x, input logic sx, input logic [W-1:0] b);
    logic [W+1:0] s;
    s = {sx, sx, x} + {{2{b[W-1]}}, b};
    if (s[W+1:W-1] == 3'b000 || s[W+1:W-1] == 3'b111) return s[W-1:0];
    return s[W+1] ? FX_MIN : FX_MAX;
endfunction

function automatic logic [W-1:0] fx_add(input logic [W-1:0] a, input logic [W-1:0] b);
    return fx_add_w1(a, a[W-1], b);
endfunction

function automatic logic [W-1:0] fx_sub(input logic [W-1:0] a, input logic [W-1:0] b);
    logic [W:0] s;
    s = {a[W-1], a} - {b[W-1], b};
    if (s[W] == s[W-1]) return s[W-1:0];
    return s[W] ? FX_MIN : FX_MAX;
endfunction
