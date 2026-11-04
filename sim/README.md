# Executed validation

`validation/rtl_simulation_report.json` records the actual October 13 PASS for
runtime model loading, fixed-Rx cache/inference equality, and four-scene
switch-and-return. The original simulator logs, generated testbenches and
input vectors accompany that report. Source testbenches are in
`../01_Vivado/sim`. Re-run through the main pipeline's `rtl` command after
generating the full runtime models.

`validation/cold_reproduction_final_gate.json` contains all 17 actual software
PASS checks and retains its original new-build physical-pending field.
The previously deployed system's physical evidence is in `../board/evidence`.
