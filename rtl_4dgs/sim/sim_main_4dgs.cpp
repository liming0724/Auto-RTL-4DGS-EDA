// Tier-1 Verilator testbench for rtl_4dgs/gs4d_accelerator_top.sv.
//
// Streams frame_0..frame_3 of the synthetic dynamic scene (golden_model/4dgs_ref.hpp)
// through the RTL with random input bubbles and random out_ready backpressure, and
// checks every output transaction bit-true against gs4d_ref::Accelerator:
// id, skipped, delta, the six 2D attributes and the TILE alpha values. Also checks
//   * outputs are held stable while out_valid && !out_ready,
//   * active cycles (excluding input bubbles and output stalls) == golden cycle model,
//   * covariance-datapath operand registers do not toggle while a skipped Gaussian
//     is in flight (operand isolation) and no cache write-back is issued for it,
//   * MOTION_THRESH == 0 never skips,
//   * at least 20,000 simulated cycles.
// Compile-time parameters NUM_PE, Q_FRAC, CACHE_WAYS, CACHE_SETS match the -G values.
// Runtime: +thresh=<MOTION_THRESH> (default 16), +seed=<n>.
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <vector>
#include <string>

#include "4dgs_ref.hpp"
#include "Vgs4d_accelerator_top.h"
#include "verilated.h"

// Internal signals via --public-flat-rw; newer Verilator stores them in rootp.
#if defined(VERILATOR_VERSION_INTEGER) && VERILATOR_VERSION_INTEGER >= 4020000
#include "Vgs4d_accelerator_top___024root.h"
#define SIG(n) top.rootp->gs4d_accelerator_top__DOT__##n
#else
#define SIG(n) top.gs4d_accelerator_top__DOT__##n
#endif

#ifndef NUM_PE
#define NUM_PE 1
#endif
#ifndef Q_FRAC
#define Q_FRAC 8
#endif
#ifndef CACHE_WAYS
#define CACHE_WAYS 2
#endif
#ifndef CACHE_SETS
#define CACHE_SETS 512
#endif

namespace {

constexpr int W = 8 + Q_FRAC;
constexpr uint64_t kMask = (uint64_t{1} << W) - 1;
constexpr uint64_t kMinCycles = 20000;

uint64_t lcg(uint64_t& s) {
  s = s * 6364136223846793005ull + 1442695040888963407ull;
  return s >> 33;
}

int64_t sext(uint64_t v) {
  v &= kMask;
  return (v >> (W - 1)) ? static_cast<int64_t>(v) - (int64_t{1} << W) : static_cast<int64_t>(v);
}

template <class T>
uint64_t bits(const T& word, int lsb) {  // scalar out_alpha (TILE*W <= 64)
  return (static_cast<uint64_t>(word) >> lsb) & kMask;
}
template <std::size_t N>
uint64_t bits(const uint32_t (&words)[N], int lsb) {  // wide out_alpha
  uint64_t v = 0;
  for (int b = 0; b < W; ++b) v |= static_cast<uint64_t>((words[(lsb + b) / 32] >> ((lsb + b) % 32)) & 1u) << b;
  return v;
}

template <class T>
void set_coef(T& words, const gs4d_ref::GaussIn& g) {  // in_coef: uint32_t[N] (Verilator 4) or VlWide (5)
  constexpr int kWords = (10 * W + 31) / 32;
  for (int i = 0; i < kWords; ++i) words[i] = 0;
  for (int i = 0; i < 10; ++i)
    for (int b = 0; b < W; ++b)
      words[(i * W + b) / 32] |= static_cast<uint32_t>((g.coef[i] >> b) & 1) << ((i * W + b) % 32);
}

// Deformation-engine vectors: constrained-random t in [0, 1] with small coefficients,
// plus directed extremes (t = 0, 1, FX_MAX, FX_MIN; coefficients at FX_MAX / FX_MIN)
// that drive the accumulator into both saturation rails.
std::vector<gs4d_ref::GaussIn> deform_vectors(uint64_t seed, int count) {
  const int64_t one = int64_t{1} << Q_FRAC, fmax = (int64_t{1} << (W - 1)) - 1, fmin = -fmax - 1;
  const int64_t ts[] = {0, one, fmax, fmin, one / 2, 1};
  const int64_t cs[] = {0, fmax, fmin, one, -one, 1, -1};
  std::vector<gs4d_ref::GaussIn> out;
  auto base = [&](gs4d_ref::GaussIn& g) {
    g.id = static_cast<uint32_t>(lcg(seed) % (1u << gs4d_ref::kIdW));
    g.mu0_x = (8 + lcg(seed) % 48) * one; g.mu0_y = (8 + lcg(seed) % 48) * one;
    g.s0_x = one / 2 + lcg(seed) % one; g.s0_y = one / 2 + lcg(seed) % one;
    g.rho = static_cast<int64_t>(lcg(seed) % (one / 2)) - one / 4;
    g.opacity = one / 4 + lcg(seed) % (one / 2);
    g.dmu_x = static_cast<int64_t>(lcg(seed) % (4 * one)) - 2 * one;
    g.dmu_y = static_cast<int64_t>(lcg(seed) % (4 * one)) - 2 * one;
    g.pix_x0 = g.mu0_x - one; g.pix_y = g.mu0_y;
  };
  for (int i = 0; i < count; ++i) {
    gs4d_ref::GaussIn g;
    base(g);
    if (i % 4 == 3) {
      g.t = ts[lcg(seed) % 6];
      for (auto& c : g.coef) c = cs[lcg(seed) % 7];
    } else {
      g.t = lcg(seed) % (one + 1);
      for (int k = 0; k < 10; ++k) {
        const int64_t span = k < 4 ? 8 * one : k < 8 ? one / 4 : one / 8;
        g.coef[k] = static_cast<int64_t>(lcg(seed) % (2 * span + 1)) - span;
      }
    }
    out.push_back(g);
  }
  return out;
}

struct Snap {
  uint64_t id, skipped, delta, mx, my, ca, cb, cc, op;
  int64_t alpha[gs4d_ref::kTile];
  bool operator==(const Snap& o) const { return std::memcmp(this, &o, sizeof(Snap)) == 0; }
};

Snap snap(const Vgs4d_accelerator_top& t) {
  Snap s{};
  s.id = t.out_id; s.skipped = t.out_skipped; s.delta = t.out_delta;
  s.mx = t.out_mean_x; s.my = t.out_mean_y; s.ca = t.out_cov_a;
  s.cb = t.out_cov_b; s.cc = t.out_cov_c; s.op = t.out_opacity;
  for (int k = 0; k < gs4d_ref::kTile; ++k) s.alpha[k] = sext(bits(t.out_alpha, k * W));
  return s;
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  uint32_t thresh = 16;
  uint64_t seed = 0x4dd5eedull;
  for (int i = 1; i < argc; ++i) {
    if (!std::strncmp(argv[i], "+thresh=", 8)) thresh = static_cast<uint32_t>(std::atoi(argv[i] + 8));
    if (!std::strncmp(argv[i], "+seed=", 6)) seed = std::strtoull(argv[i] + 6, nullptr, 0);
  }
  gs4d_ref::Config cfg{NUM_PE, Q_FRAC, CACHE_WAYS, CACHE_SETS, thresh};
  gs4d_ref::Accelerator model(cfg);
  gs4d_ref::Stats st;
  const auto scene = gs4d_ref::make_scene();

  std::deque<gs4d_ref::GaussIn> stim;
  std::deque<gs4d_ref::GaussOut> expect;
  std::deque<gs4d_ref::GaussIn> inputs;
  gs4d_ref::ConicCheck conic_check;
  uint64_t model_cycles[gs4d_ref::kFrames] = {}, noskip_cycles[gs4d_ref::kFrames] = {};
  uint64_t frame_skips[gs4d_ref::kFrames] = {};
  std::deque<int> frame_of;
  for (int t = 0; t < gs4d_ref::kFrames; ++t) {
    for (const auto& g : gs4d_ref::make_frame(scene, t, Q_FRAC)) {
      const auto o = model.step(g, &st);
      stim.push_back(g);
      expect.push_back(o);
      frame_of.push_back(t);
      model_cycles[t] += o.cycles;
      noskip_cycles[t] += 3 + cfg.raster_cycles() + cfg.projection_cycles();
      frame_skips[t] += o.skipped;
    }
  }
  for (const auto& g : deform_vectors(seed ^ 0xdef0, 800)) {
    const auto o = model.step(g, &st);
    stim.push_back(g); expect.push_back(o); frame_of.push_back(gs4d_ref::kFrames-1);
    model_cycles[gs4d_ref::kFrames-1] += o.cycles;
    noskip_cycles[gs4d_ref::kFrames-1] += 3 + cfg.raster_cycles() + cfg.projection_cycles();
    frame_skips[gs4d_ref::kFrames-1] += o.skipped;
  }
  for (auto g : gs4d_ref::conic_edges(Q_FRAC)) {
    g.dmu_x = int64_t{1} << Q_FRAC;
    const auto o = model.step(g, &st);
    stim.push_back(g); expect.push_back(o); frame_of.push_back(gs4d_ref::kFrames-1);
    model_cycles[gs4d_ref::kFrames-1] += o.cycles;
    noskip_cycles[gs4d_ref::kFrames-1] += o.cycles;
  }
  auto directed = gs4d_ref::directed_conic(Q_FRAC);
  directed.dmu_x = directed.pix_x0;  // force recompute even with nonzero threshold
  directed.pix_x0 += directed.dmu_x;
  const auto directed_out = model.step(directed, &st);
  stim.push_back(directed); expect.push_back(directed_out); frame_of.push_back(gs4d_ref::kFrames-1);
  model_cycles[gs4d_ref::kFrames-1] += directed_out.cycles;
  noskip_cycles[gs4d_ref::kFrames-1] += directed_out.cycles;
  inputs = stim;
  const size_t total = stim.size();

  Vgs4d_accelerator_top top;
  top.motion_thresh = thresh;
  top.cache_bypass = 0;
  top.clk = 0;
  top.rst_n = 0;
  top.in_valid = 0;
  top.out_ready = 0;
  top.eval();
  for (int i = 0; i < 4; ++i) { top.clk = 1; top.eval(); top.clk = 0; top.eval(); }
  top.rst_n = 1;
  top.eval();

  uint64_t cycles = 0, idle = 0, stall = 0, mismatches = 0, stability_err = 0;
  uint64_t iso_err = 0, skip_cycles = 0, outputs = 0, skipped_rtl = 0;
  uint64_t frame_active[gs4d_ref::kFrames] = {};
  bool held = false, in_flight_skip = false;
  Snap held_snap{};
  uint64_t iso_ref[10] = {};
  bool inv = false;
  const uint64_t timeout = (cfg.projection_cycles()+40ull) * total + 1000;

  while (outputs < total && cycles < timeout) {
    if (!inv) top.in_valid = !stim.empty() && (lcg(seed) % 10) < 3;
    top.out_ready = (lcg(seed) % 4) != 0;
    top.eval();
    const bool accept = top.in_valid && top.in_ready;
    const bool fire = top.out_valid && top.out_ready;
    if (top.in_valid && !top.in_ready) inv = true;  // hold in_valid until accepted
    if (top.in_ready && !top.in_valid) ++idle;
    else if (top.out_valid && !top.out_ready) ++stall;
    else if (!expect.empty() || outputs < total) ++frame_active[frame_of.empty() ? 0 : frame_of.front()];

    if (!stim.empty()) {
      const auto& g = stim.front();
      top.in_id = g.id;
      top.in_mu0_x = g.mu0_x & kMask; top.in_mu0_y = g.mu0_y & kMask;
      top.in_dmu_x = g.dmu_x & kMask; top.in_dmu_y = g.dmu_y & kMask;
      top.in_s0_x = g.s0_x & kMask;   top.in_s0_y = g.s0_y & kMask;
      top.in_ds_x = g.ds_x & kMask;   top.in_ds_y = g.ds_y & kMask;
      top.in_rho = g.rho & kMask;     top.in_opacity = g.opacity & kMask;
      top.in_pix_x0 = g.pix_x0 & kMask; top.in_pix_y = g.pix_y & kMask;
      top.in_t = g.t & kMask;           set_coef(top.in_coef, g);
      top.eval();
    }

    if (top.out_valid) {
      const Snap s = snap(top);
      if (held && !(s == held_snap)) ++stability_err;
      held = !top.out_ready;
      held_snap = s;
    } else {
      held = false;
    }

    if (in_flight_skip) {
      ++skip_cycles;
      const uint64_t now[10] = {SIG(proj_a), SIG(proj_b), SIG(sx_q), SIG(sy_q),
        SIG(numerator), SIG(quotient), SIG(remainder_q), SIG(denominator), SIG(inv_q), SIG(div_count)};
      for (int i = 0; i < 10; ++i) iso_err += now[i] != iso_ref[i];
      iso_err += SIG(cache_wr);
    }

    if (fire) {
      const auto& e = expect.front();
      const Snap s = snap(top);
      if (!s.skipped) {
        gs4d_ref::Attr attr{sext(s.mx), sext(s.my), sext(s.ca), sext(s.cb), sext(s.cc), sext(s.op)};
        // Saturation-rail deform vectors (|rho(t)| >= 1, or |s| >= 11 px so s^2 saturates
        // Q(7,F)) are not representable covariances; they stay bit-true checked but skip
        // the conic cross-check.
        const gs4d_ref::Fx fx(Q_FRAC);
        const auto d = gs4d_ref::deform(fx, inputs.front());
        const int64_t one = int64_t{1} << Q_FRAC;
        const int64_t sx = fx.add(d.s0_x, d.ds_x), sy = fx.add(d.s0_y, d.ds_y);
        if (std::llabs(d.rho) < one && std::llabs(sx) < 11 * one && std::llabs(sy) < 11 * one)
          conic_check.check(fx, d, attr);
      }
      if (outputs == total-1 && Q_FRAC == 8) {
        std::printf("directed RTL conic alpha=%lld expected=233 (not 35)\n", (long long)s.alpha[0]);
        if (s.alpha[0] != 233 || s.alpha[0] == 35 || s.skipped) ++mismatches;
      }
      const bool ok = s.id == e.id && s.skipped == (e.skipped ? 1u : 0u) && s.delta == e.delta &&
                      sext(s.mx) == e.attr.mean_x && sext(s.my) == e.attr.mean_y &&
                      sext(s.ca) == e.attr.cov_a && sext(s.cb) == e.attr.cov_b &&
                      sext(s.cc) == e.attr.cov_c && sext(s.op) == e.attr.opacity &&
                      s.alpha[0] == e.alpha[0] && s.alpha[1] == e.alpha[1] &&
                      s.alpha[2] == e.alpha[2] && s.alpha[3] == e.alpha[3];
      if (!ok) {
        if (mismatches < 10)
          std::printf("MISMATCH #%" PRIu64 " id=%u rtl{skip=%" PRIu64 " delta=%" PRIu64 " mx=%" PRId64
                      " cov_a=%" PRId64 " cov_b=%" PRId64 " a0=%" PRId64 "} ref{skip=%d delta=%u mx=%" PRId64
                      " cov_a=%" PRId64 " cov_b=%" PRId64 " a0=%" PRId64 "}\n",
                      outputs, e.id, s.skipped, s.delta, sext(s.mx), sext(s.ca), sext(s.cb), s.alpha[0],
                      e.skipped, e.delta, e.attr.mean_x, e.attr.cov_a, e.attr.cov_b, e.alpha[0]);
        ++mismatches;
      }
      skipped_rtl += s.skipped;
      expect.pop_front();
      inputs.pop_front();
      frame_of.pop_front();
      ++outputs;
    }

    top.clk = 1; top.eval();
    top.clk = 0; top.eval();
    ++cycles;

    if (accept) {
      stim.pop_front();
      inv = false;
    }
    // Track the in-flight transaction for the operand-isolation check: the decision is
    // visible once LOOKUP has resolved (state RASTER with out_skipped set).
    if (SIG(state) == 3 && SIG(out_skipped) && !in_flight_skip) {
      in_flight_skip = true;
      iso_ref[0] = SIG(proj_a); iso_ref[1] = SIG(proj_b); iso_ref[2] = SIG(sx_q); iso_ref[3] = SIG(sy_q);
      iso_ref[4] = SIG(numerator); iso_ref[5] = SIG(quotient); iso_ref[6] = SIG(remainder_q);
      iso_ref[7] = SIG(denominator); iso_ref[8] = SIG(inv_q); iso_ref[9] = SIG(div_count);
    }
    if (SIG(state) == 0) in_flight_skip = false;
  }
  if (outputs < total) {
    std::printf("TIMEOUT after %" PRIu64 " cycles: %" PRIu64 "/%zu outputs\n", cycles, outputs, total);
    return 1;
  }

  uint64_t active = 0, model_total = 0, noskip_total = 0;
  for (int t = 0; t < gs4d_ref::kFrames; ++t) {
    active += frame_active[t];
    model_total += model_cycles[t];
    noskip_total += noskip_cycles[t];
    std::printf("frame_%d: skipped=%" PRIu64 "/%d active_cycles=%" PRIu64 " model=%" PRIu64
                " no-skip=%" PRIu64 " reduction=%.1f%%\n",
                t, frame_skips[t], gs4d_ref::kGaussians, frame_active[t], model_cycles[t],
                noskip_cycles[t], 100.0 * (1.0 - double(model_cycles[t]) / double(noskip_cycles[t])));
  }
  const bool cycles_ok = active == model_total;
  const bool lossless_ok = thresh != 0 || skipped_rtl == 0;
  conic_check.report(Q_FRAC);
  const bool pass = conic_check.failures == 0 && mismatches == 0 && stability_err == 0 && iso_err == 0 && cycles_ok &&
                    lossless_ok && cycles >= kMinCycles && skipped_rtl == st.skipped;
  const double skip_rate = double(skipped_rtl) / double(total);
  const double speedup = double(noskip_total) / double(model_total);
  const double thr = double(total) * gs4d_ref::kTile / double(model_total);
  std::printf("sim_4dgs: NUM_PE=%d Q_FRAC=%d CACHE_WAYS=%d CACHE_SETS=%d MOTION_THRESH=%u\n",
              NUM_PE, Q_FRAC, CACHE_WAYS, CACHE_SETS, thresh);
  std::printf("  cycles=%" PRIu64 " (idle=%" PRIu64 " stall=%" PRIu64 " active=%" PRIu64
              " model=%" PRIu64 "%s) transactions=%" PRIu64 "\n",
              cycles, idle, stall, active, model_total, cycles_ok ? "" : " MISMATCH", outputs);
  std::printf("  bit-true mismatches=%" PRIu64 " stability_errors=%" PRIu64 " isolation_errors=%" PRIu64
              " (over %" PRIu64 " skipped cycles)\n", mismatches, stability_err, iso_err, skip_cycles);
  std::printf("  skipped=%" PRIu64 " skip_rate=%.4f cycle_reduction=%.2f%% speedup=%.4fx "
              "throughput=%.4f px/cycle lossy_alpha_px=%" PRIu64 " max_alpha_err_lsb=%" PRId64 "\n",
              skipped_rtl, skip_rate, 100.0 * (1.0 - double(model_total) / double(noskip_total)), speedup,
              thr, st.alpha_mismatch_vs_full, st.max_alpha_err);
  std::printf("RESULT {\"num_pe\": %d, \"q_frac\": %d, \"cache_ways\": %d, \"cache_sets\": %d, "
              "\"motion_thresh\": %u, \"sim_cycles\": %" PRIu64 ", \"active_cycles\": %" PRIu64
              ", \"noskip_cycles\": %" PRIu64 ", \"transactions\": %" PRIu64 ", \"mismatches\": %" PRIu64
              ", \"skipped\": %" PRIu64 ", \"skip_rate\": %.6f, \"speedup\": %.6f, "
              "\"throughput_px_per_cycle\": %.6f, \"lossy_alpha_px\": %" PRIu64
              ", \"max_alpha_err_lsb\": %" PRId64 ", \"pass\": %s}\n",
              NUM_PE, Q_FRAC, CACHE_WAYS, CACHE_SETS, thresh, cycles, active, noskip_total, outputs,
              mismatches, skipped_rtl, skip_rate, speedup, thr, st.alpha_mismatch_vs_full,
              st.max_alpha_err, pass ? "true" : "false");
  std::printf("%s\n", pass ? "PASS" : "FAIL");
  top.final();
  return pass ? 0 : 1;
}
