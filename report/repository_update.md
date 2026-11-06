# Current repository and report supplement — 2026-11-06

The original submitted PDF is retained as a historical report of the actual
project. Appendix B uses old NoPS1 path spellings. The [root README](../README.md)
supplies full current commands, environment setup and the six-step sequence.
[Directory correspondence](../docs/directory_map.md) maps both previous layouts.

All four original checkpoints, datasets, configs and fixed splits are ordinary
files under data/inputs and included by GitHub Download ZIP. Run
`python scripts/configure_repository.py --vivado "<actual vivado.bat>"` to create
an ignored local config. Do not change formal settings to adapt local paths.
No standalone source SHA256 inventory is needed.

The requested clock is 95 MHz. Archived HWH/routed Clock Summary record about
94.118 MHz with a 10.625 ns period. Unqualified 95 MHz on PDF pages 5, 71 and 74
means the requested setting. The retained legacy metadata field frequency_mhz
also denotes that target. Each new build's HWH and Clock Summary provide its
achieved value. Table 2-47 distinguishes target and achieved clock.

The October 8/9 physical FULL_PASS certifies its matched deployment. October 13
host reconstruction and November 6 checks are distinct. Newly generated
packages require new physical execution and new power data.

Power statistics also have separate scopes: sampled-power medians about
1.974–2.043 W and continuous active-window means about 2.098–2.120 W. The latter
yield integrated energy about 2.349–2.409 J/map. GPU telemetry and board input
do not share a measurement boundary. Source power videos and every alignment
input are not fully included publicly; summaries do not remove this limitation.
