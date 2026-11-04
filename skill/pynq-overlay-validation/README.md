# 通用 PYNQ Overlay 加载 校验与证据报告技能包

## 一 适用范围

本技能包面向采用 PYNQ Python API 管理 FPGA Overlay 的项目，不依赖本队的 NeRF2
模型、90×360 数据、特定寄存器地址或 PYNQ-Z2。只要目标板运行 PYNQ，并且设计提供
配套的 `.bit` 和 `.hwh` 文件，就可以用于：

- 图像、音频、通信、控制、AI 推理和自定义流处理 Overlay；
- Zynq-7000、Zynq UltraScale+ MPSoC 和其他受目标 PYNQ 版本支持的板卡；
- RTL、Vitis HLS 或二者组合生成的 AXI-Lite、AXI-Stream 和 AXI DMA 设计；
- 赛前离线检查、板端冒烟验证、黄金参考比对、延迟统计和证据归档；
- 从 HWH 自动生成接口契约与主机侧适配器骨架。

项目相关的寄存器写入、输入输出形状和 DMA 事务放在独立 `adapter.py` 中。框架本身
负责 Overlay 身份、接口存在性、寄存器读值、重复运行、超时、黄金参考比较、延迟统计
和报告。这种边界使陌生队伍可以保留框架，只替换一个小型适配器。

电脑端需要 Python 3.8 或更高版本及 NumPy；板端需要目标器件受当前 PYNQ 镜像支持，
并能导入 `pynq.Overlay` 与 `pynq.allocate`。脚本只使用 Python 标准库和 NumPy，
无需在普通电脑上安装 Vivado 或 PYNQ 才能完成 HWH 盘点、生成、dry-run 和 mock 测试。

## 二 目录内容

| 文件 | 功能 |
|---|---|
| `scripts/inspect_hwh.py` | 在普通电脑上解析 HWH，列出 IP、总线接口和地址范围 |
| `scripts/generate_validation_bundle.py` | 由 BIT/HWH 自动生成契约、Overlay 副本和适配器骨架 |
| `scripts/run_board_validation.py` | dry-run、PC mock 或真实 PYNQ 板端验证并生成报告 |
| `scripts/summarize_validation_reports.py` | 聚合多轮报告，统计板端通过数和延迟 |
| `examples/dma_loopback_adapter.py` | 显式 flush/invalidate 的通用 AXI DMA 适配器示例 |
| `examples/numpy_double_adapter.py` | 不需要板卡的 mock 示例 |
| `references/troubleshooting.md` | 常见出错条件、判断证据与排查顺序 |

## 三 标准使用流程

### 第一步 在电脑上盘点 HWH

```text
python skill/pynq-overlay-validation/scripts/inspect_hwh.py --hwh path/to/design.hwh --bit path/to/design.bit --output work/pynq_validation/hwh_inventory.json
```

检查生成的 JSON 和 Markdown，确认器件、IP 名称、DMA 候选和地址范围符合 Vivado
设计。该步骤不需要安装 PYNQ，也不需要连接开发板。

### 第二步 自动生成验证目录

```text
python skill/pynq-overlay-validation/scripts/generate_validation_bundle.py --hwh path/to/design.hwh --bit path/to/design.bit --project-name my_overlay --output-dir work/pynq_validation/my_overlay
```

输出目录包含同名 BIT/HWH、带 SHA-256 的 `overlay_validation.json`、HWH 盘点、
`adapter.py` 骨架和向量目录。生成器只把 HWH 中的 DMA 和可寻址 IP 加入默认必需项，
不会把互连或没有软件访问入口的纯流模块误当成寄存器 IP。

### 第三步 准备黄金向量并编辑适配器

把输入保存为 `vectors/input.npz`，把软件黄金输出保存为
`vectors/golden.npz`。`adapter.py` 必须实现：

```text
prepare(overlay, allocate, contract, inputs)
run_once(context, inputs) -> {golden_key: numpy_array}
close(context)
```

对于普通 AXI DMA 流水线，可以从 `examples/dma_loopback_adapter.py` 复制。该示例在
发送前调用输入缓冲区 `flush()`，接收后调用输出缓冲区 `invalidate()`，并先启动接收
通道再启动发送通道。

### 第四步 在电脑上做 dry-run

```text
python skill/pynq-overlay-validation/scripts/run_board_validation.py --contract work/pynq_validation/my_overlay/overlay_validation.json --output work/pynq_validation/my_overlay/reports/dry --dry-run
```

`PLANNED` 表示 BIT/HWH 哈希、HWH 接口、适配器语法和向量均通过预检。
`INCOMPLETE` 表示生成骨架成功但输入或黄金 NPZ 尚未补齐。

### 第五步 在电脑上验证适配器和比较规则

适配器不依赖真实 IP 时，可执行：

```text
python skill/pynq-overlay-validation/scripts/run_board_validation.py --contract overlay_validation.json --output reports/mock --mock
```

`PASS_MOCK` 只证明 Python 适配器、重复运行和黄金参考比较正确，不能作为 FPGA
实物证据。

### 第六步 在 PYNQ 板上执行

把生成目录和本 Skill 的 `scripts` 复制到板上，在 PYNQ Python 环境中运行：

```text
python scripts/run_board_validation.py --contract overlay_validation.json --output reports/board_R1
```

只有 `PASS_BOARD` 表示 BIT/HWH 身份一致、真实 Overlay 已加载、所需 IP 存在、适配器
执行完成且所有黄金输出通过。功率、HDMI、射频或其他外部接口仍须单独测量。

### 第七步 聚合多轮结果

```text
python scripts/summarize_validation_reports.py --reports reports/board_R1/validation_report.json reports/board_R2/validation_report.json reports/board_R3/validation_report.json --output reports/summary.json
```

## 四 接口契约说明

`overlay_validation.json` 包含：

- BIT/HWH 相对路径及 SHA-256，防止加载错配文件；
- 必需 IP 的名称正则、类型正则和最小匹配数量；
- 可选的 `register_checks`，用于检查寄存器或字段的上电值；
- 适配器路径和项目自定义参数；
- 输入与黄金 NPZ、预热次数、重复次数和事务超时；
- 精确比较或 `rtol/atol` 浮点容差。

契约使用项目内相对路径，整个生成目录可以复制到另一台电脑或另一块 PYNQ 板。

## 五 失效条件

下列情况需要先修改适配器、契约或底层硬件，不能直接套用：

- 目标系统不是 PYNQ，或 PYNQ 版本不支持目标器件和 Overlay 格式；
- 缺少 HWH，或 BIT/HWH 不是同一次实现产物；
- 驱动需要 XRT、AI Engine、PCIe 或厂商专用运行时，而不是 PYNQ `Overlay`；
- 数据从摄像头、射频、以太网等连续外设直接进入 PL，无法用有限 NPZ 事务描述；
- AXI DMA 使用散布聚集、多通道或自定义描述符，而示例适配器只实现简单模式；
- 事务没有 TLAST、输出长度未知或硬件可能永久阻塞，需在 RTL/HLS 和适配器中补协议；
- 黄金参考本身未经确认，或输入输出具有随机性却没有固定随机种子和容差；
- 需要资源利用率或时序收敛结论，但没有提供 Vivado 实现报告；
- 需要功耗结论，但没有统一测量边界和外接仪器记录。

## 六 已验证效果

本技能包完成了三层验证：

1. 使用本队 Stage39 的真实 `n2_stage38_raw.hwh` 进行离线解析，正确识别 Zynq-7020、
   AXI DMA、GPIO、自定义流模块和 DMA/GPIO 地址范围；
2. 使用真实 Stage39 BIT/HWH 自动生成可搬移验证目录，生成的文件哈希与原始文件一致；
3. 使用独立 NumPy 适配器在 PC mock 模式完成多轮运行、黄金参考比较和延迟报告，状态
   为 `PASS_MOCK`；错误黄金输出会得到 `FAIL`，不会被报告为板端通过。

本队作品此前已在 PYNQ-Z2 上完成 Overlay 加载、DMA 传输、缓存处理和 FPGA 原始输出
证据。这些开发观察用于提炼本技能包，但新脚本在另一支队伍的板卡上仍应重新执行
`PASS_BOARD`，不能沿用本队实物结论。

可独立审阅的离线测试、真实 Stage39 HWH/BIT 盘点和证据边界见 `VERIFICATION.md`
与 `verification/verified_effects.json`。

## 七 从失败和观察中提炼的规则

| 失败或观察 | 固化到 Skill 的规则 |
|---|---|
| BIT 与 HWH 混用时，Python 能找到文件但 IP 字典和实际硬件可能不一致 | 契约同时锁定两个文件的 SHA-256，加载前强制比较 |
| HWH 中存在纯流自定义模块，但它未必出现在 `Overlay.ip_dict` | 自动契约只要求 DMA 和具有寄存器地址范围的 IP |
| DMA 先发送、后接收会在有背压的设计中卡住 | DMA 示例先启动接收，再启动发送，并设置事务超时 |
| CPU 缓存未同步时会出现旧输入或旧输出 | 发送前 flush，接收后 invalidate，返回普通 NumPy 副本 |
| 只跑一次无法发现首轮配置开销和偶发错误 | 分离 warmup 与 repeats，并对每一轮都做黄金比较 |
| 浮点设计和整数设计不能共用同一种判定 | 契约显式选择 exact 或 rtol/atol |
| 软件 mock 容易被误写成 FPGA 结果 | 只有真实板产生 `PASS_BOARD`，mock 固定为 `PASS_MOCK` |
| DMA hang 只留下一个没有上下文的 Notebook 单元格 | 每次运行独立输出 JSON、Markdown、哈希、失败阶段和超时信息 |

完整排查表见 `references/troubleshooting.md`。

## 八 在其他题目中的直接用途

- 图像滤波：输入图像 NPZ，黄金输出为软件卷积结果；
- FFT 或通信基带：输入 IQ 序列，黄金输出为频谱或解调结果；
- 电机控制：输入采样序列，黄金输出为控制量或状态更新；
- 神经网络推理：输入张量，黄金输出为 logits 或特征图；
- 数据搬运核：直接使用 DMA loopback 适配器检查吞吐和缓存一致性；
- 自定义寄存器 IP：不使用 DMA，只在适配器中读写 `register_map` 并返回结果数组。

这些用法只需替换 `adapter.py`、输入 NPZ 和黄金 NPZ，不需要修改框架脚本。
