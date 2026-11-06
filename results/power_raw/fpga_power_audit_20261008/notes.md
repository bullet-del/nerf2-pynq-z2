# Stage39 FPGA 功耗与完成度审计（2026-10-08）

结论：四场景 × 3 次 FPGA 完整测试集的 JSON、FNB58 原始 CFN 和录屏均已找到，5175 次完整图推理的数值稳定且合理。s23 R1 录像未覆盖后来停止 FNB58 的动作，数值窗口仍完整，但证据闭环弱于其余 11 次。当前 Stage39 尚不是形式上的 FULL_PASS，因为已下载的 physical evidence ZIP 不含四场景 all-test 摘要，且尚未运行最终收口。

| 场景 | 图/轮 | 中位 s/图 | Idle W | Active W | 总 J/图 | 动态 J/图 | 时延CV | 功率CV | 能量CV |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| s23 | 1225 | 1.143991 | 1.744513 | 2.104819 | 2.408904 | 0.412824 | 0.129% | 0.026% | 0.120% |
| Meetingroom | 200 | 1.124870 | 1.743724 | 2.098470 | 2.361410 | 0.398352 | 0.069% | 0.119% | 0.066% |
| Office1 | 200 | 1.122671 | 1.744587 | 2.098009 | 2.354787 | 0.396002 | 0.240% | 0.049% | 0.277% |
| Office2 | 100 | 1.107694 | 1.752926 | 2.119973 | 2.348907 | 0.406204 | 0.777% | 0.114% | 0.885% |

功耗边界是整块 PYNQ-Z2 的 5 V J8 输入。PC 数据是 RTX 4050 驱动功率域，跨边界比值只能作描述性比较。ARM 数据未测，因此不能给出同一板卡、同一电源边界下的严格加速比或节能倍数。

已生成 `stage39_board_input_power_80_samples.csv`：每个场景从 R2 的 idle 和 active 原始区间各取 10 个均匀分布的真实样本，已经通过原 Stage39 `validate_power` 校验，可用于 Stage39 原有功率门禁。

下载的 physical evidence 已原样保存在 `E:\Codex\2026-10-04\e-codex-2026-09-05-fpga\outputs\stage39_power_comparison_kit\results\physical_evidence\Stage39_physical_evidence_20261008_INCOMPLETE_NO_ALL_TEST.zip`，SHA-256 `9481668878329f80b72021229142660c40940fe2d07906dfd0f067146e14958f`。其 manifest 的四个 `contains_full_test_summaries` 全为 false，必须从仍保留 all-test/result.json 的同一板端目录重新导出。
