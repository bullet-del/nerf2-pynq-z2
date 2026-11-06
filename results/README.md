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
All twelve original measurement screen recordings are now provided in the
[raw recording release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-repro-results-20261106).
Portable alignment files and acquisition inputs are in power_alignment and power_inputs.
Run the root README section 5.2 to download and recompute them. ARM power is outside this workflow.

Archived FULL_PASS does not certify a different model/bitstream or a new run.
New reconstruction evidence retains its own date, settings and pending physical
field until actually executed on the board.

## Added original outputs and processing records

- [cold_reproduction_20261013](cold_reproduction_20261013/README.md): 7182 original NoPS1 intermediate output files, 604.6 MiB, including all full-test maps, checkpoints, calibration, QAT, sparse/runtime assets and RTL results.
- [power_alignment](power_alignment/captures.json): all twelve capture pairings and frame events, including the original s23 R1 missing-stop-event record.
- [power_inputs](power_inputs): the exact twelve program JSON and CFN pairs.
- [power_reprocessed_20261106](power_reprocessed_20261106/processing.json): recomputed 5175 per-map energy intervals, four-scene FPGA-only summaries and 80 actual sampled observations.
- [repository_repair_20261106](repository_repair_20261106): original full-graph NeRF2 timing and package/command check results from this update.
