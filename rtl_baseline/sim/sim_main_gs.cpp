#include <cstdio>
#include <stdexcept>
#include <string>

#include "Vgs_sim_top.h"
#include "gs4d/scene.hpp"
#include "verilated.h"

using namespace gs4d;

static void check(bool pass, const char* what, uint64_t cycle) {
    if (!pass) {
        std::fprintf(stderr, "GS mismatch at cycle %llu: %s\n",
                     static_cast<unsigned long long>(cycle), what);
        throw std::runtime_error(what);
    }
}

// Directed vector (docs/BACKLOG_INVESTIGATION.md item 2) on pixel 0 (x 975, y 34): six
// Gaussians at dx = 0.5, dy = 0 (exp(beta) ~ 0.99875), then alpha-too-small filler. With the
// stale-T quirk stage 8 reads T from two Gaussians back, so T_test_k = (1 - a_k) T_test_{k-2}:
//   G1 conw 1.5   -> clamp 0.99, T 0.01     G2 0.9 -> T 0.1
//   G3 0.9        -> T 1e-3                 G4 0.5 -> T 0.05
//   G5 0.995      -> clamp 0.99, T 1e-5 < 1e-4 (s10_altb)
//   G6 0.5        -> T 0.025: valid stage-10 entry one cycle after s10_altb, kept out of
//                    RGB/depth only by tail_crossed.
static void directed_scene(std::vector<Splat<float>>& values, std::vector<uint32_t>& indices) {
    constexpr int kN = 6;
    const float conw[kN] = {1.5f, 0.9f, 0.9f, 0.5f, 0.995f, 0.5f};
    const float rgb[kN][3] = {{0.8f, 0.2f, 0.1f}, {0.1f, 0.7f, 0.3f}, {0.3f, 0.3f, 0.9f},
                              {0.6f, 0.1f, 0.5f}, {0.2f, 0.9f, 0.4f}, {0.9f, 0.9f, 0.9f}};
    for (int i = 0; i < kN; ++i)
        values[i] = {975.5f, 34.0f, rgb[i][0], rgb[i][1], rgb[i][2], 2.0f + i, 0.01f, 0.0f, 0.01f,
                     conw[i], 0.0f, 0.0f};
    for (size_t i = kN; i < values.size(); ++i)
        values[i] = {0.0f, 0.0f, 0.5f, 0.5f, 0.5f, 9.0f, 1.0f, 0.0f, 1.0f, 0.5f, 0.0f, 0.0f};
    for (size_t i = 0; i < indices.size(); ++i) indices[i] = i < kN ? uint32_t(i) : uint32_t(kN);
}

static int run(bool directed) {
    const SceneConfig config{1, 600, 1089};
    const F32Policy fp;
    std::vector<Splat<float>> values;
    std::vector<uint32_t> indices;
    build_srams(fp, make_scene(config), 0.5, config.sram_depth, values, indices);
    if (directed) directed_scene(values, indices);
    if (!write_ram_file("ram_file.txt", values) || !write_gs_value("gs_value.txt", indices))
        throw std::runtime_error("could not initialize GS simulation SRAMs");
    const auto expected = RtlRasterizer<F32Policy>(fp).run(values, indices);
    if (directed) {
        RtlQuirks unclamped, untailed;
        unclamped.alpha_clamp = false;
        untailed.rgb_stop_tail = untailed.depth_stop_tail = true;
        const auto u = RtlRasterizer<F32Policy>(fp, TileConfig{}, unclamped).run(values, indices);
        const auto t = RtlRasterizer<F32Policy>(fp, TileConfig{}, untailed).run(values, indices);
        check(expected.pixels[0].n == 6, "directed pixel 0 must stop after the post-crossing entry", 0);
        check(fp.to_bits(u.pixels[0].trans) != fp.to_bits(expected.pixels[0].trans),
              "directed vector must exercise the alpha clamp", 0);
        check(t.pixels[0].rgb != expected.pixels[0].rgb &&
              fp.to_bits(t.pixels[0].depth) != fp.to_bits(expected.pixels[0].depth),
              "directed vector must exercise the crossing/tail RGB and depth gate", 0);
    } else {
        check(expected.cycles >= 10000, "reference frame must cover at least 10000 cycles", 0);
    }
    check(expected.write_cycles.size() == expected.pixels.size() + 1,
          "reference must include the legacy terminal write strobe", 0);

    Vgs_sim_top dut;
    uint64_t clocks = 0;
    auto tick = [&] {
        dut.clk = 1;
        dut.eval();
        dut.clk = 0;
        dut.eval();
        ++clocks;
    };
    auto reset = [&] {
        dut.rst_n = 0;
        for (int i = 0; i < 4; ++i) {
            tick();
            check(!dut.en && !dut.write_enable && !dut.Finish && dut.N == 0,
                  "reset outputs", clocks);
        }
        dut.rst_n = 1;
        dut.eval();
    };

    reset();
    uint64_t first_write = 0;
    while (!dut.en && first_write < 10000) {
        tick();
        ++first_write;
        check(!dut.Finish, "Finish before first write", clocks);
    }
    check(dut.en && first_write == expected.write_cycles.front(), "first write latency", clocks);
    // Reset in flight and verify the next frame restarts from pixel zero.
    reset();
    size_t writes = 0, clamps0 = 0, tails0 = 0;
    bool altb_prev = false;
    uint64_t cycle = 0;
    for (; cycle <= expected.cycles && !dut.Finish; ++cycle) {
        if (writes == 0) {
            clamps0 += dut.probe_clamp7;
            tails0 += dut.probe_commit10 && !dut.probe_altb10 && altb_prev;
            altb_prev = dut.probe_altb10;
        }
        const bool expect_write =
            writes < expected.write_cycles.size() && cycle == expected.write_cycles[writes];
        check(dut.en == expect_write, "write strobe differs from C++ reference", clocks);
        check(dut.write_enable == expect_write, "output SRAM write enable differs from C++ reference", clocks);
        if (dut.write_enable) {
            check(dut.write_addr == writes, "output SRAM address differs from expected write index", clocks);
            if (writes < expected.pixels.size()) {
                const auto& pixel = expected.pixels[writes];
                if (dut.RGB != pixel.rgb || dut.D != fp.to_bits(pixel.depth) ||
                    dut.T != fp.to_bits(pixel.trans) || dut.N != pixel.n) {
                    std::fprintf(stderr,
                        "pixel %zu, RGB/D/T/N: %06x/%08x/%08x/%u != %06x/%08x/%08x/%u\n",
                        writes, dut.RGB, dut.D, dut.T, unsigned(dut.N), pixel.rgb,
                        unsigned(fp.to_bits(pixel.depth)), unsigned(fp.to_bits(pixel.trans)), pixel.n);
                    check(false, "pixel payload differs from C++ reference", clocks);
                }
            }
            ++writes;
        }
        tick();
    }
    check(cycle == expected.cycles && dut.Finish, "frame latency differs from C++ reference", clocks);
    check(writes == expected.write_cycles.size(), "missing frame write strobes", clocks);
    for (int idle = 0; idle < 16; ++idle) {
        tick();
        check(dut.Finish && !dut.en && !dut.write_enable,
              "COMPLETE must hold without further writes", clocks);
    }
    if (directed) {
        check(clamps0 == 2, "directed pixel 0 must clamp alpha > 0.99 at stage 7 twice", clocks);
        check(tails0 == 1, "directed pixel 0 must commit a stage-10 entry one cycle after s10_altb", clocks);
    }
    dut.final();
    std::printf("GS%s PASS: %llu total cycles, %llu frame cycles, %zu bit-exact pixels, "
                "%zu address-checked write strobes, first write at cycle %llu, 0 mismatches\n",
                directed ? " directed" : "", static_cast<unsigned long long>(clocks), static_cast<unsigned long long>(cycle),
                expected.pixels.size(), writes, static_cast<unsigned long long>(first_write));
    return 0;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    bool directed = false;
    for (int i = 1; i < argc; ++i) directed |= std::string(argv[i]) == "+directed";
    try {
        return run(directed);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
