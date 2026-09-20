# Focused improvement pass — 2026-09-19

The user authorized choosing and implementing the most useful improvements.
Findings are reproduced before implementation, developed by separate Sol/high
agents in temporary worktrees, reviewed, and integrated without committing.
Earlier uncommitted recovery work is the baseline and must be preserved.

| Plan | Function | Priority | Dependencies | Status |
| --- | --- | --- | --- | --- |
| [001](001-installer-lock-recovery.md) | Installer `locked` | P1 | None | DONE — 17 focused tests pass |
| [002](002-plist-error-boundary.md) | Inspector `_plist` | P2 | None | DONE — three focused tests pass |
| [003](003-refresh-lock-timeout.md) | Bridge `refresh` | P1 | None | DONE — queued and owner timeout cases pass |
| [004](004-mutation-lock-timeout.md) | Bridge `mutate` | P1 | None | DONE — 19 focused server tests pass |

Final integrated verification: `make test` passed 48 Python tests, the Lua
profile harness and native parser self-test. `pip check` found no broken
requirements. Each new regression was checked against the old behavior before
its fix; all implementation deltas were independently reviewed and retested.
No live MIDI setup changes or new dependencies were needed.

## Scope decisions

- Keep mapped-parameter feedback default-off: visible CC90 effect does not prove
  a callback, and no evidence justifies changing the transport/profile here.
- Keep strict concert format versions and opaque plug-in state; broader editing
  and a custom MIDI driver need separate live validation.
- No new dependencies, generic framework, automatic retries, or MIDI setup edits.
- Retain conservative invalidation after a mutation acquires the lock and then
  fails a preflight guard; changing that policy is lower value than preventing
  a queued caller from corrupting the current lock owner's transaction.
- Review covers supported Python/Lua/native paths and synthetic tests; live
  experiments, custom-driver discovery and a dependency advisory audit are out
  of scope for this pass.
