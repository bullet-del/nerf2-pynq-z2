# Board deployment and captured outputs

The complete deployable engineering package is the `board_package.zip` Release
asset linked from the root README. It contains runtime models, BIT/HWH,
notebook and drivers. The matching `pc_app_package.zip` is available alongside it.

`notebook/stage39_qat_four_scenes.ipynb` provides the exact notebook source for
review. Execute the original notebook in the deployed package root on PYNQ.
`photos` contains actual board/system photographs.

`evidence/physical_export` contains the four-scene full-test summaries, source
identity hashes, bit-exact selftests and 50-run benchmarks. Every original
exported file is checked against its archived manifest.
`evidence/physical_finalization.json` records the actual FULL_PASS.
`evidence/deployed_package_manifest.json` identifies the matched board package.

The `test_results.zip` Release asset includes all actual FPGA output-map cache
files and recorded interactive runs. This evidence was captured on physical
hardware. The publication scripts copy the recorded results.
