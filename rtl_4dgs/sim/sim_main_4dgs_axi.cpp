// Phase 5c Tier 1 testbench for gs4d_axi_wrapper (split-fetch AXI4-Stream wrapper around
// gs4d_accelerator_top with SPLIT_FETCH = 1).
//
// Workload: the 64 x 64 x 8-frame scene of golden_model/gs4d_frame.hpp (+scene=benign|pan|fast),
// one transaction per (Gaussian, 4-pixel row span), exactly as sim_main_4dgs_frame.cpp; every
// output is checked bit-true against the gs4d_ref::Accelerator golden model (same fields as the
// frame testbench, including the four out_alpha lanes).
// AXI side:
//   * Channel A source (s_axis_meta_*): one beat per Gaussian in scene order; tvalid raised
//     after an LFSR-random gap and held until the handshake (AXI4-Stream rule).
//   * Channel B source (s_axis_attr_*): reactive. It raises tvalid only after it has seen
//     s_axis_attr_tready (after an LFSR-random delay) and sends the base attributes of the
//     pending metadata beat with tuser = its gauss_id.
//   * out_ready: LFSR-random backpressure plus bursts of up to 20 stalled cycles.
// Checks (any failure -> pass = false): zero golden mismatches; one metadata beat per
// Gaussian; attr beats == golden fetch count (non-skipped transactions, tolerance +-1); at most
// one attr beat per Gaussian; s_axis_attr_tready never high while the pending Gaussian is a
// golden skip + hit (independent prediction from the golden model); MEASURED bytes equal the
// MODELED B_meta + (1 - skip_rate) * B_attr prediction within B_meta + B_attr; axi_err never
// set; at least 10000 cycles.
// MEASURED bytes = meta_beats * B_meta + attr_beats * B_attr (handshakes counted here).
// Optional modes:
//   +meta_slow=<D>  Channel A raises tvalid with probability 1/D per cycle instead of 3/4, so the
//                   wrapper waits on the source (meta_stall_cycles); also requires
//                   meta_stall_cycles >= Gaussians / 4 (the default source gives <= 14 per run).
//   +bad_tuser=<K>  negative run: attr beat K carries tuser = gauss_id ^ 1 and inverted tdata; the
//                   source then resends the correct beat. Expected failure: PASS iff axi_err is 0
//                   before that handshake and set on every cycle after it (sticky), the bad beat is
//                   not forwarded (zero golden mismatches, all outputs done), exactly one extra attr
//                   beat, and the normal checks fail.
// Usage: Vgs4d_axi_wrapper +thresh=<N> [+bypass=<0|1>] [+scene=<mode>] [+meta_slow=<D>] [+bad_tuser=<K>]
#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <type_traits>
#include <vector>

#include "gs4d_frame.hpp"
#include "Vgs4d_axi_wrapper.h"
#include "verilated.h"

#ifndef Q_FRAC
#define Q_FRAC 8
#endif
#ifndef CACHE_WAYS
#define CACHE_WAYS 4
#endif

namespace {

constexpr int W = 8 + Q_FRAC;
constexpr uint64_t kMask = (uint64_t{1} << W) - 1;
constexpr int kIdW = 12;
constexpr int kMetaBits = kIdW + 17 * W;  // Channel A TDATA
constexpr int kAttrBits = 6 * W;          // Channel B TDATA
constexpr int kBMeta = (kMetaBits + 7) / 8;
constexpr int kBAttr = (kAttrBits + 7) / 8;
constexpr int kBBase = (kIdW + 23 * W + 7) / 8;  // Phase 5a BYTES_PER_GAUSSIAN

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
void clear(T& port, int nbits) {
  if constexpr (std::is_integral_v<T>) port = 0;
  else for (int i = 0; i < (nbits + 31) / 32; ++i) port[i] = 0;
}
template <class T>
void put(T& port, int lsb, int width, uint64_t v) {
  for (int b = 0; b < width; ++b) {
    const uint64_t bit = (v >> b) & 1u;
    if constexpr (std::is_integral_v<T>) port |= static_cast<T>(bit) << (lsb + b);
    else port[(lsb + b) / 32] |= static_cast<uint32_t>(bit) << ((lsb + b) % 32);
  }
}
struct Lfsr {  // 32-bit Galois LFSR
  uint32_t s;
  uint32_t next() { s = (s >> 1) ^ (-(s & 1u) & 0xA3000000u); return s; }
  bool chance(uint32_t num, uint32_t den) { return next() % den < num; }
};
struct Job { gs4d_ref::GaussIn in; gs4d_ref::GaussOut out; };

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  uint32_t thresh = 0, bypass = 0, meta_slow = 0;
  int64_t bad_tuser = -1;
  gs4d_frame::SceneMode mode = gs4d_frame::SceneMode::kBenign;
  std::string scene_name = "benign";
  for (int i = 1; i < argc; ++i) {
    if (!std::strncmp(argv[i], "+thresh=", 8)) thresh = static_cast<uint32_t>(std::atoi(argv[i] + 8));
    if (!std::strncmp(argv[i], "+bypass=", 8)) bypass = std::atoi(argv[i] + 8) != 0;
    if (!std::strncmp(argv[i], "+meta_slow=", 11)) meta_slow = static_cast<uint32_t>(std::atoi(argv[i] + 11));
    if (!std::strncmp(argv[i], "+bad_tuser=", 11)) bad_tuser = std::atoll(argv[i] + 11);
    if (!std::strncmp(argv[i], "+scene=", 7)) {
      scene_name = argv[i] + 7;
      if (!gs4d_frame::parse_scene(argv[i] + 7, &mode)) {
        std::fprintf(stderr, "unknown scene %s\n", argv[i] + 7);
        return 2;
      }
    }
  }
  using namespace gs4d_frame;
  const auto scene = make_scene(mode);
  gs4d_ref::Config cfg{1, Q_FRAC, CACHE_WAYS, 512, bypass ? 0u : thresh};
  gs4d_ref::Accelerator model(cfg);
  gs4d_ref::Stats st;
  std::vector<Job> jobs;
  uint64_t golden_skipped = 0;
  for (int fr = 0; fr < kFrameCount; ++fr)
    for (const Span& sp : bin_spans(scene, fr)) {
      Job j{quantize(scene[sp.gauss], sp, fr, Q_FRAC), {}};
      j.out = model.step(j.in, &st);
      golden_skipped += j.out.skipped;
      jobs.push_back(j);
    }
  const uint64_t n = jobs.size();

  Vgs4d_axi_wrapper top;
  top.motion_thresh = thresh;
  top.cache_bypass = bypass;
  top.s_axis_meta_tvalid = 0;
  top.s_axis_attr_tvalid = 0;
  top.s_axis_attr_tuser = 0;
  clear(top.s_axis_meta_tdata, kMetaBits);
  clear(top.s_axis_attr_tdata, kAttrBits);
  top.out_ready = 1;
  for (int i = 0; i < 4; ++i) {  // clocked reset
    top.rst_n = 0; top.clk = 0; top.eval(); top.clk = 1; top.eval();
  }
  top.rst_n = 1;

  Lfsr rng{0x5c0ffee1u ^ (uint32_t(Q_FRAC) << 24) ^ (thresh << 8) ^ (bypass << 4) ^ uint32_t(mode)};
  size_t meta_sent = 0, done = 0;
  uint64_t cycles = 0, mismatches = 0, attr_beats = 0, meta_beats = 0, skip_hit_requests = 0, multi_attr = 0;
  uint64_t meta_stall = 0, attr_stall = 0, out_bp = 0, max_bp_run = 0, bp_run = 0, axi_err_cycles = 0;
  uint64_t err_before = 0, err_after = 0, cycles_after = 0;
  bool injected = false, bad_pending = false;
  int out_stall_left = 0, attr_delay = -1;
  std::vector<uint8_t> attr_count(n, 0);
  const uint64_t limit = 400 * n + 100000;
  while (done < n && cycles < limit) {
    top.clk = 0;
    // Channel A source: raise tvalid after a random gap, hold until accepted.
    if (!top.s_axis_meta_tvalid && meta_sent < n && (meta_slow ? rng.chance(1, meta_slow) : rng.chance(3, 4))) {
      const auto& g = jobs[meta_sent].in;
      clear(top.s_axis_meta_tdata, kMetaBits);
      put(top.s_axis_meta_tdata, 0, W, g.pix_y & kMask);
      put(top.s_axis_meta_tdata, W, W, g.pix_x0 & kMask);
      for (int c = 0; c < 10; ++c) put(top.s_axis_meta_tdata, (2 + c) * W, W, g.coef[c] & kMask);
      put(top.s_axis_meta_tdata, 12 * W, W, g.ds_y & kMask);
      put(top.s_axis_meta_tdata, 13 * W, W, g.ds_x & kMask);
      put(top.s_axis_meta_tdata, 14 * W, W, g.dmu_y & kMask);
      put(top.s_axis_meta_tdata, 15 * W, W, g.dmu_x & kMask);
      put(top.s_axis_meta_tdata, 16 * W, W, g.t & kMask);
      put(top.s_axis_meta_tdata, 17 * W, kIdW, g.id);
      top.s_axis_meta_tvalid = 1;
    }
    // Channel B source: reactive, answers only an observed s_axis_attr_tready.
    if (top.s_axis_attr_tready && !top.s_axis_attr_tvalid) {
      if (attr_delay < 0) attr_delay = static_cast<int>(rng.next() % 4);
      if (attr_delay-- == 0) {
        const auto& g = jobs[meta_sent - 1].in;
        clear(top.s_axis_attr_tdata, kAttrBits);
        put(top.s_axis_attr_tdata, 0, W, g.opacity & kMask);
        put(top.s_axis_attr_tdata, W, W, g.rho & kMask);
        put(top.s_axis_attr_tdata, 2 * W, W, g.s0_y & kMask);
        put(top.s_axis_attr_tdata, 3 * W, W, g.s0_x & kMask);
        put(top.s_axis_attr_tdata, 4 * W, W, g.mu0_y & kMask);
        put(top.s_axis_attr_tdata, 5 * W, W, g.mu0_x & kMask);
        top.s_axis_attr_tuser = g.id;
        bad_pending = !injected && bad_tuser >= 0 && attr_beats == static_cast<uint64_t>(bad_tuser);
        if (bad_pending) {
          for (int i = 0; i < (kAttrBits + 31) / 32; ++i) top.s_axis_attr_tdata[i] = ~top.s_axis_attr_tdata[i];
          top.s_axis_attr_tuser = (g.id ^ 1u) & ((1u << kIdW) - 1);
        }
        top.s_axis_attr_tvalid = 1;
        attr_delay = -1;
      }
    }
    // Output backpressure: random single-cycle stalls and bursts of up to 20 cycles.
    if (out_stall_left == 0 && rng.chance(1, 64)) out_stall_left = 1 + static_cast<int>(rng.next() % 20);
    top.out_ready = out_stall_left > 0 ? 0 : !rng.chance(1, 8);
    if (out_stall_left > 0) --out_stall_left;
    top.eval();

    const bool meta_fire = top.s_axis_meta_tvalid && top.s_axis_meta_tready;
    const bool attr_fire = top.s_axis_attr_tvalid && top.s_axis_attr_tready;
    const bool out_fire = top.out_valid && top.out_ready;
    meta_stall += top.s_axis_meta_tready && !top.s_axis_meta_tvalid && meta_sent < n;
    attr_stall += top.s_axis_attr_tready && !top.s_axis_attr_tvalid;
    if (top.out_valid && !top.out_ready) { ++out_bp; max_bp_run = std::max(max_bp_run, ++bp_run); }
    else bp_run = 0;
    if (top.s_axis_attr_tready && meta_sent > 0 && jobs[meta_sent - 1].out.skipped) ++skip_hit_requests;
    if (attr_fire && ++attr_count[meta_sent - 1] > 1) ++multi_attr;
    axi_err_cycles += top.axi_err;
    if (injected) { err_after += top.axi_err; ++cycles_after; }
    else err_before += top.axi_err;
    if (out_fire) {
      const auto& e = jobs[done].out;
      bool ok = top.out_id == e.id && top.out_skipped == (e.skipped ? 1u : 0u) && top.out_delta == e.delta &&
                sext(top.out_mean_x) == e.attr.mean_x && sext(top.out_mean_y) == e.attr.mean_y &&
                sext(top.out_cov_a) == e.attr.cov_a && sext(top.out_cov_b) == e.attr.cov_b &&
                sext(top.out_cov_c) == e.attr.cov_c && sext(top.out_opacity) == e.attr.opacity;
      for (int k = 0; k < gs4d_ref::kTile; ++k) ok = ok && sext(bits(top.out_alpha, k * W)) == e.alpha[k];
      if (!ok && mismatches < 5)
        std::printf("MISMATCH tx %zu id %u skipped rtl=%u golden=%d\n", done, e.id, top.out_skipped, e.skipped);
      mismatches += !ok;
      ++done;
    }
    top.clk = 1;
    top.eval();
    ++cycles;
    if (meta_fire) { top.s_axis_meta_tvalid = 0; ++meta_sent; ++meta_beats; }
    if (attr_fire) { top.s_axis_attr_tvalid = 0; ++attr_beats; injected = injected || bad_pending; bad_pending = false; }
  }
  const uint64_t golden_fetch = n - golden_skipped;
  const uint64_t measured = meta_beats * kBMeta + attr_beats * kBAttr;
  const uint64_t modeled = n * kBMeta + golden_fetch * kBAttr;
  const uint64_t baseline = n * kBBase;
  uint64_t attr_per_gauss_bad = multi_attr;
  for (uint64_t i = 0; i < n; ++i) attr_per_gauss_bad += attr_count[i] != (jobs[i].out.skipped ? 0 : 1);
  const int64_t attr_err = static_cast<int64_t>(attr_beats) - static_cast<int64_t>(golden_fetch);
  const int64_t byte_err = static_cast<int64_t>(measured) - static_cast<int64_t>(modeled);
  const bool normal_pass = done == n && mismatches == 0 && meta_beats == n && std::llabs(attr_err) <= 1 &&
                           attr_per_gauss_bad == 0 && skip_hit_requests == 0 &&
                           std::llabs(byte_err) <= kBMeta + kBAttr && axi_err_cycles == 0 && cycles >= 10000 &&
                           (!bypass || attr_beats == n) && (!meta_slow || 4 * meta_stall >= n);
  // The bad beat is a second attr beat for its Gaussian: one multi-beat and one count error.
  const bool negative_pass = injected && err_before == 0 && cycles_after > 0 && err_after == cycles_after &&
                             done == n && mismatches == 0 && meta_beats == n && attr_per_gauss_bad == 2 &&
                             std::llabs(attr_err - 1) <= 1 && skip_hit_requests == 0 && !normal_pass;
  const bool pass = bad_tuser >= 0 ? negative_pass : normal_pass;
  const char* mode_name = bad_tuser >= 0 ? "bad_tuser" : meta_slow ? "meta_slow" : "normal";
  if (bad_tuser >= 0)
    std::printf("negative run: bad tuser at attr beat %" PRId64 " injected=%d axi_err before=%" PRIu64
                " after=%" PRIu64 "/%" PRIu64 " cycles normal_checks=%s -> expected failure %s\n",
                bad_tuser, injected, err_before, err_after, cycles_after, normal_pass ? "PASS" : "FAIL",
                negative_pass ? "OBSERVED" : "NOT OBSERVED");
  const double skip_rate = double(golden_skipped) / n;
  const double avg = double(measured) / n;
  std::printf("mode=%s scene=%s Q%d T=%u bypass=%u gaussians=%" PRIu64 " golden_skipped=%" PRIu64 " meta_beats=%" PRIu64
              " attr_beats=%" PRIu64 " MEASURED=%" PRIu64 " B (avg %.3f B) MODELED=%" PRIu64 " B baseline=%" PRIu64
              " B cycles=%" PRIu64 " mismatches=%" PRIu64 " skip_hit_attr_requests=%" PRIu64 " axi_err=%" PRIu64 " %s\n",
              mode_name, scene_name.c_str(), Q_FRAC, thresh, bypass, n, golden_skipped, meta_beats, attr_beats, measured, avg,
              modeled, baseline, cycles, mismatches, skip_hit_requests, axi_err_cycles, pass ? "PASS" : "FAIL");
  std::printf("RESULT {\"mode\": \"%s\", \"meta_slow\": %u, \"bad_tuser\": %" PRId64 ", "
              "\"normal_checks_pass\": %s, \"scene\": \"%s\", \"q_frac\": %d, \"motion_thresh\": %u, \"cache_bypass\": %u, "
              "\"cache_ways\": %d, \"transactions\": %" PRIu64 ", \"golden_skipped\": %" PRIu64 ", \"skip_rate\": %.6f, "
              "\"meta_beats\": %" PRIu64 ", \"attr_beats\": %" PRIu64 ", \"b_meta\": %d, \"b_attr\": %d, "
              "\"b_baseline\": %d, \"measured_bytes\": %" PRIu64 ", \"modeled_bytes\": %" PRIu64 ", "
              "\"baseline_bytes\": %" PRIu64 ", \"measured_avg_bytes\": %.4f, \"modeled_avg_bytes\": %.4f, "
              "\"measured_reduction_pct\": %.4f, \"attr_requests_on_skip_hit\": %" PRIu64 ", "
              "\"attr_per_gaussian_errors\": %" PRIu64 ", \"cycles\": %" PRIu64 ", \"meta_stall_cycles\": %" PRIu64 ", "
              "\"attr_stall_cycles\": %" PRIu64 ", \"out_backpressure_cycles\": %" PRIu64 ", \"max_out_stall_run\": %" PRIu64
              ", \"mismatches\": %" PRIu64 ", \"axi_err\": %" PRIu64 ", \"pass\": %s}\n",
              mode_name, meta_slow, bad_tuser, normal_pass ? "true" : "false",
              scene_name.c_str(), Q_FRAC, thresh, bypass, CACHE_WAYS, n, golden_skipped, skip_rate, meta_beats, attr_beats,
              kBMeta, kBAttr, kBBase, measured, modeled, baseline, avg, double(modeled) / n,
              100.0 * (1.0 - double(measured) / baseline), skip_hit_requests, attr_per_gauss_bad, cycles, meta_stall,
              attr_stall, out_bp, max_bp_run, mismatches, axi_err_cycles, pass ? "true" : "false");
  top.final();
  return pass ? 0 : 1;
}
