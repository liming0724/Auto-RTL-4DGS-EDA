#include <cstdio>
#include <stdexcept>

#include "VGS_RASTERIZER_pipeline.h"
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

static int run() {
    const SceneConfig config{1, 600, 1089};
    const F32Policy fp;
    std::vector<Splat<float>> values;
    std::vector<uint32_t> indices;
    build_srams(fp, make_scene(config), 0.5, config.sram_depth, values, indices);
    if (!write_ram_file("ram_file.txt", values) || !write_gs_value("gs_value.txt", indices))
        throw std::runtime_error("could not initialize GS simulation SRAMs");
    const auto expected = RtlRasterizer<F32Policy>(fp).run(values, indices);
    check(expected.cycles >= 10000, "reference frame must cover at least 10000 cycles", 0);
    check(expected.write_cycles.size() == expected.pixels.size() + 1,
          "reference must include the legacy terminal write strobe", 0);

    VGS_RASTERIZER_pipeline dut;
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
            check(!dut.en && !dut.Finish && dut.N == 0, "reset outputs", clocks);
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
    size_t writes = 0;
    uint64_t cycle = 0;
    for (; cycle <= expected.cycles && !dut.Finish; ++cycle) {
        const bool expect_write =
            writes < expected.write_cycles.size() && cycle == expected.write_cycles[writes];
        check(dut.en == expect_write, "write strobe differs from C++ reference", clocks);
        if (dut.en) {
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
        check(dut.Finish && !dut.en, "COMPLETE must hold without further writes", clocks);
    }
    dut.final();
    std::printf("GS PASS: %llu total cycles, %llu frame cycles, %zu bit-exact pixels, "
                "%zu write strobes, first write at cycle %llu, 0 mismatches\n",
                static_cast<unsigned long long>(clocks), static_cast<unsigned long long>(cycle),
                expected.pixels.size(), writes, static_cast<unsigned long long>(first_write));
    return 0;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        return run();
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
