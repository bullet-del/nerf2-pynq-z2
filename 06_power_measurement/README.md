# PC GPU与FPGA功率入口

所有电脑命令都在仓库根目录、nerf2环境执行。完整说明见[根README](../README.md)第5节与[采集及对齐步骤](power_protocol.md)。不采集ARM功率，ARM随机射线复核继续保留。

## PC GPU

```bat
python scripts/check_pc_power_inputs.py
python 06_power_measurement/run_pc_all_scenes.py --partial-maps 1 --repeats 1 --output-root work/power_pc_smoke
python 06_power_measurement/run_pc_all_scenes.py
python 06_power_measurement/summarize_pc_full_test.py --results-root 06_power_measurement/results/pc_gpu_full_test_authoritative
```

完整NeRF2原始模型、全部测试图、三轮，功率域为NVIDIA GPU遥测。第一轮短流程只检查入口。新记录原生使用逐图窗口；旧记录可先运行realign_pc_gpu_metrics.py进行无重测的边界迁移。

## 已有FPGA原始实验

```bat
python scripts/download_artifacts.py --asset recordings
python 06_power_measurement/collect_fpga_power.py --manifest results/power_alignment/captures.json --output-root work/archived_fpga_power_recalculated
```

## 新采集

[板端12轮命令](board_power_commands.md)与[完整功率操作步骤](power_protocol.md)给出供电、同步、FNB58录制、36个文件命名、视频事件填写和最终命令。每套结果使用自己的JSON、CFN、录像与模型位流，不交叉混用。
