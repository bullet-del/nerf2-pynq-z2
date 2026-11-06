# 本项目为什么没有 Vitis 工程

Stage39 的 Zynq PS 端运行 PYNQ Linux 和 Python，使用 PYNQ 的 Overlay、AXI DMA 和 AXI GPIO 驱动 PL。PL 硬件由 Vivado Tcl 从 RTL 和 Block Design 生成 BIT/HWH；板端控制、场景运行时模型装载、DMA 传输、HTTP 服务和证据导出都由 05_pynq_board\template\jupyter 及 software 下的 Python 完成。

因此本作品没有裸机或 FreeRTOS C/C++ 应用，也不需要从 XSA 建立 Vitis Platform/Application。为了形式完整而新建一个不参与执行的 Vitis 工程会误导复现者，也无法增加可复现性。

复现链路是：

    RTL + Vivado Tcl
        ↓
    BIT + HWH
        ↓
    PYNQ Overlay / DMA / GPIO Python
        ↓
    Stage39 板端服务和 PC App

如果未来把 PS 控制程序改为裸机或 FreeRTOS，再新增 Vitis 工程；当前 Stage39 不应加入无效 Vitis 占位工程。

