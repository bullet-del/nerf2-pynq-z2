# Final physical-evidence steps

## Export the board evidence

Start the board service from the generated notebook. Confirm each scene has a
saved `all_test/result.json` and `identity.json`, run notebook cell 5, and
download:

```text
/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board/evidence/stage39_physical_gate/Stage39_physical_evidence.zip
```

The exporter packages saved results and does not rerun 1,725 maps. A missing
scene result must be regenerated before export.

## Continuous live rehearsal

With the board service, PC application, HDMI display, and continuous video
recording active, run:

```text
python stage39_continuous_rehearsal.py --scene Office2
```

The script produces a random nonce and performs a held-out blind query, ARM
cross-check, HDMI display, valid arbitrary-Tx query, second ARM/HDMI check, and
the +1-LSB negative control. Preserve the original uncompressed video and the
generated rehearsal manifest.

## Finalize

```text
python finalize_stage39_verified.py --pc-app-root "<PC_APP_ROOT>" --physical-export "<Stage39_physical_evidence.zip>" --power-csv "<stage39_board_input_power.csv>" --rehearsal-manifest "<manifest.json>" --video-file "<original_video>" --output "results/stage39_verified_final_acceptance.json"
```

Acceptance requires the original finalizer status `FULL_PASS` and the final
message `VERIFIED STAGE39 FULL_PASS`.
