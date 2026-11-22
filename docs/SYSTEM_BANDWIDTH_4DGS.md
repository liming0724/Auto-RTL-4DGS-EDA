# System Bandwidth: Modeled External DRAM Traffic (Phase 5a)

All bandwidth numbers in this document are **Modeled External DRAM Traffic**. They come
from transaction counters in the cycle-accurate Verilator frame testbench, multiplied by a
fixed per-Gaussian byte count. They are **not** measured DRAM-controller traffic: the
design has no DRAM controller, no AXI master and no memory model.

```
make profile_bw_4dgs   # 3 modes x Q_FRAC {8,12} -> build/4dgs/bandwidth/bandwidth_profile.{csv,json,md}
```

## Setup

- Scene, frame count and stream are the same as `docs/QUALITY_VS_SKIP_4DGS.md`: the
  `sim_4dgs_frame` scene, 8 frames, 148,360 transactions in total (about 18.5k per frame).
  One transaction is one (Gaussian, 4-pixel row span) record on the `in_*` stream.
- Every run is bit-true checked against the unchanged C++ golden model. All 6 runs have
  **0 mismatches**. PSNR is against the FP32 renderer, and HW SSIM is the mean `SSIM_v2`
  score, both as in the quality sweep.
- Modes (each run at Q_FRAC 8 and 12, `NUM_PE=1`, `CACHE_SETS=512`):

| Mode | `cache_bypass` | `CACHE_WAYS` | `MOTION_THRESH` |
|---|---:|---:|---:|
| Baseline | 1 | 4 | 0 |
| Skip-Only | 1 | 4 | 16 |
| Full Co-Design | 0 | 4 | 16 |

`CACHE_WAYS` has no effect when `cache_bypass=1` (no hit is ever served), so all six runs
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

`dram_read_bytes`/frame is the mean over the 8 frames. Reduction and speedup are against
the same Q_FRAC's Baseline.

| Q_FRAC | Mode | dram_read_bytes/frame | Reduction vs Baseline | dram_write_bytes | Total cycles | Speedup vs Baseline | Skip rate | PSNR (dB) | HW SSIM |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | Baseline | 890,160 | 0.0% | 0 | 5,044,240 | 1.000x | 0.0% | 44.73 | 0.99724 |
| 8 | Skip-Only | 890,160 | 0.0% | 0 | 5,044,240 | 1.000x | 0.0% | 44.73 | 0.99724 |
| 8 | Full Co-Design | 85,104 | **90.4%** | 0 | 1,421,488 | **3.549x** | 90.4% | 44.71 | 0.99726 |
| 12 | Baseline | 1,094,155 | 0.0% | 0 | 6,231,120 | 1.000x | 0.0% | 47.74 | 0.99748 |
| 12 | Skip-Only | 1,094,155 | 0.0% | 0 | 6,231,120 | 1.000x | 0.0% | 47.74 | 0.99748 |
| 12 | Full Co-Design | 161,417 | **85.2%** | 0 | 1,804,565 | **3.453x** | 85.2% | 47.74 | 0.99746 |

Totals over 8 frames: Baseline 7,121,280 B (Q8) and 8,753,240 B (Q12); Full Co-Design
680,832 B (Q8) and 1,291,333 B (Q12). Per-frame `fetch_count` and `dram_read_bytes` are
in the CSV. In Full Co-Design, frame 0 fetches only 256 transactions (cold cache: each
Gaussian's first span), and frames 1–7 fetch 1,382–2,315 (Q8) and 2,713–3,435 (Q12).

Full Co-Design cycles and PSNR equal the quality sweep's `MOTION_THRESH=16` points
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
- **Full Co-Design is where both savings come from.** Motion-aware skip plus the
  attribute cache cuts Modeled External DRAM Traffic by 90.4% (Q8) and 85.2% (Q12), and
  cycles by 3.55x and 3.45x. PSNR drops by 0.03 dB (Q8) and rises by 0.0002 dB (Q12)
  vs Baseline.
- **Model assumption.** The model assumes that a cache-served transaction fetches no
  parameter bytes. The current RTL interface streams every field on every transaction.
  Even on a hit, the skip decision (deform_engine + temporal_skip_unit) still consumes
  `in_id`, `in_dmu_*`, `in_ds_*`, `in_t` and `in_coef`, and the raster lanes consume
  `in_pix_*`. Only the six fields the cached attributes replace (`in_mu0_*`, `in_s0_*`,
  `in_rho`, `in_opacity`, 6 x W bits) are dead on a hit. The CSV therefore also reports
  `dram_read_bytes_conservative = fetch_count * BYTES_PER_GAUSSIAN + cache_served *
  ceil((12 + 17W) / 8)` (36 B at Q8, 44 B at Q12). This is an upper bound for this
  interface. On that basis, Full Co-Design reduces traffic by **22.6% (Q8)** and
  **21.7% (Q12)**. The headline 90.4% / 85.2% needs a memory system that fetches the
  skip-decision fields separately from the full record. Phase 5b (ping-pong buffer +
  AXI4-Stream wrapper) is where that split would be designed.

## Hygiene

- The profiler reuses the frame testbench and golden model. It changes no rendering math
  and no golden-model behavior: for `cache_bypass=1`, the model is configured with
  `motion_thresh=0`, which is equivalent because no hit can be served.
- Generated CSV/JSON/Markdown go to `build/4dgs/bandwidth/` (git-ignored, regenerated by
  `make profile_bw_4dgs` and in CI). No waveforms or simulator binaries are committed.
