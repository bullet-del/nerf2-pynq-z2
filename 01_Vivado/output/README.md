# Vivado output

This directory intentionally contains no generated BIT or HWH file in a clean
checkout. Run `python 03_Package_Builder/reproduce_stage39.py vivado` from the
repository root, or source `01_Vivado/tcl/build_stage39.tcl` in Vivado 2024.1.

The build must report timing closure at 95 MHz before its artifacts are used by
the packaging pipeline.
