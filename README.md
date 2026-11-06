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
| `board/` | `05_pynq_board/template`、`results/physical`、`results/photos` | 板端源码、Notebook生成器、四场景实测记录和实物照片；完整已完成部署包另见Release |
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
06_power_measurement/   PC GPU与FPGA整板功率的测量与统计
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

至少预留20 GB空间，建议40 GB；Vivado和完整模型处理可能需要数小时。连接笔记本电源，保证散热，关闭睡眠。已完成主机为Windows、Python 3.8.17、PyTorch 2.0.1+CUDA 11.8、RTX 4050 Laptop GPU、Vivado 2024.1。其他GPU可能改变用时或浮点细节，不能据此预先保证文件逐字节相同。

### 2.3 创建或更新Python环境

从未创建过`nerf2`环境时：

```bat
set CONDA_CHANNEL_PRIORITY=strict
set PIP_DEFAULT_TIMEOUT=120
set PIP_RETRIES=10
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

检查全部实际依赖及GPU：

```bat
python scripts/check_environment.py
```

继续查看GPU信息：

```bat
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

`torch.cuda.is_available()`必须为`True`。环境文件通过Conda安装Python，再从PyPI及PyTorch官方CUDA 11.8 wheel源安装固定版本依赖；安装时需要访问这些下载源。源码与数据已经在ZIP内。PYNQ使用板端已有Python环境，不在板上创建电脑端Conda环境。

### 2.4 仅配置本机Vivado安装位置

正式复现使用Vivado **2024.1**及Zynq-7000器件支持。下面命令中的Vivado路径替换为本机真实路径：

```bat
python scripts/configure_repository.py --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat"
```

它默认使用仓库自带`data/inputs`，生成`00_config/reproduction_config.local.json`。总控自动读取该本机配置，无须编辑正式参数文件。重复配置时：

```bat
python scripts/configure_repository.py --vivado "E:/AMD/Vivado/2024.1/bin/vivado.bat" --force
```

Vivado 2025.2/2026.1可能需要IP升级及重新生成硬件；本仓库的命令、IP版本和归档结果以2024.1为准。AMD指南允许其他版本，但须说明并保证复现；这里正式使用2024.1。Windows的Vivado临时项目路径应保持简短；若本机TEMP被改为很长的路径，可在当前Anaconda Prompt先执行：

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
set MODE bit
source {E:/stage39/nerf2-pynq-z2-main/01_vivado/tcl/build_stage39.tcl}
```

### 第3步：四场景结构适配、定标、QAT及1725幅完整评估

```bat
python 03_package_builder/reproduce_stage39.py models --profile full --scene all
```

执行顺序为结构适配 → 训练集定标 → PTQ → 硬件精确QAT → 完整测试集评估 → 稀疏运行时导出 → 原始NeRF2完整图计时。最后一段重新读取四个原始模型参数，调用自带原始`model.py`和`renderer.py`的`render_ss`，逐幅运行全部1725幅测试图，不复用固定Rx缓存。此段只测时间，和第5节的三轮GPU功率实验分开。原始NeRF2不用重训，但这里仍需执行部署所需的受约束微调和QAT。

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
work/full/pc_reference/<scene>/timing_reference.json
work/full/pc_reference/<scene>/per_map.csv
```

`<scene>`是四个业务场景名称之一。程序按所选阶段组织场景，运行中暂时只出现部分场景目录属正常现象。仅看到文件夹不代表全部完成，以命令结束和四场景门禁为准。

这里的PC FP32评估使用结构适配后的浮点模型，并复用固定Rx中间量；其记录的时间只属于离线精度处理，不写入App的PC时间参考。App的PC时间参考只能来自本次`work/full/pc_reference`，即原始NeRF2完整图推理；GPU功率入口也读取原始模型。原始完整图计时会额外耗费时间，看到`<scene>: n/N maps`时继续等待，不能用0.014～0.020秒的缓存评估时间代替。

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

`work/full/reports/cold_reproduction_final_gate.json`中电脑端`status`应为`PASS`，包括原始模型谱系、1725幅覆盖、精度、四个不同运行时模型、单一位流、RTL及包校验。报告中的`physical_gate`对应第4节板端运行。已有作品及其原始运行结果完整保留在`results/physical`和历史Release；从源码重新生成的包按第4节产生它自己的板端记录。再运行模型谱系检查：

```bat
python 06_power_measurement/audit_stage39_checkpoint_lineage.py --board-root outputs/N2-NeRF2-Stage39-board --output-root work/full/reports/lineage
```

结论应为`PASS`。该入口直接识别当前`package_manifest.json`，不需要私人D盘目录或额外baseline文件。

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

若原始PC计时尚未完成，再运行：

```bat
python 03_package_builder/reproduce_stage39.py pc-reference --scene all
```

然后继续第4～6步。只想处理一个场景，可把`all`替换为相应场景名，但最终打包前四个场景都必须完整。不要混用旧QAT和新定标，不要手工把FAIL改为PASS。

### 可选短流程：只检查环境连通性

```bat
python 03_package_builder/reproduce_stage39.py models --profile smoke --scene all
python 03_package_builder/reproduce_stage39.py rtl --profile smoke
```

此模式为2步适配、2步QAT、每场景1幅图，输出在`work/smoke`，不能打包为正式交付，也不能证明正式精度或功率。

## 4. 上传PYNQ-Z2并运行完整系统

### 4.1 准备板卡和网络

继续使用你现有能正常运行作品的MicroSD卡和PYNQ系统，不需要重装。若另用一张空卡，从[PYNQ官方镜像页面](https://www.pynq.io/index.html)选择**PYNQ-Z2**对应镜像，解压后按[官方写卡说明](https://pynq.readthedocs.io/en/v2.7.0/appendix/sdcard.html)写入；不要选PYNQ-Z1或其他板卡镜像。先完成[厂家板卡启动与网络步骤](https://pynq.readthedocs.io/en/v2.5/getting_started/pynq_z2_setup.html)，再进入本节的软件步骤。镜像不是本工程源文件，不随GitHub源码ZIP分发。

板卡使用套件电源适配器，启动选择为SD；电脑通过网线和USB转网口连接板卡RJ45，显示器接HDMI OUT。保持当前可用网络配置。如果是首次直连，在电脑的以太网IPv4属性填写`192.168.2.1`、掩码`255.255.255.0`，不要修改Wi-Fi适配器；板卡地址以实际系统设置为准，本手册例子为`192.168.2.99`。先在电脑运行：

```bat
ping 192.168.2.99
```

浏览器打开板卡Jupyter地址（现有系统以当前地址为准；示例`http://192.168.2.99:9090`）。下面板端命令全部在**Jupyter Terminal**运行，电脑命令仍在仓库根目录的Anaconda Prompt运行。不要在电脑终端执行`/home/xilinx/...`命令。

### 4.2 上传新包并运行六个Notebook单元

本节用于从GitHub源码重新生成的一套包。原来正常工作的目录先保留备份，不能把新文件覆盖解压进旧目录。

1. 上传本次`outputs/N2-NeRF2-Stage39-board.zip`到`/home/xilinx/jupyter_notebooks`。
2. 在Jupyter Terminal依次运行下面四行。先停止旧服务，再给已有同名目录加时间后缀，保留里面的结果。

```sh
cd /home/xilinx/jupyter_notebooks
python3 -c "import runpy; from pathlib import Path; p=Path('N2-NeRF2-Stage39-board/jupyter/stage39_service_launcher.py'); print(runpy.run_path(str(p))['stop'](port=8311)) if p.is_file() else None"
python3 -c "from pathlib import Path; from datetime import datetime; p=Path('N2-NeRF2-Stage39-board'); p.rename(p.with_name(p.name+'_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S'))) if p.exists() else None"
python3 -m zipfile -e N2-NeRF2-Stage39-board.zip .
```

3. 读取实际板端环境和镜像自带HDMI文件，输出保存在结果目录。这条命令不会装载位流：

```sh
cd /home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board
/usr/local/share/pynq-venv/bin/python3 jupyter/stage39_environment_report.py
```

若解释器不存在，先在Jupyter Notebook中执行`import sys; print(sys.executable)`，后续命令使用打印出的PYNQ解释器。若提示缺少`base.bit/.hwh`，先恢复该PYNQ-Z2镜像自带的base Overlay，不能用本工程推理位流替代HDMI位流。镜像版本和库版本按命令实际输出记录，不根据Python版本猜测。

4. 打开board根目录的`Stage39_QAT_Four_Scenes.ipynb`，运行第1单元建立路径和检查包，第2单元启动8311服务，第3单元运行四场景完整图逐位自检，第4单元运行四场景各50次时延。每个单元结束后再运行下一个。
5. 按4.3节启动电脑App，完成四场景总体精度与各项查询，再运行Notebook第5单元导出结果、第6单元查看服务状态。

### 4.3 启动PC App

若旧App仍占用8310端口，先在它原来的终端按Ctrl+C退出。电脑仍在仓库根目录、`nerf2`环境执行：

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

## 5. 功率测量与完整结果收口

本流程只包含**PC GPU和FPGA整板输入**，不要求ARM功率。ARM随机射线整数复核继续保留，不属于ARM功率实验。所有电脑命令均在仓库根目录执行。

### 5.1 PC GPU：原始NeRF2完整推理

先完成第3节并生成PC包：

```bat
python scripts/check_pc_power_inputs.py
python 06_power_measurement/run_pc_all_scenes.py --partial-maps 1 --repeats 1 --output-root work/power_pc_smoke
```

第一条核对四个原始模型；第二条只检查测量链，不能作为正式全测试集结果。正式运行全部测试图、各三轮：

```bat
python 06_power_measurement/run_pc_all_scenes.py
python 06_power_measurement/summarize_pc_full_test.py --results-root 06_power_measurement/results/pc_gpu_full_test_authoritative
```

新测量直接使用逐图推理窗口，无需再做迁移。只有处理旧版本没有指标边界标记的测量目录时，才在汇总前运行一次：

```bat
python 06_power_measurement/realign_pc_gpu_metrics.py --results-root 06_power_measurement/results/pc_gpu_full_test_authoritative
```

电脑连接电源、固定性能模式并关闭其他GPU任务。原始模型、完整`render_ss`、每幅32400条射线和每射线64点不变；逐图计时含CPU→GPU、渲染、同步、GPU→CPU，不含输入构造和绘图。`nvidia-smi`只代表GPU功率域。

### 5.2 查看并重新处理已有的12轮FPGA原始记录

你已经完成的CFN、板端JSON和逐帧对齐信息分别在`results/power_inputs`、`results/power_alignment`；原始录像超过普通Git单文件大小限制，放在配套Release。先下载12段未剪辑原片，再重算：

```bat
python scripts/download_artifacts.py --asset recordings
python 06_power_measurement/collect_fpga_power.py --manifest results/power_alignment/captures.json --output-root work/archived_fpga_power_recalculated
```

输出含12轮逐图积分、`board_power_runs.csv`、四场景FPGA汇总和80行`stage39_board_input_power.csv`。这一步处理已有实验，不需要板卡，不覆盖已归档结果。输出目录必须是新的；重复处理时改用新目录名。s23 R1原片未拍到最后停止仪表的动作，原片中的记录开始、四个程序阶段和完整推理窗口均保留；该情况在处理记录中单独记载。

### 5.3 从源码重建后另做一套FPGA功率记录

若继续使用原来的Stage39及原记录，无需重做已有实验。若按第3节重新构建并希望为新包单独采集数据，先完成第4节的四场景全测试集。详细采集、视频事件填写与数据处理步骤见[FPGA功率完整步骤](06_power_measurement/power_protocol.md)，12条板端命令见[四场景R1/R2/R3](06_power_measurement/board_power_commands.md)。

每轮准备一个板端JSON、一个FNB58 CFN和一段原始录屏。下载到电脑后，按场景和轮次命名，放进`work/fpga_power/captures`；例如`office2_r1.json`、`office2_r1.cfn`、`office2_r1.mp4`。文件改名不改内容。四场景×三轮共36个原始文件齐全后：

```bat
python 06_power_measurement/create_fpga_power_manifest.py --capture-root work/fpga_power/captures --output work/fpga_power/captures.json
```

按功率协议填写新生成的12个`*_alignment.json`中的视频事件秒数，再运行：

```bat
python 06_power_measurement/collect_fpga_power.py --manifest work/fpga_power/captures.json --output-root work/fpga_power/processed
python 06_power_measurement/summarize_comparison.py --pc-results 06_power_measurement/results/pc_gpu_full_test_authoritative --board-summary work/fpga_power/processed/fpga_power_summary.json --out work/fpga_power/processed/pc_fpga_comparison.csv
```

上述汇总不需要ARM文件。PC GPU遥测与PYNQ整板USB输入属于不同功率边界，比较只作各自边界下的数值描述。

### 5.4 保存板端导出并完成最终收口

在电脑建立`work/board_downloads`，将Notebook第5单元导出的文件保存为`work/board_downloads/Stage39_physical_evidence.zip`。四场景App完整运行后，其电脑端结果会保存在本次PC包内，不要只下载截图。

需要生成一段连续演示记录时，保持App、板端服务和显示器运行，在电脑根目录执行（IP换成实际板卡地址）：

```bat
python 06_power_measurement/stage39_final_steps/stage39_continuous_rehearsal.py --scene Office2 --board-url http://192.168.2.99:8311 --output-root work/live
```

演示原片保存为`work/live_camera.mp4`。脚本返回后执行下面完整命令，`--rehearsal-root`自动读取刚才输出目录里的本次记录指针：

```bat
python 06_power_measurement/stage39_final_steps/finalize_stage39_verified.py --pc-app-root outputs/N2-NeRF2-Stage39-PC-App --physical-export work/board_downloads/Stage39_physical_evidence.zip --power-csv work/fpga_power/processed/stage39_board_input_power.csv --rehearsal-root work/live --video-file work/live_camera.mp4 --output work/final/stage39_final_acceptance.json
```

若只需对已亲眼看到的HDMI过程收口而不附录像，把`--video-file work/live_camera.mp4`替换为`--user-observed-hdmi`。最终输出应为`FULL_PASS`。历史归档与本次新构建使用各自配套文件，不交叉替换。

## 6. 已有结果、报告和大附件

| 场景 | 测试图数 | PC平均SSIM | FPGA平均SSIM | 全测试集平均FPGA时间/s每图 |
|---|---:|---:|---:|---:|
| s23 | 1225 | 0.7546 | 0.7415 | 1.140 |
| Meetingroom | 200 | 0.6925 | 0.6826 | 1.121 |
| Office1 | 200 | 0.6616 | 0.6527 | 1.119 |
| Office2 | 100 | 0.6129 | 0.6053 | 1.101 |

上述为已有物理部署结果。归档器件占用为50069 LUT、48464寄存器、127.5 BRAM、178 DSP；WNS +0.015 ns、WHS +0.019 ns，实际时钟约94.118 MHz。三轮连续活动窗口平均功率约2.098～2.120 W，单图积分能耗约2.349～2.409 J；10点采样功率中位数约1.974～2.043 W，与前者统计方法不同。

- [本次完整电脑端复现记录](results/reproduction_20261106/README.md)：四场景完整QAT、1725幅评估、RTL、Vivado和17项电脑端检查均已实际通过；源码重建后按第4节运行板端步骤。
- [本次仓库整理及全新目录运行记录](results/repository_repair_20261106/README.md)：独立Python环境、四场景模型短流程、原始NeRF2计时、RTL仿真及21项软件包检查。
- [原NoPS1完整中间结果](results/cold_reproduction_20261013/README.md)：包含结构适配模型、QAT模型、定标、量化导出、全部PC/整数图、运行时资产、RTL输出及报告，单独归档，不作为新构建输入。
- [功率原始输入与12轮对齐](results/power_alignment/captures.json)：配套未剪辑录像通过第5.2节下载。
- [本次修改与AMD逐项对照](docs/submission_checklist.md)。
- [结果总索引](results/README.md)：逐图数据、实板自检、50次稳定性、功率和Vivado/RTL结果。
- [正式报告](report/design_report.pdf)、[当前路径及术语补充](report/repository_update.md)、[真实LLM协作记录](report/llm_collaboration.md)。
- [固定Release](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-complete-20261104)：完整已完成board/PC部署包、1725幅实际FPGA图、原始功率记录、179.8秒演示视频。另有[12轮功率未剪辑原片](https://github.com/bullet-del/nerf2-pynq-z2/releases/tag/stage39-repro-results-20261106)。Release归档不参与本次冷启动计算。

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

每项均列出适用范围、运行命令、失效条件、失败经验和已完成效果。根目录可运行离线正反例：

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
