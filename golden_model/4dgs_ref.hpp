// Bit-true C++ golden model of rtl_4dgs/gs4d_accelerator_top.sv.
//
// Fixed point: every value is Q(7, Q_FRAC) (W = 8 + Q_FRAC bits, signed); every
// multiply/add goes through gs4d::fx_mul / fx_add / fx_sub (ref_model fixed_point.hpp)
// with Round::kHalfUp and Overflow::kSaturate, which is exactly the RTL fxmul/fx_add.
//
// Per Gaussian (one accelerator transaction):
//   delta     = |dmu_x| + |dmu_y| + |ds_x| + |ds_y|       (raw LSBs, L1 temporal norm)
//   skip      = delta < MOTION_THRESH
//   skip&&hit : reuse cached {mean_x, mean_y, cov_a, cov_b, cov_c, opacity}
//   otherwise : mu(t) = mu0 + dmu, s(t) = s0 + ds, cov_a = sx^2, cov_c = sy^2,
//               cov_b = rho * (sx * sy), write the cache (hit way, else first invalid
//               way, else round-robin victim of the set)
//   then TILE pixels (pix_x0 + k, pix_y) are shaded by NUM_PE lanes (Lane::alpha).
// Cycle model (out_ready = 1, in_valid = 1): 3 + TILE/NUM_PE cycles per Gaussian,
// plus 4 projection cycles when the covariance is recomputed.
#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <vector>

#include "gs4d/fixed_point.hpp"

namespace gs4d_ref {

constexpr int kTile = 4;
constexpr int kIdW = 12;

struct Config {
  int num_pe = 1;
  int q_frac = 8;
  int cache_ways = 2;
  int cache_sets = 512;
  uint32_t motion_thresh = 0;
  int w() const { return 8 + q_frac; }
  int raster_cycles() const { return kTile / num_pe; }
};

struct GaussIn {
  uint32_t id = 0;
  int64_t mu0_x = 0, mu0_y = 0, dmu_x = 0, dmu_y = 0;
  int64_t s0_x = 0, s0_y = 0, ds_x = 0, ds_y = 0;
  int64_t rho = 0, opacity = 0, pix_x0 = 0, pix_y = 0;
};

struct Attr {
  int64_t mean_x = 0, mean_y = 0, cov_a = 0, cov_b = 0, cov_c = 0, opacity = 0;
  bool operator==(const Attr& o) const {
    return mean_x == o.mean_x && mean_y == o.mean_y && cov_a == o.cov_a &&
           cov_b == o.cov_b && cov_c == o.cov_c && opacity == o.opacity;
  }
};

struct GaussOut {
  uint32_t id = 0;
  bool skipped = false;
  uint32_t delta = 0;
  Attr attr;
  std::array<int64_t, kTile> alpha{};
  int cycles = 0;
};

class Fx {
 public:
  explicit Fx(int q_frac) : q_{7, q_frac}, m_{gs4d::Round::kHalfUp, gs4d::Overflow::kSaturate} {}
  int64_t mul(int64_t a, int64_t b) const {
    return gs4d::fx_mul(gs4d::Fx{a, q_}, gs4d::Fx{b, q_}, q_, m_).raw;
  }
  int64_t add(int64_t a, int64_t b) const {
    return gs4d::fx_add(gs4d::Fx{a, q_}, gs4d::Fx{b, q_}, q_, m_).raw;
  }
  int64_t sub(int64_t a, int64_t b) const {
    return gs4d::fx_sub(gs4d::Fx{a, q_}, gs4d::Fx{b, q_}, q_, m_).raw;
  }
  // Saturating add of a (W+1)-bit intermediate.
  int64_t sat(int64_t x) const { return gs4d::requantize(x, q_.frac_bits, q_, m_.rnd, m_.ovf); }
  int frac() const { return q_.frac_bits; }
  int w() const { return q_.width(); }

 private:
  gs4d::QFormat q_;
  gs4d::FxMode m_;
};

inline int64_t log2e_q(int f) { return ((int64_t{94548} << f) + 32768) >> 16; }

// One rasterizer PE lane (rtl_4dgs/gs4d_raster_lane.sv).
inline int64_t lane_alpha(const Fx& fx, int64_t px, int64_t py, const Attr& a) {
  const int f = fx.frac();
  const int64_t dx = fx.sub(px, a.mean_x), dy = fx.sub(py, a.mean_y);
  const int64_t t1 = fx.mul(a.cov_a, fx.mul(dx, dx));
  const int64_t t2 = fx.mul(a.cov_c, fx.mul(dy, dy));
  const int64_t t3 = fx.mul(a.cov_b, fx.mul(dx, dy));
  const int64_t half = (t1 + t2) >> 1;  // arithmetic shift (floor)
  const int64_t e = fx.sat(half + t3);
  if (e < 0) return 0;
  const int64_t y = fx.mul(e, log2e_q(f));
  if (y < 0) return 0;
  const int64_t k = y >> f;
  const int64_t frac = y & ((int64_t{1} << f) - 1);
  const int64_t m = (int64_t{1} << f) - (frac >> 1);
  const int64_t ex = k > f ? 0 : (m >> k);
  const int64_t a_raw = fx.mul(a.opacity, ex);
  return a_raw < (int64_t{1} << (f - 8)) ? 0 : a_raw;
}

inline uint32_t temporal_delta(const GaussIn& g) {
  return static_cast<uint32_t>(std::llabs(g.dmu_x) + std::llabs(g.dmu_y) +
                               std::llabs(g.ds_x) + std::llabs(g.ds_y));
}

// Full (never skipped) evaluation of the 2D attributes.
inline Attr project(const Fx& fx, const GaussIn& g) {
  Attr a;
  a.mean_x = fx.add(g.mu0_x, g.dmu_x);
  a.mean_y = fx.add(g.mu0_y, g.dmu_y);
  const int64_t sx = fx.add(g.s0_x, g.ds_x), sy = fx.add(g.s0_y, g.ds_y);
  a.cov_a = fx.mul(sx, sx);
  a.cov_c = fx.mul(sy, sy);
  a.cov_b = fx.mul(g.rho, fx.mul(sx, sy));
  a.opacity = g.opacity;
  return a;
}

struct Stats {
  uint64_t gaussians = 0, skip_eligible = 0, skipped = 0, recomputed = 0;
  uint64_t cache_hits = 0, cycles = 0, cycles_noskip = 0;
  uint64_t mults = 0, mults_noskip = 0, adds = 0, adds_noskip = 0;
  uint64_t alpha_mismatch_vs_full = 0;  // lossy-skip error count (0 when lossless)
  int64_t max_alpha_err = 0;
};

class Accelerator {
 public:
  explicit Accelerator(const Config& c)
      : cfg_(c), fx_(c.q_frac), set_w_(ilog2(c.cache_sets)),
        valid_(c.cache_sets * c.cache_ways, false), tag_(c.cache_sets * c.cache_ways, 0),
        data_(c.cache_sets * c.cache_ways), rr_(c.cache_sets, 0) {}

  GaussOut step(const GaussIn& g, Stats* st = nullptr) {
    GaussOut o;
    o.id = g.id;
    o.delta = temporal_delta(g);
    const bool skip = o.delta < cfg_.motion_thresh;
    const int set = static_cast<int>(g.id & ((1u << set_w_) - 1));
    const uint32_t tag = g.id >> set_w_;
    int hit_way = -1;
    for (int w = 0; w < cfg_.cache_ways; ++w)
      if (valid_[idx(set, w)] && tag_[idx(set, w)] == tag) { hit_way = w; break; }
    if (skip && hit_way >= 0) {
      o.skipped = true;
      o.attr = data_[idx(set, hit_way)];
    } else {
      o.attr = project(fx_, g);
      int way = hit_way;
      if (way < 0) {
        for (int w = 0; w < cfg_.cache_ways; ++w)
          if (!valid_[idx(set, w)]) { way = w; break; }
        if (way < 0) way = rr_[set];
        rr_[set] = (rr_[set] + 1) % cfg_.cache_ways;
      }
      valid_[idx(set, way)] = true;
      tag_[idx(set, way)] = tag;
      data_[idx(set, way)] = o.attr;
    }
    for (int k = 0; k < kTile; ++k)
      o.alpha[k] = lane_alpha(fx_, fx_.add(g.pix_x0, int64_t{k} << cfg_.q_frac), g.pix_y, o.attr);
    const int base = 3 + cfg_.raster_cycles();
    o.cycles = base + (o.skipped ? 0 : 4);
    if (st) {
      ++st->gaussians;
      st->skip_eligible += skip;
      st->skipped += o.skipped;
      st->recomputed += !o.skipped;
      st->cache_hits += hit_way >= 0;
      st->cycles += o.cycles;
      st->cycles_noskip += base + 4;
      // Projection: 4 deform adds + 4 covariance mults; raster: 8 mults + 4 adds/pixel.
      const uint64_t rm = 8 * kTile, ra = 4 * kTile;
      st->mults += rm + (o.skipped ? 0 : 4);
      st->mults_noskip += rm + 4;
      st->adds += ra + (o.skipped ? 0 : 4);
      st->adds_noskip += ra + 4;
      const Attr full = project(fx_, g);
      for (int k = 0; k < kTile; ++k) {
        const int64_t af = lane_alpha(fx_, fx_.add(g.pix_x0, int64_t{k} << cfg_.q_frac), g.pix_y, full);
        const int64_t err = std::llabs(af - o.alpha[k]);
        if (err) ++st->alpha_mismatch_vs_full;
        if (err > st->max_alpha_err) st->max_alpha_err = err;
      }
    }
    return o;
  }

 private:
  static int ilog2(int v) { int r = 0; while ((1 << r) < v) ++r; return r; }
  int idx(int set, int way) const { return set * cfg_.cache_ways + way; }
  Config cfg_;
  Fx fx_;
  int set_w_;
  std::vector<bool> valid_;
  std::vector<uint32_t> tag_;
  std::vector<Attr> data_;
  std::vector<int> rr_;
};

// ---------------------------------------------------------------------------------
// Synthetic dynamic scene: kGaussians distinct ids; 70% static background
// (dmu = ds = 0), 30% dynamic foreground (80% moving 0.25..2 px/frame with scale
// drift, 20% micro-motion < 0.012 px/frame). Frames t = 0..3; offsets grow with t.
// Generated in floating point from a fixed integer PRNG, quantized per Q_FRAC.
// ---------------------------------------------------------------------------------
constexpr int kGaussians = 1024;
constexpr int kFrames = 4;

struct SceneGauss {
  uint32_t id;
  int kind;  // 0 static, 1 moving, 2 micro-motion
  double mu0x, mu0y, vx, vy, s0x, s0y, gx, gy, rho, opa;
};

inline uint64_t splitmix(uint64_t& s) {
  uint64_t z = (s += 0x9E3779B97F4A7C15ull);
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
  return z ^ (z >> 31);
}
inline double urand(uint64_t& s, double lo, double hi) {
  return lo + (hi - lo) * static_cast<double>(splitmix(s) >> 11) * 0x1.0p-53;
}

inline std::vector<SceneGauss> make_scene(uint64_t seed = 0x4D47534Eull) {
  uint64_t s = seed;
  std::vector<bool> used(1u << kIdW, false);
  std::vector<SceneGauss> scene;
  for (int i = 0; i < kGaussians; ++i) {
    uint32_t id;
    do { id = static_cast<uint32_t>(splitmix(s) & ((1u << kIdW) - 1)); } while (used[id]);
    used[id] = true;
    SceneGauss g{};
    g.id = id;
    g.kind = (i % 10) < 7 ? 0 : ((i % 10) < 9 ? 1 : 2);  // 70 / 20 / 10 -> 30% dynamic
    g.mu0x = urand(s, 4.0, 56.0);
    g.mu0y = urand(s, 4.0, 56.0);
    g.s0x = urand(s, 0.3, 1.0);
    g.s0y = urand(s, 0.3, 1.0);
    g.rho = urand(s, -0.3, 0.3);
    g.opa = urand(s, 0.3, 0.99);
    if (g.kind == 1) {
      const double sp = urand(s, 0.25, 2.0), th = urand(s, 0.0, 6.283185307179586);
      g.vx = sp * std::cos(th);
      g.vy = sp * std::sin(th);
      g.gx = urand(s, -0.02, 0.02);
      g.gy = urand(s, -0.02, 0.02);
    } else if (g.kind == 2) {
      g.vx = urand(s, -0.012, 0.012);
      g.vy = urand(s, -0.012, 0.012);
      g.gx = urand(s, -0.004, 0.004);
      g.gy = urand(s, -0.004, 0.004);
    }
    scene.push_back(g);
  }
  return scene;
}

inline int64_t q(double v, int f) { return static_cast<int64_t>(std::llround(std::ldexp(v, f))); }

inline std::vector<GaussIn> make_frame(const std::vector<SceneGauss>& scene, int t, int f) {
  std::vector<GaussIn> out;
  out.reserve(scene.size());
  for (const SceneGauss& g : scene) {
    GaussIn in;
    in.id = g.id;
    in.mu0_x = q(g.mu0x, f);
    in.mu0_y = q(g.mu0y, f);
    in.dmu_x = q(g.vx * t, f);
    in.dmu_y = q(g.vy * t, f);
    in.s0_x = q(g.s0x, f);
    in.s0_y = q(g.s0y, f);
    in.ds_x = q(g.gx * t, f);
    in.ds_y = q(g.gy * t, f);
    in.rho = q(g.rho, f);
    in.opacity = q(g.opa, f);
    const double mx = g.mu0x + g.vx * t, my = g.mu0y + g.vy * t;
    in.pix_x0 = static_cast<int64_t>(std::floor(mx) - 1.0) << f;  // 4-pixel tile around mu(t)
    in.pix_y = static_cast<int64_t>(std::floor(my + 0.5)) << f;
    out.push_back(in);
  }
  return out;
}

}  // namespace gs4d_ref
