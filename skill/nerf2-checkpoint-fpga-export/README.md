# NeRF2 checkpoint 到 FPGA 运行时模型技能包

## 一 适用范围

本技能包用于把一个已经训练完成的 NeRF2 场景 checkpoint 转换为 Stage39
通用位流可读取的运行时模型，并生成可审计的转换报告。它适用于满足下列全部条件的
新场景：

- 网络结构固定为 `D=8`、`W=256`、`skips=[4]`；
- 点坐标、观察方向和 Tx 坐标均由三维输入形成 63 维位置编码；
- 输出空间谱尺寸为 90×360，单射线使用 64 个采样点；
- checkpoint 中包含 `nerf2_network_state_dict`；
- 数据集具有 `spectrum`、`tx_pos.csv`、`gateway_info.yml`、
  `train_index.txt` 和 `test_index.txt`。

换成同结构的新室内场景、室外场景或不同 Tx 轨迹时可以直接使用。它也可作为
“浮点 checkpoint 到受约束定点运行时镜像”的案例模板，但其中的两处结构掩码、
13 层执行图和 Q15 渲染器属于本作品，因此本技能包不申报“换题目、换板卡仍可用”
的通用 PYNQ 加分项。

运行前应使用本仓库声明的 `nerf2` Python 环境，并安装 PyTorch、NumPy、Pillow、
PyYAML 及本仓库模型流水线所需依赖。checkpoint 采用 PyTorch pickle 容器，只能使用
本人训练或来源可信的文件；不要对未知下载文件直接运行本工具。

## 二 它完成的工作

正式运行按固定顺序完成：

1. 校验 checkpoint 张量形状、config、划分、图像尺寸、Tx 表和 gateway 配置；
2. 对衰减分支和信号分支施加当前 FPGA 图要求的结构零掩码；
3. 用训练集执行 3000 步受约束微调；
4. 只从训练集抽取 8192 条射线完成定标；
5. 执行 1800 步硬件精确 QAT；
6. 导出 INT8 权重、INT16 偏置、移位、饱和规则、稀疏索引和 Q15 LUT；
7. 在完整固定测试集上计算 SSIM、PSNR、MSE、MAE 和角度误差；
8. 打包 `stage37_runtime_model.npz` 并检查当前位流的 3072 个权重字容量；
9. 生成 JSON、Markdown 报告、逐图结果和 SHA-256 清单。

精度指标只作描述，不设 SSIM 门禁。参数缩短的试运行会标记为
`NONDEFAULT_RUN`，不会被误写成正式转换结果。

## 三 使用方法

在完整英文仓库根目录激活 `nerf2` 环境。第一次先执行输入检查：

```text
python skill/nerf2-checkpoint-fpga-export/scripts/run_nerf2_checkpoint_export.py --project-root . --scene NEW_SCENE --checkpoint PATH/TO/CHECKPOINT.tar --config PATH/TO/config.yml --dataset PATH/TO/DATASET --output-dir work/generic_exports/NEW_SCENE_DRYRUN --dry-run
```

检查 `deployment_report.json` 的 `status` 为 `PLANNED`、
`expected_shapes_pass` 为 `true`。正式运行时去掉 `--dry-run`，并使用另一个新的空
输出目录：

```text
python skill/nerf2-checkpoint-fpga-export/scripts/run_nerf2_checkpoint_export.py --project-root . --scene NEW_SCENE --checkpoint PATH/TO/CHECKPOINT.tar --config PATH/TO/config.yml --dataset PATH/TO/DATASET --output-dir work/generic_exports/NEW_SCENE
```

不要在正式命令中填写缩短步数参数。默认值就是 3000 步结构适配、8192 条训练射线
定标、1800 步 QAT 和完整测试集评估。

## 四 输出与判读

| 输出 | 用途 |
|---|---|
| `factorized/checkpoint.tar` | 施加结构约束后的浮点 checkpoint |
| `qat/checkpoint_qat.tar` | 硬件精确 QAT checkpoint |
| `runtime/model/stage37_runtime_model.npz` | FPGA 推理运行时镜像，不可用于继续训练 |
| `evaluation/full_evaluation.json` | 完整测试集逐图和汇总指标 |
| `deployment_report.json` | 机器可读的最终结论、容量和步骤状态 |
| `deployment_report.md` | 便于审阅和提交的报告 |
| `SHA256SUMS.txt` | 生成物完整性校验 |

`READY_FOR_CURRENT_BITSTREAM` 表示正式默认流程完成且模型不超过当前 PL 参数容量。
`RUNTIME_MODEL_CREATED_OVERSIZE` 表示软件导出完成，但当前位流不能加载。3072 个
1024 位权重字位于 FPGA PL 侧片上存储器，更换更大 SD 卡不会提高该容量。

## 五 失效条件

遇到下列任一情况时，本技能包不得直接声称可部署：

- 网络层数、宽度、跳连位置、位置编码或输出尺寸改变；
- 数据集缺少固定划分，或 train/test 有重复、交叉和缺图；
- config 的采样数、near/far 或网络参数不符合约束；
- 运行时模型超过 3072 个权重字；
- 使用了缩短步数或只评估部分测试图；
- 没有重新完成板端加载、逐位比较、延迟、HDMI 或功耗验证；
- 需要把新场景直接加入现有四场景 App，但尚未完成 App 目录和证据清单集成。
- checkpoint 来源不可信，无法接受 PyTorch 反序列化带来的代码执行风险。

## 六 已验证效果

- 使用原始 s23 checkpoint 并改用未预置的新场景名完成端到端冒烟验证；
- checkpoint 形状预检、结构约束、训练集定标、QAT、定点导出、完整打包和固定 Rx
  资产生成共九个阶段全部执行成功；
- 冒烟模型为 1456 个 1024 位权重字，容量检查正确报告 1456/3072；
- 短流程被标记为 `NONDEFAULT_RUN_RUNTIME_MODEL_CREATED_COMPATIBLE`，没有冒充正式精度；
- Skill 格式校验、源码清单校验、无 `.ps1`/`.pyc` 检查和 ZIP 完整性检查均通过。

这些结果验证了工具链和报告逻辑。正式新场景的最终精度、容量和板端行为仍必须由
该场景的默认完整运行与实物证据决定。

可独立审阅的测试边界和摘要见 `verification.md` 与
`verification/verified_effects.json`。

## 七 从失败和观察中提炼的规则

| 失败或观察 | 提炼出的可复用规则 |
|---|---|
| 原始信号首层真实形状为 256×382，而不是按原始三维 Tx 直接估算的 256×322 | 长任务前必须从 checkpoint 检查实际张量契约 |
| 定标程序拒绝少于 1024 条射线 | 把底层最小值提升为运行器前置校验，正式值固定为 8192 |
| Windows 中文路径的子进程输出曾出现编码错误 | 子进程统一使用 UTF-8，日志始终单独落盘 |
| 原四场景编排器写死场景名 | 新场景运行器直接调用通用底层阶段，不使用四场景白名单 |
| 运行过的非空目录容易混入旧文件 | 正式运行拒绝非空输出目录 |
| 3072 权重字曾被误解为 SD 卡限制 | 报告明确区分 PL 片上参数存储和 SD 卡文件存储 |
| Notebook 保存会改变自身哈希 | 可复现检查不应把会被运行环境自动改写的 Notebook 当作不可变运行输入 |

更详细的输入输出契约见 `references/input-and-output-contract.md`，大模型协作提示词见
`prompts/prompt_workflow.md`。
