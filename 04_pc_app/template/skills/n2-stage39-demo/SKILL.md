---
name: n2-stage39-demo
description: Run or audit the N2-NeRF2 Stage39 four-scene PYNQ-Z2 judge demonstration, including physical exactness, latency, full-test, blind-Tx, HDMI, and evidence-hash checks. Use only for this packaged Stage39 project.
---

# N2-NeRF2 Stage39 demonstration

Preserve the distinction between three evidence classes:

- `software reference`: portable integer or PC FP32 results produced on the computer;
- `physical FPGA`: results returned by the Stage39 PYNQ service after runtime-weight loading;
- `measured power`: whole-board DC-input readings entered from an external meter.

Never relabel one class as another. A missing physical result remains `PENDING`.

Before a demonstration, run `scripts/audit_stage39_evidence.py --workspace <workspace>` and read [references/evidence-gates.md](references/evidence-gates.md). Stop if an immutable package hash fails. A `PENDING` physical gate allows setup and software preview, but does not support a claim that the new Stage39 weights were physically verified.

Use this order:

1. Verify both packages.
2. Upload and extract `N2-NeRF2-Stage39-board.zip`; run the Stage39 notebook setup and supervised-service cells.
3. Run four-scene full-map physical exact self-tests. Each expected raw-Q15 SHA-256 must equal the physical SHA-256.
4. Run 50 full maps per scene. Require median at most 1.2 s, P95 at most 1.5 s, stable output hash, and zero reference mismatch.
5. Start `run_stage39_app.bat`, select a scene, and check the board connection.
6. Complete every scene's held-out full-test run. Do not accept a saved result whose bitstream, runtime-model, or test-catalog SHA-256 differs from the active Stage39 package.
7. Present the four-scene full-test CDF and representative cases, then execute a held-out blind Tx and a free Tx query. When requested, enter a printable judge nonce and show the selected ray indices, raw Q15 values, ARM cross-check, negative control, and transcript SHA-256.
8. Run the Notebook evidence exporter after the full-test runs. Fill the external meter CSV and run `software/stage39/finalize_stage39_evidence.py`. Accept final claims only when its status is `FULL_PASS`.
9. Keep HDMI labels and the browser result visible together during the witnessed rehearsal.

The final result is the raw FPGA Q15 output divided by 32768. Stage39 has no output calibration branch. Scene switching loads QAT weights and fixed-Rx cache into the same 95 MHz bitstream; it does not synthesize or swap bitstreams.
