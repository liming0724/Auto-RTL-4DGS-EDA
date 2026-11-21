// Bit-true signed fixed-point arithmetic (two's complement, Q(I,F)).
//
// A value with format Q(I,F) is stored as a signed integer `raw` of width
// W = 1 + I + F and represents raw * 2^-F. Every operation is defined purely
// on integers so results are deterministic and map 1:1 onto RTL
// (see ref_model/rtl/q_round_sat.v).
#pragma once

#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace gs4d {

enum class Round : int {
  kTrunc = 0,     // floor (arithmetic shift right), i.e. RTL `>>>`
  kHalfUp = 1,    // round half toward +inf: (x + 2^(s-1)) >>> s
  kHalfEven = 2,  // round half to even (convergent rounding)
};

enum class Overflow : int {
  kSaturate = 0,  // clamp to [-2^(W-1), 2^(W-1)-1]
  kWrap = 1,      // keep the low W bits, sign-extend
};

struct QFormat {
  int int_bits = 0;   // I (excluding the sign bit)
  int frac_bits = 0;  // F
  constexpr int width() const { return 1 + int_bits + frac_bits; }
  constexpr int64_t max_raw() const { return (int64_t{1} << (width() - 1)) - 1; }
  constexpr int64_t min_raw() const { return -(int64_t{1} << (width() - 1)); }
  constexpr bool operator==(const QFormat& o) const {
    return int_bits == o.int_bits && frac_bits == o.frac_bits;
  }
  std::string str() const {
    return "Q" + std::to_string(int_bits) + "." + std::to_string(frac_bits);
  }
};

using i128 = __int128;

// Arithmetic shift right of `x` by `s` >= 0 bits with the given rounding.
// Shifts of 127 or more collapse to the sign (trunc) or to the rounded limit.
inline i128 shift_right_round(i128 x, int s, Round rnd) {
  if (s <= 0) return x << (-s);
  if (s >= 127) {
    // |x| < 2^126 always holds for our widths, so the true value is in (-0.5, 0.5)
    // except for the floor of a negative number.
    return (rnd == Round::kTrunc && x < 0) ? -1 : 0;
  }
  const i128 floor_q = x >> s;  // arithmetic shift: floor
  if (rnd == Round::kTrunc) return floor_q;
  const i128 rem = x - (floor_q << s);  // in [0, 2^s)
  const i128 half = i128{1} << (s - 1);
  if (rnd == Round::kHalfUp) return floor_q + (rem >= half ? 1 : 0);
  // kHalfEven
  if (rem > half) return floor_q + 1;
  if (rem < half) return floor_q;
  return floor_q + ((floor_q & 1) ? 1 : 0);
}

inline int64_t fit_width(i128 x, int width, Overflow ovf) {
  const i128 maxv = (i128{1} << (width - 1)) - 1;
  const i128 minv = -(i128{1} << (width - 1));
  if (ovf == Overflow::kSaturate) {
    if (x > maxv) return static_cast<int64_t>(maxv);
    if (x < minv) return static_cast<int64_t>(minv);
    return static_cast<int64_t>(x);
  }
  const unsigned __int128 mask = (static_cast<unsigned __int128>(1) << width) - 1;
  unsigned __int128 u = static_cast<unsigned __int128>(x) & mask;
  if (u >> (width - 1)) u |= ~mask;  // sign-extend
  return static_cast<int64_t>(static_cast<i128>(u));
}

// Requantize an integer `x` carrying `from_frac` fractional bits into `to`.
// Left shifts (adding fractional bits) are exact; right shifts round.
inline int64_t requantize(i128 x, int from_frac, QFormat to, Round rnd, Overflow ovf) {
  const int s = from_frac - to.frac_bits;
  i128 y;
  if (s >= 0) {
    y = shift_right_round(x, s, rnd);
  } else {
    // Exact left shift; saturate early if it would overflow i128.
    const int ls = -s;
    const i128 lim = (i128{1} << (125 - ls));
    if (x >= lim) y = (i128{1} << 125);
    else if (x <= -lim) y = -(i128{1} << 125);
    else y = x << ls;
  }
  return fit_width(y, to.width(), ovf);
}

struct Fx {
  int64_t raw = 0;
  QFormat fmt{};
  double to_double() const { return std::ldexp(static_cast<double>(raw), -fmt.frac_bits); }
};

struct FxMode {
  Round rnd = Round::kHalfEven;
  Overflow ovf = Overflow::kSaturate;
};

// Quantize a real number (used only at model inputs / for constants).
// Round-half-even on the exact binary value of the double.
inline Fx fx_from_double(double v, QFormat fmt, FxMode m) {
  if (std::isnan(v)) return Fx{0, fmt};
  if (std::isinf(v)) return Fx{v > 0 ? fmt.max_raw() : fmt.min_raw(), fmt};
  int e = 0;
  const double mant = std::frexp(v, &e);  // v = mant * 2^e, |mant| in [0.5,1)
  // mant * 2^53 is an exact integer.
  const i128 m53 = static_cast<i128>(std::ldexp(mant, 53));
  const int frac_of_m53 = 53 - e;  // v = m53 * 2^-(53-e)
  if (frac_of_m53 < -60) return Fx{v > 0 ? fmt.max_raw() : fmt.min_raw(), fmt};
  return Fx{requantize(m53, frac_of_m53, fmt, m.rnd, m.ovf), fmt};
}

inline Fx fx_mul(const Fx& a, const Fx& b, QFormat out, FxMode m) {
  const i128 p = static_cast<i128>(a.raw) * static_cast<i128>(b.raw);
  return Fx{requantize(p, a.fmt.frac_bits + b.fmt.frac_bits, out, m.rnd, m.ovf), out};
}

inline Fx fx_add(const Fx& a, const Fx& b, QFormat out, FxMode m) {
  const int f = a.fmt.frac_bits > b.fmt.frac_bits ? a.fmt.frac_bits : b.fmt.frac_bits;
  const i128 s = (static_cast<i128>(a.raw) << (f - a.fmt.frac_bits)) +
                 (static_cast<i128>(b.raw) << (f - b.fmt.frac_bits));
  return Fx{requantize(s, f, out, m.rnd, m.ovf), out};
}

inline Fx fx_sub(const Fx& a, const Fx& b, QFormat out, FxMode m) {
  const int f = a.fmt.frac_bits > b.fmt.frac_bits ? a.fmt.frac_bits : b.fmt.frac_bits;
  const i128 s = (static_cast<i128>(a.raw) << (f - a.fmt.frac_bits)) -
                 (static_cast<i128>(b.raw) << (f - b.fmt.frac_bits));
  return Fx{requantize(s, f, out, m.rnd, m.ovf), out};
}

inline Fx fx_requant(const Fx& a, QFormat out, FxMode m) {
  return Fx{requantize(a.raw, a.fmt.frac_bits, out, m.rnd, m.ovf), out};
}

// Exact comparison of two fixed-point values with possibly different formats.
inline int fx_cmp(const Fx& a, const Fx& b) {
  const int f = a.fmt.frac_bits > b.fmt.frac_bits ? a.fmt.frac_bits : b.fmt.frac_bits;
  const i128 x = static_cast<i128>(a.raw) << (f - a.fmt.frac_bits);
  const i128 y = static_cast<i128>(b.raw) << (f - b.fmt.frac_bits);
  return x < y ? -1 : (x > y ? 1 : 0);
}

}  // namespace gs4d
