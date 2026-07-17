---
name: gemma-coder
description: Delegate code-writing to a configured local or OpenAI-compatible model while the primary agent plans, reviews, and tests. Use when the user explicitly wants a separate model to write application code.
---

# gemma-coder

The primary agent owns requirements, architecture, task decomposition, review, and
tests. The configured worker model writes application source files.

## Resolve the command

`install.sh` links `gemma-coder.sh` to `~/.local/bin/gemma-coder` in link
mode and installs a canonical copy under `~/.local/share/gemma-coder` in copy
mode. If `~/.local/bin` is not on `PATH`, invoke `<skill-dir>/gemma-coder.sh`
directly. The wrapper targets POSIX-like systems with `readlink` and `test -h`;
it resolves direct and chained symlinks before locating its scripts.

## First run

```bash
gemma-coder setup --list
gemma-coder setup --save MODEL
```

If no backend is detected, show the setup hints and stop. Do not consume a code
retry for backend/configuration errors (worker exit code 2).

## Single-file workflow

1. Write a self-contained spec under `tasks/` with the exact output contract,
   signatures, behavior, edge cases, language version, and allowed libraries.
2. Prefer writing tests before delegating implementation.
3. Generate one file:

```bash
gemma-coder worker \
  --task tasks/01-parser.md \
  --out src/parser.py \
  --context src/types.py \
  --expect 'class Parser'
```

Useful options:

- `--lang python|javascript|bash`: override language detection from `--out`.
- `--stream`: display generated content as it arrives.
- `--no-validate`: explicitly disable syntax validation.
- `--no-backup`: replace an existing valid output without creating `.bak`.

Generated code is validated before an atomic replacement. Explicitly mismatched
language fences are rejected. Interrupted streams leave the target untouched and
save raw partial output as `<out>.partial`.

## Batch workflow

Use `tasks/manifest.json`; do not encode machine-readable dependencies in free-form
`PLAN.md` text.

```json
{
  "tasks": [
    {
      "id": "types",
      "spec": "01-types.md",
      "output": "src/types.py"
    },
    {
      "id": "parser",
      "spec": "02-parser.md",
      "output": "src/parser.py",
      "depends_on": ["types"]
    }
  ]
}
```

- `spec` is relative to the manifest directory.
- `output` is relative to `--project-root`.
- `depends_on` contains task IDs.

Run:

```bash
gemma-coder batch \
  --manifest tasks/manifest.json \
  --project-root . \
  --retries 2
```

The runner validates dependencies, detects cycles, executes a stable topological
order, passes successful dependency outputs as context, blocks dependents after a
failure, and includes the previous error in retry prompts. It intentionally has no
parallel option yet.

## Exit codes

### Worker

- `0`: validated output written
- `1`: no acceptable code block
- `2`: argument, configuration, backend, or network error
- `3`: generated code failed validation
- `4`: interrupted stream; partial response saved separately

### Batch

- `0`: every task passed
- `1`: one or more tasks failed or were blocked
- `2`: invalid/unreadable manifest

## Retry policy

Retry generation or validation failures at most twice, feeding the exact previous
error back into the revised prompt. Do not retry configuration/backend errors.
After two unsuccessful code retries, the primary agent may fix the file directly
but must disclose that in the final summary.
