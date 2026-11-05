# System interfaces

PC and PYNQ communicate over wired Ethernet using the scene-aware HTTP service.
PC sends a scene identifier, a selected test input or legal Tx coordinate and
optional ARM/HDMI settings. The board returns the actual integer-derived map and
timing/status. The source is in src/host/pc_app and board/source/jupyter.

ARM controls DMA and GPIO through PS GP0. DMA uses HP0 to access DDR and exchanges
64-bit AXI4-Stream records with the custom PL core. Runtime model writes are
committed before inference; output values are raw uint32 Q15 amplitudes. The
logical weight word is 1024 bits, transferred in sixteen 64-bit segments.

DMA input is flushed before transmission, the receive channel starts first,
and completed output is invalidated before CPU reads. A serialized hardware
queue prevents concurrent requests from changing the same Overlay/model state.
HDMI uses BaseOverlay; the next query restores the inference Overlay and caches.
The report and board drivers contain the complete packet and state diagrams.
