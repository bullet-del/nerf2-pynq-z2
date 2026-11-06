# Recorded results and artifact index

These are actual archived results, separated from original inputs and newly
generated work/outputs. No archived result is consumed by the cold pipeline.

| Location | Contents and scope |
|---|---|
| [reproduction_20261106](reproduction_20261106) | Fresh full host reconstruction, 1725-map metrics, runtime models, new BIT/HWH, raw logs and 17-check PASS; new physical execution pending |
| [reference_results](reference_results) | All 1725 per-map GT/PC/physical-FPGA comparison records |
| [physical](physical) | Actual four-scene bit-exact selftests, 4×50 benchmark, all-test identities/results, power samples, original complete physical ZIP and October 9 FULL_PASS |
| [photos](photos) | Actual system and connection photos |
| [power_summary](power_summary) | GPU and board-input summaries, continuous-window and sampled-power statistics |
| [power_raw](power_raw) | Original FNB58 CFN, board JSON, GPU telemetry and per-map/per-run analysis |
| [rtl](rtl) | Executed runtime/cache/inference and scene-switch simulation logs, vectors and host gate |
| [vivado/reports](vivado/reports) | Original routed resource/timing and hierarchical reports |
| [vivado/prebuilt](vivado/prebuilt) | Archived deployed BIT/HWH for inspection, not cold-build inputs |
| [vivado/fixed_scene_baseline](vivado/fixed_scene_baseline) | Original fixed-scene resource/timing reports supporting the report's before/after table |
| [validation_history](validation_history) | Earlier dated development snapshots with historical states retained |

Complete 1725 FPGA output-map caches, matched original PC/board deployments and
the 179.8-second demonstration are in the
[fixed GitHub Release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104).
The root README gives direct download commands. Large assets remain on GitHub
Releases rather than exceeding regular Git file limits. Original checkpoints,
datasets, numeric power logs, comparison records, photos and reports are in
the ordinary repository ZIP.

Power records retain their original date and may reference old local paths or
a then-pending finalization. Later completion is in physical/physical_finalization.json.
Complete original measurement screen recordings and every alignment file are
not included, limiting independent video-based power audits. ARM full-map power
is unmeasured and is not presented as a completed experiment.

Archived FULL_PASS does not certify a different model/bitstream or a new run.
New reconstruction evidence retains its own date, settings and pending physical
field until actually executed on the board.
