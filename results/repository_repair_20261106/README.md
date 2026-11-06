# 本次软件整理与运行记录

处理第1～8、10项；GT标签、原报告、Skill不改。checks.json列出源码、历史数值与运行时数组一致性；host_checks.json为本次21项软件包检查。original_pc_reference包含四个原始模型、完整1725幅、原始NeRF2完整图推理计时，不使用固定Rx缓存。旧功率/时延结论和原始结果保留原日期及原数值，本次计时单独保存。

PC功率短流程检查新输出的逐图窗口标记，正式三轮全测试功率记录沿用原始归档。全部12轮历史FPGA原始文件的重算在../power_reprocessed_20261106。

## 全新环境与源码ZIP运行

独立创建Python 3.8.20环境，安装环境文件中的固定依赖，从提交667c302生成源码ZIP，解压至全新短路径后依次执行。开始时没有work或outputs目录，没有复制旧模型中间结果。fresh_environment.json保存依赖版本；fresh_zip_run.json保存命令、耗时与返回值；fresh_zip_logs保存逐项日志。

| 项目 | 运行结果 |
|---|---|
| 依赖加载、CUDA及pip依赖检查 | PASS |
| 源码目录、README链接和原始数据检查 | PASS |
| 本机路径配置及四场景预检 | PASS |
| 11项主机测试 | PASS |
| 四场景结构适配、训练集定标、2步QAT、单图评估与运行时导出 | PASS，smoke模式 |
| 四个原始模型的完整NeRF2推理 | 每场景1图，SMOKE_ONLY；完整1725幅计时另存original_pc_reference |
| Vivado RTL运行时装载、缓存、推理、四场景切换与返回 | PASS |

短流程用于检查新环境和路径，不作为新的正式模型精度、功率或实板结果。完整模型运行记录保存在../reproduction_20261106，原NoPS1完整中间结果和配套Vivado产物保存在../cold_reproduction_20261013。独立环境的临时目录与本机Vivado启动访问调整记在运行记录中，源码没有因此改动。

public_recordings.json列出已公开的12段原始功率录像，名称和大小与data/downloads.json一致；根README第5.2节给出下载与重新汇总命令。
