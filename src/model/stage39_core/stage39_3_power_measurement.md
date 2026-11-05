# Stage39.3 physical board-input power gate

Only readings from a real external meter satisfy this gate. PYNQ-Z2 does not
provide board-input telemetry suitable for replacing an external 5 V
measurement, and Vivado estimates must not be labeled as measured power.

1. Insert a USB/DC power meter at the PYNQ-Z2 5 V input and record its model.
2. For every scene, load the runtime model, allow the board to settle, and record
   at least ten distinct idle voltage/current/power samples.
3. During repeated FPGA inference, record at least ten distinct inference
   samples for that scene.
4. Fill `stage39_board_input_power.csv` with at least 80 rows across four scenes.
   Include voltage, current, power, meter identity, UTC time, and a run identity.
   Power must be consistent with voltage multiplied by current within the meter
   and rounding tolerance.
5. Run `software/stage39/finalize_stage39_evidence.py`. `FULL_PASS` additionally
   requires the four 50-map latency gates, bit-exact checks, and complete
   held-out board test sets.

The report's dynamic board power is the inference median minus the same-scene
idle median. Whole-board energy uses active 5 V input power so processing-system
and peripheral overhead are not hidden.
