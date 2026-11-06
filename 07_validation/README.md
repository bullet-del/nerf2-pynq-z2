# Validation entry points

Run `python scripts/check_repository.py` from the repository root. It checks
the directory map, source syntax, documentation links, original checkpoints,
bundled dataset counts and archived 1725-map coverage. This uses the standard
library and does not need PyTorch, Vivado or a board.

Run `python -m unittest discover -s tests -v` for repository integration checks.
The generic PYNQ Skill has its own offline positive and negative controls:
`python -m unittest discover -s skill/pynq-overlay-validation/tests -v`.

Current reconstruction outputs go into `work/` and `outputs/`. Historical
software, RTL, implementation, board and power results remain under `results/`.
Reading an archived PASS does not certify a newly generated package.
