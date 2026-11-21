// Verilator harness: runs rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v
// (with the sim-only DW substitutes) on a scene directory written by
// `gs4d_ref --out DIR` and compares every pixel against DIR/golden_ans,
// which is produced by the C++ RtlRasterizer<F32Policy> model.
// Run from inside DIR (the SRAMs $readmem ram_file.txt / gs_value.txt).
// The RTL outputs are also written to DIR/rtl_out/ in the same formats.
#include <cstdio>
#include <sys/stat.h>
#include <vector>

#include "VGS_RASTERIZER_pipeline.h"
#include "verilated.h"

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  const int npix = 2550;
  std::vector<unsigned> gn(npix), gt(npix), gd(npix), gr(npix), gg(npix), gb(npix);
  FILE* fn = std::fopen("golden_ans/N_img.txt", "r");
  FILE* ft = std::fopen("golden_ans/T_img.txt", "r");
  FILE* fr = std::fopen("golden_ans/rendered_img.txt", "r");
  FILE* fd = std::fopen("golden_ans/depth_img.txt", "r");
  if (!fn || !ft || !fr || !fd) { std::printf("cannot open golden_ans/*.txt\n"); return 2; }
  for (int i = 0; i < npix; ++i) {
    if (std::fscanf(fn, "%u", &gn[i]) != 1 || std::fscanf(ft, "%x", &gt[i]) != 1 ||
        std::fscanf(fr, "%u %u %u", &gr[i], &gg[i], &gb[i]) != 3 || std::fscanf(fd, "%x", &gd[i]) != 1) {
      std::printf("short golden file at %d\n", i);
      return 2;
    }
  }
  VGS_RASTERIZER_pipeline dut;
  dut.clk = 0;
  dut.rst_n = 0;
  dut.eval();
  for (int i = 0; i < 4; ++i) { dut.clk = 1; dut.eval(); dut.clk = 0; dut.eval(); }
  dut.rst_n = 1;
  dut.eval();
  std::vector<unsigned> on(npix), ot(npix), od(npix), orgb(npix);
  long cycles = 0, mism = 0;
  int idx = 0;
  int mN = 0, mT = 0, mD = 0, mC = 0;
  while (!dut.Finish && cycles < 50000000L) {
    dut.clk = 1; dut.eval();
    ++cycles;
    if (dut.en && idx < npix) {
      const unsigned r = (dut.RGB >> 16) & 0xFF, g = (dut.RGB >> 8) & 0xFF, b = dut.RGB & 0xFF;
      on[idx] = dut.N; ot[idx] = dut.T; od[idx] = dut.D; orgb[idx] = dut.RGB;
      const bool bad_n = dut.N != gn[idx], bad_t = dut.T != gt[idx], bad_d = dut.D != gd[idx];
      const bool bad_c = r != gr[idx] || g != gg[idx] || b != gb[idx];
      mN += bad_n; mT += bad_t; mD += bad_d; mC += bad_c;
      if (bad_n || bad_t || bad_d || bad_c) {
        if (mism < 8)
          std::printf("pixel %4d: rtl N=%u T=%08x D=%08x RGB=%u,%u,%u | model N=%u T=%08x D=%08x RGB=%u,%u,%u\n", idx,
                      (unsigned)dut.N, (unsigned)dut.T, (unsigned)dut.D, r, g, b, gn[idx], gt[idx], gd[idx], gr[idx],
                      gg[idx], gb[idx]);
        ++mism;
      }
      ++idx;
    }
    dut.clk = 0; dut.eval();
  }
  ::mkdir("rtl_out", 0755);
  FILE* wn = std::fopen("rtl_out/N_img.txt", "w");
  FILE* wt = std::fopen("rtl_out/T_img.txt", "w");
  FILE* wr = std::fopen("rtl_out/rendered_img.txt", "w");
  FILE* wd = std::fopen("rtl_out/depth_img.txt", "w");
  bool dumped = wn && wt && wr && wd;
  for (int i = 0; dumped && i < idx; ++i) {
    std::fprintf(wn, "%u\n", on[i]);
    std::fprintf(wt, "%08x\n", ot[i]);
    std::fprintf(wr, "%u %u %u\n", (orgb[i] >> 16) & 0xFF, (orgb[i] >> 8) & 0xFF, orgb[i] & 0xFF);
    std::fprintf(wd, "%08x\n", od[i]);
  }
  for (FILE* f : {wn, wt, wr, wd})
    if (f && std::fclose(f) != 0) dumped = false;
  if (!dumped) { std::printf("cannot write rtl_out/*.txt\n"); return 2; }
  std::printf("GS_RASTERIZER_pipeline: %ld cycles, %d pixels written, %ld mismatching pixels (N %d, T %d, D %d, RGB %d)\n",
              cycles, idx, mism, mN, mT, mD, mC);
  return (mism == 0 && idx == npix && dut.Finish) ? 0 : 1;
}
