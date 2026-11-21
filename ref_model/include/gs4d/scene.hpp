// Seeded synthetic 4DGS scene generator and RTL memory-image I/O.
#pragma once

#include <algorithm>
#include <cstdio>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>

#include "rasterizer.hpp"

namespace gs4d {

struct SceneConfig {
  uint32_t seed = 1;
  int num_gaussians = 600;  // <= sram depth - 1 (last word is a null splat)
  int sram_depth = 1089;
};

inline std::vector<Gaussian4D> make_scene(const SceneConfig& sc, const TileConfig& tc = TileConfig{}) {
  std::mt19937 rng(sc.seed);
  auto U = [&](double a, double b) { return std::uniform_real_distribution<double>(a, b)(rng); };
  std::vector<Gaussian4D> gs(sc.num_gaussians);
  for (auto& g : gs) {
    const double sx = U(1.0, 8.0), sy = U(1.0, 8.0), rho = U(-0.6, 0.6);
    const double det = sx * sx * sy * sy * (1 - rho * rho);
    g.s.ux = tc.px_offset + U(-6, tc.width + 6);
    g.s.uy = tc.py_offset + U(-6, tc.height + 6);
    g.s.red = U(0, 1); g.s.green = U(0, 1); g.s.blue = U(0, 1);
    g.s.depth = U(1, 20);
    g.s.conx = sy * sy / det;                 // conic a
    g.s.cony = -rho * sx * sy / det;          // conic b (RTL: beta = -0.5(a dx^2 + c dy^2) - b dx dy)
    g.s.conz = sx * sx / det;                 // conic c
    g.s.conw = U(0.2, 0.99);
    g.s.coni = U(-0.05, 0.05); g.s.conj = U(-0.05, 0.05);
    g.t0 = U(0, 1);
    g.vx = U(-20, 20); g.vy = U(-20, 20); g.hax = U(-10, 10); g.hay = U(-10, 10);
    g.vd = U(-2, 2); g.lam_h = U(0.5, 20);
  }
  return gs;
}

// Depth-sorted SRAM images for frame t. sram1 gets a trailing null splat
// (opacity 0) and sram0 is padded with its index, so every word is defined.
template <class P>
void build_srams(const P& p, const std::vector<Gaussian4D>& gs, double t, int depth,
                 std::vector<Splat<typename P::V>>& sram1, std::vector<uint32_t>& sram0) {
  if (gs.empty() || static_cast<int>(gs.size()) >= depth)
    throw std::invalid_argument("build_srams: need 1 <= gaussians < SRAM depth (room for the null splat)");
  sram1 = deform_frame(p, gs, t);
  std::vector<uint32_t> order(sram1.size());
  std::iota(order.begin(), order.end(), 0u);
  std::stable_sort(order.begin(), order.end(),
                   [&](uint32_t a, uint32_t b) { return p.lt(sram1[a].depth, sram1[b].depth); });
  const uint32_t null_idx = static_cast<uint32_t>(sram1.size());
  sram1.push_back(quantize_splat(p, Splat<double>{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}));
  while (static_cast<int>(sram1.size()) < depth) sram1.push_back(sram1.back());
  sram0 = order;
  sram0.resize(depth, null_idx);
}

// ram_file.txt: one 384-bit hex word per line, field order of Splat.
inline bool write_ram_file(const std::string& path, const std::vector<Splat<float>>& s) {
  FILE* f = std::fopen(path.c_str(), "w");
  if (!f) return false;
  for (const auto& g : s) {
    const float v[12] = {g.ux, g.uy, g.red, g.green, g.blue, g.depth, g.conx, g.cony, g.conz, g.conw, g.coni, g.conj};
    for (float x : v) std::fprintf(f, "%08x", float_to_bits(x));
    std::fprintf(f, "\n");
  }
  return std::fclose(f) == 0;
}

// gs_value.txt: one 11-bit binary word per line.
inline bool write_gs_value(const std::string& path, const std::vector<uint32_t>& idx) {
  FILE* f = std::fopen(path.c_str(), "w");
  if (!f) return false;
  for (uint32_t v : idx) {
    for (int b = 10; b >= 0; --b) std::fputc((v >> b) & 1 ? '1' : '0', f);
    std::fputc('\n', f);
  }
  return std::fclose(f) == 0;
}

// Golden files in the exact formats TB.sv reads.
template <class P>
bool write_golden(const P& p, const std::string& dir, const RenderResult<typename P::V>& r, int hex_digits) {
  FILE* fn = std::fopen((dir + "/N_img.txt").c_str(), "w");
  FILE* ft = std::fopen((dir + "/T_img.txt").c_str(), "w");
  FILE* fr = std::fopen((dir + "/rendered_img.txt").c_str(), "w");
  FILE* fd = std::fopen((dir + "/depth_img.txt").c_str(), "w");
  bool ok = fn && ft && fr && fd;
  for (const auto& px : r.pixels) {
    if (!ok) break;
    std::fprintf(fn, "%u\n", px.n);
    std::fprintf(ft, "%0*llx\n", hex_digits, static_cast<unsigned long long>(p.to_bits(px.trans)));
    std::fprintf(fr, "%u %u %u\n", (px.rgb >> 16) & 0xFF, (px.rgb >> 8) & 0xFF, px.rgb & 0xFF);
    std::fprintf(fd, "%0*llx\n", hex_digits, static_cast<unsigned long long>(p.to_bits(px.depth)));
  }
  for (FILE* f : {fn, ft, fr, fd})
    if (f && std::fclose(f) != 0) ok = false;
  return ok;
}

}  // namespace gs4d
