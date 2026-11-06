# Original reconstruction inputs

inputs contains four original PyTorch training checkpoints, 8623 original
90×360 PNGs, fixed train/test splits, gateway info, Tx coordinates and configs.
They are ordinary Git files included by Download ZIP. Exact paths are listed
in the root README and [configuration](../00_config/reproduction_config.json).
Checkpoint bytes are unchanged; their training/optimizer metadata is retained.
Only repository path spellings use lowercase.

The reference Python network/renderer is bundled under
04_pc_app/template/software/third_party/nerf2, independent of later changes to
the user's external NeRF2 working tree. GPU power loads original checkpoints
here, not structurally adapted or QAT checkpoints.

[downloads.json](downloads.json) lists optional historical large assets on
GitHub Releases. Recorded results are under results; new runs use work/outputs.
