# 验证记录与证据边界

## 离线自动化测试

`tests/test_offline.py` 使用带 XML 命名空间的最小 HWH、伪 BIT、NumPy 适配器和黄金
向量，完整执行 HWH 解析、验证目录生成、三轮 mock 比较和错误黄金输出反例：

- 正例必须产生 `PASS_MOCK`，且 `physical_board_evidence=false`；
- 反例必须以非零退出码结束并写出 `FAIL`；
- HWH 解析必须识别 `dma` 和地址 `0x40400000`；
- mock 状态永远不能写成 `PASS_BOARD`。

运行命令：

```text
python -m unittest discover -s skill/pynq-overlay-validation/tests -v
```

## 本队真实工程交叉验证

使用 Stage39 实际生成的 `n2_stage38_raw.bit/.hwh` 完成离线盘点和目录生成：

- 设计名：`n2_stage37_universal`；器件：`xc7z020` 对应的 HWH 设备码 `7z020`；
- Vivado：`2024.1`；HWH 模块数：7；
- 正确识别 `dma`、`gpio` 和纯流自定义模块 `n2_wrapper`；
- DMA 寄存器基址：`0x40400000`；GPIO 寄存器基址：`0x41200000`；
- BIT SHA-256：`0c3966e4f2e0ee7f367ca2de6855d0dd3acdb90de7ae993493fdeaa056b3ecf7`；
- HWH SHA-256：`95799c8626ea8d3971ceb5ff0b31901d3c65a225c85aec383bfdd643fee52bff`；
- 使用生成目录和独立 NumPy 适配器执行 5 轮 mock，最大绝对误差为 0，状态为
  `PASS_MOCK`。

上述真实工程交叉验证在 2026-10-19 使用最终脚本重新执行；汇总器报告 1 份报告、
`PASS_MOCK=1`、`PASS_BOARD=0`、失败数为 0，并保持实物证据标志为 false。

## 尚未由新通用脚本证明的内容

当前没有把本次新脚本复制到第二块物理 PYNQ 板并取得 `PASS_BOARD`。因此提交材料只
声称已经完成离线解析、生成、mock 正反例和本队真实 BIT/HWH 交叉验证。陌生队伍必须
在自己的目标板运行并保存 `PASS_BOARD`，才能证明它们的 Overlay 真实执行通过。

机器可读摘要位于 `verification/verified_effects.json`。
