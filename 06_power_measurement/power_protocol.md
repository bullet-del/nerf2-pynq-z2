# PC GPU与FPGA整板功率执行步骤

本流程不采集ARM功率。ARM随机射线整数复核是原作品功能，继续保留。
电脑命令一律在仓库根目录的Anaconda Prompt中运行，环境为nerf2；板端命令在Jupyter Terminal中运行。

## 一 已有实验直接查看和重算

原始CFN与板端JSON在results/power_inputs，12轮视频事件在results/power_alignment。未剪辑原片从配套Release下载到external/downloads：

```bat
python scripts/download_artifacts.py --asset recordings
python 06_power_measurement/collect_fpga_power.py --manifest results/power_alignment/captures.json --output-root work/archived_fpga_power_recalculated
```

输出目录必须为新的。历史原片、原始采样和原JSON不会被改写。s23 R1视频覆盖了记录开始、四个程序阶段和完整推理过程，但没有录到最后停止仪表的动作；其原始帧事件和该情况一起保存，不虚构停止时刻。

## 二 新采集前准备

先按根README完成新包生成、Notebook第1～4单元以及App四场景总体精度。各场景all_test/result.json提供逐图原始Q15参考。准备一套新的PC GPU测量目录和FPGA采集目录，不能混入不同模型或位流的记录。

PC GPU的正式入口读取data/inputs中的原始训练模型，使用原始model.py和renderer.py；覆盖全部测试图，各三轮。根README第5.1节给出完整命令。GPU遥测不代表整台电脑的墙插功率。

FPGA每轮覆盖完整测试划分：s23 1225幅、Meetingroom 200幅、Office1 200幅、Office2 100幅；每幅90×360条射线，每射线64点。四场景×R1/R2/R3共5175次整图推理。

## 三 FPGA供电和录制

1. 板卡关机断电后，将电源选择跳线置于USB位置。连接固定5 V USB-A电源→FNB58测量通路→短USB-A转Micro-USB线→PYNQ-Z2 J8。不要同时接DC圆口或Arduino VIN，不启用快充触发。日常使用套件适配器不受影响；本节功率实验采用这条明确的USB测量链。
2. FNB58独立USB-PC通信口连接电脑。在你已经使用成功的USB测试仪软件中连接设备，新建记录，设10次/秒，关闭按电流、容量、时间自动停止，保留VBUS、IBUS、PBUS。采集不使用软件NRG累计值代替PBUS积分。
3. 同一画面录下USB测试仪软件和Jupyter Terminal。建议录屏30帧/秒，保持原始录像，不剪辑测量过程。录屏先开始，仪表记录在板端程序提示的30秒等待期间开始。
4. 每轮程序结束并出现PASS后，先停止仪表，再保存CFN，最后停止录屏。新采集尽量把仪表开始、停止及四个程序阶段都录完整。若板卡复位、供电不稳、DMA恢复或输出哈希不符，该轮不要作为完整结果。

板端先停止8311服务，并关闭仍在操作Overlay的Notebook内核：

```sh
cd /home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board
/usr/local/share/pynq-venv/bin/python3 -c "import sys; sys.path.insert(0,'jupyter'); from stage39_service_launcher import stop; print(stop(port=8311))"
```

电脑另开一个已激活nerf2环境的终端，在仓库根目录保持时钟服务运行；绑定地址替换为电脑实际以太网地址：

```bat
python 06_power_measurement/stage39_power_clock.py --serve --bind 192.168.2.1
```

每轮在板端先同步，再执行对应测量命令：

```sh
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_power_clock.py --sync-server http://192.168.2.1:8318
```

按[12条FPGA命令](board_power_commands.md)逐条执行，每轮都有30秒等待、60秒空闲和完整活动段。Windows若询问防火墙，只放行当前电脑与板卡通信需要的网络。不要跳过时钟检查。

## 四 原始文件整理与模板生成

在电脑仓库根目录建立work/fpga_power/captures。将每轮板端JSON下载到这里，并配上它自己的CFN和原始录像，按下表改文件名，不改内容：

| 场景 | 三轮文件名前缀 | 每个前缀所需后缀 |
|---|---|---|
| s23 | s23_r1、s23_r2、s23_r3 | .json、.cfn、.mp4 |
| Meetingroom | meetingroom_r1、meetingroom_r2、meetingroom_r3 | .json、.cfn、.mp4 |
| Office1 | office1_r1、office1_r2、office1_r3 | .json、.cfn、.mp4 |
| Office2 | office2_r1、office2_r2、office2_r3 | .json、.cfn、.mp4 |

36个原始文件齐全后，在电脑运行：

```bat
python 06_power_measurement/create_fpga_power_manifest.py --capture-root work/fpga_power/captures --output work/fpga_power/captures.json
```

程序生成12个*_alignment.json，自动填写输入身份和相对路径。已有模板不会覆盖。不要把根目录results里的历史时间标记套在新采集视频上。

## 五 填写视频事件时间

逐个打开模板和对应的原片，在播放器中查看时间（以视频开始为0秒，分钟×60+秒；有帧数时再加帧号/帧率）。填写：

| 字段 | 填什么 |
|---|---|
| video_fps | 原片实际帧率，例如30.0 |
| video_duration_s | 原片完整时长，单位秒 |
| recorder_active_first_video_s | 仪表界面第一次显示正在记录的时刻，即Play变灰、Pause生效 |
| recorder_stopped_first_video_s | 仪表停止、保存按钮生效的时刻 |
| phase_first_visible_video_s.idle_start | Terminal第一次出现本轮FPGA IDLE START UTC的时刻 |
| phase_first_visible_video_s.idle_end | 第一次出现FPGA IDLE END UTC的时刻 |
| phase_first_visible_video_s.active_start | 第一次出现FPGA ACTIVE START UTC的时刻 |
| phase_first_visible_video_s.active_end | 第一次出现FPGA ACTIVE END UTC的时刻 |
| reviewed | 全部核对完成后把false改为true |

例如1分47秒15帧、30帧/秒，应填107.5。JSON数字不用引号，小数使用点。不要根据功率曲线上升下降挑选阶段边界；边界来自同一段原始录屏的程序UTC标记。

正常新采集保持recorder_stop_observed为true，必须填停止时刻。只有像历史s23 R1那样确实拍完全部活动段、但未拍停止动作时，才如实设为false并将停止时刻保留null；处理程序仍要求原片覆盖所有必需事件，并在报告中记载缺少停止动作。

## 六 一次生成12轮积分、四场景汇总和80行采样

```bat
python 06_power_measurement/collect_fpga_power.py --manifest work/fpga_power/captures.json --output-root work/fpga_power/processed
```

输出如下：

| 文件 | 用途 |
|---|---|
| 各scene_rN子目录中的*_audit.json | 程序、采样和视频事件的一致性及积分结果 |
| *_per_map_energy.csv | 该轮每幅图的时间窗口和近似积分能耗 |
| board_power_runs.csv | 12轮空闲/活动共24行积分汇总 |
| fpga_power_summary.json/.csv | 每场景三轮统计，不需要ARM |
| stage39_board_input_power.csv | 从各场景R2的原始空闲、活动窗口各取10个固定等间隔采样，共80行 |
| processing.json | 四场景、12轮、5175幅推理覆盖及录制情况 |

80行是最终收口所需的采样视图，完整功率结论仍依据全部12轮CFN和逐图窗口，不能把80行当成全部采样。主指标为完整活动窗口PBUS梯形积分除以图数；逐图窗口和活动段之间的整理开销分别保留。

合并电脑GPU与FPGA结果：

```bat
python 06_power_measurement/summarize_comparison.py --pc-results 06_power_measurement/results/pc_gpu_full_test_authoritative --board-summary work/fpga_power/processed/fpga_power_summary.json --out work/fpga_power/processed/pc_fpga_comparison.csv
```

PC GPU功率域与PYNQ整板USB测量点不同，精度和执行方式也不同，只能作各自边界下的数值描述。FNB58测量点包含下游供电线损，不能称为纯FPGA芯片功率。10 Hz采样和录屏标记不提供亚毫秒功率波形或仪器标定证书。

## 七 保存与收口

保留所有原始文件、填写完成的模板和processed目录，再按[连续演示与收口命令](stage39_final_steps/README.md)运行。不要把旧包功率CSV与新模型、不同位流或另一轮板端导出交叉使用。
