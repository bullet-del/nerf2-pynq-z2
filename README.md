# N2-NeRF2 Stage39 on PYNQ-Z2

Reproducible four-scene wireless radio-map inference with one FPGA bitstream,
runtime model switching, hardware-exact quantization-aware training, complete
held-out evaluation, and evidence-aware PC/PYNQ tooling.

Target board: **PYNQ-Z2 / XC7Z020-1CLG400C**  
Validated toolchain: **Vivado 2024.1, Python 3.8.17, PyTorch 2.0.1 + CUDA 11.8**

## Repository status

| Capability | Repository support | Evidence rule |
|---|---|---|
| Clean reconstruction from four source checkpoints | Implemented | A new run must pass all software gates |
| Vivado Tcl to BIT/HWH | Implemented and previously built on 2024.1 | A new build must pass timing and record the implemented clock |
| Full 1,725-map held-out evaluation | Implemented | Smoke results never count as formal accuracy |
| Four-scene runtime switching with one bitstream | Implemented | RTL simulation and board identity must both pass |
| Physical PYNQ validation | Implemented | Must be regenerated on the actual board |
| External whole-board power capture | Implemented | Meter logs and program JSON must share a run identity |
| Reusable FPGA/ML audit Skill | Included | Static audit is demonstrated in CI |
| One-command reproduction Skill | Included | Contract steps run in order and finish with JSON and Markdown reports |
| Generic NeRF2 checkpoint export Skill | Included | Emits a runtime NPZ, capacity verdict, hashes, and descriptive metrics |

The clean repository intentionally excludes datasets, checkpoints, generated
models, BIT/HWH files, board evidence, and power logs. Their absence is part of
the clean-room contract, not a missing-source workaround.

## Technical contribution

- A single configurable Zynq-7020 PL design serves `s23`, `Meetingroom`, `Office1`,
  and `Office2`; scene changes load runtime descriptors, weights, and fixed-Rx
  caches rather than synthesizing another bitstream.
- The software flow factorizes the four source checkpoints, builds train-only
  calibration data, exports a PTQ baseline, performs hardware-exact QAT, and
  evaluates PC FP32, QAT integer, and PTQ integer paths on every held-out map.
- Runtime arithmetic uses sparse INT8 weights and Q15 values. The ARM reference,
  RTL simulation, and FPGA output use the same integer contract.
- Immutable package manifests, checkpoint hashes, split counts, runtime-model
  identities, and negative controls prevent evidence from being silently mixed
  across builds.

The 95 MHz value is a build target, not a substitute for the generated HWH
clock metadata or a measured clock frequency. A new build must report its own
implemented clock. Structural masks can change the original floating model;
only grouped all-zero-column deletion of a frozen integer model is required
to preserve that integer model exactly.

See [architecture](docs/ARCHITECTURE.md), [reproduction protocol](docs/REPRODUCIBILITY.md),
and [evidence rules](docs/EVIDENCE_AND_CLAIMS.md).

## Publish this source release

Run from any directory with Python 3.8+ and Git installed:

```text
python PATH/TO/Stage39_GitHub_Final_20261103/scripts/publish_github.py --name nerf2-pynq-z2 --visibility public
```

The script verifies this release, obtains the checksum-pinned official GitHub
CLI when it is absent on Windows x64, opens GitHub browser authorization when
needed, commits the complete source, creates the personal repository, and
pushes main without forcing history. Use `--visibility private` for a private
repository. `--check` is local-only; `--check-tools` also checks the CLI but
never authenticates or uploads. See [upload instructions](docs/GITHUB_UPLOAD.md).

This 2026-11-03 publication copy includes both competition Skills. Its current
static checks and historical implementation evidence have separate scopes;
see [publication validation](07_Validation/PUBLICATION_VALIDATION.json).
The existing rights-reserved project license is retained. Public visibility
does not grant an additional reuse license; third-party licenses are retained.

## Required external inputs

Place the original NeRF2 repository, dataset, fixed train/test split, config,
and the four checkpoints on the local machine. The preflight stage requires:

| Scene | Source checkpoint | SHA-256 | Train / test |
|---|---|---|---:|
| s23 | `logs/RFID/s23/spectrum-exp1/ckpts/010000.tar` | `eeccf2bba9cd4e84853e0fcf1dab9fa2d218b166a6f81485a9e5dc498f8a8beb` | 4898 / 1225 |
| Meetingroom | `logs/RFID/Meetingroom/spectrum-meetingroom/ckpts/005000.tar` | `b12006b8945f7fddbfb9e54c870dc9a52a634adff66eb4a48a07e8d8f20d72ae` | 800 / 200 |
| Office1 | `logs/RFID/Office1/spectrum-Office1/ckpts/005000.tar` | `8a4e437a31dc631e99cba20633a581b8f93007e43ff329e1b59d458c1dfad16d` | 800 / 200 |
| Office2 | `logs/RFID/Office2/spectrum-Office2/ckpts/005000.tar` | `95e5fc58eb2c4790517fcb3380e7f5d4247654773f3723b3c14a13bf9a7e72ba` | 400 / 100 |

The pipeline reads the external repository only for data, configs, splits, and
checkpoints. It uses the model, quantization, RTL, board, and PC source shipped
here.

## Quick start

From an Anaconda Prompt in the repository root:

```text
python scripts/verify_kit.py
conda env create -f 00_Config/environment.yml
conda activate nerf2
python scripts/configure_repository.py --input-root "D:\path\to\NeRF2-main" --vivado "C:\Xilinx\Vivado\2024.1\bin\vivado.bat"
python 03_Package_Builder/reproduce_stage39.py preflight
python 03_Package_Builder/reproduce_stage39.py vivado
python 03_Package_Builder/reproduce_stage39.py models --profile full --scene all
python 03_Package_Builder/reproduce_stage39.py rtl --profile full
python 03_Package_Builder/reproduce_stage39.py assemble
python 03_Package_Builder/reproduce_stage39.py verify
```

`configure_repository.py` creates the ignored
`00_Config/reproduction_config.local.json`. It does not edit the tracked model
contract or invalidate the source manifest.

For a quick connectivity test, replace the full model command with:

```text
python 03_Package_Builder/reproduce_stage39.py models --profile smoke --scene Office2
```

Smoke mode proves that the code path executes. It does not support a formal
accuracy, latency, resource, or power claim.

## Acceptance contract

- Four held-out test sets cover exactly **1,725 maps**.
- `PC FP32 mean SSIM - QAT integer mean SSIM <= 0.015` for every scene.
- QAT integer mean SSIM is not lower than the PTQ integer baseline.
- Each runtime model uses no more than **3,072 weight words**.
- The runtime-load/cache/inference RTL test and the four-scene switch-and-return
  RTL test pass bit exactly.
- Vivado implementation passes timing under the generated constraints. The Tcl
  request is **95 MHz**; archived HWH records **94,117,645 Hz** (approximately
  **94.118 MHz**), consistent with the 10.625 ns implemented clock period.
  Record the actual clock and timing report for every new build.
- Physical claims remain pending until a newly generated board evidence archive
  passes the physical finalizer.

Fixed formal parameters include 8,192 train-only calibration rays, activation
percentile 99.5 for every scene, and 1,800 QAT steps with learning rate 1e-5.
The preflight and lineage guards reject stale outputs from another contract.

## Repository layout

| Path | Purpose |
|---|---|
| `00_Config` | Environment and locked reproduction contract |
| `01_Vivado` | RTL, testbenches, vectors, and Tcl build scripts |
| `02_Model_Pipeline` | Factorization, quantization, QAT, and runtime export |
| `02_Vitis` | Explanation of the PYNQ Linux control architecture |
| `03_Package_Builder` | Ordered orchestration, gates, assembly, and verification |
| `04_PC_App` | Clean PC application template and Stage39 demo Skill |
| `05_PYNQ_Board` | Clean notebook, board service, and integer reference template |
| `06_Power_Measurement` | PC, ARM, FPGA power acquisition and aggregation tools |
| `07_Validation` | Validation scope and known status |
| `skill` | Competition submission with one project Skill and one board-independent PYNQ Skill |
| `skills/fpga-ml-reproducibility-audit` | Reusable cross-project audit Skill |
| `skills/nerf2-checkpoint-fpga-export` | Generic compatible-checkpoint conversion Skill |
| `work`, `outputs` | Generated and ignored artifacts |

## Reusable Skill

The repository includes a general Skill for auditing FPGA/ML projects with a
declarative evidence contract. It is not tied to NeRF2, PYNQ-Z2, or Stage39.

```text
python skills/fpga-ml-reproducibility-audit/scripts/audit_fpga_repro.py --root . --contract reproducibility-contract.json --mode source --output work/reports/source-audit.json
```

To install it for Codex, copy the complete
`skills/fpga-ml-reproducibility-audit` directory into the user's Codex skills
directory. The bundled project-specific `n2-stage39-demo` Skill remains useful
for the actual Stage39 judge demonstration.

The repository also includes a generic one-command reproduction Skill. It reads
the ordered workflow from the same contract, executes each argument list
without a command shell, saves one log per step, stops at the first failure,
and creates both JSON and Markdown reports:

```text
python skills/fpga-ml-reproduction-workflow/scripts/run_fpga_ml_workflow.py --root . --contract reproducibility-contract.json --pipeline software --report-dir work/reports/one-command
```

Use `--dry-run` to validate and display the complete plan before a long run.
The Stage39 `software` pipeline covers source verification, input preflight,
Vivado generation, four-scene model processing, RTL simulation, package
assembly, package verification, and the final software evidence audit. Physical
board actions and external meter operation remain explicit physical steps.

For another scene trained with the same NeRF2 architecture and output
contract, use the dedicated checkpoint export Skill. It applies the two fixed
structural masks for 3000 adaptation steps, performs train-only calibration and
hardware-exact QAT, then emits a runtime NPZ and a descriptive report without
an SSIM acceptance threshold:

```text
python skills/nerf2-checkpoint-fpga-export/scripts/run_nerf2_checkpoint_export.py --project-root . --scene NEW_SCENE --checkpoint PATH/TO/CHECKPOINT.tar --config PATH/TO/config.yml --dataset PATH/TO/DATASET --output-dir work/generic_exports/NEW_SCENE
```

The report is `READY_FOR_CURRENT_BITSTREAM` only when the model fits the current
3072-word PL parameter memory. An oversize conversion remains available for
analysis, but it is not loadable by the current bitstream. A larger SD card
does not change this PL capacity.

## Competition Skill submission

The `skill/` directory follows the 2026 AMD competition submission layout and
contains a README for every item. `nerf2-checkpoint-fpga-export` is the
project-derived checkpoint conversion case template. `pynq-overlay-validation`
is independent of NeRF2, scene names, tensor dimensions, and PYNQ-Z2: it parses
HWH metadata, generates a portable BIT/HWH contract and adapter skeleton,
checks live Overlay IP and registers, demonstrates cache-safe DMA handling,
compares every run with golden outputs, and writes latency and evidence reports.

```text
python skill/pynq-overlay-validation/scripts/inspect_hwh.py --hwh design.hwh --bit design.bit --output work/pynq_validation/hwh_inventory.json
python skill/pynq-overlay-validation/scripts/generate_validation_bundle.py --hwh design.hwh --bit design.bit --project-name my_overlay --output-dir work/pynq_validation/my_overlay
```

The generic Skill emits `PASS_MOCK` for PC-only adapter tests and reserves
`PASS_BOARD` for execution through a real PYNQ Overlay.

## Power claims

The strict energy-efficiency comparison is ARM versus FPGA on the same PYNQ-Z2
and the same 5 V board-input boundary. NVIDIA telemetry reports the discrete-GPU
power domain and is therefore a descriptive PC reference, not a whole-PC power
measurement. See [the power protocol](06_Power_Measurement/POWER_PROTOCOL.md).

## Version and licensing

The competition release is fixed to Vivado 2024.1. Newer Vivado releases may
upgrade block-design IP or metadata and have not been validated as equivalent.
The judge-facing application remains localized in Chinese; repository-facing
documentation and metadata are English. See [LICENSE](LICENSE) and the vendored
NeRF2 license before redistribution.
