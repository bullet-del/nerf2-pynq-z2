# 原NoPS1从原始模型开始运行的中间结果

本目录来自用户已运行完成的Stage39_Cold_Reproduction_Kit_NoPS1_20261012/work/full，保留7182个原文件内容，目录名和业务场景文件夹转为小写以满足仓库命名规则。未复制独立SHA清单；模型与科学记录内部的身份字段保留。

| 目录 | 内容 |
|---|---|
| full/factorized | 四场景结构适配checkpoint及元数据 |
| full/qat | 训练集定标、PTQ/QAT导出、QAT checkpoint、1725幅指标及门限结果 |
| full/maps | 全部PC FP32和整数参考原始NPY及逐图元数据 |
| full/runtime | 稀疏模型、权重、索引、描述符、LUT、固定Rx缓存及对应QAT模型 |
| full/rtl_sim | 仿真输入、输出和运行报告 |
| full/reports | 消融图表、统计表和电脑端收口记录 |
| vivado | 同一NoPS1运行生成的BIT、HWH、资源和布局布线时序报告，保留原文件内容 |

这些是已经产生的输出，供查看、分析和下载；不能放回新work目录冒充新运行。里面的历史日志和元数据可能保留原电脑路径及原场景大小写，它们是当次运行记录。新运行只按根README使用源代码和data/inputs中的四个原始模型。
