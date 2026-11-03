# Clean-room reproduction protocol

## Scope

The starting state contains this source repository plus the original NeRF2
data, fixed split files, configs, and four source checkpoints. It contains no
factorized checkpoints, QAT checkpoints, runtime packages, prediction maps,
Vivado project, BIT/HWH, delivery package, or physical evidence.

## Host prerequisites

- Windows 11 or an equivalent environment capable of running Vivado 2024.1.
- NVIDIA CUDA GPU supported by PyTorch 2.0.1 + CUDA 11.8.
- At least 20 GB free storage and stable cooling/power.
- Vivado 2024.1 with Zynq-7000/PYNQ-Z2 device support.
- PYNQ-Z2 for the physical phase.

## Ordered host procedure

1. Verify the unmodified source archive:

   ```text
   python scripts/verify_kit.py
   ```

2. Create or update the environment:

   ```text
   conda env create -f 00_Config/environment.yml
   conda activate nerf2
   ```

3. Create an ignored machine-local configuration:

   ```text
   python scripts/configure_repository.py --input-root "D:\path\to\NeRF2-main" --vivado "C:\Xilinx\Vivado\2024.1\bin\vivado.bat"
   ```

4. Validate checkpoint hashes, split coverage, image shape, CUDA, Vivado, source
   presence, and the locked QAT contract:

   ```text
   python 03_Package_Builder/reproduce_stage39.py preflight
   ```

5. Build BIT/HWH from RTL and Tcl:

   ```text
   python 03_Package_Builder/reproduce_stage39.py vivado
   ```

6. Rebuild all four models and evaluate all 1,725 held-out maps:

   ```text
   python 03_Package_Builder/reproduce_stage39.py models --profile full --scene all
   ```

7. Run runtime-loading, inference, and four-scene switching simulations:

   ```text
   python 03_Package_Builder/reproduce_stage39.py rtl --profile full
   ```

8. Assemble the PC and PYNQ packages, then run the final software gate:

   ```text
   python 03_Package_Builder/reproduce_stage39.py assemble
   python 03_Package_Builder/reproduce_stage39.py verify
   ```

The final software report is
`work/full/reports/cold_reproduction_final_gate.json`. Its software status must
be `PASS`; `physical_gate` must remain `PENDING_NEW_BOARD_EXECUTION` until the
new package is executed on PYNQ-Z2.

## Board procedure

1. Upload the newly generated `outputs/N2-NeRF2-Stage39-board.zip` and extract it
   as `/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board`.
2. Open `Stage39_QAT_Four_Scenes.ipynb` and run cells 1 through 4 in order.
3. Start the newly generated PC application.
4. Complete all four full-test runs: 1225 + 200 + 200 + 100 maps.
5. Demonstrate HDMI, a random held-out blind query, a valid arbitrary Tx query,
   an ARM cross-check, a random nonce, and the negative control.
6. Run the notebook evidence-export cell and download the physical evidence ZIP.
7. Add the external power CSV and run the physical finalizer. Only `FULL_PASS`
   supports a completed physical claim.

## Determinism and equivalence

The source checkpoints, split counts, seeds, calibration size, QAT parameters,
acceptance thresholds, and lineage hashes are fixed. CUDA kernels, drivers, and
Vivado patch versions can change byte-level serialization. Cross-machine
acceptance therefore uses input lineage, numeric gates, capacity, RTL/board
bit-exactness, test coverage, and timing closure rather than requiring every
generated container file to have the same byte hash.

## Recovery after interruption

If all factorized checkpoints exist and a corrected or interrupted QAT stage
must be rerun, execute these commands in order:

```text
python 03_Package_Builder/run_model_pipeline.py qat --profile full --scene all
python 03_Package_Builder/run_model_pipeline.py evaluate --profile full --scene all
python 03_Package_Builder/run_model_pipeline.py runtime --profile full --scene all
```

The QAT stage recreates calibration, PTQ, QAT, and integer export outputs. The
lineage guard rejects stale or mixed files before evaluation and packaging.
