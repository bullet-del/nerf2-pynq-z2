# Test data and reference outputs

- reference_results: all 1725 GT/PC/FPGA per-map comparison records.
- power_summary: recorded board-input and GPU power summaries.
- validation/history: dated development snapshots; historical states are retained.
- [downloads.json](downloads.json): the six complete Release artifacts.

Original datasets, four model parameter files, fixed splits and configs are in
nerf2_inputs.zip. Full PC maps are in pc_app_package.zip; every saved FPGA map
is in test_results.zip. Raw meter logs are in power_logs.zip. Use
python build/tools/download_artifacts.py from the repository root.

There are no separate publication checksum inventories. Identity fields inside
the original scientific model/output records remain intact.
