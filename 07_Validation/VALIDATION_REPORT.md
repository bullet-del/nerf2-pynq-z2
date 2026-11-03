# Validation report for the clean reproduction source

Validation date: 2026-10-13  
Target: PYNQ-Z2 / XC7Z020-1CLG400C  
Formal toolchain: Vivado 2024.1, Python 3.8.17, PyTorch 2.0.1 + CUDA 11.8

## Scope

The source chain has been exercised from the four authoritative external
checkpoints through factorization, train-only calibration, PTQ, hardware-exact
QAT, integer export, runtime packaging, RTL simulation, Vivado implementation,
PC/PYNQ package assembly, and package verification.

The clean release omits every generated artifact. A new user's formal result is
valid only after that user runs the documented full pipeline and physical board
phase. Previous results establish that the source path and fixed contract can
reach the stated gates; they do not pre-certify a future run.

## Input lineage

Preflight verified all four checkpoint SHA-256 values, dataset sizes, disjoint
train/test coverage, and 360 x 90 held-out images:

| Scene | Dataset / train / test |
|---|---:|
| s23 | 6123 / 4898 / 1225 |
| Meetingroom | 1000 / 800 / 200 |
| Office1 | 1000 / 800 / 200 |
| Office2 | 500 / 400 / 100 |

The pipeline does not import later-modified Python files from the external
NeRF2 directory; it uses that directory only for data, split files, configs,
and source checkpoints.

## Source and smoke validation

- 124 Python files passed syntax checking in the preceding source release.
- No PowerShell script or `.ps1` reference is part of the execution flow.
- The four-scene smoke path completed factorization, calibration, PTQ, QAT,
  evaluation, runtime export, runtime loading, and scene-switch simulation.
- Smoke runtime weights used 1439, 1637, 1669, and 2385 words respectively,
  each below the 3072-word capacity. These are connectivity values and are not
  formal trained-model results.

## Formal QAT contract correction

An earlier cold-package draft used activation percentiles 95.0/95.0/90.0 for
three scenes and QAT learning rate 4e-5. The accepted Stage39 contract uses
activation percentile 99.5 for every scene and QAT learning rate 1e-5. The
tracked configuration, QAT default, preflight, and stale-output lineage checks
now enforce the accepted values.

Fresh formal s23 and Meetingroom reconstructions passed:

| Scene | Maps | PC FP32 mean SSIM | QAT integer | PTQ integer | PC-QAT gap |
|---|---:|---:|---:|---:|---:|
| s23 | 1225 | 0.7545534930 | 0.7415369036 | 0.7380942855 | 0.0130165894 |
| Meetingroom | 200 | 0.6925123340 | 0.6826285871 | 0.6717961625 | 0.0098837469 |

The fresh state tensors matched the accepted formal Stage39 models, and their
PTQ/QAT hardware streams matched byte for byte. Their runtime packages used
1448 and 1851 words.

Read-only checks of the frozen accepted Office1 and Office2 models also passed
the current evaluator (gaps 0.0089328620 and 0.0075229434). Those checks define
the expected target; they do not replace a fresh cold retraining. The user's
current full reconstruction must produce its own four PASS gate files.

## RTL and Vivado

Vivado Simulator passed runtime model loading, fixed-Rx cache construction,
two-ray bit-exact inference, and the sequence
`s23 -> Meetingroom -> Office1 -> Office2 -> s23`.

Vivado 2024.1 built the nine-source design for `xc7z020clg400-1` with a
95 MHz Tcl target. The archived HWH specifies 94,117,645 Hz (about 94.118 MHz);
the implemented clock period is 10.625 ns. These must not be represented as an
actual 95 MHz clock. The
validated implementation reported WNS 0.015 ns, WHS 0.019 ns, 50,069 LUTs,
48,464 registers, 127.5 BRAM tiles, and 178 DSPs. Vivado 2025.2 and 2026.1 have
not been run and must not be described as validated equivalents.

## Package and notebook behavior

Temporary PC and board packages passed their immutable-file verifiers. The
notebook verifier canonicalizes cell source and ignores execution counts,
outputs, and runtime metadata. Re-running notebook cell 1 therefore does not
invalidate an unchanged notebook.

## Required completion for a new reproduction

The new run must independently complete four formal QAT/evaluation gates,
exactly 1,725 held-out maps, formal runtime packages, full RTL simulation,
Vivado timing closure, delivery verification, and the physical PYNQ phase. The
physical phase includes four-scene bit-exact tests, four 50-map latency gates,
all 1,725 board maps, HDMI, blind and arbitrary-Tx queries, nonce and negative
control, and new external-meter power evidence.
