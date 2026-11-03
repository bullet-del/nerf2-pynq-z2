# Changelog

## 39.0-source-2026.11.03

- Assembled the complete current source and both competition Skills in an
  independent publication copy, without datasets or generated artifacts.
- Added a standard-library publication helper, explicit visibility selection,
  checksum-pinned optional GitHub CLI bootstrap, retry support and no force push.
- Corrected target versus implemented clock terminology and 1024-bit weight-word
  labels. No RTL, model arithmetic, inference or board-control algorithm changed.
- Excluded external input trees from the source manifest, consistently with
  .gitignore and the documented external-input contract.
- Added publication tests and documented current versus historical validation.
- Retained the existing project and third-party licensing terms.


## 39.0-repro-2026.10.13

- Published an English, clean-room reproduction layout.
- Locked all four activation percentiles to 99.5 and the hardware-exact QAT
  learning rate to 1e-5, matching the accepted Stage39 model contract.
- Added stale-output lineage checks before evaluation and runtime packaging.
- Added a reusable FPGA/ML reproducibility-audit Skill and declarative contract.
- Added source-manifest verification and a lightweight CI workflow.

This release does not change the Stage39 RTL architecture, fixed-point
arithmetic, model topology, runtime packet format, board service, or acceptance
thresholds.
