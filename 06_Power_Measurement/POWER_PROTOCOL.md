# PC, ARM, and FPGA power protocol

## Common task

Each map contains 90 x 360 rays and each ray contains 64 samples. Every formal
repeat processes the entire held-out split: s23 1225, Meetingroom 200, Office1
200, and Office2 100 maps.

Report latency in seconds per map, active average power in watts, total energy
in joules per map, and dynamic energy in joules per map:

```text
total J/map = measured active-window energy / completed maps
dynamic J/map = (active energy - idle average W * active duration) / maps
speedup = ARM median s/map / FPGA median s/map
energy reduction factor = ARM median total J/map / FPGA median total J/map
```

## Measurement boundaries

| Platform | Execution | Timing boundary | Power boundary | Use |
|---|---|---|---|---|
| PC RTX GPU | original NeRF2 FP32 | GPU transfer, render, synchronization, return | NVIDIA GPU telemetry | descriptive reference |
| PYNQ ARM | Stage39 INT8/Q15 reference | complete 32,400 x 64 map | whole PYNQ-Z2 5 V input | same-boundary baseline |
| PYNQ FPGA | Stage39 INT8/Q15 PL | complete 32,400 x 64 map | whole PYNQ-Z2 5 V input | project result |

The strict efficiency claim is ARM versus FPGA. PC and PYNQ values may appear
in one table only when their different boundaries and precision are explicit.

## Repeats and validity

- Use three independent full-split repeats per scene and platform.
- Keep the board, fixed 5 V source, FNB58, cable, network, ambient conditions,
  and clock mode unchanged across ARM and FPGA runs.
- Use a 60-second idle interval and a continuous active interval. Preserve raw
  samples, start/end UTC anchors, program JSON, and a setup photograph.
- The FPGA run must retain exact test order and, when saved full-test evidence is
  present, match every saved raw-Q15 hash.
- The ARM run must retain exact test order and match every FPGA raw-Q15 hash.
- A reset, supply drop, DMA recovery, hash mismatch, or missing map invalidates
  that repeat.

## FNB58 connection

With the board powered off, put PYNQ-Z2 jumper J9 in the USB position. Connect:

```text
fixed 5 V / 3 A USB-A supply
    -> FNB58 measurement input/output path
    -> short low-resistance USB-A to Micro-USB cable
    -> PYNQ-Z2 J8
```

Do not connect the DC barrel input or Arduino VIN at the same time. Keep quick
charge triggering disabled. The FNB58 PC data connector is separate from the
measured series path and should remain connected for every run. If voltage at
load falls below approximately 4.75 V, the board resets, or Ethernet drops,
replace the cable or supply before collecting data.

Use 10 samples per second, no automatic current/time stop, and record VBUS,
IBUS, PBUS, and energy. Save one raw `.cfn` file per run. Integrate PBUS over the
aligned idle and active windows; do not substitute a single instantaneous watt
value.

## PC procedure

Connect the laptop charger, select one fixed performance mode, close other GPU
loads, and confirm `nvidia-smi` reports numeric power. First run a one-map smoke
test with `pc_gpu_benchmark.py`; then run `run_pc_all_scenes.py`. Formal summaries
must assert full-test coverage, power samples for every map, and deterministic
output hashes across repeats.

## Board procedure

The saved four-scene FPGA full-test evidence must exist before the ARM comparison
because it supplies the per-map physical reference hash. Stop the HTTP service
and notebook kernel before a standalone benchmark so only one process owns the
overlay. Start a new meter record during the script's settle interval, retain
the full idle and active windows, and stop the record after the script reports
PASS.

Complete all commands in [BOARD_POWER_COMMANDS.md](BOARD_POWER_COMMANDS.md).
Fill `board_power_runs_template.csv` from aligned and integrated meter records,
then run:

```text
python summarize_board_power.py board_power_runs_template.csv --out results/board_power_summary.json
python summarize_comparison.py --pc-results results/pc_gpu_full_test_authoritative --board-summary results/board_power_summary.json --out results/stage39_power_comparison.csv
```

For the existing Stage39 physical gate, also provide at least ten distinct idle
and ten distinct inference samples per scene in
`stage39_board_input_power.csv`, for at least 80 FPGA rows in total. ARM rows do
not belong in that gate CSV.
