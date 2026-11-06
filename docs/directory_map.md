# Directory correspondence

The root README contains the explicit seven-row AMD 3.3.5.4 directory map.
The executable structure follows the validated NoPS1 kit with lowercase names.

| Original NoPS1 directory | Current directory |
|---|---|
| 00_Config | 00_config |
| 01_Vivado | 01_vivado |
| 02_Model_Pipeline | 02_model_pipeline |
| 02_Vitis | 02_vitis |
| 03_Package_Builder | 03_package_builder |
| 04_PC_App | 04_pc_app |
| 05_PYNQ_Board | 05_pynq_board |
| 06_Power_Measurement | 06_power_measurement |
| 07_Validation | 07_validation and results/validation_history |
| External NeRF2 inputs | data/inputs |
| work and outputs | work and outputs, newly generated and ignored |

| November 5 repository path | Current directory |
|---|---|
| src/rtl | 01_vivado/rtl |
| sim/testbench | 01_vivado/sim |
| build/tcl | 01_vivado/tcl |
| build/config | 00_config |
| build/pipeline | 03_package_builder |
| src/model | 02_model_pipeline |
| src/host | 04_pc_app/template |
| board/source | 05_pynq_board/template |
| src/power | 06_power_measurement |
| build/tools | scripts |
| build/tests | tests |
| board/evidence | results/physical |
| board/photos | results/photos |
| build/reports and build/prebuilt | results/vivado/reports and results/vivado/prebuilt |
| sim/validation | results/rtl |
| data/reference_results | results/reference_results |
| data/power_summary | results/power_summary |
| skill and report | skill and report, still separate |

Business IDs remain s23/Meetingroom/Office1/Office2; tracked input paths use
lowercase names. Generated board deployments keep their original directory and
protocol filenames. Do not rename deployed packages. Checkpoint/data bytes are
preserved. Legacy release builders requiring external Stage34/35/38 working
folders are replaced by the complete cold builder, rather than exposed as
current commands. Historical PDF Appendix B paths are superseded by the root
README's full current commands.
