# Workflow contract fields

Add an `argv` array to every entry in `ordered_commands`. Keep the existing
human-readable `command` string when documentation or another auditor uses it.

```json
{
  "ordered_commands": [
    {
      "id": "source-verify",
      "command": "python scripts/check_repository.py",
      "argv": ["{python}", "scripts/check_repository.py"]
    }
  ],
  "workflow_pipelines": {
    "source": {
      "steps": ["source-verify"],
      "audit_mode": "source"
    },
    "software": {
      "steps": ["source-verify", "preflight", "vivado", "models", "rtl", "assemble", "verify"],
      "audit_mode": "software"
    }
  }
}
```

Rules:

- Step identifiers must be unique.
- Every pipeline step must name an existing ordered command.
- `argv` must be a non-empty array of strings.
- Supported tokens are `{python}`, `{root}`, and `{contract}`.
- `audit_mode` is `source`, `software`, or `physical`.
- Paths in reports and logs remain relative to the selected report directory
  where practical.
- Commands run with the project root as their working directory.

