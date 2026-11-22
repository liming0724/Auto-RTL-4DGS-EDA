// Phase 4: cycle-accurate SSIM_v2 scoring of 64 x 64 frames (ssim_sim_top harness).
//
// SSIM_v2 scores one 16 x 17 serpentine FP16 RGB image (272 words, 9-bit SRAM address)
// per run, so each frame is covered by the 4 x 4 tiles of gs4d_frame::kTileRows; each
// tile is written into the IP's input SRAMs and scored with the protocol of
// sim_main_ssim.cpp (reset ticks 0..4, writes from tick 10, 272 scores from tick 38).
// The frame score is the mean over the 272 * 16 per-pixel scores and the 3 channels.
// Spot checks: identical frames must score 1.0 everywhere, and every tile is compared
// with the double-precision software model of the same formula (ssim_tile_sw).
// Usage: Vssim_sim_top <dir> <frames>  (reads <dir>/gt_f<k>.hex and <dir>/rtl_f<k>.hex)
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "Vssim_sim_top.h"
#include "gs4d_frame.hpp"
#include "verilated.h"

namespace {
using gs4d_frame::Frame;
constexpr int kW = gs4d_frame::kTileW, kH = gs4d_frame::kTileH, kTilePx = kW * kH;
constexpr int kFrameCycles = 400, kFirstScore = 38;

Frame load(const std::string& path) {
  std::ifstream in(path);
  Frame f(gs4d_frame::kPixels * 3);
  for (int p = 0; p < gs4d_frame::kPixels; ++p) {
    uint64_t w;
    if (!(in >> std::hex >> w)) throw std::runtime_error("bad or missing word in " + path);
    for (int c = 0; c < 3; ++c) f[p * 3 + c] = gs4d_frame::from_half(static_cast<uint16_t>(w >> (32 - 16 * c)));
  }
  return f;
}

int serpentine(int row, int offset) { return row * kW + (row % 2 ? kW - 1 - offset : offset); }

// Returns the mean of the 272 x 3 hardware scores of one tile.
double score_tile(Vssim_sim_top& dut, const Frame& img, const Frame& gt, int tx, int ty, uint64_t& cycles) {
  auto word = [&](const Frame& f, int addr) {
    const int p = (ty + addr / kW) * gs4d_frame::kFrameSize + tx + addr % kW;
    return gs4d_frame::pack_rgb(f, p);
  };
  double sum = 0;
  int scores = 0;
  for (int tick = 0; tick < kFrameCycles; ++tick) {
    const int index = tick - 10;
    dut.clk = 0;
    dut.rst_n = tick >= 5;
    dut.write = index >= 0 && index < kTilePx;
    dut.in_valid = dut.write && index != 0;
    const int addr = dut.write ? serpentine(index / kW, index % kW) : 0;
    dut.write_addr = addr;
    dut.gt = word(gt, addr);
    dut.img = word(img, addr);
    dut.shift_valid = 0;
    dut.pixel = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
    ++cycles;
    const int si = tick - kFirstScore;
    if ((si >= 0 && si < kTilePx) != bool(dut.out_valid)) throw std::runtime_error("SSIM out_valid timing");
    if (dut.out_valid) {
      for (int c = 0; c < 3; ++c) sum += gs4d_frame::from_half(static_cast<uint16_t>(dut.score >> (32 - 16 * c)));
      ++scores;
    }
  }
  if (scores != kTilePx) throw std::runtime_error("SSIM score count");
  return sum / (3.0 * kTilePx);
}

double score_frame(Vssim_sim_top& dut, const Frame& img, const Frame& gt, uint64_t& cycles,
                   double* max_diff, double* sw_mean) {
  double hw = 0, sw = 0;
  int tiles = 0;
  for (int ty : gs4d_frame::kTileRows)
    for (int tx = 0; tx < gs4d_frame::kFrameSize; tx += kW) {
      const double h = score_tile(dut, img, gt, tx, ty, cycles);
      const double s = gs4d_frame::ssim_tile_sw(img, gt, tx, ty);
      if (max_diff) *max_diff = std::max(*max_diff, std::fabs(h - s));
      hw += h; sw += s; ++tiles;
    }
  if (sw_mean) *sw_mean = sw / tiles;
  return hw / tiles;
}
}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  if (argc != 3) {
    std::fprintf(stderr, "usage: Vssim_sim_top <dir> <frames>\n");
    return 2;
  }
  try {
    const std::string dir = argv[1];
    const int frames = std::atoi(argv[2]);
    Vssim_sim_top dut;
    uint64_t cycles = 0;
    double hw = 0, sw = 0, max_diff = 0, identical_min = 1;
    for (int k = 0; k < frames; ++k) {
      const Frame gt = load(dir + "/gt_f" + std::to_string(k) + ".hex");
      const Frame img = load(dir + "/rtl_f" + std::to_string(k) + ".hex");
      double s = 0;
      const double h = score_frame(dut, img, gt, cycles, &max_diff, &s);
      std::printf("frame_%d: hw_ssim=%.5f sw_ssim=%.5f\n", k, h, s);
      hw += h; sw += s;
      if (k == 0) identical_min = score_frame(dut, gt, gt, cycles, nullptr, nullptr);
    }
    hw /= frames; sw /= frames;
    const bool ok = identical_min > 0.999 && max_diff < 0.02;
    std::printf("ssim_frame: %d frames, %llu SSIM_v2 cycles, hw_ssim=%.5f sw_ssim=%.5f max |hw - sw| per tile=%.5f "
                "identical-frame hw_ssim=%.5f\n", frames, (unsigned long long)cycles, hw, sw, max_diff, identical_min);
    std::printf("RESULT {\"hw_ssim\": %.6f, \"sw_ssim\": %.6f, \"max_tile_diff\": %.6f, \"identical_hw_ssim\": %.6f, "
                "\"ssim_cycles\": %llu, \"pass\": %s}\n", hw, sw, max_diff, identical_min,
                (unsigned long long)cycles, ok ? "true" : "false");
    std::puts(ok ? "PASS" : "FAIL");
    dut.final();
    return ok ? 0 : 1;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "%s\n", e.what());
    return 1;
  }
}
