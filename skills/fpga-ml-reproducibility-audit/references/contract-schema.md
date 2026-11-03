# Contract schema

The auditor accepts JSON with schema
`fpga-ml-reproducibility-contract-v1`.

```json
{
  "schema": "fpga-ml-reproducibility-contract-v1",
  "project": "example",
  "source": {
    "required_files": ["README.md", "rtl/top.sv"],
    "required_directories": ["rtl", "scripts"],
    "forbidden_suffixes": [".pyc"],
    "forbidden_path_fragments": ["__pycache__"],
    "scan_exclude_prefixes": [".git/", "work/"],
    "scan_exclude_path_fragments": [".cache"],
    "glob_counts": [
      {"pattern": "rtl/*.sv", "minimum": 1, "maximum": 20}
    ]
  },
  "json_checks": [
    {
      "id": "locked-clock",
      "required_for": "source",
      "path": "config.json",
      "pointer": "/acceptance/frequency_mhz",
      "op": "eq",
      "value": 100
    },
    {
      "id": "formal-gate",
      "required_for": "software",
      "path": "work/final_gate.json",
      "pointer": "/status",
      "op": "eq",
      "value": "PASS"
    }
  ],
  "ordered_commands": [
    {"id": "preflight", "command": "python reproduce.py preflight"}
  ],
  "claim_boundaries": ["Physical claims require physical mode PASS."]
}
```

Paths are relative to the audited project root and must not escape it.

`scan_exclude_prefixes` and `scan_exclude_path_fragments` apply only to the
forbidden-file scan. Use them for generated, ignored working-tree content; do
not exclude a directory that contains required evidence.

Supported JSON-check operators are `eq`, `ne`, `le`, `ge`, `truthy`,
`exists`, and `length_eq`. JSON pointers use RFC 6901 escaping for `~` and `/`.

`required_for` is `source`, `software`, or `physical`. Higher modes include all
lower-mode checks. A missing source artifact is a failure. A missing generated
software or physical artifact is pending because the experiment may not have
run yet. A present artifact that violates its check is a failure.

Use `glob_counts` for structural invariants that can be decided without running
the project. Do not encode numeric acceptance results as filenames or regular
expressions when the result JSON can be checked directly.
