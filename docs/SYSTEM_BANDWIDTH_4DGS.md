# Phase 5a: 4DGS Modeled External DRAM Traffic

Run `make profile_bw_4dgs` to reproduce the six configurations on the same 64×64,
eight-frame scene used by [the quality sweep](QUALITY_VS_SKIP_4DGS.md). All runs
use one PE and 512 cache sets. The CSV at
`build/4dgs/bandwidth/bandwidth_profile.csv` contains one row per frame per mode;
cycle and quality results cover all eight frames.

## What the profiler counts

These numbers are **Modeled External DRAM Traffic**, not measured DRAM-controller
traffic. The testbench still drives all input fields; there is no simulated DRAM
controller, cache-line/burst overhead, or DRAM write path.

- One accepted transaction renders a Gaussian's four-pixel row span. At its output
  handshake, `out_skipped` says the projection attributes came from the on-chip
  cache. A raw tag hit that is not eligible for reuse still recomputes and is
  counted as a fetch. `fetch_count` counts completed transactions not served
  from the cache, grouped by frame, without deduplicating the same Gaussian
  across spans.
- `BYTES_PER_GAUSSIAN = ceil((12 + 23 × W) / 8)`, where `W = 8 + Q_FRAC`.
  The packed stream payload is the 12-bit `in_id`, thirteen W-bit fields
  (`in_mu0_{x,y}`, `in_dmu_{x,y}`, `in_s0_{x,y}`, `in_ds_{x,y}`, `in_rho`,
  `in_opacity`, `in_pix_x0`, `in_pix_y`, and `in_t`), and the ten W-bit
  coefficients in `in_coef`. Byte rounding is once per payload, not per field.
  That is **48 bytes** at Q_FRAC=8 (380 bits) and **59 bytes** at Q_FRAC=12 (472 bits).
  Configuration, clock, reset, and handshake signals are not payload.
- `dram_read_bytes = fetch_count × BYTES_PER_GAUSSIAN`.
  `dram_write_bytes = 0` in every frame and mode.
- Read reduction is `100 × (1 − mode read bytes / Baseline read bytes)`; the
  CSV compares like frames, while the summary compares eight-frame totals for
  each precision. Speedup is Baseline total cycles divided by mode total cycles.
  Skip rate is the fraction of transactions that reuse cached attributes.

## Modes and bypass policy

| Mode | cache_bypass | MOTION_THRESH | CACHE_WAYS |
|---|---:|---:|---:|
| Baseline | 1 | 0 | 2 |
| Skip-Only | 1 | 16 | 2 |
| Full Co-Design | 0 | 16 | 4 |

The top samples `cache_bypass` at input acceptance. Bypassed transactions launch
no SRAM lookup and suppress fills, so they neither read nor warm or evict cache
entries. With bypass low, lookup, fill, and replacement are unchanged.

Skip-Only tests whether the threshold alone can save compute without saving
fetches. In this RTL a threshold match is only eligibility: LOOKUP can skip
projection only when it also receives a cache hit. Forcing misses therefore
leaves **both traffic and cycles unchanged** vs Baseline. Full Co-Design is the
only mode that can save either metric. Enabling a cache-independent compute
skip would require a datapath change, which is outside Phase 5a.

## Results — Modeled External DRAM Traffic

Read bytes/frame are the mean over eight frames; the table below it lists the
individual frame counts. Every mode has zero DRAM write bytes and zero bit-true
mismatches. PSNR is against the FP32 reference; HW SSIM is scored by `SSIM_v2`.

| Q_FRAC | Mode | Mean read bytes/frame | Read reduction | Total cycles | Speedup | Skip rate | PSNR (dB) | HW SSIM |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 8 | Baseline | 890,160.0 | 0.00% | 5,044,240 | 1.000× | 0.00% | 44.7331 | 0.99724 |
| 8 | Skip-Only | 890,160.0 | 0.00% | 5,044,240 | 1.000× | 0.00% | 44.7331 | 0.99724 |
| 8 | Full Co-Design | 85,104.0 | 90.44% | 1,421,488 | 3.549× | 90.44% | 44.7054 | 0.99726 |
| 12 | Baseline | 1,094,155.0 | 0.00% | 6,231,120 | 1.000× | 0.00% | 47.7441 | 0.99748 |
| 12 | Skip-Only | 1,094,155.0 | 0.00% | 6,231,120 | 1.000× | 0.00% | 47.7441 | 0.99748 |
| 12 | Full Co-Design | 161,416.6 | 85.25% | 1,804,565 | 3.453× | 85.25% | 47.7443 | 0.99746 |

| Q_FRAC | Mode | Frame 0 | Frame 1 | Frame 2 | Frame 3 | Frame 4 | Frame 5 | Frame 6 | Frame 7 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | Baseline / Skip-Only | 889,392 | 890,256 | 889,632 | 889,536 | 891,120 | 891,408 | 890,688 | 889,248 |
| 8 | Full Co-Design | 12,288 | 66,336 | 84,672 | 92,208 | 97,776 | 105,984 | 110,448 | 111,120 |
| 12 | Baseline / Skip-Only | 1,093,211 | 1,094,273 | 1,093,506 | 1,093,388 | 1,095,335 | 1,095,689 | 1,094,804 | 1,093,034 |
| 12 | Full Co-Design | 15,104 | 160,067 | 170,569 | 173,283 | 182,487 | 191,396 | 195,762 | 202,665 |

The four-way Full Co-Design run matches the two-way sweep at T=16 on this
scene; the comparison demonstrates cache reuse, not a benefit from adding ways.

## Verification

- **Tier 1:** `verilator --lint-only -Wall` is clean. `make sim_4dgs` passes
  with bypass off; all 18 sweep points and six profiler runs have zero bit-true
  mismatches. Both CSVs are identical with Verilator 4.038 and 5.020.
- **Tier 2:** Default configuration (one PE, Q_FRAC=8, two ways, 512 sets):
  Yosys 0.23 with Berkeley ABC maps **51,082 generic gates**, down **28
  (0.055%)** from Phase 4's 51,110 on the same toolchain. There are 2,276
  flip-flops (+1 for the latched bypass), 61,179 optimized AIG ANDs, and 334
  levels. Both ABC CEC comparisons pass (pre-map vs. mapped and pre-map vs.
  optimized). Yosys 0.33 with `yosys-abc` maps 49,358 generic gates and also
  passes both CEC comparisons. These are generic-cell metrics, not 130 nm results.
- **Tier 3:** `make formal_4dgs` with the 30 s breaker and COI slicing reports
  **44/44 obligations as expected** with both Yosys 0.23 and 0.33. All 43 Phase 4 obligations
  are retained; one new obligation proves that accepting a bypassed transaction
  launches no cache response and the latched bypass prevents cache fills.
  The existing cache-dependence witness is checked with bypass off. No
  obligation needed a timeout fallback.
