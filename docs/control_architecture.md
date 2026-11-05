# Why this project does not contain a Vitis application

Stage39 runs PYNQ Linux on the Zynq processing system. Python controls the
programmable logic through PYNQ `Overlay`, AXI DMA, and AXI GPIO APIs. Vivado Tcl
builds the RTL and block design into BIT/HWH files; board-side Python loads
scene models, performs DMA transfers, serves HTTP requests, and exports
evidence.

The current system has no bare-metal or FreeRTOS C/C++ application and therefore
does not need a Vitis platform or application project. Adding an unused Vitis
placeholder would misdescribe the executable architecture.

```text
RTL + Vivado Tcl
        |
        v
BIT + HWH
        |
        v
PYNQ Linux + Overlay/DMA/GPIO Python
        |
        v
Stage39 board service + PC application
```

A Vitis project should be added only if the processing-system controller is
later migrated to bare metal or an RTOS.
