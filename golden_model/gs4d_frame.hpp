// Phase 4 frame-level scene, FP32 reference renderer and quality metrics.
//
// Scene: kFrameGaussians Gaussians on a kFrameSize x kFrameSize frame, frames
// 0..kFrameCount-1 at normalized time t = frame / kFrameCount. 60% static background
// (all deformation coefficients 0), 40% moving foreground on quadratic trajectories
// mu(t) = mu0 + c1 t + c2 t^2 with scale and rho drift (the deform_engine basis).
//
// Compositing (testbench side, never in RTL): sort-free normalized weighted sum,
// order independent like the accelerator's per-Gaussian alpha stream:
//   C(p) = (w_bg * c_bg + sum_i alpha_i(p) * c_i) / (w_bg + sum_i alpha_i(p))
// with c_bg = 0 (black) and w_bg = kBgWeight.
//
// FP32 reference (ground truth): every Gaussian at every pixel in float32 with the
// standard 3DGS alpha = min(0.99, o * exp(-0.5 d^T Sigma^-1 d)), alpha < 1/255 -> 0, and the
// conic of gs4d::conic_from_scale. ExpModel::kHardware swaps exp for the RTL lane's
// piecewise-linear 2^-y (used only to separate quantization from approximation error).
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

#include "4dgs_ref.hpp"

namespace gs4d_frame {

constexpr int kFrameSize = 64;
constexpr int kFrameCount = 8;
constexpr int kFrameGaussians = 256;
constexpr int kPixels = kFrameSize * kFrameSize;
constexpr float kBgWeight = 0.25f;
constexpr float kFootprint = 3.5f;  // binning radius in sigma: exp(-0.5*3.5^2) < 1/255

struct Gauss {
  uint32_t id;
  bool moving;
  float mu0x, mu0y, s0x, s0y, rho0, opa;
  float coef[10];  // c_k for outputs mu_x, mu_y, s_x, s_y, rho (deform_engine order)
  float rgb[3];
};

inline std::vector<Gauss> make_scene(uint64_t seed = 0x34444753ull) {
  using gs4d_ref::urand;
  uint64_t s = seed;
  std::vector<Gauss> scene;
  for (int i = 0; i < kFrameGaussians; ++i) {
    Gauss g{};
    g.id = static_cast<uint32_t>(i);
    g.moving = (i % 5) >= 3;  // 40% foreground
    g.mu0x = static_cast<float>(urand(s, 2.0, 62.0));
    g.mu0y = static_cast<float>(urand(s, 2.0, 62.0));
    const double lo = g.moving ? 0.8 : 1.5, hi = g.moving ? 2.0 : 3.5;
    g.s0x = static_cast<float>(urand(s, lo, hi));
    g.s0y = static_cast<float>(urand(s, lo, hi));
    g.rho0 = static_cast<float>(urand(s, -0.4, 0.4));
    g.opa = static_cast<float>(urand(s, g.moving ? 0.6 : 0.3, g.moving ? 0.95 : 0.7));
    for (auto& c : g.rgb) c = static_cast<float>(urand(s, g.moving ? 0.1 : 0.2, g.moving ? 1.0 : 0.8));
    if (g.moving) {
      // Motion amplitude log-uniform from ~0.002 px to 12 px over t in [0, 1) (up to
      // ~1.5 px/frame), so every MOTION_THRESH in the sweep separates some Gaussians;
      // curvature, scale and rotation drift scale with it.
      const double sp = 12.0 * std::pow(10.0, -urand(s, 0.0, 3.8)), th = urand(s, 0.0, 6.283185307179586);
      const double f = sp / 12.0;
      g.coef[0] = static_cast<float>(sp * std::cos(th));
      g.coef[2] = static_cast<float>(sp * std::sin(th));
      g.coef[1] = static_cast<float>(f * urand(s, -4.0, 4.0));
      g.coef[3] = static_cast<float>(f * urand(s, -4.0, 4.0));
      g.coef[4] = static_cast<float>(f * urand(s, -0.4, 0.4));
      g.coef[6] = static_cast<float>(f * urand(s, -0.4, 0.4));
      g.coef[5] = static_cast<float>(f * urand(s, -0.2, 0.2));
      g.coef[7] = static_cast<float>(f * urand(s, -0.2, 0.2));
      g.coef[8] = static_cast<float>(f * urand(s, -0.3, 0.3));
      g.coef[9] = static_cast<float>(f * urand(s, -0.1, 0.1));
    }
    scene.push_back(g);
  }
  return scene;
}

inline float frame_time(int frame) { return static_cast<float>(frame) / kFrameCount; }

struct Deformed { float mx, my, sx, sy, rho; };

// FP32 deformation (same basis as deform_engine, unquantized coefficients).
inline Deformed deform_fp32(const Gauss& g, float t) {
  const float t2 = t * t;
  auto d = [&](int o) { return g.coef[2 * o] * t + g.coef[2 * o + 1] * t2; };
  return {g.mu0x + d(0), g.mu0y + d(1), g.s0x + d(2), g.s0y + d(3), g.rho0 + d(4)};
}

enum class ExpModel { kExact, kHardware };

// RTL lane's 2^-y: k = floor(y), f = frac(y), (1 - f/2) >> k, in float.
inline float exp2_neg_hw(float y) {
  const float k = std::floor(y), f = y - k;
  return k > 30 ? 0.0f : std::ldexp(1.0f - 0.5f * f, -static_cast<int>(k));
}

struct ConicF { float a, b, c; };
inline ConicF conic_fp32(float sx, float sy, float rho) {
  const float det = sx * sx * sy * sy * (1.0f - rho * rho);
  return {sy * sy / det, -rho * sx * sy / det, sx * sx / det};
}

inline float alpha_fp32(const Deformed& d, float opa, float px, float py, ExpModel m,
                        float cutoff = 1.0f / 255.0f) {
  const ConicF k = conic_fp32(d.sx, d.sy, d.rho);
  const float dx = px - d.mx, dy = py - d.my;
  const float e = 0.5f * (k.a * dx * dx + k.c * dy * dy) + k.b * dx * dy;
  if (!(e >= 0.0f)) return 0.0f;
  const float g = m == ExpModel::kExact ? std::exp(-e) : exp2_neg_hw(e * 1.4426950408889634f);
  const float a = std::min(0.99f, opa * g);
  return a < cutoff ? 0.0f : a;
}

using Frame = std::vector<float>;  // kPixels * 3, row-major RGB in [0, 1]

struct Accum {
  std::vector<double> num = std::vector<double>(kPixels * 3, 0.0), den = std::vector<double>(kPixels, 0.0);
  void add(int x, int y, double a, const float* rgb) {
    if (x < 0 || y < 0 || x >= kFrameSize || y >= kFrameSize || a <= 0) return;
    const int p = y * kFrameSize + x;
    den[p] += a;
    for (int c = 0; c < 3; ++c) num[p * 3 + c] += a * rgb[c];
  }
  Frame resolve() const {
    Frame f(kPixels * 3);
    for (int p = 0; p < kPixels; ++p)
      for (int c = 0; c < 3; ++c)
        f[p * 3 + c] = static_cast<float>(num[p * 3 + c] / (kBgWeight + den[p]));
    return f;
  }
};

inline Frame render_fp32(const std::vector<Gauss>& scene, int frame, ExpModel m) {
  Accum acc;
  const float t = frame_time(frame);
  for (const Gauss& g : scene) {
    const Deformed d = deform_fp32(g, t);
    for (int y = 0; y < kFrameSize; ++y)
      for (int x = 0; x < kFrameSize; ++x)
        acc.add(x, y, alpha_fp32(d, g.opa, static_cast<float>(x), static_cast<float>(y), m), g.rgb);
  }
  return acc.resolve();
}

// One accelerator transaction: Gaussian g, 4-pixel span (pix_x0..pix_x0+3, pix_y).
struct Span { int gauss, x0, y; };

// Testbench-side binning of each Gaussian's kFootprint-sigma box (FP32 deformed
// parameters) into TILE-wide row spans. Every span of a Gaussian reuses its id.
inline std::vector<Span> bin_spans(const std::vector<Gauss>& scene, int frame) {
  std::vector<Span> out;
  const float t = frame_time(frame);
  for (int i = 0; i < static_cast<int>(scene.size()); ++i) {
    const Deformed d = deform_fp32(scene[i], t);
    const float r = kFootprint * std::max(std::fabs(d.sx), std::fabs(d.sy));
    const int y0 = std::max(0, static_cast<int>(std::ceil(d.my - r)));
    const int y1 = std::min(kFrameSize - 1, static_cast<int>(std::floor(d.my + r)));
    const int xa = std::max(0, static_cast<int>(std::ceil(d.mx - r)));
    const int xb = std::min(kFrameSize - 1, static_cast<int>(std::floor(d.mx + r)));
    for (int y = y0; y <= y1; ++y)
      for (int x = xa; x <= xb; x += gs4d_ref::kTile) out.push_back({i, x, y});
  }
  return out;
}

// Quantized accelerator transaction for a span (Q(7,f); coefficients rounded).
inline gs4d_ref::GaussIn quantize(const Gauss& g, const Span& sp, int frame, int f) {
  using gs4d_ref::q;
  gs4d_ref::GaussIn in;
  in.id = g.id;
  in.mu0_x = q(g.mu0x, f); in.mu0_y = q(g.mu0y, f);
  in.s0_x = q(g.s0x, f);   in.s0_y = q(g.s0y, f);
  in.rho = q(g.rho0, f);   in.opacity = q(g.opa, f);
  in.t = q(frame_time(frame), f);
  for (int k = 0; k < 10; ++k) in.coef[k] = q(g.coef[k], f);
  in.pix_x0 = int64_t{sp.x0} << f;
  in.pix_y = int64_t{sp.y} << f;
  return in;
}

inline double mse(const Frame& a, const Frame& b) {
  double s = 0;
  for (size_t i = 0; i < a.size(); ++i) s += (double(a[i]) - b[i]) * (double(a[i]) - b[i]);
  return s / a.size();
}
inline double psnr_db(double m) { return m <= 0 ? 99.0 : 10.0 * std::log10(1.0 / m); }

// IEEE binary16, round to nearest even (frames are in [0, 1], no NaN/Inf).
inline uint16_t to_half(float v) {
  uint32_t x;
  std::memcpy(&x, &v, 4);
  const uint32_t sign = (x >> 16) & 0x8000;
  const int exp = static_cast<int>((x >> 23) & 0xff) - 127 + 15;
  uint32_t man = x & 0x7fffff;
  if (exp >= 31) return static_cast<uint16_t>(sign | 0x7bff);
  if (exp <= 0) {
    if (exp < -10) return static_cast<uint16_t>(sign);
    man |= 0x800000;
    const int shift = 14 - exp;
    uint32_t h = man >> shift;
    const uint32_t rem = man & ((1u << shift) - 1), half = 1u << (shift - 1);
    if (rem > half || (rem == half && (h & 1))) ++h;
    return static_cast<uint16_t>(sign | h);
  }
  uint32_t h = (static_cast<uint32_t>(exp) << 10) | (man >> 13);
  const uint32_t rem = man & 0x1fff;
  if (rem > 0x1000 || (rem == 0x1000 && (h & 1))) ++h;
  return static_cast<uint16_t>(sign | h);
}
inline float from_half(uint16_t h) {
  const int exp = (h >> 10) & 31, man = h & 0x3ff;
  const float v = exp == 0 ? std::ldexp(static_cast<float>(man), -24)
                           : std::ldexp(static_cast<float>(man | 0x400), exp - 25);
  return (h & 0x8000) ? -v : v;
}
inline uint64_t pack_rgb(const Frame& f, int p) {
  return (uint64_t{to_half(f[p * 3])} << 32) | (uint64_t{to_half(f[p * 3 + 1])} << 16) | to_half(f[p * 3 + 2]);
}

// SSIM_v2 adaptation: the IP scores one 16 x 17 serpentine image (272 FP16 RGB words).
// The 64 x 64 frame is covered by 4 x 4 tiles of 16 columns x 17 rows at row offsets
// 0, 17, 34, 47 (the last tile overlaps rows 47..50); each tile is scored on its own
// with the IP's zero padding, and the frame score is the mean over all tile pixels.
constexpr int kTileW = 16, kTileH = 17;
constexpr std::array<int, 4> kTileRows{0, 17, 34, 47};

// Software model of the SSIM_v2 formula on one tile (double precision): 3x3 window with
// zero padding, mean = window sum / 8 (the IP's fp16_div8), sigma = E[xy]/8-form
// likewise, C1 = 1e-4, C2 = 9e-4, per channel, clamped to [0, 1]; returns the mean over
// the tile's pixels and channels.
inline double ssim_tile_sw(const Frame& img, const Frame& gt, int tx, int ty, double div = 8.0) {
  constexpr double c1 = 1e-4, c2 = 9e-4;
  double total = 0;
  for (int y = 0; y < kTileH; ++y)
    for (int x = 0; x < kTileW; ++x)
      for (int c = 0; c < 3; ++c) {
        double sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
        for (int dy = -1; dy <= 1; ++dy)
          for (int dx = -1; dx <= 1; ++dx) {
            const int u = x + dx, v = y + dy;
            if (u < 0 || v < 0 || u >= kTileW || v >= kTileH) continue;
            const int p = (ty + v) * kFrameSize + tx + u;
            const double a = img[p * 3 + c], b = gt[p * 3 + c];
            sa += a; sb += b; saa += a * a; sbb += b * b; sab += a * b;
          }
        const double ma = sa / div, mb = sb / div;
        const double va = saa / div - ma * ma, vb = sbb / div - mb * mb, cov = sab / div - ma * mb;
        const double s = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2));
        total += std::min(1.0, std::max(0.0, s));
      }
  return total / (kTileW * kTileH * 3);
}

inline double ssim_frame_sw(const Frame& img, const Frame& gt, double div = 8.0) {
  double s = 0;
  for (int ty : kTileRows)
    for (int tx = 0; tx < kFrameSize; tx += kTileW) s += ssim_tile_sw(img, gt, tx, ty, div);
  return s / (kTileRows.size() * (kFrameSize / kTileW));
}

}  // namespace gs4d_frame
