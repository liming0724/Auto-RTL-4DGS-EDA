// Bit-true hardware exp(x) unit for x <= 0 (Gaussian falloff / temporal opacity).
//
//   y   = x * log2(e)                 (fixed-point multiply, format y_fmt)
//   n   = floor(y), f = y - n         (f in [0,1))
//   2^f = base[seg] + (slope[seg] * r) >> rbits      (piecewise-linear table)
//   out = requantize(2^f >> -n)       (single rounding step into out_fmt)
//
// x >= 0 returns 1.0 (saturated into out_fmt). The table is generated here and
// emitted verbatim for the RTL by tools/gen_exp_lut.cpp, so model and RTL
// share the exact same integers.
#pragma once

#include <vector>

#include "fixed_point.hpp"

namespace gs4d {

struct ExpConfig {
  QFormat log2e_fmt{1, 16};  // constant log2(e)
  QFormat y_fmt{11, 16};     // x * log2(e)
  int seg_bits = 6;          // 2^seg_bits table segments over f in [0,1)
  int table_frac = 20;       // fractional bits of the 2^f table entries
};

class ExpUnit {
 public:
  ExpUnit(const ExpConfig& cfg, FxMode mode) : cfg_(cfg), mode_(mode) {
    log2e_ = fx_from_double(1.4426950408889634, cfg_.log2e_fmt, mode_);
    const int n = 1 << cfg_.seg_bits;
    base_.resize(n + 1);
    for (int i = 0; i <= n; ++i) {
      base_[i] = fx_from_double(std::exp2(static_cast<double>(i) / n),
                                QFormat{1, cfg_.table_frac}, FxMode{Round::kHalfEven, Overflow::kSaturate})
                     .raw;
    }
    slope_.resize(n);
    for (int i = 0; i < n; ++i) slope_[i] = base_[i + 1] - base_[i];
  }

  const ExpConfig& config() const { return cfg_; }
  const Fx& log2e() const { return log2e_; }
  const std::vector<int64_t>& base() const { return base_; }
  const std::vector<int64_t>& slope() const { return slope_; }
  int rbits() const { return cfg_.y_fmt.frac_bits - cfg_.seg_bits; }

  Fx operator()(const Fx& x, QFormat out) const {
    const Fx y = fx_mul(x, log2e_, cfg_.y_fmt, mode_);
    if (y.raw >= 0) return fx_from_double(1.0, out, mode_);
    const int fy = cfg_.y_fmt.frac_bits;
    const int64_t n = y.raw >> fy;                  // floor, n <= -1
    const int64_t f = y.raw - (n << fy);            // [0, 2^fy)
    const int64_t seg = f >> rbits();
    const int64_t r = f & ((int64_t{1} << rbits()) - 1);
    const int64_t pow2f = base_[seg] + ((slope_[seg] * r) >> rbits());  // Q1.table_frac
    const int shift = static_cast<int>(-n);
    return Fx{requantize(pow2f, cfg_.table_frac + shift, out, mode_.rnd, mode_.ovf), out};
  }

 private:
  ExpConfig cfg_;
  FxMode mode_;
  Fx log2e_;
  std::vector<int64_t> base_;
  std::vector<int64_t> slope_;
};

}  // namespace gs4d
