# 001: Recover installer locking after process death

- Priority: P1; category: bug; effort: S; risk: low; dependencies: none.
- Planned at: `a073567`, 2026-09-19, including the existing uncommitted recovery work.
- Status: DONE; reviewed and integrated without committing. All 17 installer
  tests pass, including process-death recovery and active-owner exclusion.

## Why and current state

`src/mainstage_mcp/installation.py:137-146` implements `locked(state)` with
`os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)` and removes the
sentinel in `finally`. Process death skips that cleanup and blocks subsequent
install/uninstall forever. Both operations share this context manager.
`native/bridge.swift:227-234` already uses a persistent inode plus `flock`.

Preserve conservative ownership checks, symlink rejection, and single-owner
installation. Never touch actual MIDI configuration, installed profiles or buses.

## Scope and implementation

Only modify `src/mainstage_mcp/installation.py` (`locked` and its imports) and
`tests/test_installation.py`. Existing tests use `unittest`, resolved temporary
directories, and fake endpoint listings; follow that pattern.

1. In a separate temporary Git worktree, reproduce the bug with a stale unlocked
   lock file. Copy the main checkout's existing tracked diff and untracked test
   files into the worktree first; record that as the review baseline, preserving
   all prior work. Do not commit, push or modify the user's branch.
2. Open the persistent lock with `O_CREAT | O_RDWR | O_NOFOLLOW`, mode `0o600`.
   Acquire stdlib `fcntl.flock(fd, LOCK_EX | LOCK_NB)` inside a `try/finally` that
   always closes the fd. Do not unlink the inode, including on contention.
   Match the current CLI error convention; retain a useful contention error.
3. Add focused regression coverage for an existing unlocked lock file, rejection
   while another process holds the lock, and reacquisition after terminating
   that owned test process. Assert the lock inode remains and symlinks remain
   refused. Use bounded waits and guaranteed child cleanup.

## Verification and completion

Set `REPO_PYTHON` to the absolute path of the main checkout's existing
`.venv/bin/python`, then run from the isolated worktree (no dependency installs):

```sh
PYTHONPATH=src "$REPO_PYTHON" -m unittest discover -s tests -p test_installation.py -v
git diff --check
```

All installer tests must pass; the new recovery regression must fail against
the old function. Report the worktree path and the delta from its recorded
baseline for review. Apply only this approved delta to the main checkout after
the reviewer's approval; never overwrite unrelated changes. No new dependency.

STOP if the cited function differs materially, another worker changes it, or
verification would require real MIDI/profile changes. This fix intentionally
leaves the empty lock file: deleting it would reintroduce inode races. Future
installer changes must continue to share this same lock.

## Documentation closeout

After code approval, the same executor may also update `docs/SETUP.md`,
`docs/HANDOFF.md` and `docs/VALIDATION.md` in its isolated worktree, subject to
separate diff review. Explain the persistent lock inode, clean malformed-plist
errors and the request-timeout fixes from plans 003/004. Preserve historical
live evidence; record final integrated test counts only after an actual run.
