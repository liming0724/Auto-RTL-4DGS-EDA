// Phase 4 frame-level Verilator testbench for gs4d_accelerator_top.
//
// Renders the 64 x 64 x 8-frame dynamic scene of golden_model/gs4d_frame.hpp twice:
//   * FP32 reference (ground truth, every Gaussian at every pixel)
//   * fixed-point RTL: one transaction per (Gaussian, 4-pixel row span) of its 3.5-sigma
//     box, in_t / in_coef driving deform_engine; every output checked bit-true against
//     the C++ golden model; per-span alphas composited here (sort-free weighted sum).
// Reports skip rate, cycles vs. the unskipped cycle count, PSNR vs. FP32, and the
// FP32-vs-fixed validation (per-pixel alpha and frame deviation, directed conic case).
// Writes FP16 RGB frames (gt_f<k>.hex, rtl_f<k>.hex) for the SSIM_v2 testbench.
// Usage: Vgs4d_accelerator_top +thresh=<N> +out=<dir> [+cache_bypass]
#include <algorithm>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>
#include <type_traits>
#include <vector>

#include "gs4d_frame.hpp"
#include "Vgs4d_accelerator_top.h"
#include "verilated.h"

#ifndef Q_FRAC
#define Q_FRAC 8
#endif
#ifndef CACHE_WAYS
#define CACHE_WAYS 2
#endif

namespace {

constexpr int W = 8 + Q_FRAC;
// Packed in_* payload: 12-bit ID, 13 W-bit fields and 10 W-bit coefficients.
constexpr unsigned BYTES_PER_GAUSSIAN = (12 + 23 * W + 7) / 8;
constexpr uint64_t kMask = (uint64_t{1} << W) - 1;
using gs4d_frame::Frame;

int64_t sext(uint64_t v) {
  v &= kMask;
  return (v >> (W - 1)) ? static_cast<int64_t>(v) - (int64_t{1} << W) : static_cast<int64_t>(v);
}
// Scalar ports up to 64 bits; wider ports are uint32_t[N] (Verilator 4) or VlWide (5).
template <class T>
uint64_t bits(const T& word, int lsb) {
  if constexpr (std::is_integral_v<T>) {
    return (static_cast<uint64_t>(word) >> lsb) & kMask;
  } else {
    uint64_t v = 0;
    for (int b = 0; b < W; ++b) v |= static_cast<uint64_t>((word[(lsb + b) / 32] >> ((lsb + b) % 32)) & 1u) << b;
    return v;
  }
}
template <class T>
void set_coef(T& words, const gs4d_ref::GaussIn& g) {  // in_coef: uint32_t[N] (Verilator 4) or VlWide (5)
  constexpr int kWords = (10 * W + 31) / 32;
  for (int i = 0; i < kWords; ++i) words[i] = 0;
  for (int i = 0; i < 10; ++i)
    for (int b = 0; b < W; ++b)
      words[(i * W + b) / 32] |= static_cast<uint32_t>((g.coef[i] >> b) & 1) << ((i * W + b) % 32);
}

void write_hex(const std::string& path, const Frame& f) {
  std::FILE* out = std::fopen(path.c_str(), "w");
  if (!out) { std::perror(path.c_str()); std::exit(2); }
  for (int p = 0; p < gs4d_frame::kPixels; ++p) std::fprintf(out, "%012" PRIx64 "\n", gs4d_frame::pack_rgb(f, p));
  std::fclose(out);
}

struct Job { gs4d_ref::GaussIn in; gs4d_ref::GaussOut out; int gauss, frame; };

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  uint32_t thresh = 0;
  bool cache_bypass = false;
  std::string dir = ".";
  for (int i = 1; i < argc; ++i) {
    if (!std::strncmp(argv[i], "+thresh=", 8)) thresh = static_cast<uint32_t>(std::atoi(argv[i] + 8));
    if (!std::strncmp(argv[i], "+out=", 5)) dir = argv[i] + 5;
    if (!std::strcmp(argv[i], "+cache_bypass")) cache_bypass = true;
  }
  using namespace gs4d_frame;
  const auto scene = make_scene();
  gs4d_ref::Config cfg{1, Q_FRAC, CACHE_WAYS, 512, thresh};
  auto reference_cfg = cfg;
  if (cache_bypass) reference_cfg.motion_thresh = 0;  // Existing full-recompute oracle.
  gs4d_ref::Accelerator model(reference_cfg);
  gs4d_ref::Stats st;
  const double unit = std::ldexp(1.0, -Q_FRAC);
  int fails = 0;

  // Directed FP32 math check: conic vs gs4d::conic_from_scale, alpha vs 3DGS / RTL.
  {
    const ConicF k = conic_fp32(2, 2, 0);
    const auto ref = gs4d::conic_from_scale(2, 2, 0);
    const Deformed d{0, 0, 2, 2, 0};
    const float exact = alpha_fp32(d, 1, 1, 0, ExpModel::kExact);
    const float hw = alpha_fp32(d, 1, 1, 0, ExpModel::kHardware);
    const long hw_q8 = std::lround(hw * 256);
    const bool ok = std::fabs(k.a - ref.a) < 1e-7 && std::fabs(k.b - ref.b) < 1e-7 &&
                    std::fabs(k.c - ref.c) < 1e-7 && std::fabs(exact - std::exp(-0.125f)) < 1e-6 && hw_q8 == 233;
    fails += !ok;
    std::printf("directed FP32 sx=sy=2 rho=0 dx=1: conic=(%.6f, %.6f, %.6f) ref=(%.6f, %.6f, %.6f) "
                "alpha_exact=%.6f alpha_hw_exp=%.6f (Q8 %ld, expected 233) %s\n",
                k.a, k.b, k.c, ref.a, ref.b, ref.c, exact, hw, hw_q8, ok ? "PASS" : "FAIL");
    // Random conic cross-check of the FP32 path against the double reference.
    uint64_t s = 0x636f6e69ull;
    double worst = 0;
    for (int i = 0; i < 10000; ++i) {
      const double sx = gs4d_ref::urand(s, 0.3, 6), sy = gs4d_ref::urand(s, 0.3, 6), r = gs4d_ref::urand(s, -0.9, 0.9);
      const ConicF f = conic_fp32(float(sx), float(sy), float(r));
      const auto c = gs4d::conic_from_scale(sx, sy, r);
      const double scale = std::max({std::fabs(c.a), std::fabs(c.b), std::fabs(c.c)});
      worst = std::max({worst, std::fabs(f.a - c.a) / scale, std::fabs(f.b - c.b) / scale, std::fabs(f.c - c.c) / scale});
    }
    fails += worst > 1e-5;
    std::printf("FP32 conic vs conic_from_scale: 10000 random cases, max relative error %.3g (tol 1e-5)\n", worst);
  }

  std::vector<Job> jobs;
  std::vector<uint64_t> frame_tx(kFrameCount), frame_skip(kFrameCount), fetch_count(kFrameCount);
  for (int fr = 0; fr < kFrameCount; ++fr)
    for (const Span& sp : bin_spans(scene, fr)) {
      Job j{quantize(scene[sp.gauss], sp, fr, Q_FRAC), {}, sp.gauss, fr};
      j.out = model.step(j.in, &st);
      ++frame_tx[fr];
      jobs.push_back(j);
    }

  Vgs4d_accelerator_top top;
  top.cache_bypass = cache_bypass;
  top.motion_thresh = thresh;
  top.out_ready = 1;
  top.in_valid = 0;
  for (int i = 0; i < 4; ++i) {  // clocked reset
    top.rst_n = 0; top.clk = 0; top.eval(); top.clk = 1; top.eval();
  }
  top.rst_n = 1;
  size_t next = 0, done = 0;
  uint64_t cycles = 0, mismatches = 0;
  std::vector<Accum> rtl_acc(kFrameCount);
  double max_hw = 0, max_exact = 0, sum_exact = 0;
  uint64_t alpha_px = 0;
  while (done < jobs.size()) {
    top.clk = 0;
    top.in_valid = next < jobs.size();
    if (top.in_valid) {
      const auto& g = jobs[next].in;
      top.in_id = g.id;
      top.in_mu0_x = g.mu0_x & kMask; top.in_mu0_y = g.mu0_y & kMask;
      top.in_dmu_x = g.dmu_x & kMask; top.in_dmu_y = g.dmu_y & kMask;
      top.in_s0_x = g.s0_x & kMask;   top.in_s0_y = g.s0_y & kMask;
      top.in_ds_x = g.ds_x & kMask;   top.in_ds_y = g.ds_y & kMask;
      top.in_rho = g.rho & kMask;     top.in_opacity = g.opacity & kMask;
      top.in_pix_x0 = g.pix_x0 & kMask; top.in_pix_y = g.pix_y & kMask;
      top.in_t = g.t & kMask;
      set_coef(top.in_coef, g);
    }
    top.eval();
    const bool accept = top.in_valid && top.in_ready;
    const bool fire = top.out_valid && top.out_ready;
    if (fire) {
      const Job& j = jobs[done];
      const auto& e = j.out;
      frame_skip[j.frame] += top.out_skipped;
      fetch_count[j.frame] += !top.out_skipped;
      bool ok = top.out_id == e.id && top.out_skipped == (e.skipped ? 1u : 0u) && top.out_delta == e.delta &&
                sext(top.out_mean_x) == e.attr.mean_x && sext(top.out_mean_y) == e.attr.mean_y &&
                sext(top.out_cov_a) == e.attr.cov_a && sext(top.out_cov_b) == e.attr.cov_b &&
                sext(top.out_cov_c) == e.attr.cov_c && sext(top.out_opacity) == e.attr.opacity;
      const Gauss& g = scene[j.gauss];
      const Deformed d = deform_fp32(g, frame_time(j.frame));
      for (int k = 0; k < gs4d_ref::kTile; ++k) {
        const int64_t a = sext(bits(top.out_alpha, k * W));
        ok = ok && a == e.alpha[k];
        const int px = static_cast<int>(j.in.pix_x0 >> Q_FRAC) + k, py = static_cast<int>(j.in.pix_y >> Q_FRAC);
        if (px >= kFrameSize) continue;
        rtl_acc[j.frame].add(px, py, a * unit, g.rgb);
        const float fx = float(px), fy = float(py);
        const double hw = alpha_fp32(d, g.opa, fx, fy, ExpModel::kHardware, 1.0f / 256);
        const double ex = alpha_fp32(d, g.opa, fx, fy, ExpModel::kExact, 1.0f / 256);
        max_hw = std::max(max_hw, std::fabs(a * unit - hw));
        max_exact = std::max(max_exact, std::fabs(a * unit - ex));
        sum_exact += std::fabs(a * unit - ex);
        ++alpha_px;
      }
      mismatches += !ok;
      ++done;
    }
    top.clk = 1;
    top.eval();
    if (accept) ++next;
    ++cycles;
    if (cycles > 400000000ull) { std::fprintf(stderr, "timeout\n"); return 1; }
  }
  top.final();

  const uint64_t noskip = jobs.size() * (3 + cfg.raster_cycles() + cfg.projection_cycles());
  double se = 0, frame_dev = 0;
  std::printf("frame_4dgs: Q_FRAC=%d MOTION_THRESH=%u %d Gaussians (%d moving), %dx%d x %d frames\n", Q_FRAC,
              thresh, kFrameGaussians, int(std::count_if(scene.begin(), scene.end(), [](const Gauss& g) { return g.moving; })),
              kFrameSize, kFrameSize, kFrameCount);
  for (int fr = 0; fr < kFrameCount; ++fr) {
    const Frame gt = render_fp32(scene, fr, ExpModel::kExact);
    const Frame rtl = rtl_acc[fr].resolve();
    const double m = mse(rtl, gt);
    se += m;
    for (size_t i = 0; i < gt.size(); ++i) frame_dev = std::max(frame_dev, double(std::fabs(rtl[i] - gt[i])));
    write_hex(dir + "/gt_f" + std::to_string(fr) + ".hex", gt);
    write_hex(dir + "/rtl_f" + std::to_string(fr) + ".hex", rtl);
    std::printf("frame_%d: transactions=%llu skipped=%llu psnr=%.2f dB\n", fr, (unsigned long long)frame_tx[fr],
                (unsigned long long)frame_skip[fr], psnr_db(m));
  }
  const double psnr = psnr_db(se / kFrameCount);
  const double skip_rate = double(st.skipped) / jobs.size();
  // Validation tolerances (MOTION_THRESH = 0, Q_FRAC = 12): fixed vs FP32 with the RTL
  // exp model is quantization only (largest on near-singular conics, |rho(t)| -> 0.8);
  // vs exact exp it adds the RTL piecewise-linear 2^-y error (<= 0.043 * opacity).
  const double tol_hw = 0.025, tol_exact = 0.05;
  const bool validate = thresh == 0 && Q_FRAC == 12;
  if (validate && (max_hw > tol_hw || max_exact > tol_exact)) ++fails;
  fails += mismatches != 0 || cycles < 10000;
  fails += cycles != st.cycles;
  std::printf("validation: %llu alpha px, max |fixed - fp32_hw_exp| = %.5f (tol %.3f), max |fixed - fp32_exact| = "
              "%.5f (tol %.3f), mean %.5f, max frame |rtl - fp32| = %.5f%s\n",
              (unsigned long long)alpha_px, max_hw, tol_hw, max_exact, tol_exact, sum_exact / std::max<uint64_t>(1, alpha_px),
              frame_dev, validate ? "" : " (tolerances enforced at Q_FRAC=12, MOTION_THRESH=0 only)");
  std::printf("cycles=%llu transactions=%zu skipped=%llu skip_rate=%.4f noskip_cycles=%llu speedup=%.4fx "
              "psnr=%.2f dB bit-true mismatches=%llu\n",
              (unsigned long long)cycles, jobs.size(), (unsigned long long)st.skipped, skip_rate,
              (unsigned long long)noskip, double(noskip) / cycles, psnr, (unsigned long long)mismatches);
  std::printf("RESULT {\"q_frac\": %d, \"motion_thresh\": %u, \"cycles\": %llu, \"noskip_cycles\": %llu, "
              "\"transactions\": %zu, \"skipped\": %llu, \"skip_rate\": %.6f, \"speedup\": %.6f, \"psnr_db\": %.4f, "
              "\"mismatches\": %llu, \"max_alpha_dev_hw_exp\": %.6f, \"max_alpha_dev_exact\": %.6f, "
              "\"max_frame_dev\": %.6f, \"pass\": %s",
              Q_FRAC, thresh, (unsigned long long)cycles, (unsigned long long)noskip, jobs.size(),
              (unsigned long long)st.skipped, skip_rate, double(noskip) / cycles, psnr, (unsigned long long)mismatches,
              max_hw, max_exact, frame_dev, fails ? "false" : "true");
  std::printf(", \"Modeled External DRAM Traffic\": {\"bytes_per_gaussian\": %u, \"frames\": [", BYTES_PER_GAUSSIAN);
  for (int fr = 0; fr < kFrameCount; ++fr)
    std::printf("%s{\"fetch_count\": %" PRIu64 ", \"dram_read_bytes\": %" PRIu64 ", \"dram_write_bytes\": 0}",
                fr ? ", " : "", fetch_count[fr], fetch_count[fr] * BYTES_PER_GAUSSIAN);
  std::puts("]}}");
  std::puts(fails ? "FAIL" : "PASS");
  return fails ? 1 : 0;
}
