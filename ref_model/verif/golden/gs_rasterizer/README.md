# GS rasterizer golden_ans

`golden_ans/` holds the reference outputs supplied by the repo owner for the
75×34 GS_RASTERIZER_pipeline tile (2550 lines each, same pixel order):

| file | format |
| --- | --- |
| `N_img.txt` | decimal Gaussian count per pixel |
| `T_img.txt` | float32 hex, final transmittance |
| `depth_img.txt` | float32 hex, depth |
| `rendered_img.txt` | `R G B` decimal |

Pixel order (from the RTL, not the files): line `i` is pixel
`(px, py) = (i % 75, i // 75)`. `GS_RASTERIZER_pipeline.v` steps `px` 0..74
then `py` 0..33 and writes pixel `py*75 + px`; TB.sv compares write address
`waddr` with line `waddr` of each file.

`verif/run_golden.sh` compares the C++ model (every `RtlQuirks` combination
plus the ideal algorithm) and the RTL against these files. It needs the scene
that produced them next to this README: `ram_file.txt` (1089 × 384-bit hex
splat words, `$readmemh`) and `gs_value.txt` (1089 × 11-bit binary sorted
indices, `$readmemb`), both supplied by the repo owner from the lab originals
(CRLF line endings normalized to LF). Without them the funnel prints the
golden statistics and reports the comparison as not run.

`q000_snrd` (every `RtlQuirks` flag off) is the configuration closest to these
files; see `ref_model/README.md` for what each flag does.
