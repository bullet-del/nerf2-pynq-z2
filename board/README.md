# PYNQ source and physical results

source contains Python drivers, services and integer reference. notebook is the
review notebook; photos are actual board/system images. evidence preserves
four-scene full-test, selftest, 50-run timing and physical finalization records.
Directory names are lower-case; original scene IDs inside JSON remain intact.

Use the complete board_package.zip and pc_app_package.zip Release downloads in
the root README for the archived system. Run the original notebook inside its
original extracted deployment directory. New builds use build/pipeline and
write matched packages under ignored outputs.

Archived FULL_PASS belongs to the matched deployment. HDMI loads a display
Overlay, and the next inference restores the inference Overlay. See
[setup and recovery](../docs/hardware_setup.md).
