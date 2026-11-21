#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>

#include "Vssim_sim_top.h"
#include "verilated.h"

namespace {
constexpr int width = 16, height = 17, pixels = width * height;
constexpr int frame_cycles = 400, frames = 25;
constexpr int first_score = 38;  // Zero-based cycle from the legacy TB trace.
constexpr uint64_t mask = (1ULL << 48) - 1;
using Image = std::array<uint64_t, pixels>;

Image load_hex(const char* path) {
    Image image{};
    std::ifstream in(path);
    for (auto& pixel : image) {
        if (!(in >> std::hex >> pixel) || pixel > mask)
            throw std::runtime_error(std::string("bad or missing 48-bit word in ") + path);
    }
    std::string extra;
    if (in >> extra)
        throw std::runtime_error(std::string("extra vector in ") + path);
    return image;
}

int serpentine(int row, int offset) {
    return row * width + (row % 2 ? width - 1 - offset : offset);
}

Image shift_stimulus(std::mt19937_64& rng) {
    Image image{};
    for (auto& pixel : image) pixel = rng() & mask;
    constexpr std::array<uint64_t, 5> corners{0, 1, mask, 1ULL << 47, 0x7fff8000ffffULL};
    for (int row = 0; row < height; ++row) {
        for (int i = 0; i < int(corners.size()); ++i)
            image[row * width + (i < 2 ? i : width - 5 + i)] = corners[(row + i) % corners.size()];
    }
    return image;
}

std::array<uint64_t, 9> reference_window(const Image& image, int index) {
    const int row = index / width, step = index % width;
    const int x = row % 2 ? step : width - 1 - step;
    const auto at = [&](int y, int col) -> uint64_t {
        if (y < 0 || y >= height || col < 0 || col >= width) return 0;
        return image[y * width + col];
    };

    // The legacy lower bank follows the next input row's scan direction.
    // At the turn its two remaining pixels sit in taps 20/21 (22 is zero).
    const int dir = row % 2 ? 1 : -1;
    const std::array<uint64_t, 3> bottom = step == width - 1
        ? std::array<uint64_t, 3>{at(row + 1, x), at(row + 1, x - dir), 0}
        : std::array<uint64_t, 3>{at(row + 1, x + dir), at(row + 1, x), at(row + 1, x - dir)};
    return {at(row - 1, x + 1), at(row - 1, x), at(row - 1, x - 1),
            at(row, x + 1), at(row, x), at(row, x - 1), bottom[0], bottom[1], bottom[2]};
}

void check(bool pass, const char* what, int cycle) {
    if (!pass) throw std::runtime_error(std::string(what) + " at cycle " + std::to_string(cycle));
}

uint16_t reference_div8(unsigned word) {
    const unsigned sign = word & 0x8000, frac = word & 0x3ff, exp = (word >> 10) & 31;
    if (exp == 31) return 0;
    if (exp == 0) return sign | (frac >> 3);
    // The legacy truncation drops the input sign for underflow from normal values.
    if (exp <= 3) return (1024 | frac) >> (4 - exp);
    return word - (3 << 10);
}

int run(const Image& gt, const Image& img, const Image& scores) {
    Vssim_sim_top dut;
    std::mt19937_64 rng(0x5353494d);
    int score_count = 0, window_count = 0;
    for (int frame = 0; frame < frames; ++frame) {
        const auto directed = shift_stimulus(rng);
        for (int tick = 0; tick < frame_cycles; ++tick) {
            const int cycle = frame * frame_cycles + tick;
            const int image_index = tick - 10;
            const int shift_index = tick - 10;
            dut.clk = 0;
            dut.rst_n = tick >= 5;
            dut.write = image_index >= 0 && image_index < pixels;
            dut.in_valid = dut.write && image_index != 0;
            const int addr = dut.write ? serpentine(image_index / width, image_index % width) : 0;
            dut.write_addr = addr;
            dut.gt = gt[addr];
            dut.img = img[addr];
            dut.shift_valid = shift_index >= 0 && shift_index < pixels;
            dut.pixel = dut.shift_valid
                ? directed[serpentine(shift_index / width, shift_index % width)] : 0;
            dut.eval();
            dut.clk = 1;
            dut.eval();

            const int score_index = tick - first_score;
            const bool expect_score = score_index >= 0 && score_index < pixels;
            check(dut.out_valid == expect_score, "SSIM out_valid", cycle);
            check(dut.img_addr == dut.gt_addr && dut.img_addr < pixels, "SSIM input SRAM address", cycle);
            if (expect_score) {
                if (dut.score != scores[score_index]) {
                    std::cerr << "SSIM score " << score_index << ": got 0x" << std::hex
                              << dut.score << ", expected 0x" << scores[score_index] << '\n';
                    check(false, "SSIM packed FP16 score", cycle);
                }
                ++score_count;
            }

            const int window_index = shift_index - 18;
            const bool expect_window = window_index >= 0 && window_index < pixels;
            check(dut.window_valid == expect_window, "shift out_valid", cycle);
            if (expect_window) {
                const std::array<uint64_t, 9> actual{dut.p00, dut.p01, dut.p02, dut.p10, dut.p11,
                                                     dut.p12, dut.p20, dut.p21, dut.p22};
                check(actual == reference_window(directed, window_index), "shift 48-bit tap data", cycle);
                ++window_count;
            }
        }
    }
    for (unsigned word = 0; word < (1U << 16); ++word) {
        dut.div8_in = word;
        dut.eval();
        if (dut.div8_out != reference_div8(word))
            throw std::runtime_error("fp16_div8 mismatch for input " + std::to_string(word));
    }
    dut.final();
    std::cout << "SSIM PASS: " << frames * frame_cycles << " cycles, " << score_count
              << " bit-exact scores, " << window_count
              << " checked windows, 65536 div8 cases, 0 mismatches\n";
    return 0;
}

// Replays a CEGAR witness (scripts/formal/cegar_to_verilator.py) on the shift window:
// each line is "cycle shift_valid pixel window_valid p00 .. p22" (hex), with the
// expected outputs sampled after that cycle's rising edge.
int replay(const char* path) {
    Vssim_sim_top dut;
    for (int tick = 0; tick < 2; ++tick) {  // clocked reset, as in run()
        dut.rst_n = 0;
        dut.clk = 0;
        dut.eval();
        dut.clk = 1;
        dut.eval();
    }
    dut.clk = 0;
    dut.rst_n = 1;
    dut.eval();
    std::ifstream in(path);
    std::string line;
    int checked = 0;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream fields(line);
        uint64_t cycle, valid, pixel, expect_valid;
        std::array<uint64_t, 9> expect{};
        fields >> std::hex >> cycle >> valid >> pixel >> expect_valid;
        for (auto& tap : expect) fields >> tap;
        if (!fields) throw std::runtime_error("bad CEGAR trace line: " + line);
        dut.clk = 0;
        dut.shift_valid = valid;
        dut.pixel = pixel;
        dut.eval();
        dut.clk = 1;
        dut.eval();
        const std::array<uint64_t, 9> actual{dut.p00, dut.p01, dut.p02, dut.p10, dut.p11,
                                             dut.p12, dut.p20, dut.p21, dut.p22};
        if (dut.window_valid != expect_valid || actual != expect) {
            std::cout << "CEGAR FAIL: cycle " << cycle << " window_valid=" << int(dut.window_valid)
                      << " (expected " << expect_valid << ")";
            for (int i = 0; i < 9; ++i)
                if (actual[i] != expect[i])
                    std::cout << ", p" << i / 3 << i % 3 << "=" << std::hex << actual[i]
                              << " (expected " << expect[i] << ")" << std::dec;
            std::cout << '\n';
            return 1;
        }
        ++checked;
    }
    dut.final();
    std::cout << "CEGAR PASS: " << checked << " replayed cycles match the trace\n";
    return 0;
}
}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const bool cegar = argc == 3 && std::string(argv[1]) == "--cegar";
    if (argc != 4 && !cegar) {
        std::cerr << "usage: Vssim_sim_top gt.txt img.txt ssim_golden.txt\n"
                     "       Vssim_sim_top --cegar cegar_trace.hex\n";
        return 2;
    }
    try {
        if (cegar) return replay(argv[2]);
        return run(load_hex(argv[1]), load_hex(argv[2]), load_hex(argv[3]));
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
