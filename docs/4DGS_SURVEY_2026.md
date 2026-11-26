# 4D Gaussian Splatting Survey 2026 — Thesis-Direction Decision

Prepared for the thesis direction freeze on **30 Nov 2026**. Scope: dynamic/4D Gaussian Splatting algorithms, 3DGS/4DGS ASIC and FPGA accelerators, compression/streaming, training acceleration, temporal reuse, multi-view input reduction, and quality.

**Count:** 83 new papers (§1.A) + 11 owner-provided baselines (§1.B) = 94 entries.

## How to read this document

- **Sources:** arXiv API searches (cs.CV, cs.GR, cs.AR, cs.DC, cs.LG) over ~415 candidates; 83 papers selected and read from arXiv HTML/PDF full text. Venues (CVPR/ICCV/ECCV/NeurIPS/SIGGRAPH/ISCA/MICRO/HPCA/DAC/ASPLOS/DATE/ICCAD/TVCG …) are taken from the paper's own arXiv comment/journal-ref field; where a venue is only claimed on a project page it is marked "not verified here".
- **Dates** are the arXiv v1 submission date (or DOI publication date for papers without arXiv).
- **Numbers** are copied from the paper text. If a number could not be found in the text, it is written **unverified**. Numbers quoted from another paper's comparison table are labelled with that source.
- **Hardware evidence labels** (stated for every hardware paper): **MEASURED silicon**, **MEASURED board** (FPGA), **GPU/device MEASURED** (software on real hardware), **post-layout**, **synthesis**, **SIMULATION** (cycle-level / cycle-accurate / analytical). Only Vorion (arXiv:2511.16831, minimal 16 nm prototype) reports measured silicon, and only LENS (arXiv:2610.25068, KR260) reports a measured FPGA board, among the accelerator papers here. 3DGauCIM uses measured statistics of a separate 16 nm DCIM chip inside a simulation.
- **Project context (not a constraint):** the repo's current RTL (deformation engine with [1, t, t²] basis, L1 motion-threshold skip, post-projection motion-aware cache) is cycle-accurate Verilator simulation with modeled DRAM traffic (~20.4–22.6% conservative external-traffic reduction). Its skip decision does not gate the deformation engine, and its cache is view-dependent. These facts are used only as motivation in §7.
- All entries were checked for an arXiv ID or DOI and a date. Future-dated (2026) preprints are reported as they appear on arXiv.

## 1. Paper catalogue

### 1.A New papers (newest first)

#### SprintGS: Eliminating Redundancy for Fast 3D Gaussian Splatting Training
- **Authors:** R. Watanabe, T. Konno · **Venue/date:** arXiv preprint, 25 Nov 2026 · **ID:** arXiv:2611.33796 · **Platform label:** GPU software, MEASURED.
- **Problem:** even compact fast-training methods (FastGS) remain dominated by rasterization, and the loss computation does not shrink with Gaussian count, so its share grows as models get compact.
- **Method:** scheduled tile culling limits each Gaussian to tiles where it matters (coarse footprint annealed to exact before the end), convergence-aware pruning of converged near-transparent Gaussians, a shuffle-efficient backward pass, and a fused loss that computes loss and image gradient inside the forward pass.
- **Key numbers:** restoring the exact footprint for the last 10% of iterations raises PSNR 27.25 → 27.38 dB for +2.4 s (77.6 → 80.0 s training). Headline end-to-end speedup vs FastGS: unverified here.
- **Limitations:** static scenes; a median-based pruning variant collapses training on 9 of 13 scenes (shows sensitivity of pruning rules).

#### VersionGS: Compact and Motion-Robust Dynamic Gaussian Splatting via Versioned Gaussians
- **Authors:** S. Wei, Y. Zhang, F. Zhu (Purdue) · **Venue/date:** arXiv preprint, 24 Nov 2026 · **ID:** arXiv:2611.33374 · **Platform label:** GPU software, MEASURED.
- **Problem:** smooth-function or constrained-update dynamic GS loses 1.1–4.0 dB PSNR on high-motion frames of dance sequences.
- **Method:** per-frame Gaussian states stored as versions in a per-GOP shared dictionary; unchanged Gaussians keep their version, changed ones append a new version; selective sequential construction refines only Gaussians flagged by optical flow / photo difference / render error; standard 3DGS rasterizer.
- **Key numbers:** +2.7 dB foreground PSNR on DanceNet3D over strongest baseline with <0.3 dB loss on fastest frames; 6.0× (N3DV) and 5.7× (Technicolor) less storage than TAD-GS at higher PSNR; e.g. big-kicks 25.90 dB @0.181 MB/frame vs TAD-GS 16.77 dB @0.860 MB/frame.
- **Limitations:** tracking failures during sequential construction cannot be recovered later; per-sequence training cost unverified.

#### HiGS: A Hierarchical Architecture for Fast Differentiable 3D Gaussian Splatting
- **Authors:** D. Pająk, M. Bisson, R. Lima, F. Trudel · **Venue/date:** arXiv preprint, 24 Nov 2026 · **ID:** arXiv:2611.31918 · **Platform label:** GPU software (CUDA), MEASURED on GPU; not a hardware accelerator.
- **Problem:** 3DGS uses one screen tile as the unit of partitioning, rasterization and parallel work. Partitioning wants large tiles, rasterization wants small ones, and the densest tile sets the frame time; a global radix sort over tens of millions of pairs per frame at 4K adds to it.
- **Method:** Gaussians are partitioned over coarse macro-tiles, rasterized over fine render tiles inside them, and processed in bounded depth-sorted batches, so dense regions issue more batches instead of one long unit. The same batches carry the backward pass; the optimizer is fused into backward, stores half the parameters/moments, and defers out-of-view appearance updates. Exact front-to-back alpha compositing is kept.
- **Key numbers:** agrees with gsplat to 67.2 dB PSNR (55.6 dB with packed SH); training 3.2× faster with 34% less memory at a PSNR 0.01 dB from the baseline row; without spatially ordered storage it takes 48% longer. Render speedup vs other methods: "faster than every published method we evaluate" (per-scene numbers not extracted here → unverified).
- **Limitations:** static 3DGS only; GPU-specific; no dynamic-scene evaluation.

#### InterGS+: Inter-Frame Gaussian Splatting Compression with Geometry-Aware Transformer Prediction and Adaptive Residual Coding
- **Authors:** M. Talha, Z. Li, J. Jung · **Venue/date:** DCC 2027 (accepted; arXiv 23 Nov 2026) · **ID:** arXiv:2611.31482 · **Platform label:** software codec.
- **Problem:** Gaussians do not keep identities across frames, so inter-frame prediction is hard.
- **Method:** geometry coded with G-PCC first; attributes predicted by KNN/bilateral predictors plus a geometry-aware transformer; adaptive residual coding with 38 PCA components.
- **Key numbers:** average −45.48% BD-rate and +9.98 dB BD-PSNR vs G-PCC Amd.1; −7.78% BD-rate / +1.10 dB vs InterGS-Lite; best sequences Musical/Makeup/Trio 67.08/66.59/64.38%.
- **Limitations:** post-hoc per-frame point-cloud codec, not tied to a renderable 4D model; encoder complexity unverified.

#### ElsaGS: Accelerating 3D Gaussian Splatting Training through Selective Freezing and Computation Caching
- **Authors:** H. Zhu, Z. Dai, Z. Liu, R. Li, H. Wei et al. · **Venue/date:** arXiv preprint (cs.AR), 17 Nov 2026 · **ID:** arXiv:2611.22059 · **Platform label:** SIMULATION + synthesis (area 1.34 mm² in 8 nm); no silicon.
- **Problem:** 3DGS training is long, and gradient accumulation is the dominant training bottleneck; earlier fixes either give small speedups or lose quality.
- **Method:** Progressive Gaussian freezing stops updating Gaussians that converged early and caches their cumulative contribution, cutting both forward rasterization and backward gradient accumulation. The ElsaGS core pairs a pixel-parallel rasterization array with a set-associative frozen-Gaussian cache (FGcache).
- **Key numbers:** freezes up to 90% of Gaussians; up to 20.6× end-to-end speedup and 99.4% energy reduction vs GPU; −78.0% training energy vs GSArch; PSNR 0.2 dB below vanilla 3DGS and 1.08 dB above GSArch; ablation: freezing alone 2.4× and −51.9% energy, backward caching a further 2.5×.
- **Limitations:** static scenes only; frozen state is per-scene training state; numbers are simulated.

#### Mitigating SM-Level Load Imbalance in 3D Gaussian Splatting on GPUs
- **Authors:** J. Ryu, H.-J. Yoo (KAIST) · **Venue/date:** arXiv preprint, 9 Nov 2026 · **ID:** arXiv:2611.12319 · **Platform label:** GPU software, MEASURED on GPU.
- **Problem:** uneven per-tile work makes a few SMs process heavy tiles while most SMs idle, creating a long execution tail, worst in large urban scenes.
- **Method:** a persistent kernel schedules heavy tiles first and splits outlier tiles across several CTAs. A per-frame policy decides when and how much to split from tile statistics the pipeline already has; no retraining or new hardware.
- **Key numbers:** alpha blending up to 2.93× faster per scene on a large urban scene, up to 5.6× on its most imbalanced frames, with the original blending formula.
- **Limitations:** policy constants are hand-set per GPU (authors leave calibration/online learning to future work); blending stage only.

#### GSCV++: Lightweight 3D Gaussian Splatting Sequence Compression via Standard Video Codecs
- **Authors:** Q. Yang, Y. Xu, K. Zhang, L. Zhang (ByteDance) · **Venue/date:** arXiv preprint, 3 Nov 2026 · **ID:** arXiv:2611.05359 · **Platform label:** software + standard video codec.
- **Problem:** industry needs low-complexity, hardware-codec-compatible GS sequence compression.
- **Method:** energy-adaptive inter-frame SH-AC dimensionality reduction, improved 3D→2D (PLAS-style) mapping, practical video-codec configuration.
- **Key numbers:** at R03, GoP32 vs GoP8: 3.557 → 4.362 MB/frame and 34.87 → 33.51 dB; GoP32 is 2.21 dB worse than GoP16 over frames 16–31 (long GOPs hurt).
- **Limitations:** quality falls with distance from the I-frame anchor.

#### Phase-DGS: Phase-guided Dynamic Gaussian Splatting from Unsynchronized Multi-view Video
- **Authors:** H. Jeon, J. Y. Jeong, S. Kwak, J. Kim, S. Kim et al. (ETRI) · **Venue/date:** NeurIPS 2026 (arXiv 3 Nov 2026) · **ID:** arXiv:2611.04666 · **Platform label:** GPU software, MEASURED.
- **Problem:** dynamic GS assumes perfectly synchronised cameras, which needs expensive hardware.
- **Method:** replaces timestamps with a learned semantic motion phase so frames in the same motion state align across unsynchronised cameras.
- **Key numbers:** up to +6.5 dB PSNR under the "Dropped" condition (SelfCap, FreeTimeGS); within 0.8 dB of backbone when synced; +0.55 / +1.29 dB on SelfCap synced with FreeTimeGS / RealTime-4DGS backbones.
- **Limitations:** slightly lower PSNR on NVIDIA Dynamic Scene; cannot capture sub-frame phase at the native frame rate.

#### SCARF: A Scene-Adaptive Depth-Guided G-3DGS Encoder Accelerator with Semantic Reuse and Fused Dataflow
- **Authors:** Z. Ma, Z. Fan, H. Wu, W. Li, F. Zhang et al. · **Venue/date:** MICRO 2026 (accepted; arXiv 2 Nov 2026) · **ID:** arXiv:2611.02365 · **Platform label:** POST-LAYOUT 28 nm design (27.8 mm², 2.1 W) + simulation; no fabricated chip reported.
- **Problem:** feed-forward (generalizable) 3DGS encoders such as MVSplat/DepthSplat/TranSplat spend >65% of memory traffic on exhaustive per-pixel depth search and create >52% redundant Gaussians before instantiation.
- **Method:** Feature-Similarity Depth Reuse turns per-pixel depth search into anchor-centered local search grouped by feature affinity (32-entry CAM). Scene-Adaptive Early Sparsification probes each tile and activates only the minimal Gaussian-generation path.
- **Key numbers:** 2.94× geo-mean end-to-end speedup vs Jetson Orin NX; max PSNR degradation <0.072%; 2.23 inferences/J; 34.5× GPU energy efficiency; DRAM traffic −41% to −62%; FSDR costs 0.08 mm² / 15 mW. Datasets Re10K, ACID, DL3DV.
- **Limitations:** encoder only, not rendering; static scenes; cross-stage scheduling left as future work.

#### AbacusGS: Gaussian Splatting without Per-Splat Sorting
- **Authors:** J. Ding, Y. Qi, C. Guo, P. Mai · **Venue/date:** arXiv preprint, 1 Nov 2026 (v2 carries a **withdrawal request** by the authors) · **ID:** arXiv:2611.01534 · **Platform label:** software (OpenGL viewer + CUDA renderer), MEASURED.
- **Problem:** web/mobile viewers sort every splat by depth on the CPU each frame, and that sort takes most of the frame time.
- **Method:** splats are grouped into octree boxes of ≤128 splats, each stored in order along an axis chosen at write time; per frame only visible boxes are sorted and one dot product per box picks forward or backward reading. A short order-aware fine-tune converts a 3DGS model; the over operator is unchanged.
- **Key numbers:** −0.13 dB PSNR average on 13 standard scenes vs an equally fine-tuned 3DGS; 4.5× faster frames than per-splat sorting in a CPU-sorting OpenGL viewer; 17-byte/splat compact format.
- **Limitations:** two-level order approximates per-splat order (itself an approximation of per-pixel order); preprint withdrawn pending revision, so numbers may change.

#### GaussianSpline: Adaptive Bézier Splines for Highly Dynamic 4D Gaussian Splatting
- **Authors:** T. Yachil, A. Sharf · **Venue/date:** arXiv preprint, 26 Oct 2026 · **ID:** arXiv:2610.35224 · **Platform label:** GPU software.
- **Problem:** global deformation fields, fixed-order polynomials and uniform splines under-fit fast motion.
- **Method:** per-Gaussian non-uniform piecewise cubic Bézier trajectories; control points and temporal partition jointly learned; G¹ continuity via reflected control points.
- **Key numbers:** not extracted → unverified (qualitative wins on N3DV coffee_martini vs Gaussian-Flow, STG, SplineGS).
- **Limitations:** per-Gaussian segment count increases storage and per-frame evaluation irregularity (hardware-unfriendly variable work).

#### LENS: Localization and Neural Splatting on the Edge — An FPGA Scene Memory for Robots
- **Authors:** H. Que, M. Liu, T. Zhu, H. Yao · **Venue/date:** arXiv preprint, 18 Oct 2026 · **ID:** arXiv:2610.25068 · **Platform label:** MEASURED ON BOARD (AMD KR260 FPGA, all latency/power/quality board-measured).
- **Problem:** robots need localization plus nearby-view rendering without a desktop GPU; ACEsplat-style pipelines are GPU-centered, and SCR is pose-sensitive while 3DGS rendering is irregular and capacity-sensitive on FPGA.
- **Method:** INT16 scene-coordinate-regression accelerator plus a bit-exact fixed-point ordered 3DGS renderer that rewrites alpha transmittance as a log-sum-exp recurrence and removes provably useless streamed Gaussians.
- **Key numbers:** SCR 25.42 ms (from 55.3 ms; Orin NX 33 ms); renderer 34.11 dB on Cambridge vs ACEsplat 33.20; RealEstate10K 27.84 vs 26.11 dB; 1.9–5.7× perf/W over Orin NX at ~¼ the power; board 3.9 W, ~0.125 J/frame vs ~0.24 J on Orin NX; on-device training on 4×A53 reaches 34.96 dB but takes 11.3 h vs 41.5 s on Orin NX.
- **Limitations:** static scenes; training not accelerated; no fused SCR-render-train bitstream.

#### ResStreamGS: Residual-Sensitive Dual-Stream Gaussian Splatting for Mobile Volumetric Video Streaming
- **Authors:** H. Liu, D. Wu, W. Luo, H. Na, M. Hu et al. · **Venue/date:** arXiv preprint, 15 Oct 2026 · **ID:** arXiv:2610.21121 · **Platform label:** MEASURED on Qualcomm Snapdragon 778G phone.
- **Problem:** V³-style streaming codes every Gaussian as frame-varying; full scenes lose quality (Neu3D drops 11.7 dB across QP vs 3.4 dB on Actors-HQ).
- **Method:** dual stream — temporally stable Gaussians sent once, residual-sensitive dynamic Gaussians coded with commodity video codecs and hardware decoding.
- **Key numbers:** +7.51–8.35 dB post-codec PSNR vs V³-style baseline; up to −56.5% amortized payload; 30.883 FPS on Snapdragon 778G.
- **Limitations:** selector behaviour at very high static ratios (paper notes a failure mode).

#### TETRIS-4DGS: Efficient Post-Training Compression of 4D Gaussian Splatting
- **Authors:** C. Wu, K. Liu, S. Li, L. Yu, Y. Liao (Zhejiang U.) · **Venue/date:** ICME 2026 (arXiv 10 Oct 2026) · **ID:** arXiv:2610.14663 · **Platform label:** software.
- **Problem:** 4DGS storage is large and existing compression is per-scene optimisation, too slow for low-latency use.
- **Method:** post-training, codec-compatible compression: importance-based adaptive quantisation and motion/importance-aware attribute packing into 2D images for standard image/video codecs.
- **Key numbers:** not extracted → unverified.
- **Limitations:** relies on the codec's 2D priors; rate–distortion numbers not verified here.

#### H2-Ray: A Hybrid and Hierarchical Ray Tracing Method for Efficient 3D Gaussian Rendering
- **Authors:** T. Zhao, S. Li, Y. Chen, H. Ding, Z. Pan et al. · **Venue/date:** arXiv preprint (cs.AR), 9 Oct 2026 · **ID:** arXiv:2610.13498 · **Platform label:** SIMULATION (cycle-accurate model of a modified RT core) combined with measured CUDA timings.
- **Problem:** ray-traced 3DGS (needed for secondary lighting, distorted cameras, DoF) suffers load imbalance, redundant traversal across neighbouring rays, and mixed Gaussian/triangle intersection work.
- **Method:** Load-Balanced Spatial BVH over uniform grids; Ray-Cluster Path Reuse where a proxy ray shares traversal state with neighbours; a Unified Intersection Engine sharing 40% of FP units between Gaussian and triangle tests.
- **Key numbers:** 3.77× geo-mean, 9.26× max speedup over RaySplats on RTX A5000 across 8 scenes.
- **Limitations:** static scenes; no comparison with GRTX (no public code); hybrid simulation methodology.

#### DynStream: Online Streaming 4D Gaussian Reconstruction of Dynamic Worlds from Unposed Video
- **Authors:** D. Xian, X. Zhou, Y. Xiong, Y. Wang, M.-H. Yang · **Venue/date:** arXiv preprint, 7 Oct 2026 · **ID:** arXiv:2610.09720 · **Platform label:** GPU software (feed-forward).
- **Problem:** feed-forward Gaussian methods are offline; online point-cloud methods lack dense geometry.
- **Method:** reconstructs local temporal windows feed-forward, then incrementally aligns and fuses them into a global 4D scene; no per-scene optimisation.
- **Key numbers:** not extracted → unverified.
- **Limitations:** monocular unposed video quality ceiling; drift over long streams (inferred, not verified).

#### Mobile-4DGS: Unified Static-Dynamic Real-time Mobile Gaussian Splatting
- **Authors:** X. Du, Z. Fang, T. Zhu, R. Hartley, X. Yu · **Venue/date:** arXiv preprint, 4 Oct 2026 · **ID:** arXiv:2610.05289 · **Platform label:** mobile-device software, MEASURED (device model unverified).
- **Problem:** storage, redundant primitives and per-frame cost block static+dynamic GS on phones.
- **Method:** Monte Carlo specular energy aggregator folds high-order SH into first-order SH; pre-baked SH offsets; multi-view alpha-based densify/prune; a "DOC" reuse policy that reuses dynamic computation across frames.
- **Key numbers:** DOC reuse table: 0% reuse 124.7 ms/s & 23.2 MB/s (monolithic) vs 65.5% reuse 25.8 ms/s & 8.0 MB/s (drift 0.26) vs 90.2% reuse 7.2 ms/s & 2.3 MB/s (drift 1.28) — shows reuse-vs-drift trade-off directly.
- **Limitations:** drift grows with reuse ratio; exact metric definition of "drift" unverified.

#### Sparse-View 4D Gaussian Splatting via Spatiotemporal Priors and Generative Assistance
- **Authors:** S. Wang, Z. Yang, K. Tian, Y. Liu, B. Liu et al. · **Venue/date:** SIGGRAPH Asia 2026 Workshops (arXiv 3 Oct 2026) · **ID:** arXiv:2610.04606 · **Platform label:** GPU software.
- **Problem:** volumetric-video challenge with only six wide-baseline cameras.
- **Method:** mask-guided init and mask-voting densification, metric monocular depth for background, frame interpolation + optical-flow motion constraints, and generative restoration of virtual views in angular gaps.
- **Key numbers:** mask-voting 28.63 dB full-frame / 27.90 dB foreground; iterative generative assistance +1.12 dB to 29.75 dB full-frame.
- **Limitations:** 4-page workshop paper; generative model cost not reported.

#### GARO: Geometry-Aware Redundancy Optimization for Real-Time and High-Fidelity Dynamic Gaussian Splatting
- **Authors:** H. Xue, K. Zhao, Z. Ming, T. Li · **Venue/date:** ICRA 2026 (arXiv 20 Sep 2026) · **ID:** arXiv:2609.23509 · **Platform label:** GPU software, MEASURED.
- **Problem:** deformable GS keeps many redundant Gaussians, hurting memory and FPS.
- **Method:** low-gradient candidates filtered by low-curvature geometry analysis, then pruned during adaptive density control.
- **Key numbers:** D-3DGS 132k Gaussians @57 FPS → 36k @184 FPS on one scene; up to 295 FPS with PSNR loss ≤0.5 dB.
- **Limitations:** evaluated mainly on synthetic/monocular dynamic sets (dataset list unverified).

#### DecoGS: Adaptive Static-Dynamic Decoupling of 3D Gaussians for Free-Viewpoint Video Streaming
- **Authors:** I. Sulo, A. Supikov, I. Demir, S. Liu (Intel) · **Venue/date:** arXiv preprint, 15 Sep 2026 · **ID:** arXiv:2609.17230 · **Platform label:** GPU software, MEASURED.
- **Problem:** streaming methods update every Gaussian every frame, causing flicker/drift in static regions.
- **Method:** optimise only spatiotemporal regions with motion or photometric change; region-aware densification.
- **Key numbers:** 34.55 dB (N3DV) and 31.60 dB (MeetRoom) PSNR at 261 FPS; closest ComGS 31.49 dB needs 28.3 s/frame training and renders 98 FPS.
- **Limitations:** per-frame training time of DecoGS itself unverified in extracted text.

#### From Splats to Silicon: Rethinking Computational Efficiency of 3DGS (survey)
- **Authors:** M. Pei, Q. Dong, Y. Zhou, G. Li, Y. Zhu et al. · **Venue/date:** arXiv preprint (cs.AR), 5 Sep 2026 · **ID:** arXiv:2609.06157 · **Platform label:** survey + reproduced GPU profiling (MEASURED-GPU).
- **Problem:** reported 3DGS speedups sit at different points of the render/update path and are not comparable end to end.
- **Method:** a workload-centric framework tracing how each optimization changes Gaussian selection, screen-space work, data movement and stage/frame time, plus controlled GPU profiling of selected implementations.
- **Key numbers:** qualitative conclusions only (no headline numbers extracted → unverified): gains depend on whether workload cuts reach downstream stages, granularity matching, and the cost of transfers, synchronization and cached results.
- **Limitations:** static 3DGS focus; recommends but does not provide a standard quality-constrained benchmark.

#### Atlas: Algorithm-Hardware Co-Design for On-Device City-Scale 3D Gaussian Splatting in VR
- **Authors:** H. Zhu, Z. Liu, X. Li, A. Wu, Z. Liu et al. · **Venue/date:** arXiv preprint (cs.AR), 2 Sep 2026 · **ID:** arXiv:2609.02352 · **Platform label:** SIMULATION + synthesis (0.25 mm² add-on, 16 nm, ~14% over GSCore's 1.78 mm²); no silicon.
- **Problem:** city-scale 3DGS models exceed mobile-GPU memory, and VR needs stereo at low motion-to-photon latency without network access.
- **Method:** hierarchical memory offloading loads only the Gaussians needed for the current pose and LoD; temporal-aware LoD search reuses the previous frame's cut; stereo rasterization shares work between eyes. Integrates with existing 3DGS accelerators.
- **Key numbers:** 18.5× motion-to-photon speedup vs mobile GPU, 3.9× vs SOTA 3DGS accelerators, 92.4% energy savings.
- **Limitations:** static scenes; LoD cut search falls back to parent/neighbour subtrees when local cut fails (irregular cost).

#### CC-4DGS: Computational Deformation and Point-Cloud Compression for Storage-Efficient Dynamic Gaussian Splatting
- **Authors:** K. Park, C. E. Rhee · **Venue/date:** IEEE TVCG 2026 (arXiv 2 Sep 2026) · **ID:** arXiv:2609.02184, DOI 10.1109/TVCG.2026.3721136 · **Platform label:** GPU software, MEASURED.
- **Problem:** dynamic GS needs tens–hundreds of MB, dominated by multi-resolution hash tables (Grid4D deform module 221.1 MB, Swift4D 68.4 MB).
- **Method:** computational deformation field — deterministic dense hash encoding computed on the fly + small decoders (deformation storage 1–3 MB); point-cloud-codec compression of canonical attributes.
- **Key numbers:** vs E-D3DGS 32.83 → 33.29 dB PSNR, SSIM 0.900 → 0.912, 46.2 → 64.7 FPS; total ≈30–32 MB with 0.01–0.02 dB loss.
- **Limitations:** no GOP/hierarchical temporal encoding; hash computation shifts cost from memory to compute (good for ASIC).

#### S²GS: Structured Sparse Gaussian Streaming for Efficient Free-Viewpoint Video Reconstruction on Edge-IoT Devices
- **Authors:** Y. Li, J. Cao, W. Gao, R. Cao, S. Zhu et al. (PolyU) · **Venue/date:** arXiv preprint, 20 Aug 2026 · **ID:** arXiv:2608.19639 · **Platform label:** MEASURED on RTX 4090.
- **Problem:** per-frame optimisation time and storage block edge-IoT FVV (QUEEN-style residuals cost 14.4 MB/s at 30 FPS).
- **Method:** streaming octree organises Gaussian residuals; structure-aware temporal sparsity updates only selected residuals.
- **Key numbers:** vs QUEEN: −59% per-frame optimisation time, −85% storage (RTX 4090).
- **Limitations:** fixed hierarchy; anchor insertion left to future work.

#### QuARC-GS: Quantized Anchored Residual Coding for Compact Dynamic Scene Streaming
- **Authors:** V. T. N. Nguyen, Y. Wang, K. C. Lee, K. C. Zhou · **Venue/date:** arXiv preprint, 18 Aug 2026 · **ID:** arXiv:2608.18285 · **Platform label:** GPU software.
- **Problem:** long FVV streams have unsustainable storage and need fast reconstruction.
- **Method:** quantization-aware online optimisation of anchor-based residuals; appearance gradients evaluated after deformation isolate non-geometric change.
- **Key numbers:** not extracted → unverified.
- **Limitations:** anchor structure fixed after first frame (inferred, unverified).

#### ProbSplat: Efficient Probabilistic Hardware for Gaussian Splatting in 3D Scene Reconstruction
- **Authors:** S. Gottumukkula, M. P. Samartha, V. Pahariya, P. Jain, A. R. Trivedi et al. · **Venue/date:** IEEE ISVLSI 2026 (accepted; arXiv 13 Aug 2026) · **ID:** arXiv:2608.13143 · **Platform label:** CIRCUIT SIMULATION (180 nm CMOS, 1.8 V, 50 MHz); energy partly projected from cited ADC/DAC designs.
- **Problem:** evaluating Gaussian-mixture log-likelihoods digitally costs compute, memory and power at the edge.
- **Method:** floating-gate inverter columns store mean and variance of each Gaussian component (set via threshold voltages) and evaluate log-likelihood in the analog domain, CIM-style.
- **Key numbers:** mean–variance independence within 2.4% deviation; 18 pJ per log-likelihood inference at 4 bits (abstract); Log-ADC is 49.47% of energy.
- **Limitations:** evaluates likelihoods for GMM reconstruction, not a rasterization pipeline; 180 nm simulation only; analog variability.

#### DeGS: A Scalable 3DGS Architecture via Decoupled Workload Parsing and Reorganization
- **Authors:** M. Pei, G. Li, Z. Zhu, S. Wang, J. Si et al. · **Venue/date:** MICRO 2026 (accepted; arXiv 3 Aug 2026) · **ID:** arXiv:2608.02099 · **Platform label:** SIMULATION (cycle-level, LPDDR bandwidth sweeps 51.2–204.8 GB/s); no silicon.
- **Problem:** adding PEs to 3DGS accelerators gives little speedup because the coupled "checking-while-blending" dataflow leaves PEs idle from irregular Gaussian coverage and asynchronous per-pixel early termination.
- **Method:** splits α-checking, transmittance checking and α-blending into consecutive parse → reorganize → blend stages, compacting fragmented, length-variable workloads into dense conflict-free work before blending.
- **Key numbers:** on dynamic scenes, mean PE utilization 23.9% → 90.2%, 4.04× rendering and 2.20× end-to-end speedup (dynamic materialization cost unchanged); quality gap avg/worst 0.225/0.478 dB PSNR. Static headline speedup: unverified here.
- **Limitations:** dynamic-scene benefit is rendering-only (deformation/materialization not accelerated); small tiles (8, 16) remain inefficient.

#### FAST-GS: Frequency Aware Space-time Gaussian Splatting
- **Authors:** Z. Zhang, Z. Lu, P. Li, H. Duan, Y. Liu et al. · **Venue/date:** ICASSP 2026 (arXiv 3 Aug 2026) · **ID:** arXiv:2608.01958 · **Platform label:** GPU software.
- **Problem:** single-polynomial motion misses high-frequency motion and drifts over long sequences.
- **Method:** Fourier motion modelling (sinusoidal components for low- and high-frequency motion).
- **Key numbers:** not extracted → unverified.
- **Limitations:** more basis terms per Gaussian increase per-frame deformation cost.

#### D²-4DGS: Dual-Depth Guided Sparse-Camera 4D Gaussian Splatting
- **Authors:** J. Zhao · **Venue/date:** arXiv preprint, 3 Aug 2026 · **ID:** arXiv:2608.01588 · **Platform label:** GPU software.
- **Problem:** fewer cameras weaken geometry and cause floaters.
- **Method:** fuses scale-ambiguous dense monocular depth with sparse but consistent multi-view geometric depth to supervise 4DGS.
- **Key numbers:** not extracted → unverified.
- **Limitations:** depth-prior networks add cost; authors list lightweight priors as future work.

#### On the Design of Mixture-of-Experts for Dynamic Gaussian Splatting (MoE-GS)
- **Authors:** I.-H. Jin, H. Mun, J. Kim, K. Yun, K. Kong · **Venue/date:** IEEE TPAMI 2026 (arXiv 9 Jul 2026) · **ID:** arXiv:2607.08250 · **Platform label:** GPU software, MEASURED.
- **Problem:** one deformation model is not robust across motion types.
- **Method:** multiple deformation experts combined via a volume-aware MoE router; pruning recovers speed.
- **Key numbers (N3V):** STG 31.92 dB / 88.5 FPS / 609.5 MB; Ex4DGS 32.01 dB / 120 FPS / 122.8 MB; MoE-GS(N=2) 32.82 dB / 44 FPS / 878.7 MB; 75%-pruned 32.45 dB / 101 FPS / 281.3 MB.
- **Limitations:** quality gain costs FPS and memory — a direct quality/efficiency trade-off.

#### TemporalGS: Training-Free Plug-and-Play Acceleration for 3D Gaussian Splatting Rendering via Temporal Priors
- **Authors:** Y. Zhou, Z. Yang, X. Zuo, J. Lu · **Venue/date:** arXiv preprint, 3 Jul 2026 · **ID:** arXiv:2607.03390 · **Platform label:** GPU software, MEASURED.
- **Problem:** many visible Gaussians limit FPS; existing speedups need retraining.
- **Method:** reuses the previous frame's visibility/contribution prior to skip Gaussians in the next frame; no retraining.
- **Key numbers:** +74/+109/+121/+102 FPS average (25/29/33/27% faster) on 3DGS/LightGS/CP3DGS/C3DGS; example 259 → 383 FPS.
- **Limitations:** static scenes; quality effect under fast camera motion unverified.

#### Beyond Static Gaussians: An Empirical Investigation of Architectural Paradigms for Dynamic 3D Scene Reconstruction
- **Authors:** A. Ramlal, J. S. Zelek · **Venue/date:** JCVIS Vol. 11 No. 1 (arXiv 30 May 2026) · **ID:** arXiv:2606.00452, DOI 10.15353/jcvis.v11i1.10019 · **Platform label:** GPU software.
- **Problem:** no controlled comparison of structure-guided vs Gaussian-centric dynamic GS.
- **Method:** benchmarks both families on D-NeRF.
- **Key numbers:** structure-guided 14–48 MB; some Gaussian-centric models >1 GB.
- **Limitations:** synthetic D-NeRF only.

#### Accelerated Spatio-Temporal Gaussian Rendering via Kinematic and Semantic Priors
- **Authors:** D. Gridusov, M. Popov, S. Kolyubin · **Venue/date:** ECCV 2026 OpenSUN3D Workshop (arXiv 25 May 2026) · **ID:** arXiv:2605.25909 · **Platform label:** GPU software.
- **Problem:** MLP deformation interpolates but cannot extrapolate in time.
- **Method:** kinematic (physically grounded) and semantic priors for forecasting-capable 4DGS.
- **Key numbers:** not extracted → unverified.
- **Limitations:** tracker loses object consistency in failure cases.

#### GEMM-GS: Accelerating 3D Gaussian Splatting on Tensor Cores with GEMM-Compatible Blending
- **Authors:** H. Li, B. Zhu, F. Liu, Z. Wang, X. Liang et al. · **Venue/date:** DAC 2026 (accepted; arXiv 2 Apr 2026) · **ID:** arXiv:2604.02120 · **Platform label:** GPU software (CUDA), MEASURED.
- **Problem:** 3DGS blending has no GEMM, so GPU Tensor Cores sit idle.
- **Method:** algebraically re-forms the Gaussian exponent/blending into a GEMM-compatible form and runs it in a three-stage double-buffered CUDA kernel that overlaps compute and memory.
- **Key numbers:** 1.42× over vanilla 3DGS; an extra 1.47× on average when combined with other accelerations.
- **Limitations:** GPU-only; static scenes; gain bounded by blending share.

#### Does it matter which Gaussians you pick in 4D Gaussian streaming?
- **Authors:** A. Dahal, R. Abdelfattah, N. Rahimi · **Venue/date:** arXiv preprint, 18 Mar 2026 · **ID:** arXiv:2603.17227 · **Platform label:** GPU software.
- **Problem:** does the anchor-selection rule (8,192 anchors in IGS) matter?
- **Method:** fixes the IGS pipeline and swaps only the sampler (gradient, random, farthest-point, …).
- **Key numbers:** gradient 32.69 dB, random 32.70 dB, FPS-sampling 32.71 dB; all paired 95% CIs overlap.
- **Limitations:** negative result — anchor choice barely matters; adaptive budget not tested. Useful for hardware: a cheap sampler is enough.

#### FLICKER: A Fine-Grained Contribution-Aware Accelerator for Real-Time 3D Gaussian Splatting
- **Authors:** W. Ou, Z. Wu, Y. Zhang, D. Wu, F. Z. Hong et al. · **Venue/date:** DATE 2026 (accepted; arXiv 1 Mar 2026) · **ID:** arXiv:2603.01158 · **Platform label:** SIMULATION (cycle-accurate simulator with LPDDR4 51.2 GB/s model); no silicon.
- **Problem:** many Gaussians contribute nothing to a pixel; contribution-based skipping helps but the contribution tests themselves are too expensive at the edge.
- **Method:** adaptive leader pixels test contribution for a group, pixel-rectangle grouping, hierarchical Gaussian testing, and a mixed-precision datapath.
- **Key numbers:** up to 1.5× speedup, 2.6× energy efficiency and 14% less area than a SOTA accelerator; 19.8× speedup vs a representative edge GPU (abstract); adaptive mode cuts PSNR loss 73% vs uniform-sparse.
- **Limitations:** static scenes; approximation (leader pixels) costs some PSNR.

#### AirGS: Real-Time 4D Gaussian Streaming for Free-Viewpoint Video Experiences
- **Authors:** Z. Wang, J. Li, Y. Zhu · **Venue/date:** IEEE INFOCOM 2026 (arXiv 24 Dec 2025) · **ID:** arXiv:2512.20943 · **Platform label:** GPU software + network emulation.
- **Problem:** 4DGS degrades over long sequences and costs bandwidth/storage.
- **Method:** keyframe-based streaming with scene-change-aware Gaussian regeneration and transmission-size optimisation.
- **Key numbers:** PSNR deviation −20% at scene changes, frame PSNR >30 dB, 6× faster training, ≈−50% per-frame transmission vs SOTA 4DGS; 1.65 dB below per-frame 3DGS.
- **Limitations:** regions with large gradients cannot always be fitted by new Gaussians.

#### Nebula: City-Scale 3D Gaussian Splatting in VR via Collaborative Rendering and Accelerated Stereo Rasterization
- **Authors:** H. Zhu, Z. Liu, X. Li, A. Wu, J. Zhao et al. · **Venue/date:** arXiv preprint (cs.AR), 23 Dec 2025 · **ID:** arXiv:2512.20495 · **Platform label:** SIMULATION + synthesis (0.25 mm² add-on, 16 nm); no silicon.
- **Problem:** large-scale 3DGS does not fit on VR clients, and streaming rendered video from the cloud exceeds VR bandwidth.
- **Method:** streams intermediate results after the cloud-side LoD search instead of video; temporal-aware LoD search exploits frame-to-frame coherence; client-side stereo rasterization shares most computation between eyes bit-accurately.
- **Key numbers:** 2.7× motion-to-photon speedup; the paper writes "1925%" lower bandwidth/communication than video streaming three times (abstract, intro, evaluation). A reduction above 100% is not physically meaningful; it may mean 19.25× but the paper does not say so → treated as unverified.
- **Limitations:** static scenes; needs a cloud link.

#### D²GSLAM: 4D Dynamic Gaussian Splatting SLAM
- **Authors:** S. Zhu, Y. Huang, W. Wu, C. Jiang, Y. Chen et al. · **Venue/date:** arXiv preprint, 10 Dec 2025 · **ID:** arXiv:2512.09411 · **Platform label:** GPU software.
- **Problem:** dense SLAM removes dynamic objects instead of modelling them.
- **Method:** dynamic/static separation (YOLO-World + MobileSAM), static mapping plus 4D Gaussian modelling of moving objects, with accurate tracking.
- **Key numbers:** not extracted → unverified.
- **Limitations:** fails when the open-vocabulary segmenter misses a dynamic object.

#### Splatonic: Architecture Support for 3D Gaussian Splatting SLAM via Sparse Processing
- **Authors:** X. Huang, H. Zhu, T. Ma, Y. Xiong, F. Liu et al. · **Venue/date:** arXiv preprint (cs.AR), 24 Nov 2025 · **ID:** arXiv:2511.18755 · **Platform label:** GPU MEASURED (mobile Ampere) for the algorithm; accelerator by SIMULATION/synthesis (1.07 mm² scaled to 16 nm).
- **Problem:** 3DGS-SLAM tracking is too expensive for mobile SoCs.
- **Method:** adaptive sparse pixel sampling renders up to 256× fewer pixels; a pixel-based rendering pipeline with Gaussian-parallel rendering and pre-emptive α-checking; a pipelined accelerator for the new bottlenecks.
- **Key numbers:** up to 121.7× on bottleneck stages, 14.6× end-to-end and −86.1% energy on mobile Ampere GPU; mapping only 3.2×; α-checking is 43.4% / 33.6% of rasterization / reverse rasterization time; area 1.07 mm² vs GSCore 1.77 mm², GSArch 3.42 mm² (all scaled to 16 nm).
- **Limitations:** static-scene SLAM; mapping speedup much smaller than tracking.

#### Vorion: A RISC-V GPU with Hardware-Accelerated 3D Gaussian Rendering and Training
- **Authors:** Y. Wang, M. Yang, C.-P. Lo, J. P. Kulkarni (UT Austin) · **Venue/date:** arXiv preprint (cs.AR), 20 Nov 2025 · **ID:** arXiv:2511.16831 · **Platform label:** **MEASURED SILICON** (TSMC 16 nm FinFET, 4 mm² die / 1.6 mm² core) for the minimal system; scaled design by SIMULATION (Ramulator 2, LPDDR4-3200 51.2 GB/s).
- **Problem:** 3DGS rendering and training are too heavy for real-time edge rendering and real-time 4D reconstruction; no GPU has 3DGS fixed-function hardware.
- **Method:** a GPGPU prototype with Gaussian rasterizers added to a conventional rasterizer path with minimal change, z-tiling for parallelism, and a Gaussian/pixel-centric hybrid dataflow for both forward and backward.
- **Key numbers:** silicon (8 SIMT cores, 2 Gaussian rasterizers) renders 6.4–19 FPS across 0.57–1.15 V and 100–530 MHz; scaled 16-rasterizer design reaches 38.6 training iterations/s (simulated).
- **Limitations:** chip is attached through an FPGA with only 12.8 GB/s DRAM; scaled results are simulated; static 3DGS.

#### Neo: Real-Time On-Device 3D Gaussian Splatting with Reuse-and-Update Sorting Acceleration
- **Authors:** C. Oh, S. Oh, J. Hwang, Y. Kim, H. Sharma et al. · **Venue/date:** arXiv preprint (cs.AR), 17 Nov 2025 · **ID:** arXiv:2511.12930 · **Platform label:** SIMULATION (cycle-accurate) + synthesis (Synopsys DC, ASAP 7 nm predictive library); Neo-SW MEASURED on Orin AGX; no silicon.
- **Problem:** at high resolution, sorting is the main bottleneck of 3DGS rendering because of its DRAM bandwidth demand.
- **Method:** reuse-and-update sorting keeps the previous frame's Gaussian depth order per tile and only updates it, exploiting temporal coherence of ordering instead of re-sorting from scratch; a dedicated accelerator implements the update.
- **Key numbers:** 65.2 FPS average vs <13.6 FPS (Orin AGX) and <24.9 FPS (GSCore) at high resolution; up to 10.0× / 5.6× throughput vs Orin AGX / GSCore; sorting traffic over 60 frames 346.5 GB (Orin) / 104.6 GB (GSCore) / 19.6 GB (Neo), i.e. −94.4% / −81.3%.
- **Limitations:** static scenes with a moving camera; benefit depends on frame-to-frame coherence (large view jumps reduce reuse).

#### StreamSTGS: Streaming Spatial and Temporal Gaussian Grids for Real-Time Free-Viewpoint Video
- **Authors:** Z. Ke, Y. Liu, X. Zhou, T. Qiu · **Venue/date:** AAAI 2026 (arXiv 8 Nov 2025) · **ID:** arXiv:2511.06046 · **Platform label:** GPU software.
- **Problem:** 3DGS FVV methods need up to 10 MB per frame.
- **Method:** canonical Gaussians + temporal features stored as 2D grids encodable by video codecs.
- **Key numbers:** big-kicks 16.22 dB @0.114 MB/frame, painter 30.79 dB @0.149 MB/frame (as reported by VersionGS); own-paper averages not extracted.
- **Limitations:** static Gaussians clone/split to chase dynamic objects; fails on fast motion (VersionGS comparison).

#### BalanceGS: Algorithm-System Co-design for Efficient 3D Gaussian Splatting Training on GPU
- **Authors:** J. Wu, J. Xu, J. Li, Y. Zhou, J. Pan et al. · **Venue/date:** ASP-DAC 2026 (arXiv 16 Oct 2025) · **ID:** arXiv:2510.14564 · **Platform label:** GPU software, MEASURED.
- **Problem:** 3DGS training has skewed densification, imbalanced projection workload and fragmented color memory access.
- **Method:** workload-sensitive density control removes ~80% redundant Gaussians in dense regions and fills sparse areas; system-level load balancing for projection and locality-aware color storage for splatting.
- **Key numbers:** 85% of neighbouring Gaussians in dense regions can be merged with little quality impact (analysis); end-to-end training speedup: unverified (not extracted).
- **Limitations:** static scenes; GPU-specific.

#### RTGS: Real-Time 3D Gaussian Splatting SLAM via Multi-Level Redundancy Reduction
- **Authors:** L. Li, J. Qin, J. Peng, Z. Wan, H. Qu et al. · **Venue/date:** MICRO 2025 (arXiv 8 Oct 2025) · **ID:** arXiv:2510.06644 · **Platform label:** SIMULATION (validated simulator) of edge-GPU plug-in hardware; no silicon.
- **Problem:** 3DGS-SLAM is too slow for edge devices; redundancy exists at several pipeline levels but removing it has overhead.
- **Method:** adaptive Gaussian pruning that reuses gradients already computed in SLAM, plus dynamic downsampling and hardware plug-ins integrated into an edge GPU.
- **Key numbers:** ≥30 FPS real time on four datasets and three SLAM algorithms; up to 82.5× energy efficiency vs baseline; DRAM bandwidth utilization only 21.5% while L2 reaches 43.6% (traffic concentrates at L2).
- **Limitations:** static-scene SLAM; drift on long sequences left to future work.

#### OMG4: Optimized Minimal 4D Gaussian Splatting for Efficient Dynamic Scene Representation
- **Authors:** M. Lee, B. Lee, L. Y. Lee, E. Lee, J. Jeong et al. · **Venue/date:** arXiv preprint, 4 Oct 2025 · **ID:** arXiv:2510.03857 · **Platform label:** GPU software.
- **Problem:** explicit 4DGS needs millions of Gaussians; temporal attributes make quantisation unstable.
- **Method:** staged sampling/pruning/merging of Gaussians, then attribute compression adapted to 4D.
- **Key numbers:** Real-Time4DGS 2 GB → ~3 MB (−99%) with comparable quality; vs GIFStream 10.0 MB → 3.59 MB (−64%) on N3DV.
- **Limitations:** baselines OOM on full sequences, so some are evaluated on clips.

#### 4DGCPro: Efficient Hierarchical 4D Gaussian Compression for Progressive Volumetric Video Streaming
- **Authors:** Z. Zheng, Z. Wu, H. Zhong, Y. Tian, N. Cao et al. (SJTU) · **Venue/date:** NeurIPS 2025 (arXiv 22 Sep 2025) · **ID:** arXiv:2509.17513 · **Platform label:** software; real-time decode/render on mobile claimed (device unverified).
- **Problem:** volumetric codecs cannot vary quality/bitrate within one model.
- **Method:** hierarchical 4D Gaussians in one progressive bitstream; enhancement layers add quality.
- **Key numbers:** +0.10 MB enhancement layer raises 30.04 → 31.18 dB (Fig. 1 example).
- **Limitations:** layer structure fixed at training.

#### GS-TG: 3D Gaussian Splatting Accelerator with Tile Grouping
- **Authors:** J. Jo, J. Park (Korea Univ.) · **Venue/date:** DAC 2025 (arXiv 31 Aug 2025) · **ID:** arXiv:2509.00911 · **Platform label:** RTL + synthesis (Synopsys DC, 28 nm), power by PrimeTime PX, speed by cycle-level SIMULATION; no silicon.
- **Problem:** large tiles reduce redundant sorting but increase useless rasterization; small tiles do the opposite.
- **Method:** small tiles are grouped into large tiles for sorting, and a per-Gaussian bitmask marks which small tiles it touches so rasterization stays fine-grained.
- **Key numbers:** A6000 runs vanilla 3DGS at only 15–25 FPS at 3840×2160 (motivation); speedup of GS-TG vs baseline accelerator: unverified (not extracted).
- **Limitations:** static scenes; bitmask generation/group sort map poorly to GPUs, so it needs custom hardware.

#### AGS: Accelerating 3D Gaussian Splatting SLAM via CODEC-Assisted Frame Covisibility Detection
- **Authors:** H. He, N. Jing, L. Jiang, X. Liang, Z. Song (SJTU) · **Venue/date:** arXiv preprint (cs.AR), 30 Aug 2025 · **ID:** arXiv:2509.00433 · **Platform label:** cycle-level SIMULATION (with Ramulator) driven by GPU traces; no silicon.
- **Problem:** 3DGS-SLAM needs many training iterations per frame over many Gaussians, so throughput is low.
- **Method:** coarse-then-fine pose tracking; Gaussian contribution information shared across adjacent frames; a frame-covisibility detection engine reuses motion data already computed by the video CODEC.
- **Key numbers:** mapping PSNR 21.55 dB vs 20.87 dB for a naive Droid+SplatAM combination; headline speedup: unverified (not extracted).
- **Limitations:** static-scene SLAM; relies on a hardware CODEC being present.

#### LS-Gaussian: No Redundancy, No Stall — Lightweight Streaming 3D Gaussian Splatting for Real-time Rendering
- **Authors:** L. Wei, J. Tang, F. Fei, B. Shi, R. Wang et al. (PKU) · **Venue/date:** ICCAD 2025 (arXiv 29 Jul 2025) · **ID:** arXiv:2507.21572 · **Platform label:** algorithm MEASURED on Jetson AGX Orin; hardware RTL synthesized in 16 nm FinFET (1.84 mm²); no silicon.
- **Problem:** continuous-viewpoint rendering recomputes nearly the same frame, and imbalanced tiles stall parallel hardware.
- **Method:** viewpoint transformation warps the previous frame and sparsely re-renders only what changed; a Load Distribution Unit balances tile workloads.
- **Key numbers:** 5.41× average speedup on Jetson AGX Orin; average SSIM loss 0.005 and PSNR loss 1.4 dB over six scenes (vs Potamoi 0.063 / 6.8 dB); area 1.84 mm² (+0.39 mm² over 16 nm-scaled GSCore 1.45 mm²; MetaSapiens 2.73 mm²).
- **Limitations:** 1.4 dB PSNR loss is large for a quality-sensitive product; static scenes.

#### 3DGauCIM: Accelerating Static/Dynamic 3D Gaussian Splatting via Digital CIM for High Frame Rate Real-Time Edge Rendering
- **Authors:** W.-H. Huang, C.-J. Shih, J.-W. Su, S. W. Wang, V. Garg et al. · **Venue/date:** arXiv preprint (cs.AR), 25 Jul 2025 · **ID:** arXiv:2507.19133 · **Platform label:** SIMULATION using **measured statistics from a 16 nm DCIM prototype chip**; the full 3DGauCIM system is not fabricated.
- **Problem:** dynamic 3DGS on the edge loads all parameters from DRAM for culling, has more parameters (longer sort), lower buffer reuse and operations that do not map to DCIM.
- **Method:** DRAM-access-reduction frustum culling, adaptive tile grouping for buffer reuse, adaptive-interval-initialized bucket-bitonic sort, and a DCIM-friendly computation flow.
- **Key numbers:** 203 FPS at 0.28 W (large real-world static) and 211 FPS at 0.63 W (large real-world dynamic); ATG energy reduction 5.2× average, 2.2× extreme.
- **Limitations:** dynamic model and dataset details are limited; system numbers depend on extrapolated DCIM data.

#### GCC: A 3DGS Inference Architecture with Gaussian-Wise and Cross-Stage Conditional Processing
- **Authors:** M. Pei, G. Li, J. Si, Z. Zhu, Z. Mo et al. · **Venue/date:** MICRO 2025 (arXiv 21 Jul 2025) · **ID:** arXiv:2507.15300 · **Platform label:** SystemVerilog synthesized at 28 nm / 1 GHz (compute units) + Python cycle-accurate SIMULATION; no silicon.
- **Problem:** in the standard decoupled preprocess→render dataflow, many preprocessed Gaussians are never used and the same Gaussian is reloaded for every tile it touches.
- **Method:** cross-stage conditional processing interleaves preprocessing with rendering to skip unneeded preprocessing; Gaussian-wise rendering finishes all work for one Gaussian before moving on; alpha-based boundary identification.
- **Key numbers:** 5.24× area-normalized speedup and 3.35× energy efficiency vs GSCore (GSCore re-simulated within 3% of its paper); peak 667 FPS on Lego; <1 W.
- **Limitations:** static scenes; GSCore comparison uses the authors' re-implementation.

#### STREAMINGGS: Voxel-Based Streaming 3D Gaussian Splatting with Memory Optimization and Architectural Support
- **Authors:** C. Zhang, Y. Feng, J. Zhao, G. Liu, W. Ding et al. (SJTU) · **Venue/date:** arXiv preprint, 9 Jun 2025 · **ID:** arXiv:2506.09070 · **Platform label:** synthesized (Synopsys/Cadence, TSMC 32 nm) + SIMULATION; no silicon.
- **Problem:** mobile GPUs reach only 2–9 FPS (Orin NX) and accelerators ignore redundant DRAM traffic.
- **Method:** switches from tile-centric to memory-centric (voxel-ordered, fully streaming) rendering so each Gaussian is fetched once and pipelined finely.
- **Key numbers:** up to 45.7× speedup and 62.9× energy savings vs mobile Ampere GPU; area similar to GSCore scaled to 32 nm (5.53 mm²).
- **Limitations:** static scenes; voxel streaming order must be precomputed.

#### Lumina: Real-Time Mobile Neural Rendering by Exploiting Computational Redundancy
- **Authors:** Y. Feng, W. Lin, Y. Cheng, Z. Liu, J. Leng et al. · **Venue/date:** arXiv preprint (cs.AR), 6 Jun 2025 · **ID:** arXiv:2506.05682 · **Platform label:** SIMULATION of mobile-SoC integration (0.4% hardware overhead); no silicon.
- **Problem:** 3DGS is too heavy for mobile SoCs.
- **Method:** S² exploits temporal coherence by sorting/sharing across frames; radiance caching (RC) reuses color integration results to skip rasterization; LuminCore accelerates cache lookup and rasterization.
- **Key numbers:** 4.5× speedup and 5.3× energy reduction vs mobile Volta GPU; ~0.2 dB PSNR loss; LuminCore alone (NRU+GPU) −62% energy.
- **Limitations:** needs continuous video sequences (cannot use Mip-NeRF360 / DeepBlending); static scenes.

#### Hybrid 3D-4D Gaussian Splatting for Fast Dynamic Scene Representation (3D-4DGS)
- **Authors:** S. Oh, Y. Lee, H. Jeon, E. Park · **Venue/date:** arXiv preprint, 19 May 2025 · **ID:** arXiv:2505.13215 · **Platform label:** GPU software, MEASURED.
- **Problem:** 4D Gaussians are wasted on static regions.
- **Method:** Gaussians with small temporal scale stay 4D, static ones are converted to 3D during training.
- **Key numbers:** comparison table includes 4DGS (Yang) 28.89 dB / 10.4 h training / 84 GB VRAM / 90 FPS / 2.68 GB on a high-res dataset; own numbers not extracted → unverified.
- **Limitations:** conversion threshold is a hyper-parameter.

#### StochasticSplats: Stochastic Rasterization for Sorting-Free 3D Gaussian Splatting
- **Authors:** S. Kheradmand, D. Vicini, G. Kopanas, D. Lagun, K. M. Yi et al. (Google/UBC) · **Venue/date:** arXiv 31 Mar 2025 (ICCV 2025 per project page; venue not verified here) · **ID:** arXiv:2503.24366 · **Platform label:** software (OpenGL), MEASURED.
- **Problem:** sorted rendering causes popping, and for a fixed model gives little control over cost vs fidelity.
- **Method:** unbiased Monte Carlo estimator of the volume rendering equation; no sorting; samples-per-pixel sets the cost/quality trade-off.
- **Key numbers:** not extracted → unverified.
- **Limitations:** Monte Carlo noise at low sample counts.

#### Gaussian Blending Unit (GBU): An Edge GPU Plug-in for Real-Time Gaussian-Based Rendering in AR/VR
- **Authors:** Z. Ye, Y. Fu, J. Zhang, L. Li, Y. Zhang et al. (Georgia Tech) · **Venue/date:** HPCA 2025 (arXiv 30 Mar 2025) · **ID:** arXiv:2503.23625 · **Platform label:** synthesis + SIMULATION of a GPU plug-in; baseline profiling MEASURED on Jetson Orin NX; no silicon.
- **Problem:** Orin NX renders Mip-NeRF360 scenes at only 7–17 FPS; the blending stage dominates (would need 1.1 TFLOPs for 60 FPS, 58% of Orin NX peak).
- **Method:** a row-centric tile engine that evaluates Gaussian contributions incrementally along rows, and a Gaussian reuse cache to cut off-chip traffic; plugs into an edge GPU and supports several Gaussian-based apps.
- **Key numbers:** speedup/energy headline truncated in the extracted text → unverified.
- **Limitations:** static scenes; blending-only plug-in.

#### Disentangled 4D Gaussian Splatting: Rendering High-Resolution Dynamic World at 343 FPS
- **Authors:** H. Feng, H. Sun, W. Xie, Z. Zuo, Z. Liu · **Venue/date:** arXiv preprint, 28 Mar 2025 · **ID:** arXiv:2503.22159 · **Platform label:** MEASURED on RTX 3090.
- **Problem:** 4D primitives require slicing and 4D covariance math per frame.
- **Method:** decouples temporal and spatial components so no 4D slicing is needed.
- **Key numbers:** 343 FPS at 1352×1014 on RTX 3090 (Plenoptic/N3DV); storage ≥4.5% lower.
- **Limitations:** storage gain is small.

#### 4DGC: Rate-Aware 4D Gaussian Compression for Efficient Streamable Free-Viewpoint Video
- **Authors:** Q. Hu, Z. Zheng, H. Zhong, S. Fu, L. Song et al. · **Venue/date:** CVPR 2025 (arXiv 24 Mar 2025) · **ID:** arXiv:2503.18421 · **Platform label:** GPU software.
- **Problem:** dynamic representation and compression are optimised separately.
- **Method:** motion-aware grid + rate-distortion-aware training with differentiable quantisation and entropy model.
- **Key numbers:** 0.5 MB/frame vs 8.1 MB for 3DGStream at comparable quality.
- **Limitations:** per-frame online training time unverified.

#### IGS: Instant Gaussian Stream
- **Authors:** J. Yan, R. Peng, Z. Wang, L. Tang, J. Yang et al. · **Venue/date:** arXiv 21 Mar 2025 (CVPR 2025 per later citations; not verified here) · **ID:** arXiv:2503.16979 · **Platform label:** GPU software.
- **Problem:** streaming methods need 10 s+ per frame and accumulate error.
- **Method:** generalised anchor-driven Gaussian motion network (feed-forward, 8,192 anchors driving the scene by LBS) + key-frame refinement.
- **Key numbers:** 32.69 dB (N3DV, as re-run by arXiv:2603.17227); per-frame time not extracted → unverified.
- **Limitations:** error accumulation between key frames.

#### 4D Gaussian Splatting SLAM
- **Authors:** Y. Li, Y. Fang, Z. Zhu, K. Li, Y. Ding et al. · **Venue/date:** arXiv preprint, 20 Mar 2025 · **ID:** arXiv:2503.16710 · **Platform label:** GPU software.
- **Problem:** dynamic SLAM usually discards moving objects.
- **Method:** RGB-D motion masks split static/dynamic Gaussians; dynamic Gaussians get a sparse-control-point motion field; incremental 4D radiance field.
- **Key numbers:** not extracted → unverified.
- **Limitations:** depends on pre-trained segmentation for motion masks.

#### GauRast: Enhancing GPU Triangle Rasterizers to Accelerate 3D Gaussian Splatting
- **Authors:** S. Li, B. Keller, Y. C. Lin, B. Khailany (Georgia Tech / NVIDIA) · **Venue/date:** DAC 2025 (arXiv 20 Mar 2025) · **ID:** arXiv:2503.16681 · **Platform label:** synthesis/SIMULATION of modified rasterizer; baseline MEASURED on Orin NX at 10 W.
- **Problem:** dedicated 3DGS accelerators cost integration area; GPUs already have triangle rasterizers.
- **Method:** extends existing triangle-rasterizer hardware to evaluate Gaussian splats.
- **Key numbers:** 23× speed and 24× energy improvement; 0.2% SoC area overhead.
- **Limitations:** static scenes; depends on GPU vendor adoption.

#### 1000+ FPS 4D Gaussian Splatting for Dynamic Scene Rendering (4DGS-1K)
- **Authors:** Y. Yuan, Q. Shen, X. Yang, X. Wang (NUS) · **Venue/date:** arXiv 20 Mar 2025 (NeurIPS 2025 per project page; not verified here) · **ID:** arXiv:2503.16422 · **Platform label:** GPU software, MEASURED.
- **Problem:** 4D-primitive 4DGS wastes storage/time on short-lifespan Gaussians and on inactive Gaussians each frame.
- **Method:** spatial-temporal variation score prunes short-lifespan Gaussians; key-frame-shared temporal mask restricts rasterization to active Gaussians.
- **Key numbers:** >1000 FPS on modern GPU (title claim); exact storage reduction and PSNR delta not extracted → unverified.
- **Limitations:** built on Yang et al. 4DGS; the mask is shared across key-frame intervals.

#### SeeLe: A Unified Acceleration Framework for Real-Time Gaussian Splatting
- **Authors:** X. Huang, H. Zhu, Z. Liu, W. Lin, X. Liu et al. · **Venue/date:** arXiv preprint, 7 Mar 2025 · **ID:** arXiv:2503.05168 · **Platform label:** GPU software, MEASURED on mobile GPU (AGX Orin).
- **Problem:** AGX Orin has 3.4% of an A100's compute and runs 3DGS at ~20 FPS on real scenes vs a 90 FPS VR target.
- **Method:** hybrid preprocessing (view-dependent scene partitions + online filtering) removes irrelevant Gaussians; contribution-aware rasterization prioritises high-contribution Gaussians.
- **Key numbers:** headline speedup not extracted → unverified.
- **Limitations:** static scenes; partitioning is precomputed per scene.

#### VR-Pipe: Streamlining Hardware Graphics Pipeline for Volume Rendering
- **Authors:** J. Lee, J. Kim, J. Park, J. Sim (SNU, GSCore group) · **Venue/date:** HPCA 2025 (arXiv 24 Feb 2025) · **ID:** arXiv:2502.17078 · **Platform label:** SIMULATION of modified GPU (frequency/bandwidth matched to Jetson AGX Orin 30 W).
- **Problem:** 3DGS has been evaluated on shader cores, not on the fixed-function graphics pipeline, which is inefficient for volume-style blending.
- **Method:** implements 3DGS through graphics APIs, then adds early termination support for blending and multi-granular tile binning with quad merging to the hardware pipeline.
- **Key numbers:** headline speedup not extracted → unverified.
- **Limitations:** static scenes; requires GPU vendor changes.

#### Exploring the Versal AI Engine for 3D Gaussian Splatting
- **Authors:** K. Shimamura, A. Ohno, S. Takamaeda-Yamazaki (U. Tokyo) · **Venue/date:** arXiv preprint (cs.AR), 17 Feb 2025 · **ID:** arXiv:2502.11782 · **Platform label:** AMD Versal AI Engine (board vs simulator not extracted → unverified).
- **Problem:** few studies show how commercial spatial VLIW arrays perform on real workloads.
- **Method:** maps 3DGS Gaussian feature computation (projection/covariance) onto AI Engine tiles with a dedicated algorithm.
- **Key numbers:** not extracted → unverified.
- **Limitations:** feature computation only, not full rendering.

#### Representing Long Volumetric Video with Temporal Gaussian Hierarchy
- **Authors:** Z. Xu, Y. Xu, Z. Yu, S. Peng, J. Sun et al. (ZJU) · **Venue/date:** SIGGRAPH Asia 2024 / ACM TOG · **ID:** arXiv:2412.09608, DOI 10.1145/3687919 · **Platform label:** GPU software, MEASURED.
- **Problem:** 4D methods handle only 1–2 s clips and blow up memory on long videos.
- **Method:** temporal hierarchy of Gaussian segments — slow-changing content stored at coarse temporal levels, fast content at fine levels; only active segments loaded.
- **Key numbers:** 18,000 frames with 17.2 GB VRAM and 2.2 GB storage; 1,200 frames 10.2 GB VRAM / 0.42 GB storage (2×/4× smaller than the next best).
- **Limitations:** dense multi-view capture required.

#### QUEEN: QUantized Efficient ENcoding of Dynamic Gaussians for Streaming Free-viewpoint Videos
- **Authors:** S. Girish, T. Li, A. Mazumdar, A. Shrivastava, D. Luebke et al. (NVIDIA) · **Venue/date:** NeurIPS 2024 (arXiv 5 Dec 2024) · **ID:** arXiv:2412.04469 · **Platform label:** GPU software.
- **Problem:** online FVV needs fast incremental training and tiny per-frame updates.
- **Method:** learns quantised attribute residuals between frames with learned latent decoders; gating skips static Gaussians.
- **Key numbers:** 0.60 MB/frame (N3DV config) and 1.49 MB (Immersive config) vs 7.8 MB for 3DGStream at higher PSNR.
- **Limitations:** residual stream still 14.4 MB/s at 30 FPS (per S²GS).

#### HiCoM: Hierarchical Coherent Motion for Streamable Dynamic Scene with 3D Gaussian Splatting
- **Authors:** Q. Gao, J. Meng, C. Wen, J. Chen, J. Zhang · **Venue/date:** NeurIPS 2024 (arXiv 12 Nov 2024) · **ID:** arXiv:2411.07541 · **Platform label:** GPU software.
- **Problem:** streaming 3DGS overfits with limited views and grows Gaussians excessively.
- **Method:** perturbation-smoothed initial model; hierarchical coherent motion (a few motion levels) shared across nearby Gaussians.
- **Key numbers:** adding motion levels +0.25 dB (Coffee Martini), +0.59 dB (Discussion); diminishing returns beyond a few levels.
- **Limitations:** instability/overfitting to training views.

#### Grid4D: 4D Decomposed Hash Encoding for High-Fidelity Dynamic Gaussian Splatting
- **Authors:** J. Xu, Z. Fan, J. Yang, J. Xie · **Venue/date:** NeurIPS 2024 (arXiv 28 Oct 2024) · **ID:** arXiv:2410.20815 · **Platform label:** GPU software.
- **Problem:** plane decomposition (HexPlane) assumes low rank and overlaps features.
- **Method:** decomposes 4D into one spatial 3D hash and three temporal 3D hashes.
- **Key numbers:** deform module 221.1 MB (reported by CC-4DGS).
- **Limitations:** large hash tables → memory-bound, poor for edge.

#### Ex4DGS: Fully Explicit Dynamic Gaussian Splatting
- **Authors:** J. Lee, C.-Y. Won, H. Jung, I. Bae, H.-G. Jeon · **Venue/date:** NeurIPS 2024 (arXiv 21 Oct 2024) · **ID:** arXiv:2410.15629 · **Platform label:** GPU software.
- **Problem:** implicit deformation slows training/rendering.
- **Method:** static/dynamic separation; dynamic Gaussians store explicit keyframe positions/rotations and interpolate between them.
- **Key numbers:** 32.01 dB / 120 FPS / 122.8 MB on N3V (as reported in MoE-GS table); own-paper numbers not extracted.
- **Limitations:** keyframe interval fixed; struggles with appearing/disappearing objects.

#### MEGA: Memory-Efficient 4D Gaussian Splatting for Dynamic Scenes
- **Authors:** X. Zhang, Z. Liu, Y. Zhang, X. Ge, D. He et al. · **Venue/date:** ICCV 2025 (arXiv 17 Oct 2024) · **ID:** arXiv:2410.13613 · **Platform label:** GPU software.
- **Problem:** millions of 4D Gaussians with large attributes (SH colour dominates).
- **Method:** replaces per-Gaussian SH with DC colour + small shared AC predictor; entropy-constrained deformation to spread Gaussians and reduce count.
- **Key numbers:** +0.22 dB over STG with −40% storage.
- **Limitations:** compaction alone does not reduce the number of Gaussians enough.

#### V³: Viewing Volumetric Videos on Mobiles via Streamable 2D Dynamic Gaussians
- **Authors:** P. Wang, Z. Zhang, L. Wang, K. Yao, S. Xie et al. (ShanghaiTech) · **Venue/date:** SIGGRAPH Asia 2024 / ACM TOG (arXiv 20 Sep 2024; venue per project page, not verified here) · **ID:** arXiv:2409.13648 · **Platform label:** MEASURED on desktop, tablet and phone.
- **Problem:** dynamic 3DGS is too heavy to stream to phones.
- **Method:** Gaussian attributes packed into 2D grids coded as ordinary video, decoded by hardware video decoders.
- **Key numbers:** desktop 435 FPS (download 7.8 / decode 13 / render 2.3 ms), tablet 96 FPS, phone 27 FPS (16.8 / 13.6 / 37.0 ms).
- **Limitations:** all Gaussians treated as frame-varying (ResStreamGS shows 11.7 dB QP drop on Neu3D).

#### MetaSapiens: Real-Time Neural Rendering with Efficiency-Aware Pruning and Accelerated Foveated Rendering
- **Authors:** W. Lin, Y. Feng, Y. Zhu (Rochester) · **Venue/date:** ASPLOS 2025 · **ID:** arXiv:2407.00435, DOI 10.1145/3669940.3707227 · **Platform label:** algorithm MEASURED on mobile GPU; accelerator SIMULATION/synthesis (area ≈6 mm² class).
- **Problem:** point-based neural rendering is not real time on mobile; a 3DGS bicycle scene is ~1.4 GB, still ~490 MB after pruning.
- **Method:** efficiency-aware pruning (optimises speed, not just count), foveated rendering that relaxes quality in the periphery, and an accelerator that fixes FR load imbalance.
- **Key numbers:** at ~6 mm², 1.6× faster than GSCore (area-matched); pruned variants hold 99/98/97% of dense PSNR.
- **Limitations:** static scenes; quality judged by human-visual study as well as PSNR.

#### E-D3DGS: Per-Gaussian Embedding-Based Deformation for Deformable 3D Gaussian Splatting
- **Authors:** J. Bae, S. Kim, Y. Yun, H. Lee, G. Bang et al. · **Venue/date:** ECCV 2024 (arXiv 4 Apr 2024) · **ID:** arXiv:2404.03613 · **Platform label:** GPU software.
- **Problem:** coordinate-based deformation fields blur complex motion.
- **Method:** per-Gaussian learnable embedding + temporal embedding → coarse and fine deformation decoders.
- **Key numbers:** 32.83 dB / SSIM 0.900 / 46.2 FPS (as reported in CC-4DGS); own numbers not extracted.
- **Limitations:** MLP evaluated per Gaussian per frame (the cost CueSplat removes).

#### HAC: Hash-grid Assisted Context for 3D Gaussian Splatting Compression
- **Authors:** Y. Chen, Q. Wu, W. Lin, M. Harandi, J. Cai · **Venue/date:** ECCV 2024 (arXiv 21 Mar 2024) · **ID:** arXiv:2403.14530 · **Platform label:** GPU software.
- **Problem:** unordered anchors are hard to entropy-code.
- **Method:** a binary hash grid provides context for entropy coding Scaffold-GS anchors.
- **Key numbers:** ~60–100× storage reduction (HAC/HAC++ as cited by GSCV++); BungeeNeRF bit allocation 14.90 MB anchors-features etc.
- **Limitations:** static scenes; context-model decoding is serial (hardware-unfriendly).

#### 3DGStream: On-the-Fly Training of 3D Gaussians for Efficient Streaming of Photo-Realistic Free-Viewpoint Videos
- **Authors:** J. Sun, H. Jiao, G. Li, Z. Zhang, L. Zhao et al. · **Venue/date:** CVPR 2024 Highlight (arXiv 3 Mar 2024) · **ID:** arXiv:2403.01444 · **Platform label:** GPU software.
- **Problem:** offline training of whole sequences; no real-time streaming.
- **Method:** per-frame Neural Transformation Cache (hash-grid MLP) moves Gaussians; adaptive addition of new Gaussians.
- **Key numbers:** 7.8–8.1 MB/frame (as cited by QUEEN and 4DGC); own per-frame training time not extracted → unverified.
- **Limitations:** cannot represent new objects well; large per-frame size.

#### 4D-Rotor Gaussian Splatting
- **Authors:** Y. Duan, F. Wei, Q. Dai, Y. He, W. Chen et al. · **Venue/date:** SIGGRAPH 2024 (arXiv 5 Feb 2024) · **ID:** arXiv:2402.03307 · **Platform label:** MEASURED on RTX 4090 / 3090.
- **Problem:** canonical+deformation fails on sudden motion.
- **Method:** 4D Gaussians with 4D rotors, sliced to 3D per timestamp; entropy and 4D-consistency losses.
- **Key numbers:** 583 FPS (RTX 4090) and 277 FPS (RTX 3090) at 1352×1014, vs 114 FPS for Yang et al.
- **Limitations:** storage of 4D primitives remains large (unverified per-scene MB here).

#### StopThePop: Sorted Gaussian Splatting for View-Consistent Real-time Rendering
- **Authors:** L. Radl, M. Steiner, M. Parger, A. Weinrauch, B. Kerbl et al. · **Venue/date:** SIGGRAPH 2024 / ACM TOG 43(4), Jul 2024 · **ID:** arXiv:2402.00525 · **Platform label:** CUDA software, MEASURED.
- **Problem:** per-Gaussian global sort causes popping under view rotation; full per-pixel sort is too costly.
- **Method:** hierarchical rasterization that re-sorts and culls splats per pixel with small overhead.
- **Key numbers:** not extracted → unverified.
- **Limitations:** early ray termination cannot precede sorting; hard to port to standard graphics APIs.

#### STG: Spacetime Gaussian Feature Splatting for Real-Time Dynamic View Synthesis
- **Authors:** Z. Li, Z. Chen, Z. Li, Y. Xu (OPPO US) · **Venue/date:** CVPR 2024 (arXiv 28 Dec 2023) · **ID:** arXiv:2312.16812 · **Platform label:** GPU software, MEASURED.
- **Problem:** high resolution, real time and compact storage simultaneously.
- **Method:** temporal radial-basis opacity, polynomial motion and rotation per Gaussian; neural features instead of SH; guided sampling.
- **Key numbers:** N3DV 32.05 dB class (MoE-GS table lists 31.92 dB re-run); Technicolor 33.6 dB / LPIPS 0.084 / 86.7 FPS; Google Immersive 29.2 dB / 99 FPS / 1.2 MB per frame.
- **Limitations:** trained per 50-frame segment; total storage 609.5 MB on N3V (re-run).

#### SC-GS: Sparse-Controlled Gaussian Splatting for Editable Dynamic Scenes
- **Authors:** Y.-H. Huang, Y.-T. Sun, Z. Yang, X. Lyu, Y.-P. Cao et al. · **Venue/date:** CVPR 2024 (arXiv 4 Dec 2023) · **ID:** arXiv:2312.14937 · **Platform label:** GPU software.
- **Problem:** dense per-Gaussian motion is redundant and hard to edit.
- **Method:** a few hundred sparse control points carry 6-DoF motion (MLP); dense Gaussians follow by linear blend skinning.
- **Key numbers:** not extracted → unverified.
- **Limitations:** sensitive to inaccurate camera poses.

#### LightGaussian: Unbounded 3D Gaussian Compression with 15x Reduction and 200+ FPS
- **Authors:** Z. Fan, K. Wang, K. Wen, Z. Zhu, D. Xu et al. · **Venue/date:** NeurIPS 2024 (arXiv 28 Nov 2023) · **ID:** arXiv:2311.17245 · **Platform label:** GPU software.
- **Problem:** 3DGS scenes take ~1 GB+ (bicycle 1.4 GB).
- **Method:** global-significance pruning, SH distillation, vector quantisation.
- **Key numbers:** 782 MB → 45 MB and 144 → 237 FPS at similar quality.
- **Limitations:** static scenes.

#### Dynamic 3D Gaussians: Tracking by Persistent Dynamic View Synthesis
- **Authors:** J. Luiten, G. Kopanas, B. Leibe, D. Ramanan · **Venue/date:** 3DV 2024 (arXiv 18 Aug 2023) · **ID:** arXiv:2308.09713 · **Platform label:** GPU software.
- **Problem:** joint dynamic view synthesis and dense 6-DoF tracking.
- **Method:** per-frame online optimisation of Gaussian position/rotation with persistent colour/opacity/size and local-rigidity losses.
- **Key numbers:** Dy3DGS row in 3D-4DGS table: 25.91 dB / 37.1 h / 19.5 GB storage — storage is the weakness.
- **Limitations:** needs dense multi-view capture; cannot handle new objects entering.

### 1.B Baselines (owner already read — cited, not re-read)

Baselines are cited from the owner's notes and abstracts only; they were not re-read as new work.

| # | Paper | Venue/date | ID | Platform label | Role / key number used in this survey |
|---|---|---|---|---|---|
| B1 | CueSplat: Learning to Schedule Sparse Deformation for Dynamic Gaussian Splatting on Edge Devices — Q. Tang, H. Zhu, S. Sun, A. Wu, J. Leng et al. | arXiv, 17 Nov 2026 | arXiv:2611.22095 | GPU software, MEASURED on A6000, Jetson AGX Orin, Orin NX | Post-training learned router + history prediction + budgeted Top-k exact deformation. 1.75–6.74× on AGX Orin over 12 backbone/dataset pairs, up to 8.15× on Orin NX; GaGS/Technicolor 3.79 → 30.91 FPS on Orin NX; worst dataset-average ΔPSNR 0.16 dB. |
| B2 | GSPIM: A Temporal-Locality-Aware 4DGS Accelerator with Polymorphic PIM and Multi-Frame Rendering Dataflow — Z. Ma, Z. Fan, H. Wu, D. Fan, X. Ye et al. | HPCA 2027 (arXiv 3 Nov 2026) | arXiv:2611.04804 | SIMULATION (LPDDR5-PIM model, 1.4%/1.2% bandwidth/latency error vs reference); no silicon | Temporal filtering is 52.4–63.3% of Orin time; sorting+raster 26.5–34.8%. 4.15× speedup / 4.99× energy (owner's notes); max PSNR difference 0.006 dB; +18.7172 mm² across 32 LPDDR5 dies. |
| B3 | OuroWorld: Bringing Any 3D World Alive as Diverse, Endlessly Looping 3D Cinemagraphs — Y.-Z. Xie, T.-W. Chou, Y.-H. Li, K. Zhang, Z. Wang, Y.-L. Liu | arXiv, 8 Oct 2026 | arXiv:2610.12461 | GPU software | Generative periodic (looping) deformation; numbers unverified here. |
| B4 | A 129FPS Full HD Real-Time Accelerator for 3D Gaussian Splatting — F.-C. Chang, T.-S. Chang | arXiv, 11 Apr 2026 | arXiv:2604.10223 | Gate-level SIMULATION, not silicon (owner's note) | 129 FPS at Full HD (title). |
| B5 | Sort-free Gaussian Splatting via Weighted Sum Rendering — Q. Hou, R. Rauwendaal, Z. Li, H. Le, F. Farhadzadeh et al. (Qualcomm) | ICLR 2025 (arXiv 24 Oct 2024) | arXiv:2410.18931 | Mobile-GPU software | Replaces sorted alpha blending by weighted sums; speed/quality numbers unverified here. |
| B6 | 4D Gaussian Splatting for Real-Time Dynamic Scene Rendering (HexPlane) — G. Wu et al. | CVPR 2024 (arXiv 12 Oct 2023) | arXiv:2310.08528 | GPU software | HexPlane features + small MLP deformation; 82 FPS at 800×800 on RTX 3090 (abstract). |
| B7 | Real-time Photorealistic Dynamic Scene Representation and Rendering with 4D Gaussian Splatting — Z. Yang et al. | ICLR 2024 (arXiv 16 Oct 2023) | arXiv:2310.10642 | GPU software | Native 4D primitives sliced per time; 28.89 dB / 84 GB VRAM / 2.68 GB on a high-res set (3D-4DGS table); 2 GB model (OMG4); 114 FPS on RTX 3090 (4D-Rotor). |
| B8 | Deformable 3D Gaussians — Z. Yang et al. | CVPR 2024 (arXiv 22 Sep 2023) | arXiv:2309.13101 | GPU software | Canonical Gaussians + time-conditioned MLP evaluated for every Gaussian every frame. |
| B9 | 3D Gaussian Splatting for Real-Time Radiance Field Rendering — B. Kerbl, G. Kopanas, T. Leimkühler, G. Drettakis | SIGGRAPH 2023 / ACM TOG (arXiv 8 Aug 2023) | arXiv:2308.04079 | GPU software | ≥30 FPS at 1080p (abstract); tile sort + front-to-back alpha blending. |
| B10 | GSCore: Efficient Radiance Field Rendering via Architectural Support for 3D Gaussian Splatting — J. Lee, S. Lee, J. Lee, J. Park, J. Sim | ASPLOS 2024 | DOI 10.1145/3620666.3651385 | Synthesis (28 nm) + SIMULATION | 15.86× average over mobile GPU; shape-aware intersection, hierarchical sorting, subtile skipping. Scaled 1.45 mm² at 16 nm (as used by LS-Gaussian/MetaSapiens). |
| B11 | Lai, sort-free 3DGS-SLAM accelerator (master's thesis, 2026) | Thesis, 2026 | No arXiv ID / DOI (internal thesis, owner-provided) | TSMC 16 nm design + Alveo U200 FPGA (owner's note) | Lab precedent for a 16 nm + FPGA flow. Not counted toward the 50-paper minimum because it has no public ID. |


## 2. Top 20 papers, ranked, with one concrete technique to reuse

Ranking criterion: value for a 2.5-year master's thesis on **edge acceleration of dynamic (4D) Gaussian Splatting**, weighted by (a) dynamic-scene relevance, (b) evidence strength (measured > post-layout > simulation), (c) how directly the technique can be put in hardware.

| Rank | Paper | ID | Evidence | Technique to learn / reuse |
|---|---|---|---|---|
| 1 | GSPIM (baseline) | arXiv:2611.04804 | Simulation | Filter temporally inactive 4D Gaussians **before** they cross the memory bus, and render several adjacent frames from one shared active set (multi-frame dataflow). |
| 2 | CueSplat (baseline) | arXiv:2611.22095 | GPU measured | Predict attributes from history and run exact deformation only for a Top-k budget picked by a tiny router; refresh on drift. Gives 1.75–6.74× on Orin with ≤0.16 dB loss. |
| 3 | DeGS | arXiv:2608.02099 | Cycle-level sim | Decouple workload parsing / reorganization / blending so PEs stay busy (23.9% → 90.2% utilization on dynamic scenes). Shows deformation ("materialization") is still un-accelerated. |
| 4 | Neo | arXiv:2511.12930 | Sim + 7 nm synth | Reuse-and-update sorting: keep last frame's per-tile order and patch it; sort traffic 346.5 GB → 19.6 GB per 60 frames. |
| 5 | 3DGauCIM | arXiv:2507.19133 | Sim on measured 16 nm DCIM data | DRAM-access-reduction frustum culling (cull from compact bounds before fetching full parameters) for dynamic scenes; 211 FPS @0.63 W. |
| 6 | Vorion | arXiv:2511.16831 | **Measured silicon** (16 nm) | Only 3DGS silicon in the set: shows what a student-scale tape-out (4 mm² die, 6.4–19 FPS) actually achieves vs simulated claims. |
| 7 | LENS | arXiv:2610.25068 | **Measured board** (KR260) | INT16/fixed-point 3DGS that survives on a 3.9 W FPGA with 34.11 dB — template for an FPGA-first evaluation. |
| 8 | SCARF | arXiv:2611.02365 | 28 nm post-layout | Scene-adaptive early sparsification and depth reuse: 41–62% DRAM traffic reduction with <0.072% PSNR change. |
| 9 | Mobile-4DGS | arXiv:2610.05289 | Device measured | Its reuse-vs-drift table (0% → 90.2% reuse: 124.7 → 7.2 ms/s, drift 0.01 → 1.28) is the clearest published curve of the temporal-reuse trade-off. |
| 10 | VersionGS | arXiv:2611.33374 | GPU measured | Versioned Gaussian dictionary per GOP: unchanged Gaussians keep their version → representation-level reuse that is camera-independent. |
| 11 | CC-4DGS | arXiv:2609.02184 | GPU measured | Compute the deformation hash instead of storing it (1–3 MB deformation, 33.3 dB, 64 FPS) — trades DRAM for logic, ideal for ASIC. |
| 12 | 4DGS-1K | arXiv:2503.16422 | GPU measured | Key-frame-shared temporal mask of active Gaussians + pruning of short-lifespan Gaussians. |
| 13 | ResStreamGS | arXiv:2610.21121 | Phone measured | Split static (send once) vs dynamic (video-coded residual) Gaussians: +7.5–8.4 dB and −56.5% payload, 30.9 FPS on Snapdragon 778G. |
| 14 | GCC | arXiv:2507.15300 | 28 nm synth + sim | Gaussian-wise rendering with cross-stage conditional preprocessing — never preprocess a Gaussian that rendering will not use. |
| 15 | Lumina | arXiv:2506.05682 | Sim | Radiance cache keyed on Gaussian color integration; temporal sort sharing (S²). |
| 16 | STREAMINGGS | arXiv:2506.09070 | 32 nm synth + sim | Memory-centric voxel-ordered streaming: each Gaussian fetched once per frame. |
| 17 | ElsaGS | arXiv:2611.22059 | 8 nm synth + sim | Progressive freezing of converged Gaussians + frozen-Gaussian cache for training hardware (up to 90% frozen). |
| 18 | OMG4 | arXiv:2510.03857 | GPU | 4D model 2 GB → ~3 MB: shows storage is a software-solved problem, so the hardware should consume compact models. |
| 19 | Temporal Gaussian Hierarchy | arXiv:2412.09608 | GPU measured | Temporal level-of-detail: slow content at coarse temporal levels, so only active segments are loaded (18,000 frames in 2.2 GB). |
| 20 | GS-TG | arXiv:2509.00911 | 28 nm synth + sim | Group small tiles for one sort and keep a per-Gaussian bitmask for fine rasterization. |

## 3. Solved vs. unsolved matrix

Legend: **Solved** = a measured result meets the need on an edge platform; **Partial** = solved only in simulation, only for static scenes, or only in software; **Unsolved** = no paper found that addresses it on edge hardware.

| Problem | Status | Best evidence | What is missing |
|---|---|---|---|
| Deformation compute | Partial | CueSplat 1.75–6.74× (Orin, software); CC-4DGS computational hash | No hardware that **skips** exact deformation per Gaussian; DeGS states materialization is not accelerated. Nobody yet in ASIC. |
| Temporal reuse | Partial | Neo (sort order), Lumina (radiance), GSPIM (active set), Mobile-4DGS (DOC) | Reuse of **deformed 3D state** across frames in hardware; all HW reuse so far is post-projection or sort-order. |
| Sorting | Solved for static (sim) | GSCore 15.86×, Neo −94.4% sort traffic, sort-free WSR | Sort cost under dynamic depth changes is unmeasured on silicon. |
| Bandwidth | Partial | GSPIM PIM filtering; SCARF −41–62% DRAM; STREAMINGGS | All simulated; requires PIM DRAM (GSPIM) or static scenes. |
| Storage | Solved (software) | OMG4 2 GB → 3 MB; CC-4DGS ~30 MB at 33.3 dB; MEGA −40% vs STG | Hardware that decodes these compact formats on the fly. |
| Transmission | Partial | ResStreamGS 30.9 FPS on phone; QUEEN 0.6 MB/frame; 4DGC 0.5 MB/frame; V³ | Phone render time (V³ 37 ms) dominates; no ASIC for decode+render. |
| Training | Partial | 3DGStream/QUEEN/DecoGS/S²GS streaming; ElsaGS, HiGS, SprintGS for static | No accelerator for **4D** or streaming per-frame training. |
| Input views | Unsolved | 6-camera challenge 29.75 dB with generative views; D²-4DGS; Phase-DGS (unsynced, +6.5 dB) | Sparse-view 4DGS still needs heavy priors (depth nets, diffusion) — far from edge real-time. |
| Cache hit rate | Unsolved | Repo's own post-projection cache collapses under camera motion; Lumina needs continuous video | Camera-independent (canonical/world-space) cache keyed by Gaussian ID — nobody yet. |
| Dynamic-scene quality | Partial | VersionGS (+2.7 dB fast motion), MoE-GS 32.82 dB N3V, GaussianSpline | Fast motion still costs 1.1–4.0 dB in smooth-motion models; best quality models are the slowest (MoE-GS 44 FPS). |
| Edge real-time | Partial | 3DGauCIM 211 FPS @0.63 W (sim); CueSplat 30.91 FPS on Orin NX (one pair); LENS/Vorion measured but static | No measured board or silicon result for a **dynamic** 4DGS renderer. |

## 4. Method-family comparison and verdict

Numbers come from different papers, datasets and GPUs; they are comparable only inside the same row of the same source table.

| Family | Representative (ID) | Quality | Speed | Storage | Per-frame hardware cost | Edge suitability |
|---|---|---|---|---|---|---|
| MLP deformation | Deformable 3DGS (2309.13101), E-D3DGS (2404.03613) | E-D3DGS 32.83 dB / SSIM 0.900 (CC-4DGS table) | 46.2 FPS (same table) | tens of MB | MLP per Gaussian per frame — compute-bound | Poor unless deformation is skipped (CueSplat) |
| HexPlane | Wu 4DGS (2310.08528) | unverified here | 82 FPS @800×800, RTX 3090 | compact (14–48 MB class for structure-guided, 2606.00452) | 6 plane lookups + small MLP — memory-irregular | Medium |
| Hash grids | Grid4D (2410.20815) | unverified here | unverified | deform module 221.1 MB | random hash lookups | Poor (memory) |
| 4D primitives | Yang 4DGS (2310.10642), 4D-Rotor (2402.03307), 4DGS-1K (2503.16422) | 28.89 dB high-res set (3D-4DGS table) | 4D-Rotor 583 FPS (4090); 4DGS-1K >1000 FPS | Yang ≈2 GB (OMG4) | slicing math + temporal filtering; filtering = 52.4–63.3% Orin time (GSPIM) | Good compute, bad memory |
| Explicit keyframe / polynomial trajectories | STG (2312.16812), Ex4DGS (2410.15629), repo's [1,t,t²] engine | STG 33.6 dB Technicolor; Ex4DGS 32.01 dB N3V | STG 86.7 FPS; Ex4DGS 120 FPS | STG 609.5 MB; Ex4DGS 122.8 MB (N3V) | a few MACs per Gaussian | **Best compute/quality ratio** |
| Triplane / Fourier motion | FAST-GS (2608.01958), GaussianSpline (2610.35224) | unverified | unverified | unverified | more basis terms → more MACs | Medium |
| Sparse scheduling | CueSplat (2611.22095) | ≤0.16 dB worst average loss | 1.75–6.74× on Orin | unchanged | router + predictor + Top-k | Good, software-only today |
| PIM filtering | GSPIM (2611.04804) | 0.006 dB max diff | 4.15× / 4.99× energy (sim) | unchanged | needs PIM-LPDDR5 | Depends on PIM DRAM availability |
| Sort-free rendering | WSR (2410.18931), StochasticSplats (2503.24366) | unverified here | removes sort | unchanged | order-independent blend | Good for HW; quality trade-off unverified |
| Compression-first | OMG4 (2510.03857), CC-4DGS (2609.02184), MEGA (2410.13613) | CC-4DGS 33.3 dB / SSIM 0.912 | 64 FPS | 3 MB (OMG4), ~30 MB (CC-4DGS) | decode cost | Good for transmission |
| Mixture of experts | MoE-GS (2607.08250) | 32.82 dB N3V (highest in its table) | 44 FPS | 878.7 MB | multiple deformations | Poor |

**Verdict (numbers, not adjectives).** In the one table that measures several families on the same dataset (N3V, MoE-GS paper), MoE-GS has the highest PSNR (32.82 dB) but the lowest FPS (44) and largest size (878.7 MB); Ex4DGS is 0.81 dB lower at 2.7× the FPS and 7.2× less storage (32.01 dB / 120 FPS / 122.8 MB); 75%-pruned MoE-GS gives 32.45 dB / 101 FPS / 281.3 MB. For fast motion, VersionGS beats smooth-motion models by 2.7 dB foreground PSNR on DanceNet3D with 6.0× less storage than TAD-GS on N3DV. **No single method wins all three axes.** For an edge accelerator, the evidence favours **explicit-trajectory or 4D-primitive Gaussians with temporal sparsity**: their per-frame compute is a few MACs, and their remaining cost is memory (GSPIM: temporal filtering 52.4–63.3% of Orin time), which hardware can attack. MLP/hash deformation models are the most hardware-expensive per frame and need CueSplat-style skipping to be viable.

## 5. Pipeline-stage payoff ranking

| Rank | Stage | Evidence of share / payoff | Who attacked it |
|---|---|---|---|
| 1 | Parameter fetch + temporal filtering (DRAM) | 52.4–63.3% of Orin execution time for 4D-primitive models (GSPIM); RTGS: traffic concentrates in L2 (43.6%) vs DRAM 21.5% utilisation | GSPIM, 3DGauCIM, STREAMINGGS, SCARF |
| 2 | Deformation evaluation (MLP/HexPlane models) | Skipping it gives 1.75–6.74× end-to-end on Orin (CueSplat) → it dominates those models | CueSplat (software only) |
| 3 | Sorting | 346.5 GB sort traffic per 60 frames on Orin at high res (Neo) | GSCore, Neo, GS-TG, sort-free WSR |
| 4 | Alpha blending / rasterization | Main bottleneck for static scenes on Orin NX (GBU); PE utilisation only 23.9% on dynamic scenes (DeGS) | GBU, DeGS, FLICKER, GEMM-GS |
| 5 | Preprocessing / projection | Many preprocessed Gaussians never used (GCC) | GCC, LS-Gaussian |
| 6 | Decode + transmission (streaming) | Phone: download 16.8 ms, decode 13.6 ms, render 37.0 ms per frame (V³) | V³, ResStreamGS, 4DGCPro |

**Single most bottlenecked stage:** fetching (and then discarding) per-Gaussian temporal parameters from DRAM every frame — i.e. the memory traffic of deciding *which* Gaussians are alive and where they are at time *t*. It is the largest measured share (52.4–63.3%, GSPIM) and the only top-3 stage whose published hardware fix requires non-standard PIM DRAM. For deformation-network models, the equal-first bottleneck is the deformation compute itself, and both point to the same fix: **decide per Gaussian whether it changed before fetching or computing anything.**

## 6. Acceleration directions (≥20)

| # | Target | Mechanism | Existing paper | Expected gain | Difficulty |
|---|---|---|---|---|---|
| 1 | Deformation compute | Hardware Top-k exact deformation + history predictor + drift refresh (CueSplat in silicon) | CueSplat (SW only) → **nobody yet in HW** | 2–7× compute, energy | Medium |
| 2 | Cache hit rate | Canonical/world-space cache of **deformed 3D attributes** keyed by Gaussian ID, independent of camera | **Nobody yet** (repo cache is post-projection) | Hit rate survives camera motion | Medium |
| 3 | Bandwidth | Compact per-Gaussian lifespan/motion bounds read first; full record fetched only if active (filter-before-fetch without PIM) | GSPIM (PIM), 3DGauCIM (static culling) | DRAM bytes −50%+ (inferred from 52–63% filter share) | Medium |
| 4 | Bandwidth | Multi-frame rendering: render 2–4 frames per active-set fetch | GSPIM | Amortised fetch | Medium |
| 5 | Sorting | Reuse-and-update sort across time and across stereo views | Neo (static) | Sort traffic −80–94% | Medium |
| 6 | Sorting | Sort-free weighted-sum blending for dynamic scenes | WSR (static), StochasticSplats | Removes sorter | Low-Medium |
| 7 | Rasterization | Decoupled parse/reorganize/blend with load balancing | DeGS, SM load-balancing (2611.12319) | PE util 24% → 90% | Medium |
| 8 | Preprocessing | Cross-stage conditional preprocessing (only Gaussians that will be blended) | GCC | Skip wasted preprocess | Low |
| 9 | Storage | Computed (not stored) deformation hash on chip | CC-4DGS (SW) | Deformation storage 1–3 MB | Medium |
| 10 | Storage | Static/dynamic split: 3D Gaussians for static, 4D only where needed | 3D-4DGS, 4DGS-1K | Fewer 4D Gaussians | Low |
| 11 | Storage | On-chip decoder for quantised / codebook / PCA attributes | OMG4, LightGaussian, InterGS+ | 10–600× smaller model in DRAM | Medium |
| 12 | Transmission | Hardware path from video-decoder output straight into the renderer (no CPU unpack) | V³, ResStreamGS (SW) → **nobody yet in HW** | Removes decode/unpack latency | Medium |
| 13 | Transmission | Progressive layered 4D bitstream with on-chip layer selection | 4DGCPro | Adaptive bitrate | Medium |
| 14 | Transmission | Send residuals only for changed Gaussians (versioned dictionary) | VersionGS, QUEEN, S²GS | 6× storage vs TAD-GS; −85% vs QUEEN | Low (SW) |
| 15 | Training speed | Freeze converged Gaussians and cache them during streaming per-frame training | ElsaGS (static), DecoGS (SW) | Up to 90% frozen; 20.6× (static, sim) | High |
| 16 | Training speed | Fused loss + scheduled tile culling in a training accelerator | SprintGS, HiGS (GPU) | 3.2× training (HiGS) | High |
| 17 | Training speed | Feed-forward anchor motion (no per-frame optimisation), cheap anchor sampler | IGS, arXiv:2603.17227 (random = FPS sampler) | 10 s+ → sub-second per frame (unverified) | Medium |
| 18 | Fewer input views | Monocular + multi-view depth fusion priors | D²-4DGS | fewer cameras | High |
| 19 | Fewer input views | Generative virtual views in angular gaps | arXiv:2610.04606 | +1.12 dB at 6 cameras | High (not edge) |
| 20 | Input bandwidth | Phase-based alignment of unsynchronised cameras (cheaper capture) | Phase-DGS | up to +6.5 dB unsynced | Medium (SW) |
| 21 | Quality | Versioned / per-frame states for fast motion | VersionGS | +2.7 dB fast motion | Medium |
| 22 | Quality | Adaptive-order trajectories (splines / Fourier) evaluated by a variable-latency engine | GaussianSpline, FAST-GS → HW **nobody yet** | Fast-motion quality | Medium |
| 23 | Edge real-time | Fixed-point / INT16 4DGS datapath with measured quality | LENS (static, board) | 3.9 W board | Low |
| 24 | Edge real-time | Foveated + stereo-shared rendering for XR | MetaSapiens, Atlas, Nebula | 1.6× vs GSCore iso-area | Medium |
| 25 | SLAM | Gradient-reuse pruning and covisibility from the video codec | RTGS, AGS (static SLAM) → dynamic SLAM HW **nobody yet** | 82.5× energy (RTGS, sim) | High |

## 7. Thesis recommendation

### Primary: "Change-before-fetch" temporal-sparse 4DGS renderer ASIC

**One sentence:** an accelerator that decides, per Gaussian and per frame, whether its deformed state has changed **before** fetching its parameters or running deformation, reuses deformed 3D state from a camera-independent cache otherwise, and renders the active set — combining directions 1 + 2 + 3 (+ 5 for sorting).

- **Novelty / urgent gap.** CueSplat (Nov 2026) shows skipping deformation is worth 1.75–6.74× on Orin but is software-only. GSPIM (HPCA 2027) shows temporal filtering is 52.4–63.3% of runtime but needs PIM DRAM. DeGS (MICRO 2026) explicitly leaves deformation un-accelerated. No paper in this survey implements per-Gaussian deformation skipping, or a camera-independent cache of deformed state, in hardware. This gap is very recent and two top groups are close to it → move fast.
- **Feasibility (2.5-year master's).** Year 1: software trace study + cycle model; Year 1.5: RTL of change-detector, predictor, canonical cache, active-set compaction; Year 2: FPGA demo + tape-out; Year 2.5: measurement and thesis. The datapath is small (polynomial/keyframe deformation is a few MACs per Gaussian), which keeps the block count tape-out-sized.
- **ASIC path.** FPGA first (lab already has Alveo U200 from the Lai thesis), then a TSMC 16 nm shuttle through TSRI, re-using the lab's 16 nm flow. Silicon goal: measured FPS/W and DRAM bytes on N3DV/Technicolor — no other dynamic-4DGS paper here has measured silicon or board results.
- **First experiment (4–6 weeks).** On 2 backbones (one MLP/HexPlane, e.g. 4DGaussians or E-D3DGS; one explicit, e.g. Ex4DGS/STG) and N3DV + Technicolor: dump per-Gaussian per-frame deltas; measure (1) fraction of Gaussians whose deformed state changes below a threshold, (2) PSNR vs reuse ratio (reproduce Mobile-4DGS-style curve), (3) hit rate of a post-projection cache vs a canonical-space cache under real camera trajectories, (4) DRAM bytes per frame with filter-before-fetch. Go/no-go: ≥50% reuse at ≤0.2 dB loss.
- **Risks that would kill it.** (a) CueSplat or GSPIM authors publish a hardware version first (both groups have hardware track records). (b) Reuse ratio collapses on fast-motion content (VersionGS: 1.1–4.0 dB losses on high-motion frames). (c) The field moves to feed-forward or 4D-primitive models where deformation is already cheap, leaving only the memory part (still covered by direction 3). (d) The learned router is too large for a small ASIC — mitigate with a threshold rule (the anchor-sampling study 2603.17227 shows cheap rules can match learned ones).

### Backup: streaming 4DGS decode-and-render engine for mobile XR

**One sentence:** hardware that takes static/dynamic split Gaussian streams (ResStreamGS / V³ style) from a standard video decoder and renders them directly, with versioned/residual updates for changed Gaussians only (directions 12 + 14 + 11).

- **Gap.** Streaming papers are measured on phones (ResStreamGS 30.9 FPS on Snapdragon 778G; V³ phone 27 FPS with 37 ms render) but there is no accelerator for the decode-to-render path. Less competition from architecture groups.
- **Feasibility.** Mostly dataflow + format conversion + a standard renderer; a strong FPGA demo is achievable even if tape-out slips.
- **ASIC path.** Same 16 nm shuttle; the decoder can be off-chip (commodity) and the chip consumes the 2D attribute planes.
- **First experiment.** Reproduce V³/ResStreamGS packing on N3DV, profile unpack + render on Jetson, and model on-chip buffers.
- **Risks.** Impact is judged by systems venues, not only architecture venues; mobile GPUs may close the gap; codec formats change quickly.
