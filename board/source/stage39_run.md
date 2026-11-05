# Stage39 board execution and physical acceptance

## Upload and start

1. Remove or rename the previous Stage39 board directory in Jupyter.
2. Upload the newly generated `N2-NeRF2-Stage39-board.zip` and extract it exactly
   as `/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board`.
3. Open `Stage39_QAT_Four_Scenes.ipynb` and run its cells in order.
4. Cell 1 verifies immutable files and canonical notebook cell source. Jupyter
   execution counts, output, and UI metadata do not invalidate unchanged source.
5. Cell 2 starts the supervised service on port 8311 and then returns. Closing
   the browser tab does not stop the independent service.

## Physical bit-exact gate

Cell 3 loads all four runtime models into the same inference bitstream (target
95 MHz; archived implementation metadata about 94.118 MHz) and executes
one complete 90 x 360 map per scene. Every raw-Q15 map SHA-256 must match the
portable integer reference.

## Latency and stability gate

Cell 4 executes 50 complete maps per scene. Each scene must retain a stable
raw-Q15 hash, remain bit exact to the integer reference, have median latency no
greater than 1.2 seconds, and have P95 latency no greater than 1.5 seconds. The
timing boundary is FPGA computation for 32,400 rays with 64 samples per ray; it
excludes HTTP rendering and HDMI transfer.

## Complete held-out test sets

Use the PC application to complete 1225 s23, 200 Meetingroom, 200 Office1, and
100 Office2 maps. The service can resume an interrupted run. If the bitstream,
runtime model, or test catalog changes, it archives the old evidence and starts
the affected run from zero.

## Evidence export and normal restart

After cells 3 and 4 and all four full-test runs pass, run cell 5 and download
`Stage39_physical_evidence.zip`. Cell 6 displays service state.

After a normal board power cycle, persistent full-test results remain on the SD
card. Run cells 1 and 2 to verify the package and restart the service. Deleting
or replacing the package/evidence directories invalidates the corresponding
saved state.

Stage39 displays raw FPGA Q15 divided by 32768 and has no output-calibration
branch. Scene switching loads scene-specific hardware-exact QAT weights and the
fixed-Rx cache; it does not synthesize or swap the accelerator bitstream.
