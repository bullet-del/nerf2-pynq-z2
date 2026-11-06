# Current numbered repository verification

The maintained tree follows the validated NoPS1 numerical workflow, with
lowercase numbered directories and separate skill/report/results folders.
The complete original inputs are regular files under data/inputs, included
by GitHub Download ZIP; no Git LFS or external private NeRF2 tree is needed.

November 6 verification actually completed:

| Check | Actual result |
|---|---|
| Source layout, links and original inputs | PASS; seven AMD correspondence rows, four original checkpoints and 8623 PNGs |
| Repository integration | Four tests PASS |
| Generic PYNQ Skill | Offline positive/negative controls PASS |
| Source audit | PASS against 00_config/workflow.json |
| Generated App HTTP and PC preview | PASS; four actual scene pages plus an Office2 GT/PC CUDA preview and saved PNG; no board execution claimed |
| Notebook integrity regression | PASS; output/count changes accepted, source tamper rejected, original restored |
| Original PC GPU power inputs | Four scenes PASS using bundled model/renderer and original checkpoints; not a new power experiment |
| New-scene export Skill | Default nine-stage dry-run PLANNED with relocated dependencies |
| Cold reconstruction | Full structural adaptation, 1800-step QAT for each scene, 1725-map evaluation and runtime export PASS |
| RTL | Runtime/cache/inference and four-scene-switch-and-return PASS |
| Vivado 2024.1 | Fresh RTL/Tcl implementation and new BIT/HWH; timing gate PASS |
| Assembly and final host verification | Both package verifiers and all 17 host checks PASS |

Raw new results and exact metrics are in
[November 6 reconstruction](../results/reproduction_20261106/README.md).
The computer rebooted during the first attempt; only completed newly generated
adaptation stages were retained, and missing work was resumed. No historical
intermediate model, map or prebuilt bitstream was used to finish that run.

Vivado starts in an isolated short directory; absolute source/output paths are
preserved. Earlier startup failures and interrupted work are not relabeled as
passed. The RTL bytes and formal model settings remain those of the NoPS1 kit.

The new package's physical field stays PENDING_NEW_BOARD_EXECUTION. This does
not claim a new board test, HDMI demonstration or power measurement. Historical
physical results retain their original identity and date.


The actual App startup test exposed two missing shared helper files in the PC
template: stage27_display.py and stage28_exact_common.py. They were copied
unchanged from the shared implementation and verified byte-for-byte against
the archived working App. Both are now required by the source checker.
Assembly and all 17 host checks were rerun after that correction; the actual
App HTTP/PC-preview test then passed.
