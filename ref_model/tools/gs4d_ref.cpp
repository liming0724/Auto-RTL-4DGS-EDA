// gs4d_ref: render a synthetic 4DGS frame with the bit-true reference model.
//
//   gs4d_ref [--seed S] [--n N] [--t T] [--out DIR] [--ideal]
//
// Prints a fixed-point vs float32 error report and, with --out, writes the
// RTL memory images (ram_file.txt, gs_value.txt) and TB.sv golden files for
// the float32 RTL-exact model, plus fixed_* golden files (raw Q-format hex).
#include <cmath>
#include <cstring>
#include <sys/stat.h>

#include "gs4d/scene.hpp"

using namespace gs4d;

template <class P>
static uint64_t fnv(const P& p, const RenderResult<typename P::V>& r) {
  uint64_t h = 1469598103934665603ull;
  auto mix = [&](uint64_t v) { for (int i = 0; i < 8; ++i) { h ^= (v >> (8 * i)) & 0xFF; h *= 1099511628211ull; } };
  for (const auto& px : r.pixels) { mix(px.rgb); mix(p.to_bits(px.depth)); mix(p.to_bits(px.trans)); mix(px.n); }
  return h;
}

template <class PA, class PB>
static void compare(const char* label, const PA& pa, const RenderResult<typename PA::V>& a, const PB& pb,
                    const RenderResult<typename PB::V>& b) {
  double se = 0, maxdt = 0, maxdd = 0;
  int maxdc = 0, nmis = 0;
  for (size_t i = 0; i < a.pixels.size(); ++i) {
    const auto &x = a.pixels[i], &y = b.pixels[i];
    for (int s = 0; s < 24; s += 8) {
      const int d = static_cast<int>((x.rgb >> s) & 0xFF) - static_cast<int>((y.rgb >> s) & 0xFF);
      se += d * d;
      maxdc = std::max(maxdc, std::abs(d));
    }
    maxdt = std::max(maxdt, std::fabs(pa.to_double(x.trans) - pb.to_double(y.trans)));
    maxdd = std::max(maxdd, std::fabs(pa.to_double(x.depth) - pb.to_double(y.depth)));
    nmis += x.n != y.n;
  }
  const double mse = se / (3.0 * a.pixels.size());
  const double psnr = mse == 0 ? INFINITY : 10 * std::log10(255.0 * 255.0 / mse);
  std::printf("%-26s PSNR %7.2f dB  max|dRGB| %3d  max|dT| %.3e  max|dD| %.3e  N-mismatch %d\n", label, psnr, maxdc,
              maxdt, maxdd, nmis);
}

int main(int argc, char** argv) {
  SceneConfig sc;
  double t = 0.5;
  std::string out;
  for (int i = 1; i < argc; ++i) {
    auto next = [&]() { return i + 1 < argc ? argv[++i] : ""; };
    if (!std::strcmp(argv[i], "--seed")) sc.seed = std::stoul(next());
    else if (!std::strcmp(argv[i], "--n")) sc.num_gaussians = std::stoi(next());
    else if (!std::strcmp(argv[i], "--t")) t = std::stod(next());
    else if (!std::strcmp(argv[i], "--out")) out = next();
    else { std::fprintf(stderr, "usage: %s [--seed S] [--n N] [--t T] [--out DIR]\n", argv[0]); return 2; }
  }
  if (sc.num_gaussians < 1 || sc.num_gaussians >= sc.sram_depth) {
    std::fprintf(stderr, "--n must be in [1, %d]\n", sc.sram_depth - 1);
    return 2;
  }
  const TileConfig tc;
  const auto scene = make_scene(sc, tc);
  F32Policy pf;
  FixedPolicy px;

  std::vector<Splat<float>> s1f; std::vector<uint32_t> s0f;
  std::vector<Splat<Fx>> s1x; std::vector<uint32_t> s0x;
  build_srams(pf, scene, t, sc.sram_depth, s1f, s0f);
  build_srams(px, scene, t, sc.sram_depth, s1x, s0x);

  const auto rf = RtlRasterizer<F32Policy>(pf, tc).run(s1f, s0f);
  const auto rx = RtlRasterizer<FixedPolicy>(px, tc).run(s1x, s0x);
  const auto idf = render_ideal(pf, s1f, s0f, tc);
  const auto idx = render_ideal(px, s1x, s0x, tc);
  const auto rff = RtlRasterizer<F32Policy>(pf, tc, RtlQuirks{false, false, false}).run(s1f, s0f);
  int blank = 0;
  for (const auto& q : rf.pixels) blank += q.n == 0;

  std::printf("scene seed=%u gaussians=%d t=%.4f  rtl cycles f32=%llu fixed=%llu\n", sc.seed, sc.num_gaussians, t,
              (unsigned long long)rf.cycles, (unsigned long long)rx.cycles);
  std::printf("hash f32-rtl %016llx  fixed-rtl %016llx\n", (unsigned long long)fnv(pf, rf),
              (unsigned long long)fnv(px, rx));
  compare("fixed-rtl vs f32-rtl", px, rx, pf, rf);
  compare("fixed-ideal vs f32-ideal", px, idx, pf, idf);
  compare("f32-rtl vs f32-ideal", pf, rf, pf, idf);
  compare("f32-rtl-fixed vs f32-ideal", pf, rff, pf, idf);
  std::printf("f32-rtl pixels with N=0 (sticky early_termination): %d / %zu\n", blank, rf.pixels.size());

  if (!out.empty()) {
    ::mkdir(out.c_str(), 0755);
    const std::string g = out + "/golden_ans";
    ::mkdir(g.c_str(), 0755);
    const std::string gx = out + "/fixed_golden_ans";
    ::mkdir(gx.c_str(), 0755);
    bool ok = write_ram_file(out + "/ram_file.txt", s1f) && write_gs_value(out + "/gs_value.txt", s0f) &&
              write_golden(pf, g, rf, 8) && write_golden(px, gx, rx, 8);
    if (!ok) { std::fprintf(stderr, "write failed\n"); return 1; }
    std::printf("wrote %s\n", out.c_str());
  }
  return 0;
}
