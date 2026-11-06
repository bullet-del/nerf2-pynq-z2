# 连续演示与最终收口

所有电脑命令均在仓库根目录、nerf2环境运行。先完成根README第4节，四场景App全测试集结果保留在PC包里。

Notebook第5单元导出的ZIP下载为work/board_downloads/Stage39_physical_evidence.zip。根README第5节生成的80行采样文件为work/fpga_power/processed/stage39_board_input_power.csv。

```bat
python 06_power_measurement/stage39_final_steps/stage39_continuous_rehearsal.py --scene Office2 --board-url http://192.168.2.99:8311 --output-root work/live
```

把实际演示原片保存为work/live_camera.mp4，再运行：

```bat
python 06_power_measurement/stage39_final_steps/finalize_stage39_verified.py --pc-app-root outputs/N2-NeRF2-Stage39-PC-App --physical-export work/board_downloads/Stage39_physical_evidence.zip --power-csv work/fpga_power/processed/stage39_board_input_power.csv --rehearsal-root work/live --video-file work/live_camera.mp4 --output work/final/stage39_final_acceptance.json
```

也可使用--rehearsal-manifest显式指定脚本打印的manifest.json路径。没有原片但已亲眼观察两次HDMI输出时，可用--user-observed-hdmi代替--video-file。最终状态必须为FULL_PASS。
