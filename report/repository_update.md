# Reproduction and terminology update

This note accompanies the original design report after the November 5, 2026
repository organization. It does not alter the archived physical experiments.

The Tcl target is 95 MHz. Matching archived HWH is about 94.118 MHz with a
10.625 ns period. Unqualified 95 MHz references on report pages 5, 71 and 74
mean the requested target, not the achieved metadata frequency. Table 2-47
already distinguishes these quantities.

Appendix B names an earlier kit. Use [the current README](../README.md),
[the full sequence](../docs/reproducibility.md) and
[the exact directory map](../docs/directory_map.md). In this tree run
build/tools/configure_repository.py to create the ignored machine-local config.
Do not change formal model settings merely to adapt local filesystem paths.

There is no source SHA256 manifest or refresh command. Run
python build/tools/check_repository.py; success prints REPOSITORY CHECK PASS.
Model/deployment checks inside the numerical pipeline remain functional.

The archived physical FULL_PASS belongs to the October 8/9 deployment. The
October 13 reconstruction has 17 software/RTL/Vivado checks and retains pending
new-board status for its newly generated package. Neither is relabeled as a
fresh physical test by repository reorganization.
