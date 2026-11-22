// CLI for the bit-true 4DGS golden model (golden_model/4dgs_ref.hpp).
//
//   4dgs_ref [--num-pe N] [--q-frac F] [--ways W] [--thresh T] [--trace-dir DIR] [--sweep]
//
// Default: run frame_0..frame_3 of the synthetic dynamic scene through one configuration
// and print per-frame skip/cycle/operation savings. --trace-dir writes the stimulus and
// expected outputs as DIR/frame_<t>.csv (raw two's-complement Q(7,F) integers).
// --sweep prints the theoretical metrics of the full DSE grid as CSV.
#include <cstdio>
#include <cstring>
#include <string>

#include "4dgs_ref.hpp"

using namespace gs4d_ref;

namespace {

Stats run(const Config& c, const std::vector<SceneGauss>& scene, const char* trace_dir,
          bool verbose) {
  Accelerator acc(c);
  Stats total;
  for (int t = 0; t < kFrames; ++t) {
    Stats st;
    FILE* fp = nullptr;
    if (trace_dir) {
      const std::string path = std::string(trace_dir) + "/frame_" + std::to_string(t) + ".csv";
      fp = std::fopen(path.c_str(), "w");
      if (!fp) { std::perror(path.c_str()); std::exit(2); }
      std::fprintf(fp, "id,mu0_x,mu0_y,dmu_x,dmu_y,s0_x,s0_y,ds_x,ds_y,rho,opacity,pix_x0,pix_y,"
                       "delta,skipped,mean_x,mean_y,cov_a,cov_b,cov_c,opa,alpha0,alpha1,alpha2,alpha3,cycles\n");
    }
    for (const GaussIn& g : make_frame(scene, t, c.q_frac)) {
      const GaussOut o = acc.step(g, &st);
      if (fp) {
        std::fprintf(fp, "%u,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%u,%d,"
                         "%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld,%d\n",
                     g.id, (long long)g.mu0_x, (long long)g.mu0_y, (long long)g.dmu_x,
                     (long long)g.dmu_y, (long long)g.s0_x, (long long)g.s0_y, (long long)g.ds_x,
                     (long long)g.ds_y, (long long)g.rho, (long long)g.opacity,
                     (long long)g.pix_x0, (long long)g.pix_y, o.delta, o.skipped ? 1 : 0,
                     (long long)o.attr.mean_x, (long long)o.attr.mean_y, (long long)o.attr.cov_a,
                     (long long)o.attr.cov_b, (long long)o.attr.cov_c, (long long)o.attr.opacity,
                     (long long)o.alpha[0], (long long)o.alpha[1], (long long)o.alpha[2],
                     (long long)o.alpha[3], o.cycles);
      }
    }
    if (fp) std::fclose(fp);
    if (verbose) {
      std::printf("frame_%d: gaussians=%llu skip_eligible=%llu skipped=%llu hits=%llu cycles=%llu "
                  "(no-skip %llu, -%.1f%%) mults=%llu (no-skip %llu) alpha_err_px=%llu max_err=%lld\n",
                  t, (unsigned long long)st.gaussians, (unsigned long long)st.skip_eligible,
                  (unsigned long long)st.skipped, (unsigned long long)st.cache_hits,
                  (unsigned long long)st.cycles, (unsigned long long)st.cycles_noskip,
                  100.0 * (1.0 - double(st.cycles) / double(st.cycles_noskip)),
                  (unsigned long long)st.mults, (unsigned long long)st.mults_noskip,
                  (unsigned long long)st.alpha_mismatch_vs_full, (long long)st.max_alpha_err);
    }
    total.gaussians += st.gaussians; total.skip_eligible += st.skip_eligible;
    total.skipped += st.skipped; total.recomputed += st.recomputed;
    total.cache_hits += st.cache_hits; total.cycles += st.cycles;
    total.cycles_noskip += st.cycles_noskip; total.mults += st.mults;
    total.mults_noskip += st.mults_noskip; total.adds += st.adds;
    total.adds_noskip += st.adds_noskip;
    total.alpha_mismatch_vs_full += st.alpha_mismatch_vs_full;
    if (st.max_alpha_err > total.max_alpha_err) total.max_alpha_err = st.max_alpha_err;
  }
  return total;
}

}  // namespace

int main(int argc, char** argv) {
  Config c;
  const char* trace_dir = nullptr;
  bool sweep = false;
  for (int i = 1; i < argc; ++i) {
    auto next = [&]() { if (i + 1 >= argc) { std::fprintf(stderr, "missing value for %s\n", argv[i]); std::exit(2); } return std::atoi(argv[++i]); };
    if (!std::strcmp(argv[i], "--num-pe")) c.num_pe = next();
    else if (!std::strcmp(argv[i], "--q-frac")) c.q_frac = next();
    else if (!std::strcmp(argv[i], "--ways")) c.cache_ways = next();
    else if (!std::strcmp(argv[i], "--thresh")) c.motion_thresh = static_cast<uint32_t>(next());
    else if (!std::strcmp(argv[i], "--trace-dir") && i + 1 < argc) trace_dir = argv[++i];
    else if (!std::strcmp(argv[i], "--sweep")) sweep = true;
    else { std::fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
  }
  const std::vector<SceneGauss> scene = make_scene();
  int fails = 0;
  for (int f : {8, 12}) {
    Fx fx(f);
    ConicCheck check;
    for (int t = 0; t < kFrames; ++t)
      for (const auto& g : make_frame(scene, t, f)) check.check(fx, g, project(fx, g));
    check.report(f, stderr);
    fails += check.failures != 0;
  }
  const auto directed = directed_conic(8);
  for (int f : {8, 12}) {
    const auto edges = conic_edges(f);
    const auto zero = project(Fx(f), edges[0]);
    const auto singular = project(Fx(f), edges[1]);
    const auto tiny = project(Fx(f), edges[2]);
    const int64_t max = (int64_t{1} << (7+f))-1;
    if (zero.cov_a || zero.cov_b || zero.cov_c || tiny.cov_a || tiny.cov_b || tiny.cov_c ||
        singular.cov_a != max || singular.cov_c != max || singular.cov_b != -max) ++fails;
  }
  const auto alpha = lane_alpha(Fx(8), directed.pix_x0, directed.pix_y, project(Fx(8), directed));
  if (alpha != 233 || alpha == 35) ++fails;
  std::fprintf(stderr, "directed conic Q_FRAC=8 alpha=%lld expected=233 (not 35)\n", (long long)alpha);
  if (sweep) {
    std::printf("num_pe,cache_ways,q_frac,motion_thresh,gaussians,skipped,skip_rate,cycles,"
                "cycles_noskip,speedup,mults,mults_noskip,alpha_err_px,max_alpha_err\n");
    for (int pe : {1, 2, 4}) for (int ways : {1, 2, 4}) for (int f : {8, 12}) for (uint32_t th : {0u, 16u}) {
      Config k{pe, f, ways, 512, th};
      const Stats s = run(k, scene, nullptr, false);
      if (th == 0 && (s.skipped || s.alpha_mismatch_vs_full)) ++fails;
      std::printf("%d,%d,%d,%u,%llu,%llu,%.4f,%llu,%llu,%.4f,%llu,%llu,%llu,%lld\n", pe, ways, f, th,
                  (unsigned long long)s.gaussians, (unsigned long long)s.skipped,
                  double(s.skipped) / double(s.gaussians), (unsigned long long)s.cycles,
                  (unsigned long long)s.cycles_noskip, double(s.cycles_noskip) / double(s.cycles),
                  (unsigned long long)s.mults, (unsigned long long)s.mults_noskip,
                  (unsigned long long)s.alpha_mismatch_vs_full, (long long)s.max_alpha_err);
    }
  } else {
    std::printf("config: NUM_PE=%d Q_FRAC=%d CACHE_WAYS=%d CACHE_SETS=%d MOTION_THRESH=%u, "
                "%d Gaussians x %d frames (70%% static / 30%% dynamic)\n",
                c.num_pe, c.q_frac, c.cache_ways, c.cache_sets, c.motion_thresh, kGaussians, kFrames);
    const Stats s = run(c, scene, trace_dir, true);
    if (c.motion_thresh == 0 && (s.skipped || s.alpha_mismatch_vs_full)) ++fails;
    std::printf("total: skipped=%llu/%llu (%.1f%%) cycles=%llu no-skip=%llu speedup=%.3fx "
                "mults saved=%.1f%% adds saved=%.1f%%\n",
                (unsigned long long)s.skipped, (unsigned long long)s.gaussians,
                100.0 * double(s.skipped) / double(s.gaussians), (unsigned long long)s.cycles,
                (unsigned long long)s.cycles_noskip, double(s.cycles_noskip) / double(s.cycles),
                100.0 * (1.0 - double(s.mults) / double(s.mults_noskip)),
                100.0 * (1.0 - double(s.adds) / double(s.adds_noskip)));
  }
  if (fails) { std::fprintf(stderr, "FAIL: golden self-check\n"); return 1; }
  return 0;
}
