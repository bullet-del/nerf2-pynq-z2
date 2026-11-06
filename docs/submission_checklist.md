# AMD指南逐项目录与执行入口

对照本项目留存的2026年AMD选题指南3.3自主选题赛道。根README保留“采用其他组织方式的队伍须在 README.md 中给出目录对照说明”原文，并逐项对应src、sim、build、board、data、skill、report。

| 指南条款 | 对应材料与执行方式 |
|---|---|
| 3.3.1与3.3.3.2 初级组器件及板级运行 | PYNQ-Z2 / XC7Z020CLG400-1；results/physical为原始板端输出，results/photos为实物 |
| 3.3.2 完整作品和软硬件协同 | RTL、PYNQ控制、PC App、HDMI、四场景模型、运行结果分别在01～06编号目录与results；根README第3～5节为顺序入口 |
| 3.3.3.1 工具版本 | Vivado 2024.1，00_config/environment.yml；scripts/check_environment.py检查完整依赖，Tcl从RTL生成BIT/HWH |
| 3.3.3.1 大模型协作记录 | report/llm_collaboration.md与原设计报告附录；保留提示词、回答与纠错过程 |
| 3.3.3.1与3.3.5.2 Skill | skill目录保留原内容，本次按用户要求不修改第12项对应的Skill实现与材料 |
| 3.3.3.3 开源 | 公开GitHub仓库、MIT许可和docs/third_party_notices.md；原始NeRF2许可随源码保留 |
| 3.3.4 性能、资源与基线 | results/vivado、results/power_summary、1725幅逐图结果；新增PC参考只调用四个原始模型的完整NeRF2，不把固定Rx缓存评估时间写入App |
| 3.3.4 从零复现 | 根README六步；models阶段同时生成完整原始PC计时；四原始模型、8623幅图、config和划分直接包含在源码ZIP |
| 3.3.5.1 完整源码 | 01_vivado/rtl、02_model_pipeline、04_pc_app/template、05_pynq_board/template、06_power_measurement |
| 3.3.5.1 仿真与正确性 | 01_vivado/sim、results/rtl、results/physical及本次work/full/rtl_sim入口 |
| 3.3.5.1 综合实现报告 | results/vivado及原NoPS1完整结果；目标95 MHz，实现值以各自HWH与时序报告为准 |
| 3.3.5.1 上板脚本与输出比对 | 六单元Notebook生成器、板端服务、自检、完整测试集、HDMI和随机射线；原physical ZIP和PC逐图归档保留 |
| 3.3.5.1 构建脚本 | 03_package_builder/reproduce_stage39.py与01_vivado/tcl；不依赖PS1 |
| 3.3.5.3 背景、原理、划分和优化 | report/design_report.pdf原文保留；本次按用户要求不修改第11项对应报告 |
| 3.3.5.3 复现说明 | 根README现行步骤及06_power_measurement/power_protocol.md；所有电脑命令根目录运行 |
| 3.3.5.4 命名与目录 | 小写英文分类目录，README目录对照完整；生成的业务包名称和场景ID保留原协议拼写 |

## 本次改动范围

处理原检查清单第1～8、10项；第9项GT标签、第11项原报告、第12项Skill保持原样。RTL、数值模型流水线、四个原始模型和历史实验数值没有改动。新增采样视图和重新处理的统计单独存放，不覆盖原实验文件。

## 结果目录

- results/cold_reproduction_20261013：原NoPS1的7182个完整中间结果，含模型、图、定标、QAT、运行时资产和RTL输出。
- results/power_inputs、results/power_alignment：12轮原始CFN/程序JSON、视频事件与便携路径；超大未剪辑原片由配套Release保存。
- results/power_reprocessed_20261106：用原始输入重新处理的5175次逐图积分、12轮汇总和80行采样视图。
- results/reproduction_20261106：前次完整模型/RTL/Vivado运行记录，原样保留。
- results/repository_repair_20261106：本次原始NeRF2完整计时与软件打包检查记录。

## 使用者在板端执行什么

继续使用现有正常作品时，无须替换其目录。从源码重新构建一套包时，按根README第4节上传新board ZIP，运行环境读取脚本、Notebook第1～4单元、App四场景总体精度及查询/HDMI；第5单元导出ZIP。需要新包的独立功率记录时，再按第5节完成12轮FPGA采集及连续演示收口。不采集ARM功率。
