// gs4d_render: run the bit-true float32 model on existing RTL memory images.
//
//   gs4d_render --ram ram_file.txt --gs gs_value.txt --out DIR
//
// Writes DIR/<config>/{N,T,rendered,depth}_img.txt in the TB.sv golden_ans
// formats for every RtlQuirks combination and for the hazard-free sequential
// algorithm:
//   q<D><T><E>  RtlRasterizer with stale_dxdy_depth=D, stale_trans=T,
//               sticky_early=E, other flags on (q111 = bit-true to the current RTL)
//   q000_<f>    q000 with one more flag off: s stop_on_crossing, n n_overshoot,
//               r rgb_wrap, d depth_stop_tail
//   q000_snrd   every RtlQuirks flag off (the golden_ans behaviour)
//   ideal       render_ideal (no pipeline hazards)
#include <cstring>
#include <sys/stat.h>

#include "gs4d/scene.hpp"

using namespace gs4d;

int main(int argc, char** argv) {
  std::string ram, gs, out;
  for (int i = 1; i < argc; ++i) {
    auto next = [&]() { return i + 1 < argc ? argv[++i] : ""; };
    if (!std::strcmp(argv[i], "--ram")) ram = next();
    else if (!std::strcmp(argv[i], "--gs")) gs = next();
    else if (!std::strcmp(argv[i], "--out")) out = next();
    else { std::fprintf(stderr, "usage: %s --ram FILE --gs FILE --out DIR\n", argv[0]); return 2; }
  }
  if (ram.empty() || gs.empty() || out.empty()) {
    std::fprintf(stderr, "usage: %s --ram FILE --gs FILE --out DIR\n", argv[0]);
    return 2;
  }
  std::vector<Splat<float>> s1;
  std::vector<uint32_t> s0;
  if (!read_ram_file(ram, s1)) { std::fprintf(stderr, "bad or missing %s\n", ram.c_str()); return 2; }
  if (!read_gs_value(gs, s0)) { std::fprintf(stderr, "bad or missing %s\n", gs.c_str()); return 2; }
  std::printf("loaded %zu splats, %zu sorted indices\n", s1.size(), s0.size());
  ::mkdir(out.c_str(), 0755);
  const TileConfig tc;
  F32Policy pf;
  auto emit = [&](const std::string& tag, const RenderResult<float>& r) {
    const std::string d = out + "/" + tag;
    ::mkdir(d.c_str(), 0755);
    if (!write_golden(pf, d, r, 8)) { std::fprintf(stderr, "write failed: %s\n", d.c_str()); return false; }
    std::printf("%-9s cycles %llu  oob reads %llu\n", tag.c_str(), (unsigned long long)r.cycles,
                (unsigned long long)r.oob_reads);
    return true;
  };
  for (int m = 7; m >= 0; --m) {
    const RtlQuirks q{(m & 4) != 0, (m & 2) != 0, (m & 1) != 0};
    char tag[8];
    std::snprintf(tag, sizeof tag, "q%d%d%d", q.stale_dxdy_depth, q.stale_trans, q.sticky_early);
    if (!emit(tag, RtlRasterizer<F32Policy>(pf, tc, q).run(s1, s0))) return 1;
  }
  for (const char* f : {"s", "n", "r", "d", "snrd"}) {
    RtlQuirks q{false, false, false};
    const std::string fs(f);
    q.stop_on_crossing = fs.find('s') == std::string::npos;
    q.n_overshoot = fs.find('n') == std::string::npos;
    q.rgb_wrap = fs.find('r') == std::string::npos;
    q.depth_stop_tail = fs.find('d') == std::string::npos;
    if (!emit("q000_" + fs, RtlRasterizer<F32Policy>(pf, tc, q).run(s1, s0))) return 1;
  }
  return emit("ideal", render_ideal(pf, s1, s0, tc)) ? 0 : 1;
}
