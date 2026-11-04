# Stage39 competition Skill submission

本目录按 2026 AMD 赛题指南 3.3.5.2 组织，包含两个相互独立的技能包。

| 技能包 | 类型 | 与本题绑定 | 换题目 | 换 PYNQ 板卡 | 主要输出 |
|---|---|---:|---:|---:|---|
| `nerf2-checkpoint-fpga-export` | 案例模板与校验脚本 | 是 | 否 | 否 | NeRF2 FPGA 运行时模型与描述性报告 |
| `pynq-overlay-validation` | 通用 PYNQ 工程骨架与校验工具 | 否 | 是 | 是 | Overlay 身份、接口、黄金参考和性能证据报告 |

每个子目录都有独立 `README.md`，写明适用范围、使用方法、失效条件、已验证效果以及
从哪些失败和观察中提炼。提交时应保留整个 `skill/` 目录层级。

第一个 Skill 展示本作品中从 checkpoint 到 FPGA 模型的完整沉淀，满足技能包的一般
评价项。第二个 Skill 不包含 NeRF2、四场景、固定图像尺寸或 PYNQ-Z2 专用参数，单独
对应指南中的通用 PYNQ Skill 加分项。

每个技能包都包含 `VERIFICATION.md` 和机器可读的
`verification/verified_effects.json`。前者说明测试方法、结果和不能外推的结论；
后者便于复核脚本或评审工具读取。根目录 `SUBMISSION_MANIFEST.json` 给出提交内容、
定位和当前验证等级。
