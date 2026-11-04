# Hardware reconstruction and original reports

- Build Tcl is `../01_Vivado/tcl/build_stage39.tcl`.
- RTL and testbenches are in `../01_Vivado/rtl` and `../01_Vivado/sim`.
- `prebuilt/n2_stage38_raw.bit` and `.hwh` are the archived hardware pair used
  by the actual physical test evidence and `board_package.zip`.
- `reports/stage37_utilization.rpt` and `stage37_timing_routed.rpt` are the
  original archived implementation reports used in the design report.
- `reports/cold_build_hierarchical_utilization.rpt` is explicitly from the
  separate cold reconstruction run.

Run `python 03_Package_Builder/reproduce_stage39.py vivado` at repository root
using Vivado 2024.1. The newly generated hardware pair is written to
`01_Vivado/output`. Record its own hardware hashes and timing results.
