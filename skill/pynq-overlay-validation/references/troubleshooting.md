# PYNQ Overlay 验证踩坑清单

| 现象 | 常见触发条件 | 先检查什么 | 处理方法 |
|---|---|---|---|
| `Overlay` 加载后找不到 IP | BIT 与 HWH 不配套、IP 名称变化、层级路径变化 | 比较两个文件哈希并查看 HWH inventory | 重新导出同一次实现的 BIT/HWH，更新契约正则 |
| DMA `wait()` 一直不返回 | 未启动接收、无 TLAST、长度错误、时钟或复位异常 | 查看发送/接收顺序、ILA、DMA 状态寄存器 | 先接收后发送；修复 TLAST；核对字节数、时钟和复位 |
| 输出像上一次运行 | CPU 缓存未失效 | 检查适配器是否调用 `invalidate()` | 接收完成后 invalidate，再复制为普通 NumPy 数组 |
| FPGA 读到旧输入 | 输入缓冲区未 flush | 检查写入后、DMA 启动前的调用顺序 | 输入复制完成后 flush，再启动 DMA |
| 输出长度或 dtype 错误 | 软件按元素计数，硬件按字节或流宽计数 | 对比数组 nbytes、AXIS TDATA 宽度和协议 | 在适配器中显式固定 shape/dtype，必要时打包/解包 |
| 浮点误差导致失败 | 用逐位规则比较浮点输出 | 查看 contract comparison | 对浮点使用经论证的 rtol/atol；整数和协议字段用 exact |
| mock 通过、板端失败 | mock 没有真实寄存器、DMA、缓存和中断 | 查看报告是否仅为 PASS_MOCK | 在目标板重新生成 PASS_BOARD，不引用 mock 作为物理证据 |
| HWH 解析有 IP 但 live Overlay 缺失 | 模块没有软件可见驱动或 PYNQ 解析规则不同 | 对比 HWH required IP 与 `overlay.ip_dict` | 只把可寻址或有驱动的 IP 设为 live 必需项 |
| `allocate` 失败 | CMA 不足、缓冲区过大、旧缓冲区未释放 | 查看 `/proc/meminfo`、缓冲区大小和 close() | 缩小批次，复用缓冲区，确保 `freebuffer()` |
| 首轮延迟特别高 | Overlay 下载、缓存、驱动初始化混入计时 | 查看 warmup 与 overlay_load_seconds | 把加载单独记录；使用 warmup；只统计稳定重复轮次 |
| Notebook 关闭后结果丢失 | 结果只存在变量或页面 | 检查 reports 目录 | 每次运行写 JSON、Markdown 和 SHA256SUMS，及时下载 |
| 换板卡无法加载 | 器件、PYNQ 版本、时钟或板级接口不兼容 | 查看 HWH DEVICE 和目标 PYNQ 支持 | 为目标器件重新实现 BIT/HWH；框架脚本和契约格式可复用 |

排查顺序固定为：文件身份、HWH 接口、Overlay IP 字典、寄存器、缓冲区、DMA 协议、
黄金参考、性能统计。不要在前一层未确认时直接修改模型算法。
