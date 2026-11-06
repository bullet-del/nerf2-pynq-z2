# November 6 cold-reconstruction results

These results were newly generated from the four original input checkpoints
and raw data in the numbered repository. No archived quantized model, map or
prebuilt bitstream was used. A machine restart interrupted the first attempt;
saved completed structural-adaptation stages were retained and the missing
stage resumed. Full QAT/evaluation, RTL, Tcl implementation, assembly and the
17-check host gate passed. See validation.json and the raw reports here.

This is host reconstruction evidence. The new package still needs physical
PYNQ execution, full board tests and new power measurements. It is not the
October physical FULL_PASS and does not relabel old experiments.
