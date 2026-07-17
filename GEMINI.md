# Review guide for gemma-vibecoding-skills

This repo is an Agent Skill: a frontier agent plans and reviews while a local model
writes code via `scripts/gemma_worker.py` (single files) and `scripts/gemma_batch.py`
(dependency-ordered batches). These scripts run unsandboxed on end-user machines, so
review with that threat model in mind.

## Invariants a PR must not break

- **Exit-code contract** (documented in SKILL.md): worker `0` written, `1` no
  acceptable code block, `2` argument/config/backend/network error, `3` validation
  failed, `4` interrupted stream; batch `0` all passed, `1` failures/blocked,
  `2` invalid manifest. Any code path that exits with the wrong status, or lets an
  exception escape `main()` as a traceback, is a correctness bug.
- **Atomic writes**: the target file must never be replaced with unvalidated or
  partial content. Validation runs before `os.replace()`; interrupted streams go to
  `<out>.partial`, never the target.
- **Symlink safety**: backup (`.bak`) and output paths must reject symlinks;
  batch spec/output/context paths must stay contained under their roots after
  `resolve()` (watch for `..`, absolute paths, and symlink escapes).
- **Fence-length extraction**: `parse_fenced_blocks` only closes a fence on a
  standalone line of >= N of the opening character. Changes to extraction must keep
  files containing embedded triple-backticks extractable.
- **Stdlib only, Python 3.9+**: no pip dependencies, no syntax newer than 3.9.
- **No implicit network or installs**: validators must skip (not download) when a
  tool (node, bash) is absent.

## Repo-specific review priorities

1. Anything that weakens the invariants above is Critical.
2. Path handling on macOS: `/var` vs `/private/var` — tests must compare resolved
   paths.
3. PR descriptions must match the diff: flag descriptions that claim to fix
   behavior that does not exist on `main`.
4. Subprocess use: fixed argv lists only, no `shell=True`, no command substitution.
5. Tests accompany behavior changes; `python3 -m unittest discover -s tests` must
   stay green.
