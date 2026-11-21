// Unit tests: Q-format rounding/overflow, exp unit accuracy, kernels sanity.
#include <cassert>
#include <cstdio>
#include <random>

#include "gs4d/rasterizer.hpp"

using namespace gs4d;
static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++fails; } } while (0)

static void test_rounding() {
  // 0b101 >> 1 = 2.5 : trunc 2, half-up 3, half-even 2
  CHECK(shift_right_round(5, 1, Round::kTrunc) == 2);
  CHECK(shift_right_round(5, 1, Round::kHalfUp) == 3);
  CHECK(shift_right_round(5, 1, Round::kHalfEven) == 2);
  CHECK(shift_right_round(7, 1, Round::kHalfEven) == 4);
  // negative: -2.5
  CHECK(shift_right_round(-5, 1, Round::kTrunc) == -3);  // floor
  CHECK(shift_right_round(-5, 1, Round::kHalfUp) == -2);
  CHECK(shift_right_round(-5, 1, Round::kHalfEven) == -2);
  CHECK(shift_right_round(-7, 1, Round::kHalfEven) == -4);
  CHECK(shift_right_round(-1, 200, Round::kTrunc) == -1);
  CHECK(shift_right_round(-1, 200, Round::kHalfEven) == 0);
  CHECK(shift_right_round(3, -2, Round::kTrunc) == 12);
}

static void test_overflow() {
  CHECK(fit_width(200, 8, Overflow::kSaturate) == 127);
  CHECK(fit_width(-200, 8, Overflow::kSaturate) == -128);
  CHECK(fit_width(128, 8, Overflow::kWrap) == -128);
  CHECK(fit_width(-129, 8, Overflow::kWrap) == 127);
  const QFormat q{3, 4};  // [-8, 7.9375]
  const FxMode m{};
  CHECK(fx_from_double(100.0, q, m).raw == 127);
  CHECK(fx_from_double(-100.0, q, m).raw == -128);
  const Fx a = fx_from_double(7.5, q, m), b = fx_from_double(2.0, q, m);
  CHECK(fx_add(a, b, q, m).raw == 127);
  CHECK(fx_mul(a, b, q, FxMode{Round::kTrunc, Overflow::kWrap}).raw == fit_width(240, 8, Overflow::kWrap));
  CHECK(fx_cmp(fx_from_double(0.5, QFormat{1, 1}, m), fx_from_double(0.5, QFormat{4, 12}, m)) == 0);
}

static void test_mul_exhaustive_small() {
  // Exhaustive check of Q(2,3)xQ(2,3)->Q(2,3) against exact rational reference.
  const QFormat q{2, 3};
  for (int rm = 0; rm < 3; ++rm)
    for (int om = 0; om < 2; ++om)
      for (int64_t a = q.min_raw(); a <= q.max_raw(); ++a)
        for (int64_t b = q.min_raw(); b <= q.max_raw(); ++b) {
          const FxMode m{static_cast<Round>(rm), static_cast<Overflow>(om)};
          const int64_t got = fx_mul(Fx{a, q}, Fx{b, q}, q, m).raw;
          const int64_t p = a * b;  // frac 6 -> 3
          const double exact = p / 8.0;
          int64_t want = (m.rnd == Round::kTrunc) ? static_cast<int64_t>(std::floor(exact))
                         : (m.rnd == Round::kHalfUp) ? static_cast<int64_t>(std::floor(exact + 0.5))
                                                     : static_cast<int64_t>(std::nearbyint(exact));
          want = fit_width(want, q.width(), m.ovf);
          if (got != want) { ++fails; std::printf("mul mismatch a=%ld b=%ld got=%ld want=%ld\n", (long)a, (long)b, (long)got, (long)want); return; }
        }
}

static void test_exp() {
  const FxMode m{};
  ExpUnit e(ExpConfig{}, m);
  const QFormat in{10, 14}, out{1, 16};
  double max_err = 0;
  for (double x = -20.0; x <= 0.0; x += 1.0 / 512) {
    const double got = e(fx_from_double(x, in, m), out).to_double();
    max_err = std::max(max_err, std::fabs(got - std::exp(x)));
  }
  std::printf("exp max abs err (Q1.16 out, 64 seg) = %.3g\n", max_err);
  CHECK(max_err < 3e-5);
  CHECK(e(fx_from_double(0.0, in, m), out).raw == (1 << 16));
  CHECK(e(fx_from_double(-500.0, in, m), out).raw == 0);
  CHECK(e(fx_from_double(3.0, in, m), out).raw == (1 << 16));
}

static void test_f32_ftz() {
  F32Policy p;
  const float tiny = bits_to_float(0x00400000);  // subnormal
  CHECK(p.add(tiny, 0.0f, Sig::kCoord) == 0.0f);
  CHECK(p.mul(1e-20f, 1e-20f, Sig::kCoord) == 0.0f);
  CHECK(p.to_uint(255.5f, 9) == 256);  // RNE tie to even
  CHECK(p.to_uint(254.5f, 9) == 254);
  CHECK(p.to_uint(1000.f, 9) == 511);
  CHECK(p.sign(-0.0f) && !p.sign(0.0f));
}

static void test_deform() {
  Gaussian4D g;
  g.s = Splat<double>{1000, 50, 0.5, 0.5, 0.5, 3, 0.1, 0, 0.1, 0.8, 0.01, 0.02};
  g.t0 = 0.5; g.vx = 10; g.vy = -4; g.hax = 2; g.hay = 0; g.vd = 1; g.lam_h = 8;
  F32Policy pf;
  FixedPolicy px;
  const auto a = deform(pf, g, 0.75);
  const auto b = deform(px, g, 0.75);
  // dt = 0.25: ux = 1000 + 2.5 + 0.125, conw = 0.8 * exp(-0.5)
  CHECK(std::fabs(a.ux - 1002.625f) < 1e-3);
  CHECK(std::fabs(b.ux.to_double() - 1002.625) < 1e-2);
  CHECK(std::fabs(b.conw.to_double() - 0.8 * std::exp(-0.5)) < 1e-4);
  CHECK(std::fabs(b.depth.to_double() - 3.25) < 1e-3);
}

int main() {
  test_rounding();
  test_overflow();
  test_mul_exhaustive_small();
  test_exp();
  test_f32_ftz();
  test_deform();
  std::printf(fails ? "FAILED (%d)\n" : "ALL PASS\n", fails);
  return fails ? 1 : 0;
}
