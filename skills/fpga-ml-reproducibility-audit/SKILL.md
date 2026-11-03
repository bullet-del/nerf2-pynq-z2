---
name: fpga-ml-reproducibility-audit
description: Audit FPGA-accelerated ML repositories with a declarative contract covering source completeness, input lineage, software and RTL gates, physical-board evidence, and power-boundary claims. Use for reproducibility reviews across FPGA/ML projects; do not use as a substitute for running a project's hardware experiments.
---

# FPGA/ML reproducibility audit

Look for a project contract named `reproducibility-contract.json`. If the user
provides another path, use that path. Read
[references/contract-schema.md](references/contract-schema.md) when creating or
changing a contract.

Choose the narrowest audit mode that answers the request:

- `source`: repository structure, required source, forbidden generated files,
  locked configuration values, and documented commands;
- `software`: source plus generated model, evaluation, RTL, Vivado, and package
  reports;
- `physical`: software plus actual board and measurement evidence.

Run the deterministic auditor before interpreting individual artifacts:

```text
python scripts/audit_fpga_repro.py --root <project> --contract <contract> --mode <source|software|physical> --output <report.json>
```

Then inspect failures and pending items in their evidence class. Preserve these
rules:

1. Treat original checkpoints, datasets, splits, and configs as inputs. Verify
   their recorded identities before accepting generated results.
2. Keep software reference, RTL simulation, physical FPGA execution, and
   external power measurement as separate evidence classes.
3. Report a missing required result as `PENDING`. Do not infer a hardware pass
   from software or simulation.
4. Do not weaken thresholds, rewrite result JSON, or discard failed samples to
   make a gate pass.
5. Check complete held-out coverage and train/test separation when the contract
   claims formal accuracy. A smoke run establishes connectivity only.
6. Tie physical results to bitstream, runtime model, and test-catalog identities
   when those identities exist.
7. State the measured power boundary. Compare energy ratios strictly only when
   task, precision, device boundary, supply boundary, and sampling treatment are
   aligned.

Read [references/evidence-rules.md](references/evidence-rules.md) before drafting
competition, paper, or release claims. Lead the final audit with the overall
status, list concrete failures before pending work, and cite artifact paths.
