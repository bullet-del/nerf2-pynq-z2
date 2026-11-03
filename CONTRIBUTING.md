# Contributing

Changes must preserve the distinction between source code, generated software
results, physical FPGA evidence, and externally measured power data.

1. Create changes from a clean checkout and do not commit `work`, generated
   packages, Vivado projects, datasets, checkpoints, or meter logs.
2. Run `python scripts/update_source_manifest.py` after changing tracked source.
3. Run `python scripts/verify_kit.py` and the source audit documented in
   `docs/REPRODUCIBILITY.md`.
4. For model or RTL changes, run the smoke pipeline before the full pipeline.
5. Do not weaken thresholds, rewrite a failed report, or label a software
   reference as physical FPGA evidence.
6. Explain affected checkpoints, splits, fixed-point formats, interfaces, and
   evidence in the change description.

Generated results should be attached to a release or archived as experiment
evidence rather than committed as source.
