# NeRF2 FPGA acceleration on PYNQ-Z2

This repository contains the Stage39 engineering submission for the 2026 AMD
self-selected junior FPGA track. Given a transmitter position and a fixed
receiver, it predicts a 90 by 360 directional spatial spectrum. Four scene models
share one PYNQ-Z2 / XC7Z020 inference bitstream.

The PL implements sparse integer neural inference, two-ray processing and Q15
ray rendering. The ARM processor prepares tasks and controls AXI DMA. The PC
application organizes requests, computes independent FP32 references and displays
GT, PC and actual FPGA results. HDMI output uses a separate display Overlay.

## Read the report and obtain the complete artifacts

[The submitted design report, including the LLM collaboration appendix](report/design_report.pdf)
is an exact copy of the final submitted AMD report. Its SHA-256 is recorded in
[publication identity](data/publication_identity.json).

[The Markdown LLM collaboration record](report/llm_collaboration.md) retains the
initial Stage plan and actual debugging conversations, changes and verification.

The [complete release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104)
provides the following checksum-pinned downloads. Some packages exceed GitHub's
100 MiB regular-file limit and are distributed as Release assets.

| Asset | Content |
|---|---|
| [nerf2_inputs.zip](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/nerf2_inputs.zip) | Four original checkpoints, original full datasets, train/test splits and configs |
| [board_package.zip](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/board_package.zip) | Archived PYNQ deployment, BIT/HWH, runtime models, drivers and six-cell notebook |
| [pc_app_package.zip](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/pc_app_package.zip) | Matching PC application, GT, full FP32 maps and scene assets |
| [test_results.zip](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/test_results.zip) | All 1725 saved FPGA maps, per-map metrics and actual interactive run records |
| [power_logs.zip](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/power_logs.zip) | Raw meter and GPU logs, board program JSON and power analysis |
| [demonstration.mp4](https://github.com/bullet-del/nerf2-pynq-z2/releases/download/stage39-complete-20261104/demonstration.mp4) | The final 179.8-second demonstration video |

[Release asset sizes and SHA-256 values](data/release_assets.json) allow verification
of every download. The input manifest records every original input file.

## Directory correspondence to guide section 3.3.5.4

| Guide category | Actual location and contents |
|---|---|
| `src/` | `01_Vivado/rtl`, `02_Model_Pipeline`, `03_Package_Builder`, `04_PC_App/template`, `05_PYNQ_Board/template`, `06_Power_Measurement` |
| `sim/` | `01_Vivado/sim` contains testbenches and vectors, `sim/validation` contains executed simulation logs, vectors and PASS results |
| `build/` | `01_Vivado/tcl` contains reconstruction scripts, `build/reports` contains original implementation reports, `build/prebuilt` contains the physically used BIT/HWH |
| `board/` | `board/notebook`, `board/photos`, `board/evidence`, full board/PC deployment Release assets and the Python board source template |
| `data/` | Per-map comparison data, power summaries, original input hashes, full original inputs and full output-map Release assets |
| `skill/` | Both competition Skills with scope, usage, failure conditions and verification records |
| `report/` | The exact submitted design report and its LLM collaboration appendix |

Existing English module and scene names preserve the executable interface contracts.
All published repository paths use English characters without spaces. The two
Skill prompt files are named `prompts/prompt_workflow.md`.

## Previously measured results

| Scene | Test maps | PC FP32 mean SSIM | FPGA mean SSIM | Full-test mean FPGA time, s/map |
|---|---:|---:|---:|---:|
| s23 | 1225 | 0.7546 | 0.7415 | 1.140 |
| Meetingroom | 200 | 0.6925 | 0.6826 | 1.121 |
| Office1 | 200 | 0.6616 | 0.6527 | 1.119 |
| Office2 | 100 | 0.6129 | 0.6053 | 1.101 |

The archived physical finalizer reports `FULL_PASS`. Scene identities match
the bitstream and all four runtime models in `board_package.zip`. The complete
reference tables are in `data/reference_results`, with physical source exports
under `board/evidence/physical_export`.

The design requests 95 MHz. The archived hardware metadata records approximately
94.118 MHz, consistent with a 10.625 ns implementation period. Resources are
50,069 LUT, 48,464 registers, 127.5 BRAM tiles and 178 DSPs. WNS is +0.015 ns
and WHS is +0.019 ns. Original reports are in `build/reports`.

Sampled median power at the PYNQ-Z2 5 V input is 1.974 to 2.043 W. The PC GPU
telemetry covers the discrete GPU power domain. These values have different
measurement boundaries. The exact definitions, full-test timing, integrated
energy and sampled-power estimates are retained in the submitted report.

## Deploy the archived, physically validated system

Use Python 3.8+ on the PC and the existing PYNQ environment on the board.

```text
python scripts/verify_publication.py
python scripts/fetch_release_assets.py --asset board
python scripts/fetch_release_assets.py --asset pc
```

1. Unzip `external/downloads/board_package.zip` on the PYNQ to
   `/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board`.
2. Open `Stage39_QAT_Four_Scenes.ipynb` in that deployed directory. Run cells 1
   through 4 in order for verification, service startup and board checks.
3. Unzip `pc_app_package.zip` on the PC. In the NeRF2 Python environment, run
   `python pc_app/stage39_pc_server.py --port 8310` from its root. Browse to
   `http://127.0.0.1:8310` and set the actual board service address, for example
   `http://192.168.2.99:8311`.
4. Use test selection, full-test evaluation, blind selection, legal continuous
   Tx, ARM ray checks and HDMI as described in the report. Notebook cell 5
   exports evidence and cell 6 shows service status.

The browseable notebook under `board/notebook` is a copy for review. The verified
deployment uses the original notebook name and package root inside the ZIP.
See [physical setup and restoration](docs/hardware_setup.md).

## Rebuild from original checkpoints without intermediate products

Validated tools are Vivado 2024.1, Python 3.8.17, PyTorch 2.0.1 with CUDA 11.8
and the PYNQ board Python environment. The sources use RTL and PYNQ Linux control.

```text
python scripts/verify_kit.py
python scripts/fetch_release_assets.py --asset inputs --extract-inputs
conda env create -f 00_Config/environment.yml
conda activate nerf2
python scripts/configure_repository.py --input-root external/nerf2_inputs --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat"
python 03_Package_Builder/reproduce_stage39.py preflight
python 03_Package_Builder/reproduce_stage39.py vivado
python 03_Package_Builder/reproduce_stage39.py models --profile full --scene all
python 03_Package_Builder/reproduce_stage39.py rtl --profile full
python 03_Package_Builder/reproduce_stage39.py assemble
python 03_Package_Builder/reproduce_stage39.py verify
```

Use your actual Vivado path. These steps rebuild BIT/HWH, constrained models,
train-only calibration, PTQ, hardware-exact QAT, all 1725 held-out maps and new
deployments. Upload the newly generated board package and perform its physical
checks. Historical evidence stays under the archived evidence directories.

The formal software conditions include 8192 training calibration rays per scene,
1800 QAT steps at learning rate 1e-5, activation percentile 99.5, PC-minus-integer
mean SSIM at most 0.015, QAT no worse than PTQ and at most 3072 parameter words.
The archived 2026-10-13 software gate contains 17 PASS checks. Its physical
status applies to that freshly built package. The October 8/9 physical archive
belongs to the explicitly matched deployed package.

## Skills

- [NeRF2 checkpoint export](skill/nerf2-checkpoint-fpga-export/README.md) produces
  a runtime NPZ, capacity check and descriptive report for the supported NeRF2
  architecture. New compatible scenes use 3000 constrained adaptation steps.
- [Generic PYNQ Overlay validation](skill/pynq-overlay-validation/README.md)
  inventories HWH interfaces, generates an adapter contract, compares golden
  outputs and reports transaction timing. The saved validation includes HWH
  inspection and PC mock positive/negative checks. Its verification document
  states the physical-board reuse scope.

```text
python -B -m unittest discover -s skill/pynq-overlay-validation/tests -v
```

## License

The project authors' source and associated documentation are distributed under
the [MIT license](LICENSE). Vendored NeRF2 retains its original MIT notice.
The original NeRF2 data/model inputs retain their attribution and license.
Vendor-generated hardware artifacts and externally cited material retain their
applicable vendor or third-party terms. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Publish the complete replacement

From the prepared local publication bundle, run

```text
python scripts/publish_complete.py --check
python scripts/publish_complete.py --publish
```

The uploader checks the local source and all six sibling `release_assets` files,
fetches the existing public repository into a separate checkout, replaces its
tracked tree, commits the complete replacement and pushes `main`. It publishes
the matching draft Release after every asset hash passes. Original commits remain
available in Git history. Interrupted uploads can use the same command again.
