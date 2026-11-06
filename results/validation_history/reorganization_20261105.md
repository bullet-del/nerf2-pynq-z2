# Verification of the organized repository

The November 5, 2026 change reorganizes the source tree. It removes standalone
publication checksum inventories and redundant upload helpers, consolidates
Skills, lowercases custom paths, and updates documentation/Python/Tcl/CI paths.

The following checks were actually executed after migration:

| Check | Result and scope |
|---|---|
| Repository layout and links | PASS; all seven AMD correspondence rows, lower-case custom paths, Python/JSON syntax and local Markdown links |
| Layout integration tests | 4/4 PASS; includes old mixed-case names and exclusion of local configs/generated outputs |
| Generic PYNQ offline workflow | PASS; HWH inventory, generated adapter bundle, golden-output mock pass and intentional negative control |
| Source workflow audit | PASS; relocated required files and fixed formal configuration |
| Input preflight | PASS; four original checkpoints, disjoint splits, CUDA and Vivado 2024.1 |
| Office2 model smoke workflow | Completed factorization, training calibration, PTQ, short QAT, one-map evaluation and runtime export; 2385/3072 words |
| Compatible-new-scene Skill | Default nine-stage dry-run PLANNED; model shape and relocated dependency checks passed |
| Ordered reproduction workflow | Seven-stage dry-run PLANNED with the relocated executable paths |
| Vivado 2024.1 Block Design | BD VALIDATED using the relocated Tcl and nine source RTL modules |
| Actual RTL simulation | Runtime-load/cache/inference and four-scene-switch-and-return both PASS using archived golden vectors |

The first BD test used a custom temporary path longer than Windows' IP-generation
limit. Retesting in an isolated short temporary directory passed. Hardware
logic and clock settings were not changed to obtain that result.

This validation is not a new full 1800-step four-scene QAT run, routed bitstream
implementation, physical PYNQ experiment or power measurement. Original source
RTL, deployed BIT/HWH, report PDF and recorded experimental results are retained.
The short model run is connectivity verification only. Newly generated work is
ignored and kept separate from the archived formal results.
