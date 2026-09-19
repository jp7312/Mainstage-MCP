# 003: Keep queued refresh timeouts from invalidating the active request

- Priority P1; correctness; effort S; low risk; dependencies none.
- Planned at `a073567`, 2026-09-19, including current uncommitted work.
- Status: DONE; reviewed and integrated. Both focused reconnect tests pass;
  the integrated suite passes 48 Python tests plus Lua/native checks.

`Bridge.refresh` in `src/mainstage_mcp/server.py:365-374` starts its timeout
before acquiring `self.lock`, but unconditionally calls `invalidate` in the
exception handler. `invalidate` at lines 135-137 clears `self.transaction`.
A queued request can therefore destroy a reconnect's partial snapshot without
ever owning the lock. Reconnect holds that lock during teardown/start as well
as its separately timed refresh, making this overlap reachable.

Own only `Bridge.refresh` and one regression in `tests/test_reconnect.py`.
Keep deadlines covering the queue wait, preserve errors and invalidation for
actual owners, and do not change reconnect or introduce a shared abstraction.

1. Develop in an isolated temporary Git worktree. Include the current main
   tracked diff and untracked tests as baseline, and save copies of owned files.
   Do not commit or push. Other workers own other methods in `server.py`.
2. Add a deterministic `IsolatedAsyncioTestCase` regression using a manually
   held bridge lock, an active transaction and reason, and a short timeout.
   Require a `ToolError` while transaction/reason/freshness metadata remain
   unchanged and no send occurs. Confirm it fails before the fix.
3. Start a local acquired flag as false; set it immediately inside the lock.
   In the exception handler invalidate only if this call acquired the lock.
   Keep the original active-owner timeout failure behavior covered.

Set `REPO_PYTHON` to the main checkout's `.venv/bin/python` and verify:

```sh
PYTHONPATH=src "$REPO_PYTHON" -m unittest discover -s tests -p test_reconnect.py -v
git diff --check
```

All tests pass; no files/methods outside scope change. Report exact baseline
delta for review. Integrate only after reviewer approval, using an exact patch
that preserves other methods and all prior changes. STOP for overlapping method
edits or a need for real MIDI. The same defect in `mutate` is plan 004; never
overwrite that worker's change. No retries or host behavior claims are added.
