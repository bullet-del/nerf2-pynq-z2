# Inputs, reference outputs and measurement data

`input_manifest.json` contains hashes and byte sizes for the original datasets,
fixed split files, configs and four checkpoints. Obtain their complete bytes
from `nerf2_inputs.zip` in the Release.

`reference_results/<scene>/validation.json` contains per-map GT/PC/FPGA metrics
for all 1725 held-out inputs. Full GT and PC FP32 maps are in the matching PC
package. Every actual FPGA map is in `test_results.zip`.

`power_summary` contains the archived summaries. `power_logs.zip` preserves
the source FNB58 records, raw NVIDIA telemetry, board run JSON and analysis.

`release_assets.json` lists the six complete downloads with bytes and SHA-256.
`publication_identity.json` records report/hardware identity and validation scope.
