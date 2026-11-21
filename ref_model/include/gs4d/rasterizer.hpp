// Bit-true, cycle-accurate model of rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v
// plus a hazard-free sequential ("ideal") version of the same algorithm.
//
// RtlRasterizer mirrors every register, the top FSM, the negedge-read SRAMs and
// the 11-stage valid pipeline of the RTL, so it reproduces the RTL's
// pipeline behaviour exactly, including:
//   * stage 3 multiplies depth_sum1/2_s3 by dx_s2/dy_s2 (the dx/dy of the
//     Gaussian one stage behind, not the Gaussian's own),
//   * stage 8 reads current_pixel.transmittance before stage 9 has written the
//     previous Gaussian's update (1-cycle read-after-write gap),
//   * early_termination / T_testx15 are not cleared in INIT_PIXEL and keep
//     updating during FINALIZE,
//   * PROCESS ends one cycle after the Gaussian that drives T below 1e-4, so
//     whatever sits in stage 10 then still commits,
//   * num_of_gaussian steps to 1090 on a full traversal,
//   * the 9-bit colour is truncated to 8 bits (wraps above 255).
#pragma once

#include <cstdint>
#include <vector>

#include "gaussian.hpp"

namespace gs4d {

struct TileConfig {
  int width = 75;            // px counts 0..74
  int height = 34;           // py counts 0..33
  uint32_t px_offset = 975;  // `px_offset
  uint32_t py_offset = 34;   // `py_offset
  int max_gaussians = 1089;  // num_of_gaussian >= 1089 ends PROCESS
  int addr0_stop = 1099;     // all_gaussians_done = (addr0 == 1099)
  int num_pixels() const { return width * height; }
};

// Each flag reproduces one observed RTL behaviour (default: all on, i.e.
// bit-true to the current RTL). Turning one off models the fix; the last four
// off give the behaviour seen in the lab golden_ans (verif/golden/).
struct RtlQuirks {
  bool stale_dxdy_depth = true;  // stage 3 uses dx_s2/dy_s2 (next Gaussian's) for depth
  bool stale_trans = true;       // stage 8 misses the stage-9 transmittance write-back
  bool sticky_early = true;      // early_termination not cleared in INIT_PIXEL
  // on: stop on the Gaussian that drives T below 1e-4 (plus the stage-10
  // entry in flight); off: blend one more alpha >= 1/255 Gaussian, then stop.
  bool stop_on_crossing = true;
  bool n_overshoot = true;       // on: N reaches 1090 on a full traversal; off: N <= 1089
  bool rgb_wrap = true;          // on: 9-bit colour & 0xFF; off: saturate at 255
  // on: depth accumulates every committed Gaussian; off: depth skips the
  // Gaussian that drives T below 1e-4 and every one after it.
  bool depth_stop_tail = true;
};

// IEEE-754 binary32 bit patterns of the RTL constants.
namespace k {
constexpr uint32_t kOne = 0x3F800000;          // 1.0
constexpr uint32_t kMinusHalf = 0xBF000000;    // -0.5
constexpr uint32_t kAlphaMin = 0x3B808081;     // 1/255
constexpr uint32_t kTMin = 0x38D1B717;         // 1e-4
constexpr uint32_t kBgDepth = 0x41700000;      // 15.0
constexpr uint32_t kColorScale = 0x437F0000;   // 255.0
}  // namespace k

template <class V>
struct PixelResult {
  uint32_t rgb = 0;  // {R[7:0], G[7:0], B[7:0]}
  V depth{};         // D = current_pixel.depth + T_testx15
  V trans{};         // T
  uint32_t n = 0;    // N (11 bits)
};

template <class V>
struct RenderResult {
  std::vector<PixelResult<V>> pixels;
  uint64_t cycles = 0;          // cycles until Finish (RTL model only)
  uint64_t oob_reads = 0;       // valid stage-1 reads past the SRAM contents
};

template <class P>
class RtlRasterizer {
 public:
  using V = typename P::V;
  using G = Splat<V>;

  RtlRasterizer(const P& p, TileConfig tc = TileConfig{}, RtlQuirks q = RtlQuirks{}) : p_(p), tc_(tc), q_(q) {}

  // sram0: sorted Gaussian indices (SRAM_1089x11), sram1: splats (SRAM_1089x384).
  RenderResult<V> run(const std::vector<G>& sram1, const std::vector<uint32_t>& sram0,
                      uint64_t max_cycles = 100000000ull) const {
    enum State { IDLE, INIT_PIXEL, PROCESS, FINALIZE, WRITE_RESULT, COMPLETE };
    const P& p = p_;
    auto Z = [&](Sig s) { return p.zero(s); };
    const G zg{Z(Sig::kCoord), Z(Sig::kCoord), Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kDepth),
               Z(Sig::kConic), Z(Sig::kConic), Z(Sig::kConic), Z(Sig::kAlpha), Z(Sig::kDGrad), Z(Sig::kDGrad)};
    const V one_t = p.cst(k::kOne, Sig::kTrans);
    const V one_a = p.cst(k::kOne, Sig::kAlpha);
    const V m_half = p.cst(k::kMinusHalf, Sig::kPower);
    const V alpha_min = p.cst(k::kAlphaMin, Sig::kAlpha);
    const V t_min = p.cst(k::kTMin, Sig::kTrans);
    const V bg_depth = p.cst(k::kBgDepth, Sig::kDepth);
    const V cscale = p.cst(k::kColorScale, Sig::kPixel);
    const uint32_t max_n = static_cast<uint32_t>(tc_.max_gaussians);

    struct Pix { V red, green, blue, depth, trans; uint32_t n; };
    struct Regs {
      State state = IDLE;
      uint32_t addr0 = 0, addr2 = 0, px = 0, py = 0;
      V fpx0, fpy0, fpx1, fpy1;
      bool sv[11] = {};
      G g2, g3, g4, g5, g6, g7, g8, g9;
      Pix cur;
      V dx2, dy2, dx3, dy3, t1_2, t2_2, t3_2, t4_2;
      V dxsq3, dysq3, dxdy3, ds1_3, ds2_3;
      V bt1_4, bt2_4, bt3_4, dxd4, dyd4;
      V b12_5, dsum5, bt3_5;
      V beta6, di6;
      V at7; bool bp7 = false; V di7;
      V ai8; V di8; bool bp8 = false;
      V oma9, tacc9, ttest9; bool small9 = false; V di9; bool bp9 = false;
      V ttest10, r10, g10, b10, d10; bool bp10 = false, small10 = false;
      bool early = false; V tx15;
      bool crossed = false, done = false;  // model-only state for the golden stop rule
      uint32_t dout0 = 0; G dout1;
    };

    Regs r;
    r.fpx0 = r.fpy0 = r.fpx1 = r.fpy1 = Z(Sig::kCoord);
    r.g2 = r.g3 = r.g4 = r.g5 = r.g6 = r.g7 = r.g8 = r.g9 = zg;
    r.cur = Pix{Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kDepth), one_t, 0};
    r.dx2 = r.dy2 = r.dx3 = r.dy3 = Z(Sig::kCoord);
    r.t1_2 = r.t2_2 = r.t3_2 = r.t4_2 = Z(Sig::kDGradTerm);
    r.dxsq3 = r.dysq3 = r.dxdy3 = Z(Sig::kDeltaSq);
    r.ds1_3 = r.ds2_3 = Z(Sig::kDGradTerm);
    r.bt1_4 = r.bt2_4 = r.bt3_4 = Z(Sig::kPower);
    r.dxd4 = r.dyd4 = Z(Sig::kDepth);
    r.b12_5 = r.bt3_5 = Z(Sig::kPower);
    r.dsum5 = Z(Sig::kDepth);
    r.beta6 = Z(Sig::kPower);
    r.di6 = r.di7 = r.di8 = r.di9 = Z(Sig::kDepth);
    r.at7 = r.ai8 = r.oma9 = Z(Sig::kAlpha);
    r.tacc9 = Z(Sig::kTrans);
    r.ttest9 = r.ttest10 = one_t;
    r.r10 = r.g10 = r.b10 = Z(Sig::kColor);
    r.d10 = r.tx15 = Z(Sig::kDepth);
    r.dout1 = zg;

    RenderResult<V> res;
    res.pixels.resize(tc_.num_pixels());
    bool dout1_oob = false;

    for (uint64_t cyc = 0; cyc < max_cycles; ++cyc) {
      // ---------------- negedge: SRAM reads / writes ----------------
      {
        const uint32_t a1 = r.dout0;  // addr1 = dout0 (old value, nonblocking)
        dout1_oob = a1 >= sram1.size();
        r.dout1 = dout1_oob ? zg : sram1[a1];
        r.dout0 = (r.addr0 < sram0.size()) ? (sram0[r.addr0] & 0x7FF) : 0;
        if (r.state == WRITE_RESULT && r.addr2 < static_cast<uint32_t>(tc_.num_pixels())) {
          PixelResult<V>& o = res.pixels[r.addr2];
          o.rgb = (to_u8(r.cur.red, cscale) << 16) | (to_u8(r.cur.green, cscale) << 8) | to_u8(r.cur.blue, cscale);
          o.depth = p.add(r.cur.depth, r.tx15, Sig::kDepth);
          o.trans = r.cur.trans;
          o.n = r.cur.n;
        }
      }
      if (r.state == COMPLETE) { res.cycles = cyc; break; }

      // ---------------- posedge: combinational next-state ----------------
      Regs n = r;
      const V fpx = p.from_uint((r.px + tc_.px_offset) & 0x7FF, Sig::kCoord);
      const V fpy = p.from_uint((r.py + tc_.py_offset) & 0x7FF, Sig::kCoord);
      if (r.state == INIT_PIXEL) { n.fpx0 = fpx; n.fpy0 = fpy; }
      else { n.fpx1 = r.fpx0; n.fpy1 = r.fpy0; }

      const bool all_pixels_done = r.addr2 == static_cast<uint32_t>(tc_.num_pixels());
      const bool all_gauss_done = r.addr0 == static_cast<uint32_t>(tc_.addr0_stop);
      switch (r.state) {
        case IDLE: n.state = INIT_PIXEL; break;
        case INIT_PIXEL: n.state = PROCESS; break;
        case PROCESS:
          n.state = (r.cur.n >= max_n || (q_.stop_on_crossing ? r.early : r.done) || all_gauss_done) ? FINALIZE : PROCESS;
          break;
        case FINALIZE: n.state = WRITE_RESULT; break;
        case WRITE_RESULT: n.state = all_pixels_done ? COMPLETE : INIT_PIXEL; break;
        case COMPLETE: break;
      }

      if (r.state == INIT_PIXEL) n.addr0 = 0;
      else {
        if (r.state == PROCESS && r.sv[0]) n.addr0 = (r.addr0 + 1) & 0x7FF;
        if (r.state == WRITE_RESULT) n.addr2 = (r.addr2 + 1) & 0xFFF;
      }
      if (r.state == WRITE_RESULT) {
        if (r.px < static_cast<uint32_t>(tc_.width - 1)) n.px = r.px + 1;
        else { n.px = 0; if (r.py < static_cast<uint32_t>(tc_.height - 1)) n.py = r.py + 1; }
      }

      // Stage-10 commit gating. With the default quirks every stage-10 entry
      // commits, as in the RTL.
      const bool valid10 = !(r.small10 || r.bp10);
      const bool live10 = r.sv[10] && (q_.n_overshoot || r.cur.n < max_n) && (q_.stop_on_crossing || !r.done);
      const bool cross10 = live10 && valid10 && p.lt(r.ttest10, t_min);
      const bool done_now = r.done || (live10 && valid10 && r.crossed);
      const uint32_t seq9 = r.cur.n + (live10 ? 1 : 0);  // 0-based index of the stage-9 entry
      const bool live9 = (q_.n_overshoot || seq9 < max_n) && (q_.stop_on_crossing || !done_now);

      // current_pixel
      if (r.state == INIT_PIXEL) {
        n.cur = Pix{Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kColor), Z(Sig::kDepth), one_t, 0};
        n.crossed = n.done = false;
      } else if (r.state == PROCESS) {
        if (r.sv[9] && !(r.small9 || r.bp9) && live9) n.cur.trans = r.ttest9;
        if (live10) {
          if (valid10) {
            n.cur.red = p.add(r.cur.red, r.r10, Sig::kColor);
            n.cur.green = p.add(r.cur.green, r.g10, Sig::kColor);
            n.cur.blue = p.add(r.cur.blue, r.b10, Sig::kColor);
            if (q_.depth_stop_tail || !(r.crossed || cross10)) n.cur.depth = p.add(r.cur.depth, r.d10, Sig::kDepth);
          }
          n.cur.n = (r.cur.n + 1) & 0x7FF;
        }
        n.crossed = r.crossed || cross10;
        n.done = done_now;
      }

      // valid pipeline
      const bool run = r.state == INIT_PIXEL || r.state == PROCESS;
      n.sv[0] = run;
      for (int i = 1; i < 11; ++i) n.sv[i] = run ? r.sv[i - 1] : false;

      // stage 1 (consumes dout1)
      if (r.sv[1]) {
        const G& d = r.dout1;
        if (dout1_oob) ++res.oob_reads;
        n.dx2 = p.sub(d.ux, r.fpx1, Sig::kCoord);
        n.dy2 = p.sub(d.uy, r.fpy1, Sig::kCoord);
        n.t1_2 = p.mul(d.coni, d.conx, Sig::kDGradTerm);
        n.t2_2 = p.mul(d.conj, d.cony, Sig::kDGradTerm);
        n.t3_2 = p.mul(d.coni, d.cony, Sig::kDGradTerm);
        n.t4_2 = p.mul(d.conj, d.conz, Sig::kDGradTerm);
        n.g2 = d;
      }
      if (r.sv[2]) {
        n.dxsq3 = p.mul(r.dx2, r.dx2, Sig::kDeltaSq);
        n.dysq3 = p.mul(r.dy2, r.dy2, Sig::kDeltaSq);
        n.dxdy3 = p.mul(r.dx2, r.dy2, Sig::kDeltaSq);
        n.ds1_3 = p.add(r.t1_2, r.t2_2, Sig::kDGradTerm);
        n.ds2_3 = p.add(r.t3_2, r.t4_2, Sig::kDGradTerm);
        n.dx3 = r.dx2;
        n.dy3 = r.dy2;
        n.g3 = r.g2;
      }
      if (r.sv[3]) {
        n.bt1_4 = p.mul(r.g3.conx, r.dxsq3, Sig::kPower);
        n.bt2_4 = p.mul(r.g3.conz, r.dysq3, Sig::kPower);
        n.bt3_4 = p.mul(r.g3.cony, r.dxdy3, Sig::kPower);
        n.dxd4 = p.mul(q_.stale_dxdy_depth ? r.dx2 : r.dx3, r.ds1_3, Sig::kDepth);
        n.dyd4 = p.mul(q_.stale_dxdy_depth ? r.dy2 : r.dy3, r.ds2_3, Sig::kDepth);
        n.g4 = r.g3;
      }
      if (r.sv[4]) {
        n.b12_5 = p.mul(p.add(r.bt1_4, r.bt2_4, Sig::kPower), m_half, Sig::kPower);
        n.dsum5 = p.add(r.dxd4, r.dyd4, Sig::kDepth);
        n.bt3_5 = r.bt3_4;
        n.g5 = r.g4;
      }
      if (r.sv[5]) {
        n.beta6 = p.sub(r.b12_5, r.bt3_5, Sig::kPower);
        n.di6 = p.sub(r.g5.depth, r.dsum5, Sig::kDepth);
        n.g6 = r.g5;
      }
      if (r.sv[6]) {
        n.at7 = p.exp(r.beta6, Sig::kAlpha);
        n.bp7 = !p.sign(r.beta6);
        n.di7 = r.di6;
        n.g7 = r.g6;
      }
      if (r.sv[7]) {
        n.ai8 = p.mul(r.g7.conw, r.at7, Sig::kAlpha);
        n.di8 = r.di7;
        n.bp8 = r.bp7;
        n.g8 = r.g7;
      }
      if (r.sv[8]) {
        const V t_now = (!q_.stale_trans && r.sv[9] && !(r.small9 || r.bp9)) ? r.ttest9 : r.cur.trans;
        n.oma9 = p.sub(one_a, r.ai8, Sig::kAlpha);
        n.tacc9 = p.mul(r.ai8, t_now, Sig::kTrans);
        n.ttest9 = p.mul(n.oma9, t_now, Sig::kTrans);
        n.small9 = p.lt(r.ai8, alpha_min);
        n.di9 = r.di8;
        n.bp9 = r.bp8;
        n.g9 = r.g8;
      }
      if (r.sv[9]) {
        n.ttest10 = r.ttest9;
        n.r10 = p.mul(r.tacc9, r.g9.red, Sig::kColor);
        n.g10 = p.mul(r.tacc9, r.g9.green, Sig::kColor);
        n.b10 = p.mul(r.tacc9, r.g9.blue, Sig::kColor);
        n.d10 = p.mul(r.tacc9, r.di9, Sig::kDepth);
        n.bp10 = r.bp9;
        n.small10 = r.small9;
      }
      if (r.state == INIT_PIXEL && !q_.sticky_early) n.early = false;
      if (r.sv[10]) n.early = (r.small10 || r.bp10) ? false : p.lt(r.ttest10, t_min);
      if (live10) n.tx15 = p.mul(r.ttest10, bg_depth, Sig::kDepth);
      r = n;
    }
    return res;
  }

 private:
  uint32_t to_u8(const V& c, const V& cscale) const {
    const uint32_t u = p_.to_uint(p_.mul(c, cscale, Sig::kPixel), 9);
    return q_.rgb_wrap ? (u & 0xFF) : (u > 255 ? 255 : u);
  }

  P p_;
  TileConfig tc_;
  RtlQuirks q_;
};

// Hazard-free sequential reference of the same per-pixel algorithm: every
// Gaussian uses its own dx/dy and the up-to-date transmittance. Used to
// quantify how far the pipelined RTL behaviour deviates from the algorithm.
template <class P>
RenderResult<typename P::V> render_ideal(const P& p, const std::vector<Splat<typename P::V>>& sram1,
                                         const std::vector<uint32_t>& sram0, TileConfig tc = TileConfig{}) {
  using V = typename P::V;
  RenderResult<V> res;
  res.pixels.resize(tc.num_pixels());
  const V one_t = p.cst(k::kOne, Sig::kTrans), one_a = p.cst(k::kOne, Sig::kAlpha);
  const V m_half = p.cst(k::kMinusHalf, Sig::kPower), alpha_min = p.cst(k::kAlphaMin, Sig::kAlpha);
  const V t_min = p.cst(k::kTMin, Sig::kTrans), bg_depth = p.cst(k::kBgDepth, Sig::kDepth);
  const V cscale = p.cst(k::kColorScale, Sig::kPixel);
  for (int py = 0; py < tc.height; ++py) {
    for (int px = 0; px < tc.width; ++px) {
      const V fpx = p.from_uint((px + tc.px_offset) & 0x7FF, Sig::kCoord);
      const V fpy = p.from_uint((py + tc.py_offset) & 0x7FF, Sig::kCoord);
      V red = p.zero(Sig::kColor), green = red, blue = red, depth = p.zero(Sig::kDepth), T = one_t;
      uint32_t n = 0;
      for (int i = 0; i < tc.max_gaussians && i < static_cast<int>(sram0.size()); ++i) {
        const uint32_t idx = sram0[i] & 0x7FF;
        if (idx >= sram1.size()) { ++res.oob_reads; ++n; continue; }
        const auto& d = sram1[idx];
        ++n;
        const V dx = p.sub(d.ux, fpx, Sig::kCoord), dy = p.sub(d.uy, fpy, Sig::kCoord);
        const V ds1 = p.add(p.mul(d.coni, d.conx, Sig::kDGradTerm), p.mul(d.conj, d.cony, Sig::kDGradTerm), Sig::kDGradTerm);
        const V ds2 = p.add(p.mul(d.coni, d.cony, Sig::kDGradTerm), p.mul(d.conj, d.conz, Sig::kDGradTerm), Sig::kDGradTerm);
        const V bt1 = p.mul(d.conx, p.mul(dx, dx, Sig::kDeltaSq), Sig::kPower);
        const V bt2 = p.mul(d.conz, p.mul(dy, dy, Sig::kDeltaSq), Sig::kPower);
        const V bt3 = p.mul(d.cony, p.mul(dx, dy, Sig::kDeltaSq), Sig::kPower);
        const V beta = p.sub(p.mul(p.add(bt1, bt2, Sig::kPower), m_half, Sig::kPower), bt3, Sig::kPower);
        const V dsum = p.add(p.mul(dx, ds1, Sig::kDepth), p.mul(dy, ds2, Sig::kDepth), Sig::kDepth);
        const V di = p.sub(d.depth, dsum, Sig::kDepth);
        const V alpha = p.mul(d.conw, p.exp(beta, Sig::kAlpha), Sig::kAlpha);
        if (!p.sign(beta) || p.lt(alpha, alpha_min)) continue;
        const V tacc = p.mul(alpha, T, Sig::kTrans);
        const V ttest = p.mul(p.sub(one_a, alpha, Sig::kAlpha), T, Sig::kTrans);
        red = p.add(red, p.mul(tacc, d.red, Sig::kColor), Sig::kColor);
        green = p.add(green, p.mul(tacc, d.green, Sig::kColor), Sig::kColor);
        blue = p.add(blue, p.mul(tacc, d.blue, Sig::kColor), Sig::kColor);
        depth = p.add(depth, p.mul(tacc, di, Sig::kDepth), Sig::kDepth);
        T = ttest;
        if (p.lt(T, t_min)) break;
      }
      auto& o = res.pixels[py * tc.width + px];
      o.rgb = ((p.to_uint(p.mul(red, cscale, Sig::kPixel), 9) & 0xFF) << 16) |
              ((p.to_uint(p.mul(green, cscale, Sig::kPixel), 9) & 0xFF) << 8) |
              (p.to_uint(p.mul(blue, cscale, Sig::kPixel), 9) & 0xFF);
      o.depth = p.add(depth, p.mul(T, bg_depth, Sig::kDepth), Sig::kDepth);
      o.trans = T;
      o.n = n;
    }
  }
  return res;
}

}  // namespace gs4d
