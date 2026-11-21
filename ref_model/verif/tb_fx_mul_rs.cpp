// Tier 1: Verilator testbench comparing fx_mul_rs against gs4d::fx_mul.
// Parameters arrive as -D macros matching the -G overrides.
#include <cstdio>
#include <cstdlib>
#include <random>

#include "Vfx_mul_rs.h"
#include "gs4d/fixed_point.hpp"
#include "verilated.h"

using namespace gs4d;

static int64_t sext(uint64_t v, int w) { return static_cast<int64_t>(v << (64 - w)) >> (64 - w); }
static uint64_t mask(int w) { return (w >= 64) ? ~0ull : ((1ull << w) - 1); }

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  const long n_random = argc > 1 ? std::atol(argv[1]) : 20000;
  const uint32_t seed = argc > 2 ? std::atoi(argv[2]) : 1;
  const QFormat qa{WA - 1 - FA, FA}, qb{WB - 1 - FB, FB}, qo{WO - 1 - FO, FO};
  const FxMode m{static_cast<Round>(ROUND), SAT ? Overflow::kSaturate : Overflow::kWrap};
  Vfx_mul_rs dut;
  long cycles = 0, mism = 0;
  auto check = [&](int64_t a, int64_t b) {
    dut.a = static_cast<uint64_t>(a) & mask(WA);
    dut.b = static_cast<uint64_t>(b) & mask(WB);
    dut.eval();
    ++cycles;
    const int64_t got = sext(dut.y, WO);
    const int64_t want = fx_mul(Fx{a, qa}, Fx{b, qb}, qo, m).raw;
    if (got != want) {
      if (mism < 10) std::printf("MISMATCH a=%lld b=%lld rtl=%lld model=%lld\n", (long long)a, (long long)b,
                                 (long long)got, (long long)want);
      ++mism;
    }
  };
  // Directed: corners and exact rounding ties (b = 2^k makes the product a shift).
  const int64_t ca[] = {qa.min_raw(), qa.min_raw() + 1, -1, 0, 1, qa.max_raw() - 1, qa.max_raw()};
  const int64_t cb[] = {qb.min_raw(), qb.min_raw() + 1, -1, 0, 1, qb.max_raw() - 1, qb.max_raw()};
  for (int64_t a : ca) for (int64_t b : cb) check(a, b);
  const int sh = FA + FB - FO;
  for (int64_t a = -64; a <= 64; ++a)
    for (int k = 0; k < WB - 1; ++k) { check(a, int64_t{1} << k); check(a, -(int64_t{1} << k)); }
  for (int64_t a = -4096; a <= 4096; ++a) check(a, (int64_t{1} << (sh - 1)) & mask(WB - 1));
  // Constrained random: uniform, small-magnitude and near-saturation operands.
  std::mt19937_64 rng(seed);
  for (long i = 0; i < n_random; ++i) {
    int64_t a = sext(rng(), WA), b = sext(rng(), WB);
    switch (i % 3) {
      case 1: a >>= (rng() % WA); b >>= (rng() % WB); break;
      case 2: a = (rng() & 1) ? qa.max_raw() - (rng() & 15) : qa.min_raw() + (rng() & 15); break;
    }
    check(a, b);
  }
  std::printf("fx_mul_rs WA=%d FA=%d WB=%d FB=%d WO=%d FO=%d ROUND=%d SAT=%d: %ld vectors, %ld mismatches\n", WA, FA,
              WB, FB, WO, FO, ROUND, SAT, cycles, mism);
  return mism ? 1 : 0;
}
