# 002: Reject malformed plists through the inspector's error contract

- Priority: P2; category: bug; effort: S; risk: low; dependencies: none.
- Planned at: `a073567`, 2026-09-19, including earlier uncommitted recovery work.
- Status: DONE; reviewed and integrated without committing. All three inspector
  tests pass, including malformed XML and clean CLI error handling.

## Why and current state

`src/mainstage_mcp/concert_inspector.py:37-40` catches only
`plistlib.InvalidFileException` around `plistlib.loads(data)`. Malformed XML can
also raise `ValueError` (integer), `AttributeError` (date) and `ExpatError`
(truncation). These escape `ConcertFormatError`, so `main` at lines 173-176
prints a traceback and exits 1 instead of the format-error exit 2.

This CLI is read-only, supports only the recorded private format versions and
treats `.cst` files as opaque. Those boundaries and all byte/hierarchy limits
must remain unchanged.

## Scope and steps

Only `_plist` in `src/mainstage_mcp/concert_inspector.py` and
`tests/test_concert_inspector.py` may change. Follow its stdlib `unittest` and
synthetic `TemporaryDirectory` fixture pattern; no personal concerts.

1. Create an isolated temporary Git worktree from the current checkout baseline,
   including its tracked diff and untracked tests. Save exact copies of owned
   files before editing. Do not commit, push or change the user's branch.
2. Add one table-driven regression for truncated XML, invalid/overlong integers
   and invalid dates; assert `ConcertFormatError`. Confirm failures on old code.
   Include a CLI assertion for exit 2, useful error text, no traceback and no
   stdout result. Verify valid XML remains accepted as well as existing binary
   fixtures.
3. Translate ordinary exceptions only at the `plistlib.loads` call into the
   existing `ConcertFormatError` with exception chaining. A narrow-scope
   `except Exception` is acceptable here because all parser failures mean this
   input cannot be inspected. Do not catch `BaseException`, expand the try block
   to filesystem/output operations, rewrite the parser or add dependencies.

## Verification and completion

Set `REPO_PYTHON` to the absolute path of the main checkout's existing
`.venv/bin/python`, then run from the worktree:

```sh
PYTHONPATH=src "$REPO_PYTHON" -m unittest discover -s tests -p test_concert_inspector.py -v
git diff --check
```

All tests must pass and the new regression must fail before the fix. Report the
worktree path and exact delta against recorded baseline. Wait for review, then
integrate only the approved delta without overwriting other edits. Reviewer
updates the plan index. STOP for unexpected drift, overlapping edits, or any
need for a live/personal concert. Future parser changes must preserve this
single exception boundary and the CLI's clean rejection path.
