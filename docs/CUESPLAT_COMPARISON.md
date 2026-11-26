# CueSplat comparison: what it means for our deformation engine and temporal skip unit

Analysis only. No RTL, testbench, Makefile, CI or existing doc changes. Base: `main` at
`af43f4c`.

**Paper:** Q. Tang, H. Zhu, S. Sun, A. Wu, J. Leng, M. Guo, Y. Feng (Shanghai Jiao Tong
University), "CueSplat: Learning to Schedule Sparse Deformation for Dynamic Gaussian
Splatting on Edge Devices", **arXiv:2611.22095v1** [cs.CV], 17 Nov 2026. Section, table and
figure numbers below refer to that v1 PDF (10 pages). The supplementary material it cites
(Supp. Sec. B–E, Tbl. S3/S4) is not in the v1 PDF, so nothing here relies on it. The paper
text does not give a code link or any power or energy numbers.

**Our design** (names as in the repo; "Temporal Culling Unit" in the task is
`temporal_skip_unit`):

- `rtl_4dgs/deform_engine.sv`: combinational polynomial basis `[1, t, t²]`. 11 W×W products, 5 outputs.
- `rtl_4dgs/temporal_skip_unit.sv`: `skip_deform = |Δμx|+|Δμy|+|Δsx|+|Δsy| < motion_thresh`.
- `rtl_4dgs/motion_aware_cache.sv`: id-keyed set-associative cache of projected 2D attributes.
- `rtl_4dgs/gs4d_accelerator_top.sv`: FSM ACCEPT → LOOKUP → (PROJ | reuse) → RASTER → OUT.

## Labels used in this document

| Label | Meaning |
|---|---|
| **MEASURED / Jetson-SW** | CueSplat wall-clock FPS on Jetson AGX Orin 64 GB or Orin NX 16 GB, MAXN mode (Sec. 5.1) |
| **MEASURED / GPU-SW** | CueSplat on RTX A6000, or their server-side warp-instruction profiling |
| **MEASURED / RTL-sim** | Our cycle-accurate Verilator RTL counts and PSNR, bit-true vs golden model (0 mismatches) |
| **MODELED / RTL-sim** | Derived from RTL-sim counters with a fixed model: DRAM bytes = `fetch_count × BYTES_PER_GAUSSIAN`, miss-penalty cycles, FPS at a nominal 100 MHz |

**Correction to the task framing.** Our byte numbers are not measured AXI bytes. The
design has no AXI master, DRAM controller or memory model (`docs/SYSTEM_BANDWIDTH_4DGS.md`,
first paragraph). The ~21–23% figure is the **conservative Modeled External DRAM Traffic**
reduction: 20.4–22.6% over the T=1/3/16 Full Co-Design rows, and 20.5% (Q8) / 20.4% (Q12) at
T=1 in steady state [MODELED / RTL-sim]. The transaction counts behind it are measured in
RTL simulation. The byte cost per transaction is a model.

---

## 1. CueSplat's method, in our terms

CueSplat is a **post-training, software-only** scheduler. It runs on top of a frozen,
pretrained deformation backbone. The canonical Gaussians, the deformation network and the
Gaussian set stay unchanged; only a router is trained (Sec. 4.1).

| CueSplat component | What it does (paper ref) | Nearest thing in our design |
|---|---|---|
| Frozen deformation network D_φ | MLP/feature-grid backbones: E-D3DGS, 4DGaussians, GaGS, SpeeDe3DGS (Sec. 5.1) | `deform_engine` (polynomial basis; swappable for an MLP/HexPlane front-end per its interface contract) |
| History predictor P | Every Gaussian's next state is extrapolated from its 3 most recent accepted states. Damped finite differences for position, `λv = 0.85, λa = 0.15` (Eq. 2); damped quaternion increments for rotation; latest value for scale | None. Our unselected Gaussians reuse a stale *exact* result; nothing is extrapolated |
| Learned router R_θ | ~2.0k–2.6k-parameter MLP (Sec. 5.1). Scores each Gaussian from a frozen descriptor, timestamp, motion, visibility, contribution, update age and past residuals (Sec. 4.3) | `temporal_skip_unit`: a fixed L1 motion threshold on the current deformation offset |
| Budgeted Top-k selection | Exactly `k_s = ⌈K_s·G⌉` Gaussians get an exact deformation update per frame (Eq. 3). `K_s` is calibrated per scene offline, with a proxy-PSNR gate: mean ≥ 40 dB, P10 ≥ 37 dB (Sec. 4.4) | No budget. The skip count follows from the threshold |
| Index reuse | Router and Top-k rerun only every N = 5 frames. The index set is reused in between, and the selected Gaussians are still deformed exactly every frame (Sec. 4.2–4.3, Fig. 6b) | `motion_aware_cache`: reuses *results* (2D attributes) keyed by Gaussian id, not a selection list |
| Drift-triggered refresh | Drift score `d_t = 0.4Ē + 0.2r̄ + 0.2m̄ + 0.2a` (Eq. 6). After at least 20 frames, two consecutive frames with `d_t ≥ 0.6` schedule 3 full-deformation frames. A refresh is forced 40 frames after the previous refresh start. Targets are `τ+10 … τ+12`, with refresh precomputation spread over the 10 frames before (Sec. 4.5) | None. Our reuse never chains predictions, so error does not compound; no refresh exists (see §3, note on A) |

**Reported results** (all from the paper; every speedup is FPS over the same backbone with full deformation):

| Claim | Value | Where | Label |
|---|---|---|---|
| Deformation share of GPU pipeline time, E-D3DGS on N3DV | ~85% | Sec. 1 | MEASURED / Jetson-SW (AGX Orin) |
| Speedup range, AGX Orin, 12 backbone–dataset pairs | 1.75–6.74× | Sec. 5.2, Tbl. 1 | MEASURED / Jetson-SW |
| Peak AGX Orin speedup: GaGS, Technicolor | 10.65 → 71.79 FPS = **6.74×** | Tbl. 1 | MEASURED / Jetson-SW |
| Orin NX speedup range; peak (GaGS, Technicolor) | 1.81–8.15×; 3.79 → 30.91 FPS = **8.15×** | Sec. 5.2, Fig. 5b | MEASURED / Jetson-SW |
| A6000 peak speedup | 5.04× (GaGS, Technicolor: 45.23 → 228.05 FPS) | Sec. 5.2, Fig. 5a | MEASURED / GPU-SW |
| Worst dataset-average PSNR drop | **0.16 dB** (E-D3DGS, Technicolor: 34.07 → 33.91) | Abstract, Tbl. 1 | MEASURED (quality vs dataset ground truth) |
| Executed GPU warp instructions per frame | −29.9% to −90.7% | Sec. 5.2, Tbl. 1 | MEASURED / GPU-SW (server profiling) |
| Moving-camera fidelity, E-D3DGS, N3DV | 48.54 dB vs full-deformation renderings | Sec. 5.2 | MEASURED |
| Offline preparation per scene | 1–2 min on A6000, plus ~1 min per budget escalation | Sec. 5.1 | MEASURED / GPU-SW |

FPS is end-to-end wall clock: prediction, selection, deformation, rendering and refresh
(Sec. 5.1). The renderer is gsplat/AccuTile with standard front-to-back alpha compositing
(Sec. 4.1, 5.1). The method assumes sequential rendering at uniform time steps (Sec. 6).

## 2. Head-to-head vs our approach

### 2.1 The cost targets are not the same

The task says both designs target deformation cost. In the RTL at `af43f4c` that is not the case:

- **CueSplat skips evaluations of the deformation network.** That is the dominant cost
  on Jetson (~85% for E-D3DGS, Sec. 1).
- **Our skip never gates `deform_engine`.** The top-level header says: "The engine runs for every Gaussian (never gated by its
  own skip decision)". The skip decision is computed *from* the engine's outputs, so it
  cannot be used to avoid computing them.
- **What our skip and cache save** is the projection/conic path: 11 + 2F cycles of
  covariance, divide and reciprocal, with operand isolation. Under the model, they also
  save the parameter fetch for cache-served transactions. The engine is combinational and
  runs in parallel with input capture (`docs/QUALITY_VS_SKIP_4DGS.md` §4), so it is not our
  bottleneck.

The two methods are therefore **complementary, not competing**:

- CueSplat answers "which Gaussians must run the expensive network this frame".
- We answer "which Gaussians can reuse a projected 2D splat without recomputing or
  re-fetching it".

### 2.2 Numbers side by side, with conditions

| Axis | CueSplat | Ours (`af43f4c`) | Comparable? |
|---|---|---|---|
| Platform | Jetson AGX Orin / Orin NX / A6000 GPU, software | Cycle-accurate RTL simulation (Verilator), no silicon, no timing closure | No |
| Workload | Real datasets: Technicolor (5 seq.), N3DV (9), NeRF-DS (7); four trained backbones (Sec. 5.1) | One synthetic 64×64, 8-frame scene, 256 Gaussians (60% static), plus pan/fast stress variants (`QUALITY_VS_SKIP_4DGS.md` §2, §6) | No |
| Deformation model | MLP/feature-grid networks, ~85% of frame time (Sec. 1) | Quadratic polynomial basis, combinational | No |
| Compositing | Sorted front-to-back alpha (gsplat) | Sort-free normalized weighted sum in the testbench; the RTL emits per-Gaussian α | No |
| Quality reference | Dataset ground-truth images; drop vs the full-deformation backbone | FP32 renderer of the same synthetic scene; drop vs our T=0 | Only as "drop vs own full-compute baseline" |
| Best lossless point | Not offered: always ≥ 0 dB drop, up to 0.16 dB dataset average (Tbl. 1) | **T=1: ΔPSNR 0.0000 dB** at Q8 and Q12, HW SSIM identical [MEASURED / RTL-sim] | Yes, on the qualitative claim "lossless vs lossy" |
| Speed metric | End-to-end FPS ratio, 1.75–6.74× (AGX Orin) [MEASURED / Jetson-SW] | Compute-only cycle ratio, no memory stalls: 3.02× (Q8, T=1), 3.21× (Q12, T=1), up to 3.80× (Q8, T=64, −0.25 dB) [MEASURED / RTL-sim] | **No.** Different numerator, denominator and platform |
| Memory metric | Not reported | Modeled DRAM read bytes: −84.2% (Q8, T=1, 8-frame), −82.2% steady; conservative −20.5% (Q8) / −20.4% (Q12) at T=1 steady [MODELED / RTL-sim] | No CueSplat counterpart |
| Camera motion | 48.54 dB vs full deformation on moving cameras, unchanged routers (Sec. 5.2) | Global pan: hit rate falls to 12.0%, traffic to 88.0% of baseline, speedup to 1.10× (Q8) [MODELED / MEASURED RTL-sim] | Qualitatively, yes (see below) |
| Training / calibration | Per scene–backbone–budget router training, 1–2 min on A6000 (Sec. 4.4, 5.1) | None: one quasi-static threshold register | Yes, as a deployment-cost difference |
| Formal guarantees | None stated | Z3: `motion_thresh == 0` ⇒ no skip; `cache_bypass` ⇒ cache-independent datapath (45/45 obligations) | n/a |

**Where CueSplat's numbers are stronger:**

- **Magnitude of end-to-end speedup** on the platform that matters for its users: up to
  6.74× / 8.15× measured FPS on Jetson, on real scenes with real networks. We have no
  end-to-end FPS, no real-scene workload and no silicon.
- **Camera motion.** CueSplat reuses view-independent deformation state (before
  projection), so a moving camera does not invalidate its reuse (Sec. 5.2).
  - Our cache stores view-dependent 2D post-projection attributes.
  - Our skip metric is the offset from the canonical pose. A global pan therefore pushes
    every Gaussian past T and collapses the reuse (`SYSTEM_BANDWIDTH_4DGS.md`, Phase 5b).
  - Caveat: our pan is synthesized as extra `mu(t)` terms, because the pipeline is 2D
    only. A real camera pan would hit the same projection-level invalidation.
- **Evidence for its selection rule.** Replacing the learned router with motion-magnitude
  ranking (the family our threshold belongs to) costs **−1.44 dB** mean PSNR on Technicolor
  (32.50 → 31.06, Tbl. 3) [MEASURED / Jetson-SW].
  - This is evidence *against* motion-only selection, but in CueSplat's setting:
    unselected Gaussians are extrapolated and the errors compound.
  - In our setting, a skipped Gaussian reuses the exact result of its last recompute
    (no extrapolation, no chaining), so the evidence does not carry over directly.

**Where our numbers are stronger, or where only we have numbers:**

- **Exactly lossless operating point.** At T=1 (`delta < 1` ⇔ `delta == 0`), 84.2% (Q8)
  and 82.6% (Q12) of transactions are reused with 0.0000 dB change [MEASURED / RTL-sim].
  CueSplat has no zero-loss mode except full deformation.
- **Memory traffic is quantified**, even if modeled: 80–84% headline steady-state
  reduction, 20.4–22.6% conservative [MODELED / RTL-sim]. CueSplat reports FPS and warp
  instructions, not bytes.
- **No training, no scene-specific calibration**, and formal proofs of the lossless and
  bypass modes.
- **No compounding error without refresh**: reuse never chains predictions (§3, note on A).

**A caveat that cuts against us.** A large share of our reuse happens *inside* a frame.
- Each Gaussian is streamed as several 4-pixel span transactions with the same id, and
  every span after the first hits the cache.
- Frame 0 has a cold cache and still fetches only 256 of ~18.5k transactions
  (`SYSTEM_BANDWIDTH_4DGS.md`).
- CueSplat counts work per Gaussian per frame, so it has no counterpart to this reuse.

A per-Gaussian, per-frame comparison would need our skip rate re-counted over unique
(Gaussian, frame) pairs.

## 3. What to adopt or borrow

| # | Candidate | Kind | One-line cost / benefit | Conflicts with sort-free? |
|---|---|---|---|---|
| A | **Drift- or age-triggered full refresh** instead of relying only on a fixed T | RTL change (small) | Cost: a few-bit age counter per cache entry (or per set) plus a frame-level aggregate in `motion_aware_cache`/top. Benefit today: guards the far-offset stale-entry case (see note). Becomes necessary if we switch to a frame-relative metric (candidate D) | No |
| B | **Router-style budgeted Top-k selection** instead of the threshold skip | RTL change (large) plus a learned/offline component | Cost: scoring MLP (~2.0k–2.6k parameters, Sec. 5.1), per-Gaussian feature state, and a global Top-k over G scores every N frames. Benefit: a predictable per-frame budget and +1.44 dB over motion-only ranking in their setting (Tbl. 3) | **Yes, partly**: exact Top-k is a global ranking pass. A two-pass histogram threshold (count scores per bin, pick the cut bin) avoids a full sort but needs a second sweep over the Gaussians |
| C | **Index reuse across frames** (select every N = 5 frames, reuse the list) | Software idea; RTL only as a scheduler | Cost: a stored list of `k_s` indices plus a gather-style fetch, versus our streaming id-keyed lookup. Benefit: amortizes selection (+10.0% FPS going from N=1 to N=5, Fig. 6b), which matters only if selection is expensive (i.e. with B). With a 1-cycle threshold compare we have nothing to amortize | No, but a list-driven gather breaks our in-order streaming interface |
| D | **Decide the skip before deformation** (history/router-based), so that `deform_engine` itself can be gated | RTL change (moderate); prerequisite for attacking CueSplat's cost target | Cost: one or more prior states per Gaussian in SRAM or DRAM (CueSplat keeps three, Eq. 2), and a skip metric that no longer depends on the engine's output. Benefit: lets an MLP/HexPlane front-end be skipped, which is where CueSplat's whole speedup comes from | No |
| E | **History extrapolation for unselected Gaussians** (Eq. 2) instead of stale reuse | RTL change (moderate) | Cost: 3 states per Gaussian of storage and bandwidth, plus a small add/multiply datapath. Gives up exact losslessness. Benefit: +0.21 dB over hold-last in their setting (Tbl. 3) | No |
| F | **Reuse at the view-independent (pre-projection) level** | Architectural RTL change; needs a 3D projection stage we do not have | Cost: a new pipeline stage and a wider cached record. Benefit: fixes our pan collapse (12.0% hit rate); CueSplat's moving-camera result (Sec. 5.2) suggests this is the right reuse level | No |
| G | **Scene-specific budget calibration with a proxy-PSNR gate** (Sec. 4.4) | Software / offline methodology | Cost: an offline sweep per scene. Benefit: picks T per scene instead of the global knee (Q8 T=16, Q12 T=64). Directly reusable with our `eval_quality_4dgs` sweep | No |
| H | **Refresh precomputation** spread over the 10 preceding frames (Sec. 4.5) | Software scheduling idea | Benefit: FPS P5 17.68 → 26.20 (+48.2%) at −0.3% average FPS (Tbl. 4). For us it matters only after adopting A, to avoid fetch bursts | No |

**Note on A: our current design has staleness, not drift.**
- CueSplat's predicted states feed later predictions, so errors compound. Their
  no-refresh ablation is −0.21 dB on N3DV (25.71 → 25.50, Tbl. 4), and a 1-frame refresh
  is −0.38 dB vs 3 frames (Tbl. 5).
- Our reuse never chains. A hit returns the entry written at the Gaussian's most recent
  exact recompute (every recompute writes the cache), and a reuse writes nothing. The
  error is therefore |offset now − offset at last recompute| and does not grow with
  repeated reuse.
- It is **not** bounded by T alone. The last recompute may have happened at an offset
  ≥ T, e.g. a Gaussian that moves away and then returns near its canonical pose. In
  that case, a later frame with offset < T reuses that far-offset entry.
  - The benign, pan and fast scenes do not show this as a PSNR cliff (`QUALITY_VS_SKIP_4DGS.md` §3, §6).
  - The scenes do not target this case, though.
  - An age bound (candidate A, CueSplat's `a_t` term uses 20 frames) is the cheap guard.
- A drift-style refresh becomes necessary only if the metric becomes frame-to-frame (one
  way to fix pan), because then small per-frame errors can add up.
- (Reasoning from the RTL, not a measured result.)

**Recommended order, if any are pursued:** G (zero RTL), then D (needed before an MLP
front-end makes deformation the bottleneck), then F (pan). Treat B/C/E as research
options to discuss, not commitments: B partly conflicts with sort-free streaming, and E
gives up losslessness.

## 4. Thesis baseline recommendation

**Recommendation: yes, but as the state-of-the-art *software* baseline for deformation
sparsity, not as a number we claim to beat.**

Reasons to include it:

- It is the closest prior work: edge-targeted, deformation-reuse, post-training, and recent
  (17 Nov 2026). A committee will ask about it.
- It makes our differentiators concrete:
  - exactly lossless mode, versus up to 0.16 dB loss;
  - hardware bandwidth reduction, which CueSplat does not report;
  - no per-scene training;
  - formal guarantees.
- It exposes our gaps honestly (deformation not gated, camera motion, synthetic workload).
  It is better to name these in the proposal than to have them raised at the defense.

Reasons not to make it a *quantitative head-to-head* in the frozen proposal:

- None of the metrics line up today (§2.2): FPS on Jetson vs compute-only RTL cycles;
  real datasets vs one synthetic scene; MLP backbones vs a polynomial basis; sorted vs
  sort-free compositing.
- Without code (no link in the paper text), reproducing CueSplat means re-implementing
  four backbones plus router training.

What a fair hardware comparison against CueSplat would require:

1. **Same deformation backbone.** Implement at least one of E-D3DGS or 4DGaussians in the
   `deform_engine` slot, so deformation is the dominant cost, as in Sec. 1.
2. **Same workload.** Trained checkpoints on at least N3DV (the dataset in Sec. 1, Tbl. 4,
   Fig. 6), with the same frame counts and uniform time steps.
3. **Same quality metric.** Dataset-average PSNR/SSIM/LPIPS vs ground truth, plus tOF/tLP
   (Sec. 5.1). Use the same compositing order, or quantify the sort-free vs sorted gap
   separately.
4. **Same scope of time.** End-to-end frame time including memory stalls: an AXI/DRAM
   model or a measured FPGA/ASIC implementation, not compute-only cycles. Our miss-penalty
   model is a bound, not a substitute.
5. **Iso-platform or normalized platform.**
   - Either run both schedulers on one target: CueSplat's router/predictor on our
     hardware (candidates B/D/E), or our threshold rule in their software on Jetson;
   - or report energy per frame and area/power-normalized throughput against Jetson power
     in MAXN mode, measured, not datasheet.
6. **Same lossy budget.** Compare at matched ΔPSNR, e.g. our T at ≤ 0.16 dB vs CueSplat's
   `K_s`, and report our lossless T=1 point separately.

## 5. Open questions for Prof. Wu (direction freeze before 2026-11-30)

1. **Thesis claim scope.** Is the contribution "lossless hardware reuse + bandwidth
   reduction" (what we have), or "deformation-compute reduction" (CueSplat's target, which
   our RTL does not currently reduce)? This decides whether candidate D is in scope.
2. **Deformation front-end.** Should the frozen plan commit to an MLP/HexPlane front-end,
   so deformation becomes the bottleneck as in CueSplat Sec. 1? Or stay with the
   polynomial basis and argue projection/bandwidth as the edge bottleneck?
3. **Workload.** Is the synthetic 64×64 scene enough for the thesis, or is a real-dataset
   pipeline (N3DV checkpoints) required? It is also the prerequisite for any CueSplat
   comparison.
4. **Baseline framing.** Is CueSplat cited as related work plus a qualitative comparison
   table (this document's recommendation), or required as a quantitative baseline with a
   re-implementation? Is Jetson Orin hardware available to us?
5. **Lossless vs lossy.** Keep exactly-lossless T=1 as the headline, or set a lossy
   target (e.g. ≤ 0.16 dB, to match CueSplat's worst case) and tune T per scene
   (candidate G)?
6. **Camera motion.** Is fixing the pan collapse (12.0% hit rate) in scope? The clean fix
   is pre-projection reuse (candidate F), which needs a 3D projection stage.
7. **Learned components.** Is an offline-trained, scene-specific router acceptable in a
   hardware thesis, given that it costs 1–2 min A6000 preparation per scene (Sec. 5.1)
   and needs a global Top-k that partly conflicts with sort-free streaming?
8. **Memory evidence.** Before the freeze, should "Modeled External DRAM Traffic" be
   upgraded to measured bytes (AXI4-Stream wrapper + memory model, the Phase 5b follow-up
   named in `SYSTEM_BANDWIDTH_4DGS.md`)? The conservative 20.4–22.6% is the defensible
   figure today.
