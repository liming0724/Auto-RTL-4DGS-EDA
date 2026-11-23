# System Bandwidth: Modeled External DRAM Traffic (Phase 5a)

All bandwidth numbers in this document are **Modeled External DRAM Traffic**. They come
from transaction counters in the cycle-accurate Verilator frame testbench, multiplied by a
fixed per-Gaussian byte count. They are **not** measured DRAM-controller traffic: the
design has no DRAM controller, no AXI master and no memory model.

```
make profile_bw_4dgs   # 5 runs x Q_FRAC {8,12} -> build/4dgs/bandwidth/bandwidth_profile.{csv,json,md}
```

## Setup

- Scene, frame count and stream are the same as `docs/QUALITY_VS_SKIP_4DGS.md`: the
  `sim_4dgs_frame` scene, 8 frames, 148,360 transactions in total (about 18.5k per frame).
  One transaction is one (Gaussian, 4-pixel row span) record on the `in_*` stream.
- Every run is bit-true checked against the unchanged C++ golden model. All 10 runs have
  **0 mismatches**. PSNR is against the FP32 renderer, and HW SSIM is the mean `SSIM_v2`
  score, both as in the quality sweep.
- Modes (each run at Q_FRAC 8 and 12, `NUM_PE=1`, `CACHE_SETS=512`):

| Mode | `cache_bypass` | `CACHE_WAYS` | `MOTION_THRESH` |
|---|---:|---:|---:|
| Baseline | 1 | 4 | 0 |
| Skip-Only | 1 | 4 | 16 |
| Full Co-Design | 0 | 4 | 1 (lossless knee) |
| Full Co-Design | 0 | 4 | 3 (Q8 Pareto point) |
| Full Co-Design | 0 | 4 | 16 |

The T=1 and T=3 Full Co-Design runs use the knee and Pareto points of the fine sweep
(`docs/QUALITY_VS_SKIP_4DGS.md`), because T=16 is lossy at Q8 (−0.03 dB vs T=0).
`CACHE_WAYS` has no effect when `cache_bypass=1` (no hit is ever served), so all ten runs
use one `CACHE_WAYS=4` build per Q_FRAC.

## `cache_bypass` semantics

`cache_bypass` is a new quasi-static top-level input of `gs4d_accelerator_top`, held like
`motion_thresh`. Every existing testbench and sim main drives it to 0, so default behavior
is unchanged (`make sim_4dgs` passes with identical numbers).

- `cache_bypass=1` masks the cache hit at the top (`lk_serve = lk_resp && lk_hit &&
  !cache_bypass`), so every LOOKUP sees a miss. Every transaction takes the full
  recompute path from its streamed parameters (`out_skipped == 0`).
- **Fills are not suppressed.** `motion_aware_cache` is unchanged and keeps its normal
  lookup/fill/replacement sequence, so it stays warm. This keeps the change at the top
  level only: the cache's replacement policy, the skip unit's threshold logic and the
  datapath are untouched. It also means that toggling the bypass back to 0 resumes from a
  consistent cache, and the cache module's own formal obligations still hold unchanged.
  The formal harness proves that under `cache_bypass == 1` no datapath or control
  register depends on cache contents (`docs/FORMAL_VERIFICATION.md`).

## Counting formulas

Per frame, in the frame testbench:

```
fetch_count      = transactions with out_skipped == 0   (2D attributes NOT served from the cache;
                                                          parameters fetched from the input stream)
cache_served     = transactions with out_skipped == 1
dram_read_bytes  = fetch_count * BYTES_PER_GAUSSIAN
dram_write_bytes = 0
```

`dram_write_bytes = 0` because **this design has no DRAM write path**: results leave on
the `out_*` stream to the compositor, and the attribute cache is on-chip SRAM.

`BYTES_PER_GAUSSIAN` is the byte-rounded sum of the per-Gaussian input ports of
`gs4d_accelerator_top` (`W = 8 + Q_FRAC`, `COEF_W = 10*W`, `ID_W = 12`):

| Field(s) | Bits |
|---|---:|
| `in_id` | 12 |
| `in_mu0_x`, `in_mu0_y`, `in_dmu_x`, `in_dmu_y`, `in_s0_x`, `in_s0_y`, `in_ds_x`, `in_ds_y`, `in_rho`, `in_opacity`, `in_pix_x0`, `in_pix_y`, `in_t` | 13 x W |
| `in_coef` | 10 x W |
| **Total** | **12 + 23 x W** |

| Q_FRAC | W | Bits per Gaussian | **BYTES_PER_GAUSSIAN** |
|---:|---:|---:|---:|
| 8 | 16 | 380 | **48** |
| 12 | 20 | 472 | **59** |

Control inputs (`clk`, `rst_n`, `motion_thresh`, `cache_bypass`, `in_valid`, `out_ready`)
are not per-Gaussian data and are not counted.

## Results (Modeled External DRAM Traffic)

Reduction and speedup are against the same Q_FRAC's Baseline. Two averages are given for
`dram_read_bytes`/frame:

- **8-frame**: total over frames 0–7 divided by 8.
- **Steady (frames 1–7)**: frame 0 excluded. Frame 0 is a cold start: in Full Co-Design
  the cache is empty, so only each Gaussian's first span is fetched (**256 fetches** at
  every threshold, vs about 18.5k transactions) and the later spans of the same Gaussian
  hit. Frame 0 therefore under-counts the traffic of a running stream, and the 8-frame
  average overstates the reduction. The steady-state reduction is the figure to quote
  for continuous operation.

**Headline** (cache-served transactions fetch 0 bytes):

| Q_FRAC | Mode | T | dram_read_bytes/frame (8-frame) | Reduction (8-frame) | dram_read_bytes/frame (steady) | Reduction (steady) | dram_write_bytes | Total cycles | Speedup vs Baseline | Skip rate | PSNR (dB) | HW SSIM |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | Baseline | 0 | 890,160 | 0.0% | 890,270 | 0.0% | 0 | 5,044,240 | 1.000x | 0.0% | 44.7331 | 0.99724 |
| 8 | Skip-Only | 16 | 890,160 | 0.0% | 890,270 | 0.0% | 0 | 5,044,240 | 1.000x | 0.0% | 44.7331 | 0.99724 |
| 8 | Full Co-Design | 1 | 140,460 | **84.2%** | 158,770 | **82.2%** | 0 | 1,670,590 | 3.019x | 84.2% | 44.7331 | 0.99724 |
| 8 | Full Co-Design | 3 | 117,900 | **86.8%** | 132,987 | **85.1%** | 0 | 1,569,070 | 3.215x | 86.8% | 44.7359 | 0.99721 |
| 8 | Full Co-Design | 16 | 85,104 | **90.4%** | 95,506 | **89.3%** | 0 | 1,421,488 | 3.549x | 90.4% | 44.7054 | 0.99726 |
| 12 | Baseline | 0 | 1,094,155 | 0.0% | 1,094,290 | 0.0% | 0 | 6,231,120 | 1.000x | 0.0% | 47.7441 | 0.99748 |
| 12 | Skip-Only | 16 | 1,094,155 | 0.0% | 1,094,290 | 0.0% | 0 | 6,231,120 | 1.000x | 0.0% | 47.7441 | 0.99748 |
| 12 | Full Co-Design | 1 | 190,282 | **82.6%** | 215,308 | **80.3%** | 0 | 1,941,555 | 3.209x | 82.6% | 47.7441 | 0.99748 |
| 12 | Full Co-Design | 3 | 186,971 | **82.9%** | 211,523 | **80.7%** | 0 | 1,925,840 | 3.236x | 82.9% | 47.7441 | 0.99748 |
| 12 | Full Co-Design | 16 | 161,417 | **85.2%** | 182,318 | **83.3%** | 0 | 1,804,565 | 3.453x | 85.2% | 47.7443 | 0.99746 |

**Conservative** (cache-served transactions still read the 36 B / 44 B of non-replaced
fields; see Interpretation). Baseline and Skip-Only are unchanged because they serve no
hits.

| Q_FRAC | Mode | T | Conservative bytes/frame (8-frame) | Reduction (8-frame) | Conservative bytes/frame (steady) | Reduction (steady) |
|---:|---|---:|---:|---:|---:|---:|
| 8 | Full Co-Design | 1 | 702,735 | 21.1% | 707,395 | 20.5% |
| 8 | Full Co-Design | 3 | 697,095 | 21.7% | 700,949 | 21.3% |
| 8 | Full Co-Design | 16 | 688,896 | 22.6% | 691,579 | 22.3% |
| 12 | Full Co-Design | 1 | 864,357 | 21.0% | 870,820 | 20.4% |
| 12 | Full Co-Design | 3 | 863,515 | 21.1% | 869,858 | 20.5% |
| 12 | Full Co-Design | 16 | 857,018 | 21.7% | 862,433 | 21.2% |

Cold-start effect: excluding frame 0 lowers the headline reduction by 1.1–2.3 points and
the conservative reduction by 0.3–0.6 points. Per-frame `fetch_count`, `dram_read_bytes`
and conservative bytes are in the CSV. In Full Co-Design, frames 1–7 fetch 2,904–3,603
(Q8 T=1), 2,272–2,972 (Q8 T=3), 1,382–2,315 (Q8 T=16), 3,628–3,673 (Q12 T=1),
3,296–3,673 (Q12 T=3) and 2,713–3,435 (Q12 T=16) transactions. Totals over 8 frames:
Baseline 7,121,280 B (Q8) and 8,753,240 B (Q12).

Full Co-Design cycles and PSNR equal the quality sweep's T=1, 3 and 16 points
(`CACHE_WAYS=2`) exactly. The scene's working set fits in both cache sizes, so 4 ways adds
no hits on this scene.

## Interpretation

- **Skip-Only saves nothing on this RTL: not DRAM bytes, and not compute cycles either.**
  The task expected Skip-Only to save cycles but not bytes. In this design, "skip" means
  reusing the cached 2D attributes (`skip_deform && drho == 0 && hit`). With the cache
  bypassed, a skip-eligible Gaussian has no attributes to reuse, so it must recompute.
  The skip unit still flags 90.6% (Q8) and 85.4% (Q12) of transactions as skip-eligible,
  but 0% are skipped, and cycles, PSNR and SSIM are bit-identical to Baseline. Saving
  compute without the cache would need a separate attribute store, which would be a
  datapath change and is out of scope here.
- **Full Co-Design is where both savings come from.** At the lossless knee (T=1, PSNR
  and HW SSIM identical to Baseline at both Q_FRAC values), motion-aware skip plus the
  attribute cache cuts Modeled External DRAM Traffic by 84.2% / 82.2% steady (Q8) and
  82.6% / 80.3% steady (Q12), and cycles by 3.02x and 3.21x. Most of the T=16 saving is
  already there at T=1. T=16 adds 6.2 (Q8) and 2.6 (Q12) points of 8-frame reduction,
  and at Q8 costs 0.03 dB of PSNR vs Baseline. T=3 is the Q8 Pareto point: 86.8% /
  85.1% steady at +0.003 dB.
- **Model assumption.** The model assumes that a cache-served transaction fetches no
  parameter bytes. The current RTL interface streams every field on every transaction.
  Even on a hit, the skip decision (deform_engine + temporal_skip_unit) still consumes
  `in_id`, `in_dmu_*`, `in_ds_*`, `in_t` and `in_coef`, and the raster lanes consume
  `in_pix_*`. Only the six fields the cached attributes replace (`in_mu0_*`, `in_s0_*`,
  `in_rho`, `in_opacity`, 6 x W bits) are dead on a hit. The CSV therefore also reports
  `dram_read_bytes_conservative = fetch_count * BYTES_PER_GAUSSIAN + cache_served *
  ceil((12 + 17W) / 8)` (36 B at Q8, 44 B at Q12). This is an upper bound for this
  interface. On that basis, Full Co-Design reduces traffic by 20.4–22.6% across the
  T=1/3/16 rows (T=1 steady: **20.5% Q8, 20.4% Q12**). The headline reductions need a memory system that fetches the
  skip-decision fields separately from the full record. Phase 5b (ping-pong buffer +
  AXI4-Stream wrapper) is where that split would be designed.

## Verification (three-tier funnel)

| Tier | Check | Result |
|---|---|---|
| 1 Verilator | `verilator --lint-only -Wall` on `gs4d_accelerator_top` (Q8 default; Q12 with `CACHE_WAYS=4`) | 0 warnings |
| 1 Verilator | `make eval_quality_4dgs` fine sweep | 18 points, 0 mismatches; the 12 Phase 4 points are identical |
| 1 Verilator | `make profile_bw_4dgs` | 10 runs, 0 mismatches vs golden model; the 6 original rows reproduce exactly after adding the Full Co-Design T=1/T=3 rows |
| 1 Verilator | `make sim_4dgs` (default config, `cache_bypass=0`) | PASS, same numbers as Phase 4 |
| 2 Yosys + ABC | `synth_4dgs`, default config `NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2, CACHE_SETS=512` | **50,910 generic gates** (Phase 4: 51,110; Δ −200, −0.39%), 2,275 FFs, 61,125 AIG ANDs, 336 AIG levels (Phase 4: 334); **CEC PASS** |
| 3 Z3 | `make formal_4dgs` (30 s timeout, COI slicing, BMC-20) | **45/45 as expected** (43 Phase 4 + 2 bypass obligations) |

The Full Co-Design T=1/T=3 rows and steady-state metrics changed only the profiler
script (`scripts/dse/quality_sweep.py`). No RTL, golden model or formal harness changed,
so the Tier 2 and Tier 3 results above still apply.

Gate counts are Yosys 0.23 + Berkeley ABC generic-cell numbers. They are not 130 nm
standard-cell figures. The Phase 4 reference (51,110) was re-synthesized from e49676f in
the same environment and reproduced exactly. The bypass adds one AND term to the hit
path. The 200-gate decrease is ABC restructuring that logic, not a removed function, and
CEC confirms the optimized netlist matches the RTL.

## Hygiene

- The profiler reuses the frame testbench and golden model. It changes no rendering math
  and no golden-model behavior: for `cache_bypass=1`, the model is configured with
  `motion_thresh=0`, which is equivalent because no hit can be served.
- Generated CSV/JSON/Markdown go to `build/4dgs/bandwidth/` (git-ignored, regenerated by
  `make profile_bw_4dgs` and in CI). No waveforms or simulator binaries are committed.
