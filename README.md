# NeRF2 FPGA inference on PYNQ-Z2

An engineering submission for the **2026 AMD self-selected junior FPGA track**.
Given a transmitter position in a trained environment and a fixed receiver,
the system produces a **90 × 360 directional spatial spectrum**. Four scene
models share the same PYNQ-Z2 / XC7Z020 inference hardware.

The FPGA performs sparse integer network inference and Q15 ray rendering.
ARM prepares inputs, loads model parameters and controls AXI DMA. The PC handles
interaction, independent FP32 inference and GT/PC/FPGA visualization. HDMI uses
a separate display Overlay.

## Read and reproduce

- [Design report](report/design_report.pdf) and [current reproduction update](report/repository_update.md).
- [System architecture](docs/architecture.md), [interfaces](docs/interfaces.md) and [hardware setup](docs/hardware_setup.md).
- [Complete reproduction instructions](docs/reproducibility.md).
- [Competition Skills](skill/README.md) and [actual LLM collaboration](report/llm_collaboration.md).
- [Complete deployment, inputs, results and video](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104).

## Directory structure and AMD guide correspondence

The repository follows **guide 3.3.5.4**. This table explicitly maps every guide
category to the actual source and evidence. `docs/` is an additional category
for detailed instructions and does not replace any required material.

```text
src/       RTL, model processing, PC application and power tools
sim/       Testbenches, vectors and executed simulation results
build/     Environment, Tcl, pipeline, tools and implementation reports
board/     PYNQ source, notebook, photos and physical results
data/      Per-map comparisons, power summaries and artifact download list
skill/     Competition Skills and supporting reusable workflows
report/    Design report, LLM collaboration and reproduction update
docs/      Architecture, interfaces, setup and detailed instructions
```

| AMD recommended directory | Actual location | Material and entry point |
|---|---|---|
| `src/` | `src/rtl`, `src/model`, `src/host`, `src/power`; PS code in `board/source` | RTL, model export/QAT, PC application, measurement and PYNQ control source |
| `sim/` | `sim/testbench`, `sim/validation` | Testbench/vector source, actual runtime-load and scene-switch PASS logs |
| `build/` | `build/config`, `build/tcl`, `build/pipeline`, `build/tools`, `build/reports`, `build/prebuilt` | Environment, reconstruction scripts, original resource/timing reports and archived BIT/HWH |
| `board/` | `board/source`, `board/notebook`, `board/photos`, `board/evidence` | Board drivers/services, review notebook, real photos, full-test and timing evidence; complete deployment in Release |
| `data/` | `data/reference_results`, `data/power_summary`, `data/downloads.json` | 1725 per-map comparisons, power summaries and original-input/full-output downloads |
| `skill/` | `skill/nerf2-checkpoint-fpga-export`, `skill/pynq-overlay-validation` | Both competition items with scope, commands, failure conditions and verified effects |
| `report/` | `report/design_report.pdf`, `report/llm_collaboration.md`, `report/repository_update.md` | Design report, actual planning/debugging record and current reproduction mapping |
| Additional `docs/` | Architecture, interfaces, setup and reproduction documents | Detailed explanation and navigation |

[Detailed directory and old-to-current command map](docs/directory_map.md).
Custom paths use lower-case English, digits, underscores or hyphens. Standard
`README.md`, `LICENSE` and agent `SKILL.md` names are retained. The archived,
tested deployment ZIP retains its original internal names. Scene identifiers
inside model/config records stay unchanged; repository directories are lower-case.

## Recorded results

| Scene | Test maps | PC FP32 mean SSIM | FPGA mean SSIM | Full-test mean FPGA time, s/map |
|---|---:|---:|---:|---:|
| s23 | 1225 | 0.7546 | 0.7415 | 1.140 |
| Meetingroom | 200 | 0.6925 | 0.6826 | 1.121 |
| Office1 | 200 | 0.6616 | 0.6527 | 1.119 |
| Office2 | 100 | 0.6129 | 0.6053 | 1.101 |

The Tcl target is **95 MHz**. Archived HWH specifies about **94.118 MHz** with
a 10.625 ns period. Resources: 50,069 LUTs, 48,464 registers, 127.5 BRAM tiles,
178 DSPs. Routed WNS is +0.015 ns and WHS +0.019 ns. [Reports](build/README.md).

Sampled median power at the PYNQ-Z2 5 V input is **1.974–2.043 W**. PC telemetry
covers the discrete GPU power domain. These boundaries differ; the ratio is a
descriptive comparison. Full-test mean, fixed-sample 50-run timing, integrated
energy and sampled-power estimates are separate statistics in the report and
[power protocol](src/power/power_protocol.md).

The recorded `FULL_PASS` belongs to the matched October 8/9 physical deployment.
The October 13 rebuilt software/RTL/Vivado gate has 17 PASS checks, with a pending
new-board field for that separately rebuilt package. Reorganization is not a new
physical experiment.

## Run the archived complete system

Use Python 3.8+ on the PC and the existing PYNQ Python environment on the board.

```text
python build/tools/check_repository.py
python build/tools/download_artifacts.py --asset board
python build/tools/download_artifacts.py --asset pc
```

1. Upload `external/downloads/board_package.zip` to PYNQ and extract it as
   `/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board`.
2. Open the deployed `Stage39_QAT_Four_Scenes.ipynb`. Run cells 1–4 for package
   verification, service startup, full-map selftests and latency tests.
3. Extract the PC ZIP. In the NeRF2 environment run
   `python pc_app/stage39_pc_server.py --port 8310` from its root.
4. Open `http://127.0.0.1:8310` and set the actual board URL, for example
   `http://192.168.2.99:8311`. Use test queries, arbitrary legal Tx, ARM ray
   checks, saved comparisons and HDMI.
5. Notebook cell 5 exports evidence; cell 6 shows service status. After a normal
   restart cells 1–2 restore the service; cells 3–4 repeat the checks.

The notebook under `board/notebook` is for review. Run the original notebook
inside the tested deployment package. [Wiring and recovery](docs/hardware_setup.md).

## Rebuild without intermediate products

Validated: **Vivado 2024.1, Python 3.8.17, PyTorch 2.0.1 + CUDA 11.8**. The AMD
guide permits other documented, reproducible versions. 2025.2/2026.1 equivalence
is untested. The design uses RTL and PYNQ Linux; [no bare-metal Vitis project is required](docs/control_architecture.md).

```text
python build/tools/check_repository.py
python build/tools/download_artifacts.py --asset inputs --extract-inputs
conda env create -f build/config/environment.yml
conda activate nerf2
python build/tools/configure_repository.py --input-root external/nerf2_inputs --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat"
python build/pipeline/reproduce_stage39.py preflight
python build/pipeline/reproduce_stage39.py vivado
python build/pipeline/reproduce_stage39.py models --profile full --scene all
python build/pipeline/reproduce_stage39.py rtl --profile full
python build/pipeline/reproduce_stage39.py assemble
python build/pipeline/reproduce_stage39.py verify
```

Use your real Vivado path. Configuration generates an ignored local file. Formal
processing uses 8192 training calibration rays, percentile 99.5 and 1800 QAT
steps at 1e-5. The four-scene gate remains PC-minus-integer mean SSIM ≤ 0.015,
QAT ≥ PTQ and ≤ 3072 1024-bit weight words. Generated `work/`, `outputs/` and
`build/output/` are ignored. The new board package requires its own physical run.
[Full sequence and recovery](docs/reproducibility.md).

## Skills

The two submitted items are [NeRF2 export](skill/nerf2-checkpoint-fpga-export/README.md)
and [generic PYNQ validation](skill/pynq-overlay-validation/README.md). Each gives
scope, usage, failure conditions, observed failures and verified effects.
The generic runner keeps mock and real-board results distinct. New compatible
NeRF2 scenes default to 3000 structural adaptation steps and descriptive metrics
without an SSIM threshold. Additional source-audit and ordered-workflow helpers
are consolidated under [the same skill directory](skill/README.md).

## Complete artifacts and license

The [fixed Release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104)
provides original inputs, complete board/PC deployments, all output maps, raw
power records and the 179.8-second video. [Download list](data/downloads.json).
The downloader checks file size and ZIP data integrity. No separate source
SHA256 list or hash-manifest refresh step is required.

[MIT](LICENSE) covers project contributions. NeRF2/vendor materials retain their
[notices and terms](docs/third_party_notices.md).
