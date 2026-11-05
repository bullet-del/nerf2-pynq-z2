# Directory correspondence and entry points

The root README gives the required AMD 3.3.5.4 correspondence. This page adds
the exact old/current mapping for existing users and the archived PDF appendix.

| Earlier directory | Current repository directory | Responsibility |
|---|---|---|
| 00_Config | build/config | Fixed environment/config and ignored machine-local config |
| 01_Vivado/rtl | src/rtl | Nine Verilog/SystemVerilog sources |
| 01_Vivado/sim | sim/testbench | Testbench source and base vectors |
| 01_Vivado/tcl | build/tcl | Vivado reconstruction and simulation Tcl |
| 01_Vivado/output | build/output | Ignored newly generated hardware files |
| 02_Model_Pipeline | src/model | Structural adaptation, PTQ, QAT, runtime export |
| 02_Vitis | docs/control_architecture.md | Why this design uses PYNQ Linux control |
| 03_Package_Builder | build/pipeline | Ordered full reconstruction and assembly |
| 04_PC_App/template | src/host | PC source/template |
| 05_PYNQ_Board/template | board/source | ARM, Notebook/service and integer-reference source |
| 06_Power_Measurement | src/power | Power acquisition and analysis source |
| 07_Validation | data/validation/history | Dated historical validation snapshots |
| scripts/configure_repository.py | build/tools/configure_repository.py | Machine-local paths |
| scripts/fetch_release_assets.py | build/tools/download_artifacts.py | Input/deployment/result downloads |
| scripts/verify_kit.py | build/tools/check_repository.py | Layout, syntax, contract and result checks |
| skills | skill | All reusable Skills in one directory |

The recommended guide directories have direct roles: src is source, sim is
simulation/verification, build is reproducible construction and implementation
reports, board is deployment/physical evidence, data is inputs/reference results,
skill is reusable experience, and report is the design/collaboration record.
docs supplies explanatory detail. No additional source tree is hidden elsewhere.

Old names in this table or in historical logs are explanatory text, not current
repository paths. Business scene identifiers remain s23/Meetingroom/Office1/Office2.
Their repository evidence directories use s23/meetingroom/office1/office2.
Do not globally lowercase model IDs or change the archived deployment ZIP.
