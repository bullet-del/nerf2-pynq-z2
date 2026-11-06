# NeRF2无线空间谱FPGA推理系统：从原始模型参数复现

2026 AMD自主选题初级组，PYNQ-Z2 / XC7Z020CLG400-1。系统在固定接收机位置与姿态的已建模环境中，输入发射机Tx三维坐标，输出90×360方向空间谱。PL完成稀疏整数神经网络和射线渲染，ARM组织任务、装载参数和控制DMA，PC提供交互、独立FP32参考与可视化。HDMI通过独立显示Overlay输出。

本仓库恢复为已经使用过的 **NoPS1分类结构和六步执行顺序**，目录统一为小写。根README就是完整执行手册，不需要自行拼接旧阶段工程。**四个原始checkpoint、四场景全部数据、config和固定训练/测试划分均作为普通Git文件保存；GitHub的Code → Download ZIP会将它们一起下载，没有Git LFS指针。** 不需要重新训练原始NeRF2，也不读取你电脑上后来修改的NeRF2代码。

已有真实实验结果单独存放在`results/`，本次运行生成`work/`和`outputs/`。复现流水线不把历史BIT/HWH、量化模型、预测图或实板结果作为输入。

## 1. 目录及AMD选题指南对照

> 采用其他组织方式的队伍须在 README.md 中给出目录对照说明。

以下为本仓库与AMD选题指南3.3.5.4推荐目录的逐项对照；采用编号分类并不省略任何一类材料。

| AMD推荐目录 | 本仓库实际目录 | 内容及入口 |
|---|---|---|
| `src/` | `01_vivado/rtl`、`02_model_pipeline`、`04_pc_app/template`、`05_pynq_board/template`、`06_power_measurement` | RTL、结构适配/QAT、PC、ARM/PYNQ及功耗源码 |
| `sim/` | `01_vivado/sim`、`results/rtl` | 仿真源文件、基础向量及已执行日志 |
| `build/` | `00_config`、`01_vivado/tcl`、`03_package_builder`、`scripts`、`results/vivado` | 环境、Tcl重建、总控与打包、工具、资源/时序报告及归档硬件 |
| `board/` | `05_pynq_board/template`、`results/physical`、`results/photos` | 板端源码、Notebook生成器、四场景实测记录和实物照片；完整已验证部署包另见Release |
| `data/` | `data/inputs`、`results/reference_results`、`results/power_summary`、`data/downloads.json` | 原始输入、1725幅逐图比较、功率汇总及大附件入口 |
| `skill/` | `skill/` | 模型导出、通用PYNQ校验及辅助工作流，各自包含说明和验证边界 |
| `report/` | `report/` | 正式设计报告、实际大模型协作记录及当前复现补充说明 |

```text
00_config/              固定参数、环境文件、工作流契约
01_vivado/              rtl、sim、tcl；运行后生成output
02_model_pipeline/      结构适配、训练集定标、QAT、运行时导出
02_vitis/               本作品使用PYNQ Linux，无独立Vitis应用的说明
03_package_builder/     总控、评估、仿真准备、打包、最终电脑端检查
04_pc_app/template/     PC App及随包提供的NeRF2参考源码
05_pynq_board/template/ PYNQ驱动、服务及独立整数参考
06_power_measurement/   PC GPU、FPGA、ARM的测量与统计
07_validation/          检查入口说明
data/inputs/            原始数据、模型参数、config及固定划分
results/                已有真实实验和历史复现结果（不参与冷启动）
skill/                  技能包
report/                 设计报告与协作记录
docs/                   接口、架构及补充说明
scripts/                检查、配置、结果附件下载
tests/                  自动检查
work/                   本次中间结果，运行后生成，不纳入Git
outputs/                本次PC/板端交付目录与ZIP，运行后生成，不纳入Git
```

自定义仓库路径使用小写英文。原来的`00_Config`等仅大小写改变，业务场景标识仍为`s23`、`Meetingroom`、`Office1`、`Office2`。固定协议和板端生成目录保留原名称，例如`N2-NeRF2-Stage39-board`；不要对运行时包再批量改名。旧PDF的命令路径以本README和[目录对照](docs/directory_map.md)为准。

## 2. 输入、环境和开始条件

### 2.1 四个原始checkpoint已经包含

输入根目录为`data/inputs`，下表路径均相对此目录。文件内容保持原始训练产物；只将仓库路径改为小写。

| 场景 | 原始模型参数 | 原始config | 训练/测试/总图数 |
|---|---|---|---:|
| s23 | [010000.tar](data/inputs/logs/rfid/s23/spectrum-exp1/ckpts/010000.tar) | 同实验目录`config.yml` | 4898 / 1225 / 6123 |
| Meetingroom | [005000.tar](data/inputs/logs/rfid/meetingroom/spectrum-meetingroom/ckpts/005000.tar) | 同实验目录`config.yml` | 800 / 200 / 1000 |
| Office1 | [005000.tar](data/inputs/logs/rfid/office1/spectrum-office1/ckpts/005000.tar) | 同实验目录`config.yml` | 800 / 200 / 1000 |
| Office2 | [005000.tar](data/inputs/logs/rfid/office2/spectrum-office2/ckpts/005000.tar) | 同实验目录`config.yml` | 400 / 100 / 500 |

数据分别在`data/inputs/data/rfid/s23`、`meetingroom`、`office1`、`office2`，每个目录包含`spectrum/*.png`、`tx_pos.csv`、`gateway_info.yml`、`train_index.txt`和`test_index.txt`。总计8623幅原始图、1725幅固定测试图。预检会核对原始checkpoint身份、数据数量和划分，不接受用另一份模型参数代替。

### 2.2 下载解压和命令行位置

在本GitHub页面选择 **Code → Download ZIP**，完整解压到较短的英文路径，例如`E:\stage39\nerf2-pynq-z2-main`。以下所有电脑命令都在**Anaconda Prompt**中、仓库根目录执行。路径含空格时必须加双引号。示例进入目录：

```bat
cd /d "E:\stage39\nerf2-pynq-z2-main"
python scripts/check_repository.py
```

通过标志为`REPOSITORY CHECK PASS`。它检查文件结构、原始模型、数据数量和已归档测试覆盖，不会训练或运行FPGA。也可使用兼容入口`python scripts/verify_kit.py`。没有独立源码SHA256清单或手工刷新清单的步骤；数值模型和运行时包内部的身份检查仍保留。

至少预留20 GB空间，建议40 GB；Vivado和完整模型处理可能需要数小时。连接笔记本电源，保证散热，关闭睡眠。已验证主机为Windows、Python 3.8.17、PyTorch 2.0.1+CUDA 11.8、RTX 4050 Laptop GPU、Vivado 2024.1。其他GPU可能改变用时或浮点细节，不能据此预先保证文件逐字节相同。

### 2.3 创建或更新Python环境

从未创建过`nerf2`环境时：

```bat
conda env create -f 00_config/environment.yml
conda activate nerf2
```

已经有能够运行原NoPS1代码包的`nerf2`环境时，直接激活，不必更新已经可用的依赖：

```bat
conda activate nerf2
```

只有检查发现缺少依赖时，再执行更新，不要同时重复创建环境：

```bat
conda env update -n nerf2 -f 00_config/environment.yml
conda activate nerf2
```

检查环境：

```bat
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

`torch.cuda.is_available()`必须为`True`。依赖安装需要访问Conda源；源码与数据已经在ZIP内。PYNQ使用板端已有Python环境，不在板上创建电脑端Conda环境。

### 2.4 仅配置本机Vivado安装位置

正式复现使用Vivado **2024.1**及Zynq-7000器件支持。下面命令中的Vivado路径替换为本机真实路径：

```bat
python scripts/configure_repository.py --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat"
```

它默认使用仓库自带`data/inputs`，生成`00_config/reproduction_config.local.json`。总控自动读取该本机配置，无须编辑正式参数文件。重复配置时：

```bat
python scripts/configure_repository.py --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat" --force
```

Vivado 2025.2/2026.1可能需要IP升级与重新验证，本仓库不把它们标为已经验证。AMD指南允许其他版本，但须说明并保证复现；这里正式使用2024.1。Windows的Vivado临时项目路径应保持简短；若本机TEMP被改为很长的路径，可在当前Anaconda Prompt先执行：

```bat
mkdir E:\vivado_tmp
set TEMP=E:\vivado_tmp
set TMP=E:\vivado_tmp
```

目录已存在时`mkdir`提示可忽略；不要删除别人的临时目录。此设置仅影响当前终端及其启动的程序。

## 3. 电脑端正式复现：按六步依次运行

所有步骤直接运行Python，Vivado由Python调用Tcl。**没有`.ps1`文件。** 不需要先下载旧BIT/HWH或旧部署包。以下生成目录是本次运行结果；`results/`中已有文件不能用来代替本次检查。

### 第1步：输入与环境预检

```bat
python 03_package_builder/reproduce_stage39.py preflight
```

应输出`status: PASS`，生成`work/reports/00_preflight.json`。检查四个原始模型、8623幅数据、固定训练测试数量、源文件、CUDA、Vivado路径及正式QAT参数。失败时先处理列出的第一项原因，不进入长时间运算。

### 第2步：由RTL和Tcl重新生成BIT/HWH

```bat
python 03_package_builder/reproduce_stage39.py vivado
```

该命令创建自己的临时Vivado工程，导入9个RTL源文件，生成PS、DMA、GPIO、互连、复位及自定义流计算模块，执行综合、布局布线、时序检查和位流生成。输出：

```text
01_vivado/output/n2_stage38_raw.bit
01_vivado/output/n2_stage38_raw.hwh
01_vivado/output/stage37_summary.txt
01_vivado/output/stage37_timing_routed.rpt
01_vivado/output/stage37_utilization.rpt
01_vivado/output/stage37_hierarchical_utilization.rpt
```

通过标志为`BITSTREAM OK`，摘要`timing_met=1`且WNS/WHS非负。95 MHz是请求目标，归档实际时钟约94.118 MHz；请以本次时序报告Clock Summary和HWH记录实现值。旧兼容字段`frequency_mhz=95`表示目标值，不是独立实测频率。

也可以在Vivado Tcl Console切到一个短的临时目录，再用绝对路径载入同一Tcl，二选一即可；Python入口会自动创建独立的短启动目录，工程和产物位置仍由Tcl确定：

```tcl
cd $env(TEMP)
source {E:/stage39/nerf2-pynq-z2-main/01_vivado/tcl/build_stage39.tcl}
```

### 第3步：四场景结构适配、定标、QAT及1725幅完整评估

```bat
python 03_package_builder/reproduce_stage39.py models --profile full --scene all
```

执行顺序为结构适配 → 训练集定标 → PTQ → 硬件精确QAT → 完整测试集评估 → 稀疏运行时导出。原始NeRF2不用重训，但这里仍需执行部署所需的受约束微调和QAT。

- 四场景复现的结构适配：s23确定性掩码、0步；另外三个场景各3000步。施加掩码本身会改变一般浮点模型，不能称为对原始任意checkpoint完全无损。
- 定标：每场景8192条训练射线，激活百分位99.5；QAT为1800步、batch 192、学习率1e-5。定标及QAT参数更新均使用训练划分。
- 测试：s23 1225幅、Meetingroom 200幅、Office1 200幅、Office2 100幅，共1725幅，完整生成PC FP32、QAT影子FP32和硬件整数参考指标；PTQ也按完整划分评估。
- 门禁：PC减QAT整数平均SSIM≤0.015、QAT不低于PTQ、权重容量≤3072个1024位字。不会自动放宽门限。
- 全零列删除保持冻结整数网络的计算结果；固定Rx中间量与信号支路分开组织，生成模型、索引、偏置、移位量、LUT和缓存。

每个场景应得到：

```text
work/full/factorized/<scene>/checkpoint.tar
work/full/qat/<scene>/checkpoint_qat.tar
work/full/qat/<scene>/train_calibration.json
work/full/qat/<scene>/full_evaluation.json
work/full/qat/<scene>/ptq_full_evaluation.json
work/full/qat/<scene>/software_gate.json
work/full/maps/<scene>/pc_fp32/
work/full/maps/<scene>/qat_integer/
work/full/runtime/<scene>/model/stage37_runtime_model.npz
work/full/runtime/<scene>/package_manifest.json
```

`<scene>`是四个业务场景名称之一。程序按所选阶段组织场景，运行中暂时只出现部分场景目录属正常现象。仅看到文件夹不代表全部完成，以命令结束和四场景门禁为准。

这里的PC FP32评估使用结构适配后的浮点模型，并复用固定Rx中间量；其记录的时间与第5节原始NeRF2整图GPU功率基准的执行方式不同，不能混作同一个时延指标。原始checkpoint仍保留，GPU正式功率入口读取原始模型。

### 第4步：本次模型的RTL逐位与场景切换仿真

```bat
python 03_package_builder/reproduce_stage39.py rtl --profile full
```

应同时出现：

```text
STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS
STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS
```

检查模型装载、缓存构建、整数推理以及s23 → Meetingroom → Office1 → Office2 → s23切换。报告为`work/full/rtl_sim/rtl_simulation_report.json`。

### 第5步：用本次产物生成PC和板端包

```bat
python 03_package_builder/reproduce_stage39.py assemble
```

使用本次四场景模型、预测图、本次BIT/HWH和干净软件模板，生成自检契约、报告、Notebook及交付包：

```text
outputs/N2-NeRF2-Stage39-PC-App/
outputs/N2-NeRF2-Stage39-board/
outputs/N2-NeRF2-Stage39-PC-App.zip
outputs/N2-NeRF2-Stage39-board.zip
```

此步骤会重建它负责的上述输出目录。若输出目录里已经放入新的实测文件，应先另行备份，避免再次打包时被替换。模型与部署包的内部完整性记录由程序自动生成，无须手工改摘要。

### 第6步：最终电脑端检查

```bat
python 03_package_builder/reproduce_stage39.py verify
```

`work/full/reports/cold_reproduction_final_gate.json`中电脑端`status`应为`PASS`，包括原始模型谱系、1725幅覆盖、精度、四个不同运行时模型、单一位流、RTL及包校验。`physical_gate=PENDING_NEW_BOARD_EXECUTION`是正确状态：它明确要求下一步运行真实PYNQ，不能用历史实测替代。

已经理解六步后，可用一条等价命令顺序执行全部电脑步骤：

```bat
python 03_package_builder/reproduce_stage39.py all --profile full --scene all
```

### 中断后如何继续

第2步中断可重新执行第2步。第3步已完成全部结构适配但QAT或精度检查失败时，先查第一处错误；确认输入和参数正确后，按下面三条重新生成后续产物：

```bat
python 03_package_builder/run_model_pipeline.py qat --profile full --scene all
python 03_package_builder/run_model_pipeline.py evaluate --profile full --scene all
python 03_package_builder/run_model_pipeline.py runtime --profile full --scene all
```

然后继续第4～6步。只想处理一个场景，可把`all`替换为相应场景名，但最终打包前四个场景都必须完整。不要混用旧QAT和新定标，不要手工把FAIL改为PASS。

### 可选短流程：只检查环境连通性

```bat
python 03_package_builder/reproduce_stage39.py models --profile smoke --scene all
python 03_package_builder/reproduce_stage39.py rtl --profile smoke
```

此模式为2步适配、2步QAT、每场景1幅图，输出在`work/smoke`，不能打包为正式交付，也不能证明正式精度或功率。

## 4. 上传PYNQ-Z2并运行完整系统

### 4.1 准备板卡

板卡需有支持PYNQ-Z2的PYNQ系统、正常SD启动和可用Jupyter。使用套件电源适配器供电；电脑经网线与板卡RJ45相连，无网口的电脑使用USB转网口；显示器连接板卡HDMI OUT。供电选择和启动跳线遵循板卡手册。功率测量使用另一条明确记录的5 V USB输入链路，见第5节。

板端Python需可导入`pynq`、`numpy`和`PIL`。在Jupyter Terminal检查：

```sh
/usr/local/share/pynq-venv/bin/python3 -c "import sys,pynq,numpy,PIL; print(sys.version); print(pynq.__version__); print(numpy.__version__); print(PIL.__version__)"
```

记录本机镜像、PYNQ和Python版本。历史Notebook使用板端Python 3.10；没有把未验证镜像标为已兼容。如果该解释器路径不存在，应先确认镜像提供的PYNQ解释器，不能换成一个未安装PYNQ的普通Python直接运行。

### 4.2 上传新包并启动Notebook

1. 在Jupyter中先备份旧board目录和实测结果，再改名移走旧目录，防止解压混合。
2. 上传本次`outputs/N2-NeRF2-Stage39-board.zip`并解压；根目录必须为`/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board`，不要形成双层同名目录。
3. 打开根目录的`Stage39_QAT_Four_Scenes.ipynb`。不要直接运行源码模板。
4. 第1单元：校验包和导入路径。Notebook输出、执行计数变化不会使未修改的源单元校验失败。
5. 第2单元：启动8311独立服务，单元会返回。
6. 第3单元：四场景各一幅完整90×360原始Q15逐位自检。
7. 第4单元：四场景各50次完整图时延与稳定性检查。

### 4.3 启动PC App

电脑仍在仓库根目录、`nerf2`环境执行：

```bat
python outputs/N2-NeRF2-Stage39-PC-App/pc_app/stage39_pc_server.py --port 8310
```

保持终端打开，在浏览器访问`http://127.0.0.1:8310`。填写实际板卡URL，例如`http://192.168.2.99:8311`；该IP只是例子，PC与板卡需位于可互通网段。也可以双击生成目录里的`run_stage39_app.bat`。

在App依次完成：

1. 四场景总体精度：分别1225、200、200、100幅；每幅须本次真实板端生成。
2. 测试样本查询：显示GT、PC、FPGA；盲测使用测试位置。
3. 场景合法范围内自由Tx：没有实测GT，显示PC/FPGA，不编造GT准确率。
4. 随机nonce、16条随机射线ARM独立整数复核，检查差值；执行+1 LSB反例。
5. 启用HDMI观察外接显示器；随后再查询一次确认推理Overlay恢复。
6. 回Notebook运行第5单元导出`Stage39_physical_evidence.zip`，下载备份；第6单元查看服务状态。

四场景总体精度不必在每次打开App时重算；保存在SD卡目录中的结果重启后仍可使用。正常断电重启后通常只重跑Notebook第1、2单元。删除或替换board目录会丢失其中结果，必须事先备份。

### 4.4 保存结果和连续演示

PC端逐场景下载全部已保存FPGA图；同时保留板端physical ZIP。它们用途不同，不能只保留界面截图。连续演示可人工随机点击并录像，或在PC另开已激活环境的终端运行：

```bat
python 06_power_measurement/stage39_final_steps/stage39_continuous_rehearsal.py --scene Office2 --board-url http://192.168.2.99:8311
```

把命令中的板卡IP换为实际地址。脚本说明及最终收口命令见[连续演示与最终检查](06_power_measurement/stage39_final_steps/README.md)。最终FULL_PASS需匹配本次板端结果、功率数据与真实演示；电脑端PASS本身不能代替它。

## 5. 功率测量与结果

### 5.1 电脑GPU：直接使用自带原始模型

完成第3节和PC包生成后，先检查四场景功率入口实际选择的模型：

```bat
python scripts/check_pc_power_inputs.py
```

应为4场景PASS并输出`PC POWER INPUT CHECK PASS`。测量使用`data/inputs`中的四个**原始checkpoint**，参考网络来自生成PC包内的NeRF2源码，不使用结构适配后的checkpoint作为原始GPU功率基准，也不依赖D盘私人目录。

先用一幅图检查NVIDIA遥测及运行环境（此项不是正式结果）：

```bat
python 06_power_measurement/run_pc_all_scenes.py --partial-maps 1 --repeats 1 --output-root work/power_pc_smoke
```

正式测量全部测试图、每场景R1/R2/R3：

```bat
python 06_power_measurement/run_pc_all_scenes.py
python 06_power_measurement/summarize_pc_full_test.py --results-root 06_power_measurement/results/pc_gpu_full_test_authoritative
```

连接笔记本电源、固定性能模式，关闭其他GPU负载；`nvidia-smi`功率必须是数值。脚本记录逐图时间、采样和输出身份。正式结果不能使用`--partial-maps`。

### 5.2 FPGA和ARM：外接FNB58

先读[完整供电、窗口及比较协议](06_power_measurement/power_protocol.md)。FNB58串接板卡5 V USB输入时按板卡要求切换USB供电，不能同时接DC圆口供电，不触发快充。保留每轮原始CFN、程序JSON及时间对齐录屏。

独立测量前，在板端board根目录停止8311服务：

```sh
cd /home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board
/usr/local/share/pynq-venv/bin/python3 -c "import sys; sys.path.insert(0,'jupyter'); from stage39_service_launcher import stop; print(stop(port=8311))"
```

在PC另开终端启动时钟服务，绑定电脑实际以太网IP（下例为192.168.2.1）：

```bat
python 06_power_measurement/stage39_power_clock.py --serve --bind 192.168.2.1
```

保持该终端打开。每轮板端测量前先执行同步，地址替换为实际PC地址：

```sh
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_power_clock.py --sync-server http://192.168.2.1:8318
```

然后按[四场景12轮FPGA及12轮ARM命令](06_power_measurement/board_power_commands.md)逐条运行。每轮单独录制FNB58数据，保留60秒空闲段及完整活动窗口。ARM为独立扩展测量；尚未完成时如实保持未测。

PC独显遥测与PYNQ整板5 V输入边界不同，仅作各自边界下的描述性比较。严格同边界的ARM/FPGA能效倍数需要两端实际测量。连续活动窗口的积分能耗、稀疏采样功率×固定样本时延是不同统计量，不能混写。

## 6. 已有结果、报告和大附件

| 场景 | 测试图数 | PC平均SSIM | FPGA平均SSIM | 全测试集平均FPGA时间/s每图 |
|---|---:|---:|---:|---:|
| s23 | 1225 | 0.7546 | 0.7415 | 1.140 |
| Meetingroom | 200 | 0.6925 | 0.6826 | 1.121 |
| Office1 | 200 | 0.6616 | 0.6527 | 1.119 |
| Office2 | 100 | 0.6129 | 0.6053 | 1.101 |

上述为已有物理部署结果。归档器件占用为50069 LUT、48464寄存器、127.5 BRAM、178 DSP；WNS +0.015 ns、WHS +0.019 ns，实际时钟约94.118 MHz。三轮连续活动窗口平均功率约2.098～2.120 W，单图积分能耗约2.349～2.409 J；10点采样功率中位数约1.974～2.043 W，与前者统计方法不同。

- [本次完整电脑端复现记录](results/reproduction_20261106/README.md)：四场景完整QAT、1725幅评估、RTL、Vivado和17项电脑端检查均已实际通过；本次新包仍待实板执行。
- [结果总索引](results/README.md)：逐图数据、实板自检、50次稳定性、功率和Vivado/RTL结果。
- [正式报告](report/design_report.pdf)、[当前路径及术语补充](report/repository_update.md)、[真实LLM协作记录](report/llm_collaboration.md)。
- [固定Release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104)：完整已验证board/PC部署包、1725幅实际FPGA图、原始功率记录、179.8秒视频。Release归档不参与本次冷启动计算。

下载上述大型历史附件（仅查看旧系统或旧结果时需要，重新复现不用先下载）：

```bat
python scripts/download_artifacts.py --asset results
python scripts/download_artifacts.py --asset power
python scripts/download_artifacts.py --asset video
```

文件保存至`external/downloads`。`results/`中原始数据和报告保留原日期；旧October 8/9物理FULL_PASS、October 13重建电脑端PASS以及本次检查有不同对象，不能互相替代。

## 7. Skill

[Skill索引](skill/README.md)提供两个竞赛技能包：

1. [同结构NeRF2模型导出](skill/nerf2-checkpoint-fpga-export/README.md)：任意符合输入契约的NeRF2模型、数据、划分 → 结构适配、定标、QAT、描述性全测试报告和运行时NPZ。新场景默认3000步适配，不设SSIM门槛；硬件容量仍需检查。它与本README复现固定四场景的精度门禁不同。
2. [通用PYNQ Overlay验证](skill/pynq-overlay-validation/README.md)：BIT/HWH、项目适配器和黄金向量 → 接口检查、功能/时延报告，支持换题目、换受支持板卡；mock与实板结论分开。

每项均列出适用范围、运行命令、失效条件、失败经验和已验证效果。根目录可运行离线正反例：

```bat
python -m unittest discover -s tests -v
python -m unittest discover -s skill/pynq-overlay-validation/tests -v
python skill/fpga-ml-reproducibility-audit/scripts/audit_fpga_repro.py --root . --contract 00_config/workflow.json --mode source
```

## 8. 常见问题

- **提示找不到模型或数据**：确认完整解压，先运行`check_repository.py`和第1步；不要仅下载几个Python文件。
- **模型哈希不符**：不是指定四个原始训练产物，或文件发生改变；不要绕过身份检查。
- **CUDA不可用**：确认进入`nerf2`环境、NVIDIA驱动和CUDA版PyTorch正常。
- **s23的SSIM差变成0.02045**：检查是否误用旧99.5之外的激活百分位或4e-5学习率；正式值为99.5和1e-5，不能直接放宽0.015门槛。
- **Vivado长路径错误**：使用短解压目录和短TEMP；不要为绕过路径问题改RTL。
- **容量超过3072字**：是PL片上容量，换大SD卡不能解决。
- **Notebook首单元失败**：确认解压层级及原始board目录名，检查报错具体文件；正常Jupyter保存状态应被允许。
- **轮播提示缺少all-test结果**：对应场景板端结果未完成或已被删除；只有真实结果仍在时才能直接恢复。关闭App本身不会删除结果。
- **重新打包前**：先备份生成目录里的实测结果，再运行assemble；禁止混合两次构建的模型、BIT/HWH和功率记录。

项目贡献采用[MIT](LICENSE)，NeRF2和供应商材料保留[原有归属与条款](docs/third_party_notices.md)。
