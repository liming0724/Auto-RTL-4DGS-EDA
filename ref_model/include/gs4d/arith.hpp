// Arithmetic policies for the 4DGS reference kernels.
//
// Every kernel is written once against a policy `P` and instantiated with:
//   * F32Policy   - IEEE-754 binary32, round-to-nearest-even, subnormals
//                   flushed to zero (DesignWare inst_ieee_compliance = 0), i.e.
//                   the arithmetic of rtl_baseline/gs_rasterizer.
//   * FixedPolicy - bit-true Q-format arithmetic; each signal class (Sig) has
//                   its own Q(I,F) format. These formats are the DSE knobs for
//                   roadmap item (b).
// The output format of every operation is named by a Sig tag; F32Policy
// ignores it.
#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>

#include "exp_unit.hpp"
#include "fixed_point.hpp"

namespace gs4d {

enum class Sig : int {
  kCoord = 0,  // pixel / Gaussian centre coordinates, dx, dy
  kDeltaSq,    // dx^2, dy^2, dx*dy
  kConic,      // conic (inverse 2D covariance) a, b, c
  kDGrad,      // depth gradient coefficients (coni, conj)
  kDGradTerm,  // products / sums of conic and depth-gradient terms
  kPower,      // Gaussian exponent beta and its partial terms
  kDepth,      // depth and depth products
  kAlpha,      // opacity, exp(beta), alpha, 1 - alpha
  kTrans,      // transmittance T, T_accum, T_test
  kColor,      // colour channels and accumulated colour
  kPixel,      // colour * 255 before float-to-int conversion
  kTime,       // frame time t, Gaussian centre time t0, dt
  kTimeSq,     // dt^2
  kVel,        // velocities / half-accelerations / temporal precision
  kCount
};

inline const char* sig_name(Sig s) {
  static const char* names[] = {"coord", "delta_sq", "conic", "dgrad", "dgrad_term", "power", "depth",
                                "alpha", "trans",    "color", "pixel", "time",  "time_sq",    "vel"};
  return names[static_cast<int>(s)];
}

inline float bits_to_float(uint32_t b) {
  float f;
  std::memcpy(&f, &b, 4);
  return f;
}
inline uint32_t float_to_bits(float f) {
  uint32_t b;
  std::memcpy(&b, &f, 4);
  return b;
}

// ---------------------------------------------------------------------------
struct F32Policy {
  using V = float;

  static float ftz(float x) {
    return std::fpclassify(x) == FP_SUBNORMAL ? std::copysign(0.0f, x) : x;
  }
  V in(double v, Sig) const { return ftz(static_cast<float>(v)); }
  V cst(uint32_t f32_bits, Sig) const { return bits_to_float(f32_bits); }
  V zero(Sig) const { return 0.0f; }
  V add(V a, V b, Sig) const { return ftz(ftz(a) + ftz(b)); }
  V sub(V a, V b, Sig) const { return ftz(ftz(a) - ftz(b)); }
  V mul(V a, V b, Sig) const { return ftz(ftz(a) * ftz(b)); }
  // DW_fp_exp: modelled as the correctly rounded binary32 exp (see README).
  V exp(V x, Sig) const { return ftz(static_cast<float>(std::exp(static_cast<double>(ftz(x))))); }
  // DW_fp_cmp altb (false when unordered).
  bool lt(V a, V b) const { return ftz(a) < ftz(b); }
  // ~x[31] in the RTL: +0.0 counts as positive, -0.0 as negative.
  bool sign(V x) const { return std::signbit(x); }
  // DW_fp_i2flt, unsigned integer input (exact for < 2^24).
  V from_uint(uint32_t v, Sig) const { return static_cast<float>(v); }
  // DW_fp_flt2i with rnd = 0 (RNE), unsigned `width`-bit result, saturating.
  uint32_t to_uint(V x, int width) const {
    const uint32_t maxv = (1u << width) - 1;
    if (std::isnan(x) || x <= 0.0f) return 0;
    const float r = std::nearbyint(x);  // default FE_TONEAREST
    if (r >= static_cast<float>(maxv)) return maxv;
    return static_cast<uint32_t>(r);
  }
  double to_double(V x) const { return x; }
  uint64_t to_bits(V x) const { return float_to_bits(x); }
  std::string name() const { return "f32"; }
};

// ---------------------------------------------------------------------------
struct FixedConfig {
  std::array<QFormat, static_cast<int>(Sig::kCount)> fmt{{
      {12, 8},   // coord
      {20, 8},   // delta_sq
      {3, 16},   // conic
      {7, 16},   // dgrad
      {9, 16},   // dgrad_term
      {10, 14},  // power
      {10, 14},  // depth
      {1, 16},   // alpha
      {1, 20},   // trans
      {2, 16},   // color
      {10, 8},   // pixel
      {6, 16},   // time
      {12, 16},  // time_sq
      {9, 16},   // vel
  }};
  FxMode mode{Round::kHalfEven, Overflow::kSaturate};
  ExpConfig exp{};
  QFormat operator[](Sig s) const { return fmt[static_cast<int>(s)]; }
  QFormat& operator[](Sig s) { return fmt[static_cast<int>(s)]; }
};

class FixedPolicy {
 public:
  using V = Fx;

  explicit FixedPolicy(const FixedConfig& cfg = FixedConfig{}) : cfg_(cfg), exp_(cfg.exp, cfg.mode) {}

  const FixedConfig& config() const { return cfg_; }
  const ExpUnit& exp_unit() const { return exp_; }

  V in(double v, Sig s) const { return fx_from_double(v, cfg_[s], cfg_.mode); }
  V cst(uint32_t f32_bits, Sig s) const { return in(static_cast<double>(bits_to_float(f32_bits)), s); }
  V zero(Sig s) const { return Fx{0, cfg_[s]}; }
  V add(const V& a, const V& b, Sig s) const { return fx_add(a, b, cfg_[s], cfg_.mode); }
  V sub(const V& a, const V& b, Sig s) const { return fx_sub(a, b, cfg_[s], cfg_.mode); }
  V mul(const V& a, const V& b, Sig s) const { return fx_mul(a, b, cfg_[s], cfg_.mode); }
  V exp(const V& x, Sig s) const { return exp_(x, cfg_[s]); }
  bool lt(const V& a, const V& b) const { return fx_cmp(a, b) < 0; }
  bool sign(const V& x) const { return x.raw < 0; }
  V from_uint(uint32_t v, Sig s) const { return Fx{requantize(v, 0, cfg_[s], cfg_.mode.rnd, cfg_.mode.ovf), cfg_[s]}; }
  uint32_t to_uint(const V& x, int width) const {
    const int64_t i = requantize(x.raw, x.fmt.frac_bits, QFormat{40, 0}, cfg_.mode.rnd, Overflow::kSaturate);
    const int64_t maxv = (int64_t{1} << width) - 1;
    return static_cast<uint32_t>(i < 0 ? 0 : (i > maxv ? maxv : i));
  }
  double to_double(const V& x) const { return x.to_double(); }
  uint64_t to_bits(const V& x) const {
    return static_cast<uint64_t>(x.raw) & ((x.fmt.width() >= 64) ? ~0ull : ((1ull << x.fmt.width()) - 1));
  }
  std::string name() const { return "fixed"; }

 private:
  FixedConfig cfg_;
  ExpUnit exp_;
};

}  // namespace gs4d
