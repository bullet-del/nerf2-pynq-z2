# Reproduce the Stage39 system

## Starting state

Start with this repository, the original four scene model parameters, datasets,
fixed train/test splits and training configs. No precomputed integer model,
map, BIT/HWH or board evidence is required for a new reconstruction.
The validated host is Windows with Vivado 2024.1, Python 3.8.17 and PyTorch
2.0.1/CUDA 11.8. The board uses its PYNQ Python environment.

## Source, inputs and configuration

```text
python build/tools/check_repository.py
python build/tools/download_artifacts.py --asset inputs --extract-inputs
conda env create -f build/config/environment.yml
conda activate nerf2
python build/tools/configure_repository.py --input-root external/nerf2_inputs --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat"
python build/pipeline/reproduce_stage39.py preflight
```

If the environment exists use conda env update. Choose the real Vivado path.
The configuration tool creates build/config/reproduction_config.local.json,
which is ignored. Existing input data can be used via --input-root instead of
downloading. The directory must contain data/RFID and logs/RFID. The preflight
checks original models, data split counts, image shape, CUDA and formal settings.

## Hardware reconstruction

```text
python build/pipeline/reproduce_stage39.py vivado
```

This calls build/tcl/build_stage39.tcl, imports src/rtl and creates the PS/DMA/
GPIO/stream Block Design. Newly generated BIT/HWH and reports are in build/output.
On Windows use a short temporary-project location: Vivado IP generation retains
the 260-byte path limit. The Tcl uses the normal TEMP directory. An unusually
long custom TEMP or workspace can exceed the limit; move that temporary location
to a short path before running rather than changing the hardware design.
From Vivado Tcl Console the equivalent is source build/tcl/build_stage39.tcl
from the repository root. The Tcl target is 95 MHz; record the actual generated
clock and timing closure. Archived hardware records about 94.118 MHz.

## Full model pipeline, RTL and assembly

```text
python build/pipeline/reproduce_stage39.py models --profile full --scene all
python build/pipeline/reproduce_stage39.py rtl --profile full
python build/pipeline/reproduce_stage39.py assemble
python build/pipeline/reproduce_stage39.py verify
```

The models stage performs structural adaptation, train-only calibration, PTQ,
hardware-exact QAT, full evaluation and runtime export. Formal four-scene
adaptation uses s23=0 and the other scenes=3000 steps. Calibration uses 8192
rays per scene; QAT uses 1800 steps, batch 192, 1e-5 learning rate and percentile
99.5. The total held-out coverage is 1225+200+200+100=1725 maps. Accuracy/capacity
gates are unchanged. Outputs are under work/full and outputs.

The RTL stage runs runtime loading, cache construction, inference, and the
four-scene switch-and-return sequence. Assembly builds matched PC/PYNQ packages.
The final report is work/full/reports/cold_reproduction_final_gate.json. Its
physical field remains PENDING_NEW_BOARD_EXECUTION until that new package is
executed on the actual PYNQ board.

For a short connectivity test use models --profile smoke --scene Office2.
Smoke runs have shortened training and one test map; they never establish the
formal accuracy, implementation, resource, power or full-test result.

## Resume after interrupted or corrected QAT

With existing valid factorized checkpoints, run these stages in order:

```text
python build/pipeline/run_model_pipeline.py qat --profile full --scene all
python build/pipeline/run_model_pipeline.py evaluate --profile full --scene all
python build/pipeline/run_model_pipeline.py runtime --profile full --scene all
```

Provenance checks reject mixed or stale model/quantization files. Do not weaken
the accuracy gate or manually change a failed JSON to PASS.

## Physical phase

Upload the new outputs/N2-NeRF2-Stage39-board.zip and extract it under the
original deployment root. Run the six-cell notebook as described in the root
README, start the matching PC App, and execute all four full test sets.
Check arbitrary Tx, held-out blind queries, ARM ray checks, nonce/negative
control and HDMI. Export the new physical evidence. For power, use src/power
instructions, record meter logs and board task JSON for matching runs, and
run the physical finalizer. Save the original results before deleting a board
directory. Prior physical records do not certify a different rebuild.
