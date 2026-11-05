# Evidence rules

## Lineage

Record the source checkpoint or model identity, dataset identity, split identity,
configuration, random seed, tool versions, and generated model/bitstream
identity. A filename alone is not lineage.

## Software and simulation

Formal metrics require the declared complete held-out set. Training and
calibration may not read the held-out set unless the method explicitly declares
that design and the claim is adjusted. RTL simulation establishes implemented
logic behavior; it does not establish physical timing, board integration, or
measured energy.

## Physical execution

Evidence should identify the active bitstream and runtime model, include an
unpredictable witnessed input when relevant, and retain raw integer output or a
cryptographic digest. Negative controls should demonstrate that the verifier
rejects a changed value.

## Power and energy

For strict ratios, align the workload, precision, input set, device boundary,
supply boundary, idle treatment, sample integration, and repeat statistic.
Driver-reported GPU power, CPU package power, whole-board DC input, and wall
power are different boundaries. Label each value with its boundary.

## Claim language

Use `PASS` only for a completed declared gate, `FAIL` for a completed gate that
violated its contract, and `PENDING` when required evidence has not been
generated. Describe cross-boundary numbers as descriptive comparisons rather
than strict energy-efficiency ratios.
