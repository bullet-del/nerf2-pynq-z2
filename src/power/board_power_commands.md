# Board full-test power commands

Run one command at a time from the extracted board package. Start and save a
separate FNB58 record for every command. Continue only after the command reports
PASS.

```text
cd /home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board
```

## FPGA: 12 runs

```text
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office2 --repeat-id R1 --output scenes/Office2/evidence/stage39/power/fpga_Office2_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office2 --repeat-id R2 --output scenes/Office2/evidence/stage39/power/fpga_Office2_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office2 --repeat-id R3 --output scenes/Office2/evidence/stage39/power/fpga_Office2_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Meetingroom --repeat-id R1 --output scenes/Meetingroom/evidence/stage39/power/fpga_Meetingroom_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Meetingroom --repeat-id R2 --output scenes/Meetingroom/evidence/stage39/power/fpga_Meetingroom_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Meetingroom --repeat-id R3 --output scenes/Meetingroom/evidence/stage39/power/fpga_Meetingroom_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office1 --repeat-id R1 --output scenes/Office1/evidence/stage39/power/fpga_Office1_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office1 --repeat-id R2 --output scenes/Office1/evidence/stage39/power/fpga_Office1_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene Office1 --repeat-id R3 --output scenes/Office1/evidence/stage39/power/fpga_Office1_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene s23 --repeat-id R1 --output scenes/s23/evidence/stage39/power/fpga_s23_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene s23 --repeat-id R2 --output scenes/s23/evidence/stage39/power/fpga_s23_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_fpga_fulltest_power.py --scene s23 --repeat-id R3 --output scenes/s23/evidence/stage39/power/fpga_s23_R3.json
```

## ARM: 12 runs

```text
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office2 --repeat-id R1 --output scenes/Office2/evidence/stage39/power/arm_Office2_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office2 --repeat-id R2 --output scenes/Office2/evidence/stage39/power/arm_Office2_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office2 --repeat-id R3 --output scenes/Office2/evidence/stage39/power/arm_Office2_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Meetingroom --repeat-id R1 --output scenes/Meetingroom/evidence/stage39/power/arm_Meetingroom_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Meetingroom --repeat-id R2 --output scenes/Meetingroom/evidence/stage39/power/arm_Meetingroom_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Meetingroom --repeat-id R3 --output scenes/Meetingroom/evidence/stage39/power/arm_Meetingroom_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office1 --repeat-id R1 --output scenes/Office1/evidence/stage39/power/arm_Office1_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office1 --repeat-id R2 --output scenes/Office1/evidence/stage39/power/arm_Office1_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene Office1 --repeat-id R3 --output scenes/Office1/evidence/stage39/power/arm_Office1_R3.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene s23 --repeat-id R1 --output scenes/s23/evidence/stage39/power/arm_s23_R1.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene s23 --repeat-id R2 --output scenes/s23/evidence/stage39/power/arm_s23_R2.json
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_arm_fullmap_benchmark.py --scene s23 --repeat-id R3 --output scenes/s23/evidence/stage39/power/arm_s23_R3.json
```

For every JSON, require `exact_full_test_index_order` and
`every_test_map_measured` to be true. FPGA runs with a saved reference also
require `all_outputs_match_saved_full_test`; ARM runs require
`all_maps_bit_exact_to_fpga`.
