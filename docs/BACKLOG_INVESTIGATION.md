# Backlog investigation: PR #1 33 pixels, `depth_stop_tail`, GS opaque 48 vs 49, multiplier miter UNKNOWN

Base: `main` @ `af43f4c`. Toolchain: Verilator 4.038, Yosys 0.23 (`7ce5011c24b`), Berkeley ABC,
sv2v, Z3 4.13.0 (Python), clean-room DesignWare simulation substitutes only.

Every number is labelled **MEASURED** (produced by running the RTL, a tool or the repo's
comparison scripts on this commit) or **MODELED** (produced by the C++ golden model or an
analysis, not by the RTL). Golden = the lab's `ref_model/verif/golden/gs_rasterizer/golden_ans`.
Pixel `i` is `(px, py) = (i % 75, i / 75)`; "tolerance" is the TB.sv tolerance used by
`ref_model/verif/golden_compare.py`.

| # | Item | Verdict |
|---|---|---|
| 1 | 33 unexplained pixels | **Root cause found.** Two golden rules the RTL does not implement: 3DGS alpha clamp `alpha = min(0.99, o * G)` (19 px), and "crossing Gaussian is not blended into RGB" (9 px); 5 px need both. With both rules the model matches the golden on all 2550 px within tolerance. |
| 2 | `depth_stop_tail` | **Confirmed** against the RTL (RTL always adds depth, as the flag's ON state models), but **mis-scoped** on the golden side: the golden skips the crossing Gaussian for RGB as well, not depth only. |
| 3 | GS opaque 48 vs 49 | **Root cause found.** `sram2` (`SRAM_2550x99`, frame buffer) has an unread output; Tier 2 marks `SRAM_*` `keep`, Tier 3 does not, so Yosys removes it in Tier 3. |
| 4 | Multiplier miter UNKNOWN | **Diagnosed: solver limitation on multiplier equivalence, not state space.** The module has 0 registers. Fixed by a new complete exhaustive proof over all 2^24 operand pairs. The obligation is unchanged. |

**Status: items 1–4 are implemented in the Phase 5e PR** (base `main` @ `b86da86`): RTL alpha
clamp and crossing gate, golden-model `rgb_stop_tail` / `alpha_clamp`, Tier 3 SRAM `keep`, and
`make prove_products` in CI. See "Fixed in Phase 5e" at the end. The sections below are the
original PR #13 investigation (on `af43f4c`, report-only there); their "Proposed fix" text
describes the state before Phase 5e.

## Item 1: the 33 pixels left with all 7 `RtlQuirks` OFF

### Reproduction (MEASURED)

`bash ref_model/verif/run_golden.sh` on `af43f4c`:

```
vs golden      N exact/tol   T exact/tol   D exact/tol RGB exact/tol    pixels (tol)
q000_snrd            12/12        2446/4       2025/23       2250/14              33
```

The same script's RTL check passes: `GS_RASTERIZER_pipeline: 10863 cycles, 2550 pixels written,
0 mismatching pixels` against `q111` (all quirks on).

### Root cause

`golden_ans` follows the standard 3DGS forward pass. Two of its rules are absent from both the
RTL and the `RtlRasterizer` model, and no `RtlQuirks` flag covers them:

1. **Alpha clamp (golden only).** Golden uses `alpha = min(0.99, conw * exp(beta))`. The RTL takes
   stage-7 `DW_fp_mult S7_MULT0` (`g_s7.conw * alpha_term_s7`,
   `rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v:753-764`) straight into `alpha_i_s8` with
   no clamp. The model does the same at `ref_model/include/gs4d/rasterizer.hpp:280`
   (`n.ai8 = p.mul(r.g7.conw, r.at7, ...)`). The repo's own 4DGS frame golden already applies the
   clamp (`golden_model/gs4d_frame.hpp:179`, `std::min(0.99f, opa * g)`).
   - If alpha > 0.99, the RTL drives T about 10x lower than the golden. The `T < 1e-4`
     crossing then moves earlier, so N stops early and the depth tail changes.
   - Across all 2550 px, 61 px have at least one Gaussian with raw alpha > 0.99 (MODELED,
     sequential float32 scan in RTL order). 27 of the 33 failing pixels are among them.
2. **The crossing Gaussian is not blended into RGB (golden only).** In 3DGS, a Gaussian that
   drives `T*(1-alpha) < 1e-4` is not blended (`done = true; continue`). The RTL's stage-10
   update (`GS_RASTERIZER_pipeline.v:343-350`) writes `s10_r/g/b/d` for every committed
   Gaussian that is not `alpha_too_small`/`beta_positive`. The crossing test (`S10_CMP0`,
   `:924-925`, `T_test_s10 < 32'h38D1B717`) only sets `early_termination` (`:958-959`). It never
   gates the RGB write. The model already skips the crossing Gaussian for **depth** when
   `depth_stop_tail` is OFF (`rasterizer.hpp:219`), but always blends it into RGB
   (`rasterizer.hpp:216-218`).
   - On the failing pixels, the crossing Gaussian adds 0.6–2.3 LSB of red (MODELED). After
     float-to-int truncation that is a +1..+3 RGB error.

### Experiment (MEASURED via `golden_compare.py`; candidate model rules are MODELED)

Two candidate flags were added to a private copy of `rasterizer.hpp`:
- `alpha_clamp`: after `n.ai8 = ...`, `if (alpha_clamp && 0.99f < ai8) ai8 = 0.99f` (`0x3F7D70A4`).
- `rgb_stop_tail = false`: gate the three RGB adds with the same `!(r.crossed || cross10)` that
  `depth_stop_tail` uses.

Each row adds rules to the existing `q000_snrd`:

| config | N exact/tol | T exact/tol | D exact/tol | RGB exact/tol | px beyond tol |
|---|---|---|---|---|---|
| `q000_snrd` (as merged) | 12/12 | 2446/4 | 2025/23 | 2250/14 | **33** |
| + `alpha_clamp` | 0/0 | 2445/0 | 2005/0 | 2249/14 | 14 |
| + RGB stop tail | 12/12 | 2446/4 | 2025/23 | 2200/4 | 24 |
| + both | 0/0 | 2445/0 | 2005/0 | 2198/0 | **0** |
| + both, but `depth_stop_tail` ON | 0/0 | 2445/0 | 1310/1310 | 2198/0 | 1310 |

- The remaining exact mismatches (T 2445, D 2005, RGB 2198) are all within tolerance. They come
  from last-ulp `exp`/operation-order differences, not from a rule.
- The last row shows depth must also skip the tail. The golden behaves like the
  `depth_stop_tail` OFF state.

### Per-pixel trace (values MEASURED from the comparison CSV; alpha/crossing columns MODELED)

The golden and RTL-model columns are `q000_snrd`. "Max alpha" is the largest raw alpha and
its position `i` in the sorted list. "Crossing" gives the position `i` of the Gaussian that drives
T < 1e-4, its alpha, and its red contribution in LSB (-1 means no crossing before 1089).
"Fixed by" names the rule(s) that bring the pixel within tolerance.

| px | (x,y) | golden vs RTL-model | max alpha @i | crossing i / alpha / dR | fixed by |
|---|---|---|---|---|---|
| 377 | (2,5) | D 4059BD54 vs 4059B876; RGB 232 212 205 vs 234 214 207 | 0.9946 @690 | 690 / 0.9946 / 1.03 | both |
| 378 | (3,5) | RGB 232 212 205 vs 234 213 206 | 0.9855 @690 | 690 / 0.9855 / 0.96 | rgb tail |
| 389 | (14,5) | N 847 vs 628 | 0.9979 @587 | 621 / 0.2512 / 0.01 | clamp |
| 398 | (23,5) | N 533 vs 523; D 40594A0A vs 4059545F | 0.9944 @510 | 514 / 0.4070 / 0.01 | clamp |
| 535 | (10,7) | N 1089 vs 957; D 405A77D4 vs 405A74A4 | 0.9932 @661 | 936 / 0.0176 / 0.00 | clamp |
| 839 | (14,11) | RGB 226 206 200 vs 228 208 202 | 0.9823 @656 | 656 / 0.9823 / 1.21 | rgb tail |
| 859 | (34,11) | RGB 222 203 198 vs 224 204 199 | 0.9760 @471 | 471 / 0.9760 / 0.71 | rgb tail |
| 1136 | (11,15) | D 40599695 vs 405990CE; RGB 224 204 199 vs 226 206 201 | 0.9940 @742 | 742 / 0.9940 / 1.39 | both |
| 1187 | (62,15) | RGB 211 194 190 vs 213 196 192 | 0.9918 @302 | 302 / 0.9918 / 1.39 | rgb tail |
| 1188 | (63,15) | RGB 212 194 190 vs 213 195 192 | 0.9911 @302 | 302 / 0.9911 / 0.64 | rgb tail |
| 1205 | (5,16) | D 405A2509 vs 405A2125; RGB 221 202 199 vs 223 204 201 | 0.9923 @813 | 813 / 0.9923 / 1.75 | both |
| 1276 | (1,17) | D 405BE0D1 vs 405BEB69 | 0.9954 @841 | 856 / 0.9557 / 0.04 | clamp |
| 1277 | (2,17) | N 863 vs 857; D 405BD1A3 vs 4056ECE3 | 0.9963 @841 | 841 / 0.9963 / 4.99 | clamp |
| 1278 | (3,17) | N 863 vs 857; D 405BD9CB vs 405936E9 | 0.9923 @841 | 841 / 0.9923 / 2.68 | clamp |
| 1410 | (60,18) | D 40573DE1 vs 40573980; RGB 212 194 190 vs 214 196 192 | 0.9991 @347 | 347 / 0.9991 / 1.17 | both |
| 1488 | (63,19) | RGB 212 195 190 vs 213 196 192 | 0.9756 @354 | 354 / 0.9756 / 0.62 | rgb tail |
| 1517 | (17,20) | N 1089 vs 696; D 405A54DD vs 405A3F02 | 0.9978 @620 | 686 / 0.4824 / 0.00 | clamp |
| 1558 | (58,20) | D 405A0C7C vs 405A0A14 | 0.9919 @386 | 388 / 0.3353 / 0.01 | clamp |
| 1563 | (63,20) | RGB 212 194 190 vs 213 196 191 | 0.9891 @354 | 354 / 0.9891 / 1.40 | rgb tail |
| 1580 | (5,21) | N 873 vs 848; D 405B6899 vs 405B71B2 | 0.9911 @767 | 845 / 0.6517 / 0.01 | clamp |
| 1598 | (23,21) | D 40597FB2 vs 4059832A | 0.9953 @568 | 571 / 0.8584 / 0.00 | clamp |
| 1601 | (26,21) | N 612 vs 572; D 405977E0 vs 40594431 | 0.9935 @551 | 568 / 0.9235 / 0.06 | clamp |
| 1602 | (27,21) | N 612 vs 572; D 405A55F2 vs 405A357C | 0.9938 @551 | 568 / 0.8729 / 0.03 | clamp |
| 1638 | (63,21) | N 363 vs 358; D 40589B0D vs 405617F4 | 0.9925 @354 | 354 / 0.9925 / 2.55 | clamp |
| 1695 | (45,22) | T 39CF782A vs 399826D8; D 40577C63 vs 4057684B | 0.9927 @403 | -1 | clamp |
| 1833 | (33,24) | D 405A369D vs 405A3192 | 0.9901 @504 | -1 | clamp |
| 1843 | (43,24) | T 3B1EA523 vs 3ACE7FEE; D 40597C01 vs 4058DB23 | 0.9935 @429 | -1 | clamp |
| 1967 | (17,26) | D 4059BC11 vs 405A1C67 | 0.9975 @701 | 703 / 0.9667 / 0.14 | clamp |
| 2456 | (56,32) | N 1089 vs 796; T 39D1EC08 vs 38C4A510; D 405953EE vs 405918E0 | 0.9978 @543 | 762 / 0.1260 / 0.00 | clamp |
| 2495 | (20,33) | D 4059B6A6 vs 4059AD12; RGB 253 253 253 vs 255 255 255 | 0.9979 @773 | 773 / 0.9979 / 1.43 | both |
| 2498 | (23,33) | RGB 252 252 252 vs 255 255 255 | 0.9928 @734 | 734 / 0.9928 / 2.29 | rgb tail |
| 2531 | (56,33) | N 1089 vs 564; T 399E1EA0 vs 37A46E9A; D 40596212 vs 4047FA3D | 0.9998 @543 | 543 / 0.9998 / 17.27 | clamp |
| 2548 | (73,33) | RGB 207 191 188 vs 208 193 189 | 0.9882 @378 | 378 / 0.9882 / 0.85 | rgb tail |

Totals: 19 clamp only, 9 RGB stop tail only, 5 both (MEASURED).

### Test vectors (inputs from the lab files `ram_file.txt` / `gs_value.txt`)

- **Clamp, failing today:** pixel 2531 = (56,33), screen (1031, 67). Sorted position 543 is splat
  518 (`gs_value.txt` line 544 = `01000000110`). Its raw alpha is 0.9998 (MODELED).
  - RTL rule: T falls below 1e-4 at position 543 and N ends at 564.
  - Golden: N = 1089, T = 0x399E1EA0 (3.0159e-4).
  - With the clamp, the model matches the golden within tolerance (MEASURED compare).
- **RGB tail, failing today:** pixel 2498 = (23,33). The crossing Gaussian at position 734 has
  alpha 0.9928 and adds 2.29 LSB (MODELED).
  - RTL-model RGB 255 255 255 vs golden 252 252 252.
  - With the RGB tail rule the pixel passes (MEASURED compare).
- **Passing control:** pixel 0 has no alpha > 0.99 (max 0.9079 at position 621, MODELED). It is
  within tolerance in the first four configurations above. It fails only depth in the last row,
  as the depth tail predicts.

### Proposed fix (report only)

This is a golden-model semantics change, so it is reported and not implemented (when in doubt,
report only).

- **Patch to `ref_model/include/gs4d/rasterizer.hpp`:**
  - Add `bool alpha_clamp = false;` and `bool rgb_stop_tail = true;` to `RtlQuirks`, both
    defaulting to RTL behaviour.
  - After `:280`, insert
    `if (q_.alpha_clamp && p.lt(p.cst(0x3F7D70A4u, Sig::kAlpha), n.ai8)) n.ai8 = p.cst(0x3F7D70A4u, Sig::kAlpha);`.
  - Wrap `:216-218` in `if (q_.rgb_stop_tail || !(r.crossed || cross10)) { ... }`.
  - In `tools/gs4d_render.cpp`, add a `q000_snrdc` render with `alpha_clamp = true,
    rgb_stop_tail = false`.
- **Effect on the 7 flags and on bit-true behaviour:**
  - The defaults do not change any existing prediction. `q111` stays bit-true to the RTL:
    MEASURED 0 mismatching pixels for RTL vs patched `q111`, both with the new defaults and
    with only `rgb_stop_tail` flipped. That works because `sticky_early` lets only pixel 0
    cross, and its crossing RGB contribution truncates away.
  - The new flags are golden-side rules, not RTL quirks. Their "RTL" values are
    `alpha_clamp = false` and `rgb_stop_tail = true`.
  - `depth_stop_tail` and `rgb_stop_tail` describe one 3DGS rule ("do not blend the crossing
    Gaussian"). They could be merged into one `stop_tail` flag later; that would rename a PR #1
    flag, so it is left to review.
- **RTL fix, if the lab wants golden behaviour.** Not implemented: it changes the lab
  baseline's bit-true contract.
  - Add a `DW_fp_cmp` + mux after `S7_MULT0` (`alpha_i_s8_next = min(0.99, z)`).
  - Gate the stage-10 RGB/depth writes (`:343-350`) with `!s10_altb`.

## Item 2: `depth_stop_tail` against the baseline RTL

**Verdict: confirmed (RTL side), mis-scoped (golden side).**

RTL evidence (`rtl_baseline/gs_rasterizer/GS_RASTERIZER_pipeline.v`):

- `:877` `S9_MULT3` computes `d_term = T_accum_s9 * depth_i_3_s9`. `:945-952` `S10_ADD3`
  computes `s10_d = current_pixel.depth + d_term`.
- `:343-350`: if `stage_valid_10 && !(alpha_too_small_1_s10 || beta_positive_3_s10)`, then
  `current_pixel_next.depth = s10_d`. Nothing in this condition depends on `s10_altb` /
  `early_termination`.
- `:924-925, :958-959`: the `T < 1e-4` comparison only drives `early_termination`, which the FSM
  uses to leave `PROCESS`. By then the crossing Gaussian, plus whatever is already in stages 1–10,
  has been accumulated into depth (and RGB).
- So the RTL behaves as `depth_stop_tail = true`. That is the flag's documented RTL state.

Measured vector:

- The Tier 1 run's RTL output (`rtl_out`, Verilator + clean-room substitutes) matches model
  `q111` (all 7 quirks ON) on 2550/2550 px.
- With only `depth_stop_tail` flipped OFF (all other quirks ON), exactly 1 px differs: pixel 0
  depth, RTL `0x4080587B` (4.0108) vs model `0x408051DA` (4.00999) (MEASURED).
- Pixel 0 is the only pixel where the lab vector exercises this rule: `sticky_early` keeps
  `early_termination` set after pixel 0, so no later pixel crosses in the RTL.
- The model attributes the difference to the crossing Gaussian at position 627 (splat 22,
  alpha 0.7219, MODELED) and the Gaussians committed after it.

Golden side: the golden does skip the tail for depth (last row of the item 1 table: turning
`depth_stop_tail` back ON creates 1310 depth failures, MEASURED). It also skips the crossing
Gaussian for RGB (item 1, 14 px). So the inferred rule is right for depth but incomplete. It is
one 3DGS rule that covers all colour and depth accumulators; T and N still include the Gaussian.

**Proposed fix (report only):** the `rgb_stop_tail` flag from item 1.
- Coverage: one lab-vector pixel exercises the rule. Before relying on it beyond pixel 0, add a
  directed Verilator vector: a tile with 3 Gaussians of alpha 0.9 / 0.995 / 0.5 on one pixel,
  with `sticky_early` irrelevant at pixel 0. Expected RTL depth = the sum of all three terms
  (MODELED).

## Item 3: GS opaque cells, Tier 3 = 48 vs Tier 2 = 49

**Verdict: root cause found. Tier 3 drops `sram2` (`SRAM_2550x99`).**

Instance diff (MEASURED, Yosys 0.23 JSON, both flows rerun on `af43f4c`):

| cell type | Tier 2 `pre.json` / `mapped.json` | Tier 3 `gs_rasterizer.yosys.json` |
|---|---|---|
| DW_fp_mult / add / sub / flt2i / i2flt / cmp / exp | 24 / 9 / 5 / 3 / 2 / 2 / 1 | 24 / 9 / 5 / 3 / 2 / 2 / 1 |
| SRAM_1089x11, SRAM_1089x384 | 1, 1 | 1, 1 |
| **SRAM_2550x99 (`sram2`)** | **1** | **0** |
| total opaque | 49 | 48 |

Why:

- `sram2` is the output frame buffer (`GS_RASTERIZER_pipeline.v:50`). The RTL only writes it
  (`din2/we2` at `:1016-1022`, `addr2` at `:262-285`) and never reads `dout2`.
- Tier 2 (`scripts/synth/baseline.py:126`) runs `setattr -set keep 1 t:SRAM_*` before `synth`.
- Tier 3 (`scripts/formal/rtl_to_netlist.py:188-201`) has no such `keep`, so Yosys removes the
  instance as a cell with no used outputs. Running the Tier 3 script step by step (MEASURED),
  `select -count t:SRAM_2550x99` is 1 after `synth -run begin:coarse` and 0 after
  `-run coarse:fine`.
- Confirmed by adding `setattr -set keep 1 t:SRAM_*` to a copy of the generated Tier 3 `.ys`:
  49 opaque cells, flip-flops unchanged at 2729 (MEASURED).

Related flip-flop delta, 2732 vs 2729:
- Tier 2 runs FSM extraction/recoding (`synth` without `-nofsm`); Tier 3 uses `-nofsm`.
- Removing `-nofsm` from a copy of the Tier 3 script gives 2732 FFs (MEASURED). The opaque delta
  and the FF delta are independent.

Impact: with `sram2` gone, the `din2` and `we2` nets are also absent from the Tier 3 netlist
(MEASURED, JSON netnames). `addr2`, `int_r` and `depth_final_sum` remain because other logic or
ports still read them. No Tier 3 property can observe frame-buffer writes; no current property
refers to `sram2`. The two flows still disagree on the documented count.

**Proposed fix (report only):** the fix belongs in `scripts/formal/rtl_to_netlist.py`, which
PR #10 owns.
- Patch: in the `reads` list (`:188-195`), append `"setattr -set keep 1 t:SRAM_*"` for
  `gs_rasterizer` (and SSIM, for symmetry).
- Then update `docs/FORMAL_VERIFICATION.md` to 49 opaque cells, or document the 48 as
  "write-only frame buffer elided".

## Item 4: direct multiplier miter returns UNKNOWN at W=12/W=10

**Verdict: solver limitation on multiplier equivalence; neither state space nor COI.** Fixed with a
complete exhaustive proof of the same obligation.

Diagnosis:

1. **No state space.** The Tier 3 `gs4d_deform` netlist (`Q_FRAC=4`, W=12) has 12,078 gates and
   **0 registers** (MEASURED). `deform_engine` is combinational
   (`rtl_4dgs/deform_engine.sv:72-74`).
   - BMC-20 therefore cannot help. It unrolls 20 identical copies of a combinational miter, so the
     query is strictly harder.
   - Reproduced with the repo's `Breaker` (30 s / 3 retries / COI / BMC-20) on `prod[0][0]`
     (MEASURED): monolithic UNKNOWN ×3; COI slices for bits 0–10 UNSAT, bit 10 in 17.8 s; bit 11
     (388 nets, 24 leaves) UNKNOWN ×3; BMC-20 UNKNOWN ×3; final status `UNKNOWN` after 296 s.
2. **COI cannot shrink it.** Product bit `j` depends on every operand bit below `j`. Bit 11's
   cone already holds all 24 inputs, so slicing gives no reduction where the difficulty starts.
3. **Multiplier equivalence is hard for CDCL.** The miter compares Yosys's ABC-restructured
   array multiplier against Z3's own `bvmul` bit-blast. These are structurally unrelated XOR-heavy
   circuits, and CDCL SAT has no algebraic reasoning to match them. Width scaling for an isolated
   `prod = sext(a) * sext(b)` (same Yosys/ABC flow, one 30 s Z3 query, MEASURED):

| W | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|
| cells | 80 | 145 | 215 | 317 | 415 | 537 | 658 | 806 | 954 |
| Z3 (2W sign-extended encoding) | 0.01 s | 0.03 s | 0.12 s | 0.93 s | 6.55 s | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| Z3 (`$signed(a) * $signed(b)` W×W encoding) | 0.01 s | 0.02 s | 0.16 s | 1.07 s | 7.69 s | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

   Runtime grows about 7x per bit (MEASURED W=5..8), so the 30 s budget ends at W=8. The RTL
   encoding is not the cause: both encodings give the same netlist size and the same wall. A
   smaller-width Z3 proof (W ≤ 8) would therefore not cover the W=12 formal configuration.

Path forward, implemented: `scripts/formal/prove_products_exhaustive.py` (new file, no PR #10
file touched).

- **Same obligation, unweakened:** `prod[o][k] == sext(coef[o][k]) * sext(basis_k)` for all 10
  products, bit-true over 2W = 24 bits. `basis_1` is the netlist's own `basis_t2`, as in
  `verify_z3_4dgs.py:101-122`; `basis_t2 == fxmul(t, t)` stays a separate Z3 obligation.
- **Complete, not bounded:** the module has 0 registers, so each product is a pure function of
  24 input bits. The script simulates all 2^24 operand pairs bit-parallel on the Tier 3 BLIF
  from `rtl_to_netlist.py`. Any extra cone leaf raises an error.
- **Non-vacuity self-test:** the script first inverts one XOR in a product cone and requires
  mismatches.
- **Result (MEASURED):**
  ```
  self-test: inverted XOR prod[12] -> 16777216 mismatches (17.2s)
  PASS: 10 products x 2^24 operand pairs = 167772160 checks, 0 mismatches (17.0s)
  ```
- **Usage:** `python3 scripts/formal/rtl_to_netlist.py gs4d_deform`, then
  `python3 scripts/formal/prove_products_exhaustive.py`. Needs `numpy`.
- **CI:** not wired into `make formal_4dgs` or CI, because `Makefile` and
  `.github/workflows/rtl-baseline.yml` are PR #10 files. Wiring it in is a one-line follow-up after
  PR #10.
- **Scaling (MODELED):** at the production `Q_FRAC=8` (W=16), the sweep would be 2^32 pairs,
  about 256x longer (roughly 75 min). The fallback there is a Z3 case split on the 16-bit
  basis (2^16 constant-multiplier queries) or an algebraic multiplier checker (e.g. AMulet-style
  Gröbner rewriting). All of these keep the obligation unbounded.

`docs/QUALITY_VS_SKIP_4DGS.md` ("Multiplier products") should cite this proof instead of relying
on Tier 1 alone. That doc edit is left to review.

## Regression (MEASURED, after the change)

- `make sim` (lint + Tier 1):
  - SSIM PASS: 10000 cycles, 6800 bit-exact scores, 0 mismatches.
  - GS PASS: 432919 total cycles, 2550 bit-exact pixels, 2551 address-checked write strobes,
    0 mismatches.
- `make sim_4dgs`: PASS at `MOTION_THRESH=0` and `16`, 0 mismatches.
- `ref_model/verif/run_golden.sh`: RTL vs `q111` 0 mismatching pixels.
- No RTL or golden-model source changed.

## Fixed in Phase 5e (MEASURED on the Phase 5e branch, base `main` @ `b86da86`)

Toolchain as above, except Z3 5.4.1 (the pinned CI version) and Berkeley ABC for local Tier 2.

| # | Change | Result |
|---|---|---|
| 1 | `GS_RASTERIZER_pipeline.v`: stage 7 clamps `alpha = min(0.99, conw * exp)` (`0x3F7D70A4`, unsigned compare on the positive non-NaN product); stage 10 does not blend RGB or depth for the Gaussian with `T_test < 1e-4` (`s10_altb`) or any later one in the pixel (`tail_crossed`, cleared at INIT_PIXEL). `RtlQuirks` gets `alpha_clamp` (default on) and `rgb_stop_tail` (default off), `depth_stop_tail` now defaults off, so `q111` is still the current RTL. | `run_golden.sh`: RTL vs `q111` 0 mismatching pixels (N/T/D/RGB 0). `q000_snrdtc` (every golden rule) vs `golden_ans`: 0 tolerance mismatches on all 2550 px (was 33 for `q000_snrd`). `make sim`: SSIM PASS, GS PASS 432919 cycles, 2550 bit-exact pixels, 0 mismatches (unchanged). |
| 2 | `depth_stop_tail` scope: the golden rule now covers RGB too (`rgb_stop_tail`), and the RTL implements both. | `q000_snrdt` (tail rule without the clamp) leaves 24 px; `q000_snrdc` (clamp without the RGB tail rule) leaves 14 px; both rules: 0. |
| 3 | `rtl_to_netlist.py`: `setattr -set keep 1 t:SRAM_*` before `synth`, as in Tier 2. | Tier 3 GS: 49 opaque cells (was 48). Keep alone on the `b86da86` RTL: 2729 FFs, 49 cuts (FF count unchanged). With the item 1 RTL: 2730 FFs (+1 `tail_crossed`) vs Tier 2 2733; the 3-FF gap is still `-nofsm`. |
| 4 | `prove_products_exhaustive.py` (from PR #13 `a9b180e`, with `check_leaves`) + `make prove_products` + a CI step in the `baseline` job (numpy 2.2.6 added to the venv). | `self-test: inverted XOR prod[12] -> 16777216 mismatches (19.2s)`; `PASS: 10 products x 2^24 operand pairs = 167772160 checks, 0 mismatches (17.5s)`. |

`render_ideal` (the hazard-free sequential reference) applies the same two rules: it clamps
alpha and breaks before blending the crossing Gaussian (T still takes its `T_test`). Its
`run_golden.sh` tolerance mismatches vs `golden_ans` drop from T 4 / D 1351 / RGB 70 (1460 px) to
T 0 / D 705 / RGB 56 (1453 px); the rest is its N count and the RTL-only quirks it does not model.

GS Tier 2 (generic-cell, Yosys 0.23 + Berkeley ABC, `make cec_baseline`), before -> after:
631 -> 797 pre-map gates, 580 -> 668 mapped gates, 2732 -> 2733 FFs, 49 -> 49 opaque, 564 -> 651 /
542 -> 629 AIG ANDs, 12 -> 15 levels, CEC PASS. These are generic-cell metrics, not 130 nm figures.

Not done: the directed 3-Gaussian vector proposed in item 2 (pixel 0 of the lab scene is still
the only pixel where the RTL crosses, because of `sticky_early`).
