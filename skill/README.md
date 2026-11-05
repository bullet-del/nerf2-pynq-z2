# Competition Skills and reusable workflows

本目录对应AMD指南3.3.5.2。前两项为竞赛Skill，后两项为辅助工作流。所有可复用内容统一放在skill，没有重复的另一个skills目录。

| 项目 | 定位 | 产物与验证边界 |
|---|---|---|
| [NeRF2模型导出](nerf2-checkpoint-fpga-export/README.md) | 本题案例模板与校验脚本 | 同结构模型、数据和划分转NPZ与描述性报告；非默认小规模端到端验证通过，新场景完整精度和实板需重新执行 |
| [通用PYNQ校验](pynq-overlay-validation/README.md) | 不绑定NeRF2或PYNQ-Z2的通用Skill | HWH、适配器与黄金参考转功能/时延报告；真实HWH盘点与mock正反例通过，不宣称已完成第二块板实测 |
| [工程源检查](fpga-ml-reproducibility-audit/SKILL.md) | 辅助工具 | 按工程契约检查必需源文件、配置和结果；缺失物理结果保留待执行 |
| [有序复现](fpga-ml-reproduction-workflow/SKILL.md) | 辅助工具 | 顺序执行、逐步日志与最终报告；失败即停，物理操作单独完成 |

两个竞赛Skill都有适用范围、使用方法、失效条件、已验证效果、提示词及失败观察。子目录README和verification.md说明具体命令及边界，保留整个目录层级即可，无须独立SHA清单。
