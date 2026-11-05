# Simulation and verification

testbench contains the source testbenches and base vectors. validation contains
the actual archived runtime-loading, inference and scene-switch simulation
logs, generated vectors and result JSON. To run a new check, first generate
runtime models, then run python build/pipeline/reproduce_stage39.py rtl --profile full.
The new run writes work/full/rtl_sim and does not overwrite the archived result.
