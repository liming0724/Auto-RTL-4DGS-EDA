// Gaussian records and the spatiotemporal (4D) deformation kernel.
#pragma once

#include <vector>

#include "arith.hpp"

namespace gs4d {

// 2D splat record, field order == SRAM_1089x384 word, MSB first:
// {ux, uy, red, green, blue, depth, conx, cony, conz, conw(opacity), coni, conj}
template <class V>
struct Splat {
  V ux, uy, red, green, blue, depth, conx, cony, conz, conw, coni, conj;
};

// 4DGS primitive in screen space. The spatiotemporal model is the slice of a
// 4D Gaussian at time t (conditional mean + temporal marginal), extended with
// a constant-acceleration term:
//   dt      = t - t0
//   mu(t)   = mu + v*dt + ha*dt^2          (ha = a/2)
//   depth(t)= depth + vd*dt
//   o(t)    = o * exp(-lam_h * dt^2)       (lam_h = 1 / (2 sigma_t^2))
// The conic and depth-gradient coefficients are time-invariant (conditional
// covariance of a 4D Gaussian does not depend on t).
struct Gaussian4D {
  Splat<double> s;  // canonical parameters (at t = t0)
  double t0 = 0, vx = 0, vy = 0, hax = 0, hay = 0, vd = 0, lam_h = 0;
};

template <class P>
Splat<typename P::V> quantize_splat(const P& p, const Splat<double>& g) {
  return {p.in(g.ux, Sig::kCoord),   p.in(g.uy, Sig::kCoord),   p.in(g.red, Sig::kColor),
          p.in(g.green, Sig::kColor), p.in(g.blue, Sig::kColor), p.in(g.depth, Sig::kDepth),
          p.in(g.conx, Sig::kConic),  p.in(g.cony, Sig::kConic), p.in(g.conz, Sig::kConic),
          p.in(g.conw, Sig::kAlpha),  p.in(g.coni, Sig::kDGrad), p.in(g.conj, Sig::kDGrad)};
}

// Bit-true deformation kernel: evaluates one 4D Gaussian at frame time t.
// The operation order below is the contract for the future deformation RTL.
template <class P>
Splat<typename P::V> deform(const P& p, const Gaussian4D& g, double t) {
  using V = typename P::V;
  Splat<V> o = quantize_splat(p, g.s);
  const V tq = p.in(t, Sig::kTime);
  const V t0 = p.in(g.t0, Sig::kTime);
  const V vx = p.in(g.vx, Sig::kVel), vy = p.in(g.vy, Sig::kVel);
  const V hax = p.in(g.hax, Sig::kVel), hay = p.in(g.hay, Sig::kVel);
  const V vd = p.in(g.vd, Sig::kVel), lam = p.in(g.lam_h, Sig::kVel);

  const V dt = p.sub(tq, t0, Sig::kTime);
  const V dt2 = p.mul(dt, dt, Sig::kTimeSq);
  o.ux = p.add(o.ux, p.add(p.mul(vx, dt, Sig::kCoord), p.mul(hax, dt2, Sig::kCoord), Sig::kCoord), Sig::kCoord);
  o.uy = p.add(o.uy, p.add(p.mul(vy, dt, Sig::kCoord), p.mul(hay, dt2, Sig::kCoord), Sig::kCoord), Sig::kCoord);
  o.depth = p.add(o.depth, p.mul(vd, dt, Sig::kDepth), Sig::kDepth);
  const V beta_t = p.sub(p.zero(Sig::kPower), p.mul(lam, dt2, Sig::kPower), Sig::kPower);
  o.conw = p.mul(o.conw, p.exp(beta_t, Sig::kAlpha), Sig::kAlpha);
  return o;
}

template <class P>
std::vector<Splat<typename P::V>> deform_frame(const P& p, const std::vector<Gaussian4D>& gs, double t) {
  std::vector<Splat<typename P::V>> out;
  out.reserve(gs.size());
  for (const auto& g : gs) out.push_back(deform(p, g, t));
  return out;
}

}  // namespace gs4d
