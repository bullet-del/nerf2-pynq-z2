# Evidence and claim rules

## Status vocabulary

- `PASS`: the named gate ran on the required artifact and satisfied its fixed
  acceptance contract.
- `FAIL`: the gate ran and did not satisfy the contract.
- `PENDING`: the required artifact or physical experiment has not been produced
  in the current reproduction.

Missing evidence is never converted to `PASS`. A smoke run is never promoted to
a formal run.

## Evidence classes

| Class | Examples | What it can establish |
|---|---|---|
| Source lineage | checkpoint hashes, split counts, config, source manifest | the intended inputs and code were selected |
| Software | FP32/QAT/PTQ metrics, prediction maps, runtime capacity | algorithmic accuracy and export consistency |
| RTL/Vivado | xsim transcript, timing and utilization reports, BIT/HWH | hardware logic behavior and implementation feasibility |
| Physical FPGA | raw-Q15 hashes, latency runs, full-test results, nonce transcript, HDMI record | execution on the actual board |
| Measured power | FNB58 raw log, run JSON, aligned intervals, summary | power and energy within the stated boundary |

## Accuracy and coverage

Formal software evaluation reads every ID in each fixed `test_index.txt`. QAT
calibration and optimization use the training split only. Per-scene acceptance
requires a PC-to-QAT integer mean-SSIM gap no greater than 0.015 and QAT integer
mean SSIM no lower than PTQ integer mean SSIM.

## Hardware identity

Physical evidence must match the active bitstream hash, runtime-model hash, and
test-catalog hash. Results from an older board package remain historical
evidence and must not be inserted into a new reproduction's final gate.

## Power boundaries

ARM and FPGA strict energy-efficiency claims use the same PYNQ-Z2, 5 V input,
meter, task, model, and full-map definition. NVIDIA driver telemetry covers the
discrete GPU domain. It may appear as a descriptive reference, with the boundary
named in the table, but it is not a whole-PC wall-power measurement.

## Competition claim matrix

| Candidate claim | Current source support | Required demonstration evidence |
|---|---|---|
| One bitstream serves four scenes | Yes | switch-and-return RTL pass plus physical identities |
| Hardware-exact QAT preserves quality | Yes | four full software gates from the new run |
| Full held-out coverage | Yes | exactly 1,725 software and 1,725 board records |
| FPGA output is integer-reference exact | Yes | per-map raw-Q15 hash agreement |
| FPGA is faster/more energy-efficient than ARM | Measurement tools included | same-boundary three-repeat measurements |
| PC is less efficient than FPGA | Descriptive comparison only | explicitly different boundary and precision labels |
| Clean-room reproducibility | Yes | independent six-stage host run and new board run |
| Reusable engineering Skill | Yes | run the generic source audit on this and another contract |
| LLM collaboration record | Outside this release | add a curated, privacy-reviewed record separately |

The repository can support these claims; the result of a particular clean run
is established only by that run's generated reports and physical evidence.
