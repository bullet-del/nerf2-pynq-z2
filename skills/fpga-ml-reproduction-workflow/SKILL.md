---
name: fpga-ml-reproduction-workflow
description: Execute a contract-defined FPGA/ML reproduction pipeline from declared inputs to a machine-readable and human-readable report with one command. Use when a project needs an ordered, restartable clean-room workflow rather than a checklist alone.
---

# FPGA/ML reproduction workflow

Use this Skill when the user wants one command to turn declared source inputs
into generated FPGA/ML artifacts and an acceptance report.

The project must provide a `reproducibility-contract.json` file with
`ordered_commands` and `workflow_pipelines`. Read
[references/workflow-schema.md](references/workflow-schema.md) before creating
or changing those fields.

Run the workflow from the project root:

```text
python skills/fpga-ml-reproduction-workflow/scripts/run_fpga_ml_workflow.py --root . --contract reproducibility-contract.json --pipeline software --report-dir work/reports/one-command
```

The runner performs the following sequence:

1. Load the selected pipeline and resolve its ordered step identifiers.
2. Expand only the documented `{python}`, `{root}`, and `{contract}` tokens.
3. Execute every command as an argument list without a command shell.
4. Stream output to the terminal and save one log per step.
5. Stop immediately after the first failed step.
6. Run the reusable reproducibility audit at the pipeline's evidence level.
7. Write `workflow_report.json` and `workflow_report.md` with commands, timing,
   return codes, log paths, and final audit status.

Use `--dry-run` first when reviewing an unfamiliar contract. Dry-run validates
the step graph and writes a plan without executing project commands.

Preserve these rules:

1. Never use `shell=True` or execute a free-form command string.
2. Never continue past a failed generation or verification step.
3. Keep smoke and full pipelines separate. A smoke run cannot produce a formal
   accuracy, timing, physical-board, or power claim.
4. Treat a physical pipeline as complete only when the contract includes the
   real board and measurement finalizer and its evidence audit passes.
5. Do not edit thresholds or generated evidence to make a failed run pass.
6. Report the first failed step, the saved log, and the remaining unexecuted
   steps so another person can resume from evidence rather than guesswork.

