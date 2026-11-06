# Reproduction entry point

The [root README](../README.md) is the complete current step-by-step command
manual. It starts with the four original checkpoints and full bundled datasets,
rebuilds BIT/HWH from RTL/Tcl, converts all four models, evaluates all 1725 test
maps, simulates the protocol and assembles new PC/PYNQ packages.

No external NeRF2 working directory, precomputed map or prebuilt BIT/HWH is
required. Run the six host steps in order, followed by the board, application
and power procedures. Formal parameters are unchanged from the corrected NoPS1
kit: s23 adaptation 0 steps, other scenes 3000; 8192 calibration rays; 1800 QAT
steps, batch 192, learning rate 1e-5 and percentile 99.5.

The generic new-scene Skill is separate, with descriptive accuracy and no SSIM
threshold. Historical evidence under results remains separate from a new run
under work and outputs. New packages need new physical execution and power data.
