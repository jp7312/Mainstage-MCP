# 004: Keep queued mutation timeouts from invalidating the active request

- Priority P1; correctness; effort S; low risk; dependencies none.
- Planned at `a073567`, 2026-09-19, including current uncommitted work.
- Status: DONE; reviewed and integrated. All 19 focused server tests pass;
  the integrated suite passes 48 Python tests plus Lua/native checks.

`Bridge.mutate` at `src/mainstage_mcp/server.py:385-388` starts its deadline
before acquiring `self.lock`. Its catch at lines 488-509 invalidates shared
state even when acquisition timed out. `invalidate` clears the active snapshot
transaction. A queued call can thus break an in-progress reconnect. The timeout
error text is also blank because `TimeoutError()` is truthy but stringifies empty.

Own only `Bridge.mutate` and one regression in `tests/test_server.py`. Preserve
every partial-send receipt, cancellation behavior, stale-state rule after
acquisition, context guard and no-retry policy. Do not change `refresh`.

1. Develop in an isolated temporary Git worktree with the current main tracked
   diff and untracked tests copied as baseline. Save owned-file copies; no
   commits/push. Other workers own different methods in `server.py`.
2. Following existing `IsolatedAsyncioTestCase` tests, hold the bridge lock with
   an active transaction/reason and invoke a queued mutation with a short
   deadline. Require `ToolError` with `No MIDI mutation sent: timeout`, no send,
   and unchanged transaction/reason/freshness metadata. Prove old code fails.
3. Track whether this call acquired the lock; only its owner may invalidate in
   the exception handler. Keep the deadline around lock acquisition. For the
   final error text use `str(error) or 'timeout'` instead of testing the exception
   object's truthiness. No changes to pure preflight rejection policy.

Set `REPO_PYTHON` to the main checkout's `.venv/bin/python` and verify:

```sh
PYTHONPATH=src "$REPO_PYTHON" -m unittest discover -s tests -p test_server.py -v
git diff --check
```

All server tests and existing partial-send checks must pass. Report an exact
baseline delta for review, then integrate only the approved method/test patch.
STOP for conflicting method changes or any need for live MIDI. Preserve the
parallel `refresh` fix from plan 003. Reviewer maintains this index.
