# Physical setup and archived-system restoration

The target is a PYNQ-Z2 using XC7Z020CLG400-1. The ARM runs PYNQ Linux/Python
and the PL runs the packaged inference Overlay. Board photos are in
`board/photos`. The submitted report gives the detailed interface diagrams.

Connect the board RJ45 to the PC Ethernet interface or USB Ethernet adapter.
Configure the board/PC addresses for the same subnet, preserving the established
working network. The archived service example is `192.168.2.99:8311` and the
PC browser server uses port 8310. Use HDMI OUT for the external display.

Maintain the already verified board supply connection. The archived power
experiment measures the complete board at the 5 V USB input using FNB58.
Changing the supply path changes the measurement setup. The report and power
protocol define that setup, warmup, idle period and measurement window.

The full `board_package.zip` deployment includes every scene runtime NPZ,
fixed-Rx cache, driver, selftest reference, BIT/HWH and notebook. Keep the package
directory name `N2-NeRF2-Stage39-board`. Its notebook integrity checks depend on
source cells, with runtime outputs excluded from the source identity.

The exact archived PC application is distributed as `pc_app_package.zip`.
Run its Python server directly in the NeRF2 environment. The separate clean
source template also supplies a BAT launcher. Archived deployment archives
retain their original filenames and original integrity manifests.

To browse already captured all-test images, download `test_results.zip` and
restore each `scenes/<scene>/evidence/stage39_pc_app` directory under the archived
PC application. These caches contain all 1725 actual FPGA maps. If the newly
built board has a different identity, generate new board evidence for that build.

The source checkpoint, original data and complete split files needed for a full
rebuild are in `nerf2_inputs.zip`. The download script verifies SHA-256 before
extracting them under `external/nerf2_inputs`.
