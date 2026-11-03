# Competition engineering review

## Four requested engineering differentiators

1. **LLM collaboration record**: intentionally outside the current deliverable.
   Add it only after removing private paths, credentials, irrelevant discussion,
   and unsupported claims.
2. **Reusable Skill**: complete. The repository contains a project-specific
   judge-demo Skill and the independent `fpga-ml-reproducibility-audit` Skill.
3. **English standardized repository**: complete as a local release candidate.
   It has an English entry page, architecture and reproduction documents,
   contribution/security/license/citation metadata, ignored generated content,
   source hashes, CI, and a declarative audit contract. The application UI
   remains Chinese for the target competition audience; repository-facing
   documentation and metadata are English.
4. **Clean environment reproduction**: implemented. The source starts from the
   four original checkpoints and fixed data/config/splits, regenerates BIT/HWH
   from Tcl, recreates the models and all 1,725 software results, runs RTL gates,
   and assembles new PC/PYNQ packages. A current run must still finish all formal
   and physical gates before its results are described as independently
   reproduced.

## Suggested judge demonstration

1. Show a clean checkout without `work/full`, generated packages, BIT/HWH,
   checkpoints, or datasets.
2. Run the source-manifest verifier and generic Skill source audit.
3. Show the machine-local configuration and preflight PASS report.
4. Show that the Vivado Tcl creates BIT/HWH and passes timing and records the implemented clock (95 MHz requested; archived HWH about 94.118 MHz).
5. Show the four software gate JSON files and the 1,725-map coverage total.
6. Show the RTL switch-and-return transcript and runtime capacity records.
7. On the board, show the package identity, a blind query, an arbitrary Tx,
   ARM equality, HDMI, nonce, negative control, and evidence export.
8. Present ARM/FPGA same-boundary energy results and label the PC GPU boundary
   separately.

This sequence makes each claim traceable to an artifact and prevents a polished
interface from being mistaken for physical or reproducibility evidence.
