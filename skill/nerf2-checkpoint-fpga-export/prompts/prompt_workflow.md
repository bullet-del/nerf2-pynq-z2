# 提示词工作流

## 输入审查

```text
请使用本目录的 NeRF2 checkpoint FPGA export Skill。先执行 dry-run，检查 checkpoint
张量形状、config、数据划分、图像尺寸、Tx 表和 gateway 配置。不要开始正式训练。
读取 deployment_report.json，逐项解释所有不满足项；如果全部满足，只给出正式命令，
不要修改固定的 3000 步结构适配、8192 条定标射线、1800 步 QAT 和完整测试集设置。
```

## 正式转换

```text
请使用本目录的 Skill 对这个新场景执行正式默认转换。输出目录必须是新的空目录。
不得依据测试集结果修改超参数，不设置 SSIM 通过门槛。完成后检查九个步骤、模型哈希、
weight_words、current_bitstream_compatible 和 SHA256SUMS，并区分软件转换证据与实物板证据。
```

## 结果审查

```text
请审查 deployment_report.json、full_evaluation.json 和 SHA256SUMS.txt。报告实际 SSIM、
PSNR、MSE、MAE、角度误差和权重字数量。若不是 READY_FOR_CURRENT_BITSTREAM，禁止写成
已部署；若缺少板端新证据，只能写成软件转换完成。
```
