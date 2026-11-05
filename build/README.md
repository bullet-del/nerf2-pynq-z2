# Reproducible build and implementation

config contains the environment and fixed formal settings. tools contains
local configuration, artifact downloads and the repository checker. pipeline
contains model orchestration, RTL preparation, packaging and final checks.
tcl contains Vivado build/simulation scripts. reports are original routed
resource/timing reports; prebuilt contains the archived deployed BIT/HWH.

Run python build/pipeline/reproduce_stage39.py vivado from the repository root.
New artifacts are written to ignored build/output. Tcl imports src/rtl.
See [the complete order](../docs/reproducibility.md).
