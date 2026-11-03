# Input and output contract

## Required input

- A PyTorch checkpoint containing `nerf2_network_state_dict`.
- The training `config.yml` used for that checkpoint.
- A dataset directory containing `spectrum/*.png`, `tx_pos.csv`,
  `gateway_info.yml`, `train_index.txt`, and `test_index.txt`.
- A CUDA-enabled environment with the dependencies declared by the repository.

The runner verifies the tensor shapes that define the Stage39 graph, a 90x360
spectrum sample, nonempty disjoint train/test indices, and the required project
sources before starting expensive work.

The signal branch's first layer is `256 x 382`: 256 shared feature values plus
63 encoded view-direction values and 63 encoded Tx-position values. The raw Tx
coordinate has three values, but it is positionally encoded before this layer.

## Fixed processing contract

- Structural adaptation: 3000 steps, batch 512, Adam, learning rate 2e-4,
  cosine annealing, seed 20260905.
- Calibration: 8192 training rays, seed 3902, activation percentile 99.5.
- Hardware-exact QAT: 1800 steps, batch 192, learning rate 1e-5, seed 3902.
- Structural masks:
  - `attenuation_linears.5.weight[:, 63:] == 0`
  - `signal_linears.0.weight[:, :256] == 0`
- Evaluation: the complete declared test split by default. Accuracy is reported
  and is not an acceptance threshold.

The factorization program emits a small before/after test diagnostic for
visibility. The runner does not tune any setting or stop early from that
diagnostic: structural adaptation is always the declared 3000 steps, and the
formal report is generated only after QAT and export are frozen.

## Output directory

- `factorized/checkpoint.tar`: structurally constrained floating-point model.
- `qat/checkpoint_qat.tar`: hardware-exact QAT checkpoint.
- `qat/ptq_train_only/`: train-only quantization contract.
- `qat/qat_export/`: fixed-point export and test vectors.
- `evaluation/full_evaluation.json`: descriptive complete-test metrics.
- `runtime/model/stage37_runtime_model.npz`: packed FPGA runtime model.
- `runtime/model/stage37_runtime_model.json`: storage and identity metadata.
- `runtime/model/`: fixed-Rx assets when the current bitstream capacity fits.
- `deployment_report.json` and `deployment_report.md`: final result and limits.
- `SHA256SUMS.txt`: hashes of generated artifacts.

Only an unmodified default run can report `READY_FOR_CURRENT_BITSTREAM`.
Parameter-reduced smoke runs use a `NONDEFAULT_RUN_...` status even when their
generated model fits the current PL memory.

`stage37_runtime_model.npz` is an inference image, not a trainable checkpoint.
If `weight_words > 3072`, the NPZ remains useful as a diagnostic export but the
current PYNQ-Z2 bitstream cannot load it. Increasing SD-card capacity does not
increase PL parameter memory.

