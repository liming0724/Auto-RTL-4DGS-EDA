# Phase 1 RTL verification baseline

## Reproduce

From the repository root, run:

```sh
make lint
make -j2 sim
make -j2 synth_baseline
make -j2 cec_baseline
```

Each synthesis invocation reruns the lint and simulation gates; the last command
is enough to run the entire flow. Install GNU Make, a C++17 compiler, Python 3,
Verilator, Yosys (with ABC), and [sv2v 0.0.12][sv2v]. Ubuntu 24.04 works with
`apt-get install make g++ python3 verilator yosys` and the sv2v release binary on
`PATH`. Make accepts `VERILATOR`, `YOSYS`, `ABC`, `SV2V`, and `PYTHON` overrides.
The default ABC is the first of `berkeley-abc`, `abc`, and `yosys-abc` available.
All generated files stay in the ignored `build/baseline/` directory.

### Toolchain used for the reported measurements

The measurements below were made on Ubuntu 22.04 (Jammy, x86-64) using the
**Debian amd64 Yosys package `0.23-6`** (git `7ce5011c24b`), extracted locally.
It was Jammy's `0.9-2` package, not Yosys 0.23, that failed to parse the GS RTL.
The measured run paired Yosys 0.23 with Jammy's Berkeley ABC
`1.01+20211229git48498af+dfsg-2` (2022-01-29 build), Verilator 4.038,
sv2v 0.0.12, and GCC 11.4.0.

On Jammy, install `make g++ python3 verilator berkeley-abc curl unzip` and the
Yosys runtime libraries `libffi8 libreadline8 libtcl8.6` with apt, then run the
following from the repository root. The checksum-verified tools stay in the
ignored build directory and do not replace system Yosys.

```sh
(
  set -eu
  tools="$PWD/build/baseline/tools"
  mkdir -p "$tools"
  cd "$tools"
  curl -fsSLo yosys.deb https://deb.debian.org/debian/pool/main/y/yosys/yosys_0.23-6_amd64.deb
  curl -fsSLo sv2v.zip https://github.com/zachjs/sv2v/releases/download/v0.0.12/sv2v-Linux.zip
  sha256sum -c <<'EOF'
00cb3419d9c2e8964e588cbfa8a46b8d0ff4b391083a53a82d49ec1c2766330b  yosys.deb
ff8c9eea5bc029b372fb4953427625cddb7cf7e58c1240623ac9f260818d5a00  sv2v.zip
EOF
  dpkg-deb -x yosys.deb yosys
  unzip -oq sv2v.zip
  cd - >/dev/null
  make -j2 cec_baseline YOSYS="$tools/yosys/usr/bin/yosys" \
    SV2V="$tools/sv2v-Linux/sv2v" ABC=/usr/bin/berkeley-abc
)
```

CI verifies the same flow on Ubuntu 24.04 with Verilator 5.020, Yosys 0.33,
Yosys-bundled ABC 1.01 (2024-04-01 build), and GCC 13.3.0; it is not the
source of the table below. Mapping counts can vary across tool versions.

[sv2v]: https://github.com/zachjs/sv2v/releases/tag/v0.0.12

### Import-only EDA dependency check

`fast_eda` built locally with pybind11 2.13.6; `fast_eda`, `parser`, and `agent`
imported successfully with no cloud credentials. This is independent of RTL CI
and does not run the agent or call Vertex AI:

```sh
python3 -m venv build/baseline/eda-venv
. build/baseline/eda-venv/bin/activate
python -m pip install pybind11==2.13.6 z3-solver pyyaml httpx google-genai
mkdir -p build/baseline/eda
g++ -O3 -Wall -shared -std=c++17 -fPIC $(python -m pybind11 --includes) \
    eda_core/fast_eda.cpp -o "build/baseline/eda/fast_eda$(python3-config --extension-suffix)"
PYTHONPATH=build/baseline/eda:eda_core python -c 'import fast_eda, parser, agent'
```

## Tier 1: Verilator

Both `--lint-only -Wall` targets pass with filename, unused-signal and
newer-Verilator EOF-newline warnings waived. GS also waives the legacy FSM's
incomplete case. Other warnings remain fatal.

- SSIM's `fp16_div8` explicitly retains the low 16 bits of the legacy 17-bit
  underflow assignments; all 65,536 encodings are checked, including the legacy
  dropped sign for exponents 1–3.
- GS inlines two combinational temporary flags that were only read immediately
  after assignment within the same branch. No latch value was consumed.
- The GS simulation-only float/int models use equivalent explicit integer
  conversions for Verilator 4/5 compatibility. The integer-to-float substitute
  rejects input widths above 24 bits at simulation startup.

| Test | Clock cycles | Checks | Result |
| --- | ---: | --- | --- |
| `sim_ssim` | 10,000 | 6,800 packed binary16 scores and 6,800 nine-tap windows | Pass, 0 mismatches |
| `sim_gs` | 432,919 total; 431,792 for the checked frame | 2,550 bit-exact pixels and 2,551 cycle-exact, address-checked write strobes | Pass, 0 mismatches |
| `sim_gs` `+directed` | 2,796,453 total; 2,796,410 for the checked frame | Same checks on the directed alpha-clamp / post-crossing vector, plus 2 stage-7 clamps and 1 post-`s10_altb` stage-10 entry at pixel 0 | Pass, 0 mismatches |

SSIM repeats the checked-in `gt.txt` / `img.txt` frame through the actual input
SRAMs. The test checks output-valid every cycle. A separate shift-window
instance receives deterministic vectors with 48-bit extremes, alternating scan
directions, and zero padding at edges. The pinned score fixture was captured
with Icarus Verilog 11.0 from the legacy `TB.v` running the original RTL at
`176e240`, with only FSDB calls omitted and the clean-room binary16 models
supplied. To reproduce it from the repository root:

```sh
mkdir -p build/baseline
out="$PWD/build/baseline"
git show 176e240:rtl_baseline/ssim_eval/fp_operation.v > "$out/fp_operation.original.v"
sed '/\$fsdbDump/d' rtl_baseline/ssim_eval/TB.v > "$out/ssim_tb_icarus.v"
(
  cd rtl_baseline/ssim_eval
  iverilog -g2012 -s SSIM_tb -o "$out/ssim_icarus" \
    ../../ref_model/rtl/sim_substitutes/ssim_fp16.v SSIM_v2.v ssim_custom_shift_safe.v \
    "$out/fp_operation.original.v" SRAM_IMG_GT.v SRAM_other.v "$out/ssim_tb_icarus.v"
  vvp "$out/ssim_icarus" > "$out/ssim_icarus.log"
)
sed -nE '/^\[SSIM OUTPUT\]/s/.* raw=([[:xdigit:]]+).*/\1/p' "$out/ssim_icarus.log" |
  diff - rtl_baseline/sim/ssim_golden.txt
```

GS uses a deterministic synthetic scene (seed 1, 600 active Gaussians in 1,089
slots) and the cycle-accurate C++ reference. The top does not expose ready/valid
ports; the test checks reset and restart, write timing (first write at frame
cycle 1,103), pixel payloads, and `Finish`. A simulation-only wrapper observes
write enable and address at the output SRAM inputs. The test expects consecutive
addresses 0–2,549, followed by the legacy terminal strobe at 2,550 with no
additional pixel payload.

Both simulations use clean-room substitutes, not real DesignWare. SSIM's fixture
is a regression baseline for this stimulus and model, not an independent SSIM
algorithm oracle or evidence of bit-exactness against DesignWare.

## Tier 2: Generic synthesis and CEC

Yosys lowers and flattens the RTL using `synth -noabc` (sv2v converts SSIM's
unpacked ports). DesignWare operators and internal SRAMs are opaque in both
designs; SSIM input SRAMs and the directed shift probe are outside the synthesized
top. No simulation arithmetic substitutes are synthesized.

Yosys maps the combinational logic with `abc -g AND,OR,XOR,MUX` (inverters are
allowed). The next ABC pass runs `strash; refactor; rewrite; balance` on the
mapped combinational network.

| Top | Generic gates, pre → mapped | Flip-flops | Opaque macros | AIG ANDs, mapped → optimized | AIG levels, mapped → optimized |
| --- | ---: | ---: | ---: | ---: | ---: |
| `SSIM_v2` | 30,149 → 21,424 | 13,220 | 115 | 37,846 → 37,519 | 17 → 16 |
| `GS_RASTERIZER_pipeline` | 797 → 668 | 2,733 | 49 | 651 → 629 | 15 → 15 |

Phase 5e (`docs/BACKLOG_INVESTIGATION.md`, "Fixed in Phase 5e") added the 3DGS alpha clamp
and the stage-10 crossing gate to GS. Before it (`main` @ `b86da86`, same toolchain) GS was
631 → 580 gates, 2,732 flip-flops, 49 opaque macros, 564 → 542 AIG ANDs, 12 → 12 levels: the
fix costs +88 mapped gates, +1 flip-flop (`tail_crossed`) and +3 AIG levels. The GS
simulation result (432,919 cycles, 2,550 bit-exact pixels) is unchanged.

Counts exclude opaque macro internals. AIG ANDs and levels are **generic-cell
area and depth proxies**, not cell area, delay, frequency, or the 130 nm
measurements in the project summary.

Each register and macro output is treated as a free input; each register and
macro input becomes an observed output. The driver checks that the opaque cells
and boundary pins match across netlists and compares the resulting combinational
logic for all cut-point assignments. Undefined bits are resolved to zero before
mapping. This proves combinational preservation across ABC, not full sequential
equivalence back to the RTL or equivalence of SRAM or floating-point internals.

ABC logs (`build/baseline/synth/{ssim,gs}/cec.log`), in order: pre-ABC vs. mapped,
then pre-ABC vs. optimized. Either a counterexample or an inconclusive run fails
the target. The adjacent `synth.log`, `abc.log`, and `metrics.json` retain the
synthesis and AIG measurements.

```text
SSIM_v2
Networks are equivalent.  Time =    12.29 sec
Networks are equivalent.  Time =    12.41 sec

GS_RASTERIZER_pipeline
Networks are equivalent.  Time =     0.02 sec
Networks are equivalent.  Time =     0.01 sec
```

Tier 3 (Z3 corner-case proofs) was not run for this Phase 1 baseline; Phase 2 adds it
as `make formal_z3` / `make test_cegar` ([Formal verification](FORMAL_VERIFICATION.md)).
