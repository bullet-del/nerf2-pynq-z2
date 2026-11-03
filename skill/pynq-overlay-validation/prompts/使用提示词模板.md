# 通用 PYNQ Skill 提示词

## 生成验证骨架

```text
请使用 pynq-overlay-validation Skill。先解析我提供的 BIT/HWH，列出器件、DMA、
可寻址 IP 和地址范围，再生成验证 bundle。不要猜寄存器含义。需要项目知识的部分只写入
adapter.py 和 overlay_validation.json，不得修改通用框架。
```

## 定位 DMA 卡死

```text
请按本 Skill 的排查顺序分析 DMA 卡死：先核对 BIT/HWH 哈希与 live ip_dict，再检查复位、
时钟、接收先于发送、传输字节数、TLAST、flush/invalidate 和 CMA。每一步列出可观察证据，
在前一层未确认前不要修改算法。
```

## 审查验证报告

```text
请读取 validation_report.json。区分 PLANNED、PASS_MOCK 和 PASS_BOARD；只把 PASS_BOARD
写成实物 Overlay 证据。汇总黄金比较、重复次数、median/p95 延迟、BIT/HWH 哈希和失败点。
不得从该报告推导未测量的功耗或外部接口结论。
```
