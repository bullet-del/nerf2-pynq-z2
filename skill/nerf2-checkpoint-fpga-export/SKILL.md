---
name: nerf2-checkpoint-fpga-export
description: Convert a new scene trained with the Stage39-compatible NeRF2 architecture into a constrained checkpoint, hardware-exact QAT checkpoint, FPGA runtime model, and descriptive deployment report. Use when checkpoint, config, dataset, and fixed train/test split are available.
---

# NeRF2 checkpoint FPGA export

Use this Skill to process one previously unseen NeRF2 scene through the
Stage39 model pipeline without adding the scene to the four-scene application.
The input architecture is assumed to match the Stage39 contract: `D=8`,
`W=256`, `skips=[4]`, separate 63-dimensional point, view-direction, and Tx
positional encodings (each produced from a raw 3D vector), 90x360 output, and
64 samples per ray.

Read [references/input-and-output-contract.md](references/input-and-output-contract.md)
before interpreting a completed report or changing defaults.

Run from the English repository root:

```text
python skill/nerf2-checkpoint-fpga-export/scripts/run_nerf2_checkpoint_export.py --project-root . --scene NEW_SCENE --checkpoint PATH/TO/CHECKPOINT.tar --config PATH/TO/config.yml --dataset PATH/TO/DATASET --output-dir work/generic_exports/NEW_SCENE
```

Use `--dry-run` first for an unfamiliar input. The default full run performs
3000 constrained adaptation steps, train-only calibration, 1800 hardware-exact
QAT steps, complete held-out test evaluation, exact sparse export, runtime model
packing, and runtime-asset generation when the current bitstream capacity fits.
Runs that override the formal defaults are labeled as nondefault so a short
smoke run cannot be mistaken for a formal conversion.

Preserve these rules:

1. Keep both structural zero masks active during adaptation and QAT. They are
   part of the current FPGA execution graph, not an optional accuracy gate.
2. Record SSIM, PSNR, MSE, MAE, and FP32-to-integer differences as descriptive
   results. Do not reject or rewrite a result because its SSIM is low.
3. Treat the 3072-word limit as PL runtime parameter memory backed by on-chip
   resources. SD-card capacity does not change it.
4. If the runtime model exceeds 3072 words, retain the generated NPZ and report,
   mark it incompatible with the current bitstream, and do not claim board
   deployability.
5. A completed report never proves physical FPGA execution. Board loading,
   bit-exact checks, latency, HDMI, and power remain separate physical evidence.
6. Do not use test results to change steps or hyperparameters within the same
   run. The factorization script prints a small before/after diagnostic on the
   declared test split, but fixed step counts and hyperparameters must not be
   selected from that diagnostic. Formal descriptive results use the complete
   test split after the model is frozen.

