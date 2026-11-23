# Phase 5d: Tile Pixel Renderer + Color Compositing (DRAFT, decision material)

> **DRAFT, docs only.** Nothing here is implemented. This document does not change
> any RTL, testbench, golden model, Makefile, CI or existing doc. It lists the design
> space for the Phase 5d renderer and the decisions that have to be made with Prof. Wu
> before 5d is implemented (target: thesis direction frozen before 2026-11-30).
> Base: `main` at `af43f4c`. The Phase 5c AXI split-fetch wrapper (PR #10) is **not
> merged** and is not a dependency. This draft designs against the
> `gs4d_accelerator_top` interface on `main`. "Channel A / Channel B" are used below only
> as names for the two field groups (`12 + 17W` skip-decision fields and `6W` base
> fields) that `docs/SYSTEM_BANDWIDTH_4DGS.md` already defines on `main`.

## Number labels

Every number in this document has one of these labels:

| Label | Meaning |
|---|---|
| **MEASURED** | Quoted from a committed doc or CSV on `main` (Verilator RTL runs, Yosys/ABC, Z3). The source is named. |
| **MODELED-SW** | Produced for this draft by an offline C++ harness that reuses the committed golden model and frame scene (`golden_model/4dgs_ref.hpp`, `golden_model/gs4d_frame.hpp`) without the RTL. The golden model is bit-true to the RTL (0 mismatches in every committed run), but these numbers are **not** RTL simulations. The harness reproduces the committed MEASURED PSNRs to 4 decimals (Q8 T=0 44.7331, Q8 T=3 44.7359, Q8 T=16 44.7054, Q12 T=0 47.7441 dB), which validates it. Method: Appendix A. |
| **MODELED** | Arithmetic from field widths, the golden cycle formula, or MEASURED counts × modeled byte sizes. No simulation. |

Gate counts quoted are Yosys/ABC **generic-cell** numbers. They are not 130 nm figures.

---

## 1. Current state (facts only)

### 1.1 What the top outputs per transaction

`rtl_4dgs/gs4d_accelerator_top.sv` takes **one transaction = one (Gaussian, TILE-pixel
row span)** on `in_valid/in_ready`. It returns one result on `out_valid/out_ready`.

- **Outputs:**
  - `out_id`, `out_skipped`, `out_delta` (W+2 bits);
  - `out_mean_x/y`;
  - `out_cov_a/b/c`: conic coefficients (inverse covariance), despite the name;
  - `out_opacity`;
  - `out_alpha[TILE*W-1:0]`: the alpha of the 4 pixels `(pix_x0 + k, pix_y)`, k = 0..3.
- **Format:** every field is signed Q(7, Q_FRAC), `W = 8 + Q_FRAC` (16 bits at Q8, 20 bits
  at Q12). `out_alpha` is 64 bits at Q8 and 80 bits at Q12.
- **Alpha** (`gs4d_raster_lane.sv`, bit-true to `Lane::alpha`):
  - `alpha = opacity · 2^-(e·log2e)`, using the piecewise-linear 2^-y.
  - It is 0 if `e < 0` or below 2^-8.
  - **There is no 0.99 clamp in the RTL.** The FP32 reference clamps, the RTL does not.
- **No pixel or color data on the outputs:**
  - The outputs do **not** carry `pix_x0`/`pix_y`, and the top has no color input or
    output.
  - The testbench knows where each result belongs because it keeps its own in-order job
    list.
  - The top has at most one transaction in flight: `in_ready` is high only in `ACCEPT`, and
    `ACCEPT` is reached only after `OUT & out_ready`.
- **Handshake:** results are held stable under `out_ready = 0`. Formal obligation
  (MEASURED, `docs/DSE_PARETO_4DGS.md`, `docs/FORMAL_VERIFICATION.md`): "out_ready == 0
  in OUT freezes state, outputs and cache arrays/valid/LRU; no accept".
- **FSM:** `ACCEPT → LOOKUP → [PROJ/DIV/INV: 11 + 2F cycles, recompute only] →
  RASTER (TILE/NUM_PE cycles) → OUT`.
  - Golden cycle model: `3 + TILE/NUM_PE` cycles per transaction, plus `11 + 2F` on
    recompute.
  - `NUM_PE` lanes: lane `l` in raster cycle `cnt` shades pixel `cnt·NUM_PE + l`.

### 1.2 What the testbench does in software

`rtl_4dgs/sim/sim_main_4dgs_frame.cpp` and `golden_model/gs4d_frame.hpp`:

- **Binning (testbench side).** `bin_spans` takes each Gaussian's 3.5σ box, using the
  FP32-deformed parameters clipped to the 64×64 frame. It cuts the box into 4-pixel row
  spans (`x += kTile`). Each span is one transaction, and every span of a Gaussian reuses
  its id.
- **Stream order is Gaussian-major over the whole frame:** Gaussian 0's spans, then
  Gaussian 1's spans, and so on. It is **not** tile-major. For any pixel, contributions
  arrive in id order.
- **Compositing (testbench side, never in RTL):** a sort-free normalized weighted sum
  in `double`:
  ```
  C(p) = (w_bg·c_bg + Σ_i α_i(p)·c_i) / (w_bg + Σ_i α_i(p)),   c_bg = 0 (black), w_bg = kBgWeight = 0.25
  ```
  - `Accum::add` accumulates `den += α` and `num[c] += α·rgb[c]`.
  - It drops `α ≤ 0` and off-frame pixels (`px ≥ 64` from a span that crosses the right
    edge).
  - `resolve()` divides per channel.
  - The weighted sum is commutative, so transaction order does not matter.
- **Alpha into the compositor:** RTL `out_alpha[k] · 2^-Q_FRAC` (exact fixed-point value
  as a double).
- **Where RGB comes from:**
  - `Gauss::rgb[3]` is a per-Gaussian `float`, drawn once by `make_scene`: U(0.1, 1.0)
    for moving Gaussians and U(0.2, 0.8) for static ones.
  - RGB is time-invariant and view-independent (no SH).
  - **It never enters the RTL and is not quantized.**
- **Ground truth:** `render_fp32` uses the same weighted sum, with FP32 alpha
  `min(0.99, o·exp(-½ dᵀΣ⁻¹d))`, cutoff 1/255, every Gaussian at every pixel.
- **Quantization of the output:**
  - The PSNR in every committed doc is computed on the **unquantized float frame**:
    `mse(rtl_acc.resolve(), gt)`.
  - The frame is converted to IEEE binary16 (`to_half`, RNE) only when written to
    `gt_f*.hex` / `rtl_f*.hex` for the `SSIM_v2` testbench (48-bit FP16 RGB words).
- **Committed quality (MEASURED, `docs/QUALITY_VS_SKIP_4DGS.md`):**
  - 44.7331 dB at Q8 T=0 and 47.7441 dB at Q12 T=0.
  - HW SSIM 0.99724 / 0.99748.
  - Composited frame max |RTL − FP32| per channel: 0.0167 (Q12 T=0).

### 1.3 What the 5a profiler counts as bytes per Gaussian

`docs/SYSTEM_BANDWIDTH_4DGS.md` (MEASURED counts × MODELED bytes):

- `BYTES_PER_GAUSSIAN = ceil((12 + 23W) / 8)`. This covers `in_id`, the 13 W-bit fields
  and `in_coef` (10W): **48 B at Q8 and 59 B at Q12**.
- It is charged per **transaction** (per span, not per unique Gaussian), and only when
  `out_skipped == 0`.
- The conservative variant also charges `ceil((12 + 17W)/8)` (36 B / 44 B) on each
  cache-served transaction.
- `dram_write_bytes = 0`: the design has no write path. Results go to the testbench
  compositor.
- **Color is not counted anywhere: 0 bytes.**
- Reference points (MEASURED, benign scene, per frame, 8-frame average):

| Q_FRAC | Mode | Bytes/frame |
|---|---|---:|
| 8 | Baseline | 890,160 |
| 8 | Full Co-Design T=1 | 140,460 |
| 8 | Full Co-Design T=1, conservative | 702,735 |
| 12 | Baseline | 1,094,155 |
| 12 | Full Co-Design T=1 | 190,282 |

### 1.4 Scene statistics that size the renderer (MODELED-SW, Appendix A)

| Scene, Q_FRAC | Transactions | Pixel slots (tx × 4) | Slots with α > 0 | Max contributions per pixel | Mean contributions per covered pixel | Max Σα per pixel | Max Σα·c (one channel) |
|---|---:|---:|---:|---:|---:|---:|---:|
| benign Q8 | 148,360 | 593,440 | 47.1% | 22 | 8.54 | 3.94 | 2.50 |
| benign Q12 | 148,360 | 593,440 | 44.6% | 21 | 8.10 | 3.95 | 2.53 |
| pan Q8 | 115,528 | 462,112 | 46.2% | 17 | 6.53 | 3.18 | 2.32 |
| fast Q8 | 143,367 | 573,468 | 47.1% | 25 | 8.27 | **5.34** | 3.45 |

The transaction counts equal the MEASURED counts in the committed docs (148,360 / 115,528 / 143,367).

---

## 2. Design options and their costs

### 2.a Per-pixel accumulator and pixel loop

The renderer sits on the `out_*` stream. For each result it updates the accumulators of
the TILE pixels `(pix_x0 + k, pix_y)`.

- **Pixel coordinates** must reach the renderer.
  - Option 1: a 1-entry side register captured at `accept`. This works because only
    one transaction is ever in flight.
  - Option 2: new `out_pix_x0/out_pix_y` pass-through ports. This is a top change.

**Per-pixel entry under the current weighted sum** (`den`, `num_r/g/b`). Widths are
MODELED from the §1.4 bounds:

- `den` needs 3 integer bits. Max Σα is 5.34 on fast and 3.94 on benign. Unsigned, 3.F
  gives headroom to 8.0.
- `num` needs 2 integer bits for the observed 3.45 max, with saturation; 3 bits for the
  same headroom as `den`.

| Entry variant | Q8 bits (bytes) | Q12 bits (bytes) | Notes |
|---|---:|---:|---|
| Rounded: den 3.F + 3 × num 3.F | 44 (5.5 B) | 60 (7.5 B) | num rounded to F bits after α·c |
| Exact products, u8 color: den 3.F + 3 × num 3.(F+8) | 68 (8.5 B) | 84 (10.5 B) | no rounding in the accumulator |
| Front-to-back: T 1.F + 3 × C 1.F (+ optional N count 5 b) | 36 (+5) | 52 (+5) | C ≤ 1, T ≤ 1; see §3.1 |

**Pixel loop options:**

| Option | Loop | Cost |
|---|---|---|
| L1, span-streaming (current shape) | Each `out_*` result updates TILE consecutive pixels of one row, NUM_PE per cycle | **No RTL change in the top.** The accumulator must cover the whole region the stream can address before it restarts. Because the stream is Gaussian-major over the frame (§1.2), that region is the **full 64×64 frame**, not a tile. |
| L2, tile-major stream | Binning is reordered so all spans that hit one tile arrive together, then the tile is flushed | Small ping-pong tile buffer. Needs a binning/order change in the testbench and golden model, which changes cache hit patterns and so cycles and bytes (§3.3). |
| L3, 2D tile loop in hardware | One transaction per (Gaussian, 8×8 tile). The top or renderer loops over `py` as well as `px` | Fewest transactions (16,078 vs 148,360 on benign, MODELED-SW). Needs a top FSM change (RASTER over `TILE_H` rows; `out_alpha` is 64·W wide or streamed). |

**Read-modify-write hazard (MODELED from the FSM):**

- Within one transaction, the TILE pixels are distinct.
- Between two transactions there are at least 3 cycles (`OUT → ACCEPT → LOOKUP →
  RASTER`). A 1–2 cycle RMW pipeline therefore needs no forwarding at the current FSM.
- A skid buffer (§2.e) or a pipelined top would remove that margin and would need
  forwarding.

### 2.b Color path: where per-Gaussian RGB comes from

Color is time-invariant in this scene and is identical for every span of a Gaussian.

**Byte costs** (MODELED, `ceil(bits/8)`, per transaction as in the 5a profiler):
- u8 RGB = 24 bits, u16 RGB = 48 bits, and Q(7,F) RGB = 3W (48 bits at Q8, 60 at Q12).
- Per-frame figures use the MEASURED benign `fetch_count` at T=1: 23,410 (Q8) and 25,801
  (Q12) over 8 frames, and 148,360 transactions.

| Option | Where the color lives | Bytes per transaction, Q8 / Q12 (u8 RGB) | Same, u16 RGB | Benign T=1 bytes/frame, Q8 u8 (vs 140,460 headline / 702,735 conservative today) | Hardware cost |
|---|---|---|---|---|---|
| C1, widen the monolithic record (today's `in_*` bundle) | streamed with every fetched transaction | 51 / 62 B (was 48 / 59) | 54 / 65 B | headline 149,239 (+6.2%); Baseline 945,795 (+6.2%) | Color must also be **cached** for hits (C1 alone has no color on a hit). Cache DW 6W → 6W+24: +25% (Q8) / +20% (Q12) data bits. |
| C2, pack with the 6W base fields ("Channel B") | fetched only on recompute misses; cached with the 2D attributes | Channel B 12 → 15 B (Q8), 15 → 18 B (Q12) | 18 / 21 B | split-fetch accounting: 711,514 (+1.2% vs 702,735) | Same cache widening as C1. Default cache: 512 sets × 2 ways × 99 bits = 101,376 bits (MEASURED); +24,576 bits with u8, +49,152 bits with u16. Cached color is always correct: color is time-invariant and the id is the tag. |
| C3, separate color channel ("Channel C") | streamed with **every** transaction, hit or miss | +3 B every transaction | +6 B | +55,635 B/frame u8, +111,270 u16 (Q8 or Q12) | No cache change. Most bytes. |
| C4, on-chip color table indexed by `id` | loaded once per scene, not per frame | 0 B/frame | 0 B/frame | +0 | `2^ID_W` = 4,096 ids × 24 bits = 12 KiB SRAM (u8) or 24 KiB (u16). One-time load: 768 B for the 256-Gaussian scene. Only works while color has no view or time dependence. |
| C5, color with the 2D attributes, computed on chip (SH evaluation) | — | — | — | — | Out of scope: the scene and golden model have no SH. Listed for completeness. |

- Under C1/C2 the profiler formula would change (`BYTES_PER_GAUSSIAN` grows), so every
  committed byte number would need a re-run with a labeled delta.
- Under C4 the committed byte numbers stay valid as written.

### 2.c RGB quantization and estimated PSNR impact (MODELED-SW)

Setup:
- Golden-model alphas, benign scene, 8 frames, the same compositor and PSNR as the
  committed testbench.
- Only the color or output format changes.
- Δ is relative to the same row's current (float RGB, float output) value. That value
  matches the committed MEASURED PSNR.

| Variant | Q8 T=0 | Q8 T=3 | Q8 T=16 | Q12 T=0 |
|---|---:|---:|---:|---:|
| Current: float RGB, float output | 44.7331 | 44.7359 | 44.7054 | 47.7441 |
| u16 per channel, round(c·65535)/65535 | 44.7331 (0.0000) | 44.7358 (−0.0001) | 44.7053 (−0.0001) | 47.7445 (+0.0004) |
| u8 per channel, round(c·255)/255 | 44.7279 (**−0.0052**) | 44.7304 (−0.0055) | 44.7007 (−0.0047) | 47.7358 (**−0.0083**) |
| Q(7,F) color, round(c·2^F)/2^F | 44.7671 (+0.0340) | 44.7698 (+0.0339) | 44.7391 (+0.0337) | 47.7418 (−0.0023) |
| Float RGB, **FP16 output** (the SSIM_v2 hex path) | 44.7327 (−0.0004) | 44.7354 | 44.7049 | 47.7422 (−0.0019) |
| Float RGB, **u8 output pixel** | 44.5812 (**−0.152**) | 44.5838 | 44.5538 | 47.4681 (**−0.276**) |
| u8 RGB in **and** u8 output pixel | 44.5640 (−0.169) | 44.5647 | 44.5384 | 47.4292 (−0.315) |

- **Per-Gaussian color quantization is not the quality bottleneck.** u8 costs ≤ 0.01 dB,
  and u16 is indistinguishable from float.
- The Q8 Q(7,F) "gain" of +0.034 dB is a quantization coincidence on this scene's random
  colors (1/256 vs 1/255 steps). It is not a real improvement.
- **The output pixel format matters more.**
  - u8 output adds a uniform-quantization noise floor of `10·log10(12·255²)` = 58.9 dB
    (MODELED).
  - Added in power to 44.73 dB, that predicts 44.57 dB (MODELED). This matches the
    MODELED-SW 44.58 dB.
  - The loss is larger at Q12 (−0.28 dB) because the base error is smaller there.
  - FP16 output (what SSIM_v2 already consumes) costs ≤ 0.002 dB.
- These PSNRs keep the current sort-free weighted sum. A different compositing formula
  (§3.1) changes the reference itself, so these deltas would have to be re-derived.

### 2.d NUM_PE work split across the pixel loop

Per pixel update:
- Weighted sum: 1 add (`den`) and 3 multiply-adds (`α·c`), so **3 multipliers per lane**.
  These are W×8 for u8 color.
- Front-to-back: `C += T·α·c`, `T ·= (1−α)`. That is 4–5 multipliers per lane, with a
  serial per-pixel dependence.

| Split | Description | Accumulator ports | Cost / risk |
|---|---|---|---|
| P1, lanes over pixels of one span (current top) | Lane `l` writes pixel `cnt·NUM_PE + l` | Bank by `x mod NUM_PE`: one RMW port per bank per cycle, conflict-free | Matches the existing raster schedule. Throughput is capped at NUM_PE px/cycle, and the 3-cycle FSM overhead per transaction stays. |
| P2, lanes over Gaussians (NUM_PE results in parallel on the same pixels) | Needs NUM_PE parallel top lanes or a pipelined top | Same-pixel conflicts. An adder tree merges them for the weighted sum (commutative), but **not** for front-to-back (order matters) | Not compatible with the single-transaction top. Large change. |
| P3, lanes over rows of a 2D tile (L3) | Lane `l` shades row `l` of the 8×8 tile | Bank by `y mod NUM_PE` | Pairs with L3. NUM_PE ∈ {1, 2, 4, 8} divides 8. |

**Cycle budget** (MODELED, golden cycle formula `transactions × (3 + px_per_tx/NUM_PE +
proj)`):
- No-skip upper bound, benign scene, 8 frames.
- The skip/cache hit rate under a different tiling is **not modeled**: fewer transactions
  per Gaussian means fewer lossless repeat-span hits.

| Tiling | Transactions (MODELED-SW) | Q8 NUM_PE = 1 | Q8 NUM_PE = 2 | Q8 NUM_PE = 4 | Q12 NUM_PE = 1 |
|---|---:|---:|---:|---:|---:|
| 4-px row span (current) | 148,360 | 5,044,240 (= MEASURED Baseline) | 4,747,520 | 4,599,160 | 6,231,120 |
| 8-px row span | 80,814 | 3,070,932 | 2,747,676 | 2,586,048 | 3,717,444 |
| 8×8 tile, one transaction per (Gaussian, tile) | 16,078 | 1,511,332 | 996,836 | 739,588 | 1,639,956 |

**Accumulator flush and resolve** (weighted sum only): one divide per pixel, shared by
the 3 channels as a reciprocal.
- With the existing restoring-divider style (2F+1 cycles), one divider takes 4,096 px ×
  17 = **69,632 cycles/frame** at Q8 and 102,400 at Q12 (MODELED).
- For comparison, the MEASURED compute time is 630,530 cycles/frame at Q8 T=0 and
  208,824 at Q8 T=1 (5,044,240 / 8 and 1,670,590 / 8).
- So a non-overlapped serial divider would add 11% / 33% at Q8. This is the main reason
  for ping-pong.
- Front-to-back with `c_bg = 0` needs no divide. With a nonzero `c_bg` it needs one
  multiply-add.

### 2.e Front-end stall rule under downstream backpressure

| Rule | Behavior | Cost |
|---|---|---|
| S1, stall at `OUT` (recommended default) | The renderer deasserts `out_ready` while the target bank is not writable (flush in progress) | **No top change.** It reuses the MEASURED, proven obligation (`OUT ∧ ¬out_ready` freezes state, outputs and cache; no accept). Lost throughput = non-overlapped flush time. |
| S2, k-entry skid FIFO between top and renderer | The top keeps running while the renderer is busy | k × (TILE·W + pix + color + id) bits. At Q8 with u8 color: 64 + 32 + 24 + 12 = 132 bits per entry (MODELED). Removes the 3-cycle RMW margin of §2.a, so it needs forwarding. |
| S3, tile-boundary stall only (with L2/L3) | Backpressure only when a result targets the *next* tile while the other bank is still flushing | Needs a tile id with the result (sideband or derived from `pix_*`). Degenerates to S1 under L1. |

Under any rule, the renderer must never drop or reorder a result. With S1 and the
weighted sum, reordering is harmless to the frame (commutative) but breaks bit-true
traceability.

### 2.f Ping-pong tile buffer sizing

- One bank receives accumulations while the other is resolved and drained.
- Entry widths come from §2.a.
- Sizes are MODELED, per bank, with ×2 for ping-pong.

| Tile option | Entries per bank | Q8 rounded (44 b) | Q12 rounded (60 b) | Q8 exact u8 (68 b) | Front-to-back Q8 (36 b) | Prerequisite |
|---|---:|---:|---:|---:|---:|---|
| Current TILE=4 span (4×1) | 4 | 22 B | 30 B | 34 B | 18 B | Works only if each pixel is touched by a single span, which is false (up to 25 contributions/pixel). **Not a valid render target by itself.** |
| 8×8 tile | 64 | 352 B | 480 B | 544 B | 288 B | Tile-major stream (L2) or a 2D loop (L3). 8×8 list length per tile: mean 31.4, max 62 (benign), max 64 (fast) (MODELED-SW). |
| 16×16 tile | 256 | 1,408 B | 1,920 B | 2,176 B | 1,152 B | Same as 8×8. |
| Full 64×64 frame (single bank; ping-pong = 2 frames) | 4,096 | 22,528 B | 30,720 B | 34,816 B | 18,432 B | **Works with today's Gaussian-major stream (L1)** with no binning change, but needs frame-sized SRAM. |

- **Key fact for the decision:** a tile-sized buffer is only correct if the stream is
  tile-major.
- With the current stream, only a frame-sized accumulator renders correctly.
- Moving to a tile-major order is a change in testbench and golden-model binning. It
  changes the transaction order seen by the attribute cache, and so it can change
  MEASURED skip rate, cycles and bytes. Under the weighted sum it does not change PSNR.

---

## 3. Open decisions (NOT decided here)

### 3.1 Compositing formula

| | |
|---|---|
| **Undecided** | (i) Keep the current **sort-free normalized weighted sum** `C = Σαc / (w_bg + Σα)`, or (ii) switch to **depth-ordered front-to-back** `C = Σ c_i α_i Π_{j<i}(1−α_j)` (standard 3DGS, as in `ref_model/`'s legacy `GS_RASTERIZER` reference). |
| **Facts** | The weighted sum is commutative. The committed golden model, FP32 reference and **every committed PSNR/SSIM number** use it. The scene has **no depth attribute** and the stream has no sort stage. Front-to-back is order-dependent: on the benign scene, FP32 front-to-back in id order vs reverse-id order differs at **26.13 dB PSNR** (MODELED-SW). The order is not a detail; it would need a defined depth and a sort. RTL alphas composited front-to-back in id order vs an FP32 front-to-back reference give 41.28 dB (Q8) / 41.67 dB (Q12) (MODELED-SW, id order as a stand-in depth). These are not comparable to the 44.73 / 47.74 dB weighted-sum numbers. |
| **What it blocks** | Accumulator entry format (§2.a), the divider vs. no divider (§2.d), whether P2 is possible (§2.d), the golden-model renderer, and the reference for **all PSNR numbers** in Phases 4–5b. Choosing (ii) redefines the ground truth and needs a depth/sort stage (new scene field, a sort in the golden model, and a per-tile or global sort in hardware or software). |
| **Input needed from Prof. Wu** | Whether the thesis claims compare against standard 3DGS compositing (then (ii), with re-baselined quality numbers and a sorting story) or keep the sort-free weighted sum as an explicit, documented approximation (then (i), and all existing numbers remain valid). |

### 3.2 Color format and source

| | |
|---|---|
| **Undecided** | Source: C1 monolithic, C2 with base fields, C3 separate channel, or C4 on-chip table (§2.b). Per-Gaussian format: u8 / u16 / Q(7,F). Output pixel format: FP16 (SSIM_v2-native) / u8 / wider. |
| **Facts** | u8 per-Gaussian color costs ≤ 0.01 dB (MODELED-SW). The output pixel format dominates: u8 output costs 0.15 dB (Q8) / 0.28 dB (Q12), FP16 ≤ 0.002 dB. C4 costs 0 B/frame, but needs 12 KiB and assumes view-independent color. C1/C2 change `BYTES_PER_GAUSSIAN` and need cache widening. |
| **What it blocks** | Profiler byte formula and all re-run bandwidth numbers. Cache DW and SRAM bits (Tier 2 area). Renderer multiplier widths. The SSIM_v2 hand-off format. |
| **Input needed from Prof. Wu** | Whether view-dependent color (SH) is ever in thesis scope (rules C4 in or out). Whether DRAM-byte claims should include color. The required output format for the quality metric (FP16 for SSIM_v2, or a u8 display frame). |

### 3.3 Tile size and stream order

| | |
|---|---|
| **Undecided** | Keep the 4-px row span with a frame-sized accumulator (L1). Or go to 8×8 (or 16×16) tiles with a tile-major stream (L2) or a 2D hardware loop (L3). |
| **Facts** | Tile buffers are only correct with a tile-major stream (§2.f). 8×8 cuts transactions 148,360 → 16,078 and the no-skip upper bound 5.04 M → 1.51 M cycles (Q8, NUM_PE=1, MODELED). Its effect on the cache hit rate and on the MEASURED 82–93% byte reductions is **unknown** (not modeled). L3 changes the top's FSM and `out_alpha` width. |
| **What it blocks** | Buffer SRAM size, NUM_PE split (P1 vs P3), top FSM change or not, and comparability of Phase 5d cycles/bytes with Phases 3–5b. |
| **Input needed from Prof. Wu** | Whether the thesis may change the transaction definition, which re-baselines cycles and bytes (with old numbers kept as a labeled reference), or must stay comparable with the committed Phase 3–5b tables (then L1 + frame buffer). |

### 3.4 Minor (can be decided in the implementation PR)

- Accumulator rounding vs exact products (§2.a).
- Stall rule S1 vs S2 (§2.e); S1 is the low-risk default.
- Whether to add a 0.99 alpha clamp to the RTL to match the FP32 reference (§1.1). This
  changes the golden model and needs a labeled PSNR delta.

---

## 4. Draft acceptance criteria for the 5d implementation PR (DRAFT)

> **DRAFT. Contingent on §3.1–§3.3.** The bracketed choices `[...]` must be fixed by
> the Prof. Wu decisions before this becomes a task spec. Same funnel as `DEVIN.md` and
> the Phase 5a–5c specs: Tier 1 before Tier 2 before Tier 3.

### Scope

- New `rtl_4dgs/gs4d_tile_renderer.sv`: accumulator banks, pixel loop, resolve/drain,
  and `out_ready` generation per the chosen stall rule.
- An optional wrapper `gs4d_render_top.sv` = `gs4d_accelerator_top` + renderer.
- Color input per `[C1 | C2 | C3 | C4]`.
- The existing `gs4d_accelerator_top` ports and behavior are **unchanged** unless L3 is
  chosen. Any top change is gated by a parameter whose default is legacy.
- Golden model: add a bit-true fixed-point renderer
  (`gs4d_ref::Renderer`) implementing `[weighted sum | front-to-back]` with the chosen
  widths.
- Software `Accum` stays as the float reference.

### Tier 1: Verilator

1. `verilator --lint-only -Wall` on the renderer and the composed top: **0 warnings**.
2. **Bit-true renderer:** every accumulator entry at every flush, and every resolved
   pixel, equals the golden fixed-point renderer. **0 mismatches.** The test covers
   ≥ 10,000 cycles on the benign, pan and fast scenes at Q8 and Q12, with random
   `out_ready` / drain backpressure (the LFSR style of `sim_main_4dgs`).
3. **Quality, with the same label rules:**
   - Report PSNR from the **hardware-resolved** frame vs FP32, labeled MEASURED.
   - Report the delta vs the committed software-composited PSNR at the same point.
   - Gate DRAFT, to be fixed after §3.2: with `[u8 color, FP16 out]`, |ΔPSNR| ≤ 0.02 dB
     vs the MODELED-SW table in §2.c; with `[u8 out]`, ≥ committed − 0.35 dB.
   - Under front-to-back, gate against the new reference only, and state that the
     reference changed.
4. **SSIM_v2 on hardware frames:** `sim_main_ssim_frame` scores the renderer's FP16
   output. Report HW SSIM next to the software-composite SSIM.
5. **No regressions, byte-identical.** Run with the renderer disabled or not
   instantiated:
   - `make sim_4dgs`, `sim_4dgs_frame`, `eval_quality_4dgs`, `profile_bw_4dgs`,
     `stress_4dgs`, `conic_cliff_4dgs`;
   - CSV/RESULT lines must be byte-identical to `af43f4c`, or to the then-current main.
6. **Throughput:**
   - Report MEASURED total cycles including flush/stall, vs the MODELED no-overlap bound
     in §2.d.
   - Report the stall share. With ping-pong, flush stalls ≤ `[x]`% of cycles (DRAFT).
7. **Bandwidth:**
   - If color is streamed (C1–C3), update the profiler's byte formula with a new
     labeled column. Do not overwrite the existing column.
   - Report MEASURED counts × MODELED bytes, as in 5a.

### Tier 2: Yosys + ABC (generic cells)

1. `synth` + **CEC PASS** for the renderer alone and for the composed top at the default
   config (`NUM_PE=1, Q_FRAC=8, CACHE_WAYS=2`).
2. Report generic gates, FFs, AIG ANDs and levels.
   - Report the delta vs the committed 50,910-gate top (MEASURED, `docs/SYSTEM_BANDWIDTH_4DGS.md`), from
     the same toolchain only.
   - List accumulator/bank SRAMs as opaque macros with their bit counts.
3. Keep large configurations (NUM_PE ≥ 2, Q12, CACHE_WAYS=4) **out of the 120-minute CI
   job**, as in Phase 5b/5c. Report them from local runs, labeled.

### Tier 3: Z3 via `eda_core` (30 s timeout, 3 retries, COI slicing, then BMC-20)

1. **Accumulator safety:**
   - `den` and `num` cannot wrap: saturate or provably fit, over the declared max
     contributions per pixel `[N_max]` and the alpha range.
   - The resolve step's rounding and saturation match the spec. This covers the
     reciprocal/divider for the weighted sum, or `T` monotone non-increasing in [0, 1]
     for front-to-back.
2. **Bank-swap FSM:**
   - No write to the bank being drained.
   - Every accepted result is accumulated exactly once.
   - Swap only after the drain completes.
   - Deadlock-free (strictly decreasing rank).
   - Legal-state closure.
3. **Backpressure composition:**
   - Under `[S1]`, a renderer-induced `out_ready = 0` freezes the top.
   - The existing obligation is reused and the composition is shown.
   - No accept while stalled.
4. **Regression:** the existing **45/45** `formal_4dgs` obligations still pass.
   - With the top unchanged, they pass unchanged.
   - With L3, they pass under the legacy parameter, and the new obligations are
     documented.

### Deliverables

- `docs/TILE_RENDERER_4DGS.md` with the results above. Every number is labeled
  MEASURED / MODELED.
- CI job(s) within the time budget.
- No DesignWare files, no binary sim artifacts, no `config.yaml`.

---

## Appendix A: MODELED-SW harness (not committed)

An offline C++17 program, compiled with
`g++ -std=c++17 -O2 -Iref_model/include -Igolden_model`:
- It includes `gs4d_frame.hpp`.
- It runs `gs4d_ref::Accelerator(Config{1, Q, 2, 512, T}).step()` on exactly the
  `bin_spans`/`quantize` transaction list of `sim_main_4dgs_frame.cpp`. This is the
  `CACHE_WAYS=2` quality-sweep configuration.
- It uses golden `alpha[k] · 2^-Q`.
- It accumulates into `gs4d_frame::Accum` with the color replaced by the variant under
  test, then computes `psnr_db(mean_frame_mse)` vs `render_fp32(..., kExact)`, exactly as
  the testbench does.
- Output quantization is applied to the resolved frame:
  - u8: `round(v·255)/255`;
  - FP16: `from_half(to_half(v))`.
- Front-to-back rows run `C += T·α·c; T ·= (1−α)` per pixel in stream (id) order. The
  FP32 front-to-back reference uses `alpha_fp32(kExact)`, in id order and in reverse.
- Scene statistics (§1.4) are counted on the same accumulators.
- Alternative tilings (§2.d, §2.f) re-bin the same 3.5σ boxes with an 8-px step, or per
  8×8 tile overlap. They are counted only, never simulated.

Validation: the current-format rows reproduce the committed MEASURED PSNRs to 4
decimals (§ Number labels), and the transaction counts equal the committed ones.
