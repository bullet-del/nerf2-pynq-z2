---
name: pynq-overlay-validation
description: Generate and execute a board-independent PYNQ Overlay validation contract with HWH inspection, IP and register checks, cache-safe DMA adapter patterns, golden-reference comparison, latency statistics, and evidence reports. Use for RTL or HLS overlays on any PYNQ-supported board.
---

# PYNQ Overlay validation

Read `README.md` before using this Skill. Start with `scripts/inspect_hwh.py`,
then create a portable bundle with `scripts/generate_validation_bundle.py`.

Keep project-specific behavior inside the generated `adapter.py`. Do not edit
the validation framework to hard-code one accelerator, board, array shape, or
register address.

Use `--dry-run` on a PC before board execution. `PASS_MOCK` validates Python
adapter and golden comparison logic only. Claim physical Overlay execution only
from a `PASS_BOARD` report generated on the target PYNQ board.

Preserve these rules:

1. Pair BIT and HWH by SHA-256 and reject mismatches before loading.
2. Start an AXI DMA receive channel before the send channel when backpressure
   may occur; use a bounded transaction timeout.
3. Flush CPU-written input buffers and invalidate device-written output buffers.
4. Compare every measured repetition with the golden output.
5. Use exact comparison for integer/protocol results and documented tolerance
   for floating-point results.
6. Keep Overlay loading time separate from steady-state transaction latency.
7. Save JSON, Markdown, and hashes for every run. Keep power and external-I/O
   evidence in their own measurement protocols.
