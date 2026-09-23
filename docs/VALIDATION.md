# Validation — 0.1.0a2 candidate

Recorded 2026-09-18; second-Mac pass added 2026-09-19. The live baseline below was established with 0.1.0a1. Recovery, bank targeting, opt-in named actions, the mapped-parameter probe and offline concert inspection are 0.1.0a2 candidate changes unless a later paragraph states specific live evidence. This is a source-built developer alpha, not a full MainStage object model or a stable compatibility promise.

## Live environment

- macOS 26.4 (25E246), Apple Silicon arm64.
- MainStage 4.3.1 (5233), one synthetic two-patch concert.
- Python 3.13, MCP Python SDK 2.2.0, stdio protocol 2026-07-28.
- Original profile version 2.1 / bridge wire protocol 2, two temporary Apple IAC buses.

## Observed results

| Check | Result |
| --- | --- |
| Official MCP client initializes and lists tools | All six 0.1.0a1 tools exposed over real stdio |
| Correlated refresh | Responsive, non-stale snapshot with negotiated capabilities |
| List and current selection | One set, its patch, and a patch outside the set reported correctly |
| Assigned Program Changes | 0 → 1 → 0 reached the expected patches; `sent: true`, `observed: true` |
| Metronome through final MCP tool | Two explicit invocations changed visible UI off → on → off; each returned `sent: true`, `observed: false`, `messages_confirmed: 2` |
| Metronome port isolation | Sending the binding on the other temporary bus did not toggle the UI |
| MainStage closes during MCP session | Refresh timed out; cached state became stale and host responsiveness false |
| Native CoreMIDI loopback | Passed; this separately checks transport, not MainStage |
| Owned profile install/remove | Final profile installed and removed successfully |

Baseline program-only selection required disabling Bank Select on one synthetic patch. The program-only tool does not send bank messages; the new bank/program tool does. Metronome UI observation was performed separately by the tester; the MCP does not possess reliable metronome state feedback.

The live test used disposable patches named Bridge Alpha and Bridge Beta. Reproduce with a disposable concert: assign distinct program numbers, select the dedicated command input in MainStage's concert Program Changes settings, disable Bank Select for a program-only test, refresh, then call the selection tool with the returned session/revision. Compare its reported selection to the UI. Test the metronome twice with fresh context, checking the UI between calls and restoring its initial state. Never use an automatic retry to compensate for an ambiguous toggle.

## Automated and independent verification

The original integrated candidate passed 35 Python tests, the Lua profile harness, the native parser self-test and a CoreMIDI loopback check. Coverage includes fragmented/realtime MIDI bytes, UTF-8 escaping, input and buffer bounds, atomic snapshots, connection-scoped session/revision guards, process-unique request correlation, explicit crash recovery, changed-route refusal, correlated deadlines, partial sends, cancellation, conservative installer ownership checks, default-off experimental rendering, strict per-action capability gates and exact CC80/85–87 press/release pairs. The server tests exercise real SDK stdio with a synthetic native helper, including protocol versions 2026-07-28 and 2025-11-25; those checks and the loopback are not MainStage effect evidence.

A final focused reliability pass on 2026-09-19 passed 48 Python tests in 3.659s, the Lua profile harness and native parser self-test; `pip check` also passed. Its installer checks cover the persistent lock inode: an unlocked pre-existing file is reusable, a separate live owner is rejected, ownership is reacquired after that process is terminated, and a lock symlink is refused. The empty inode intentionally remains; its presence alone does not indicate a running installer and it should not be deleted. Malformed XML plists produce a clean concert-format rejection, and the inspector CLI exits 2 without a traceback. A request that times out before acquiring the server lock leaves the active owner's snapshot transaction unchanged.

For a read-only live smoke check after installation, omit `--program`:

```sh
PYTHONPATH=src .venv/bin/python tests/live_check.py \
  --bridge "$PWD/build/bridge" --concert 'Exact Disposable Concert Name'
```

The helper refreshes and checks the exact concert name but sends no mutation by default. Each repeated `--program 0..127` is an explicit opt-in to one Program Change, for example `--program 0 --program 1`; it stops without retry if selection is not observed.

### Second-Mac offline recovery pass — 2026-09-19

The original 35 Python tests, Lua profile harness, native parser self-test and CoreMIDI loopback passed on macOS 26.6.2 (25G83), arm64, Python 3.12.13, MCP SDK 2.2.0, Swift 6.3.3 and Lua 5.5.1. The isolated driver build also passed its bundle-loader, ABI/routing and mock lifecycle checks; it was not installed. This initial stage supplied offline evidence; the subsequent live pass is recorded below.

Update 2026-09-23: MainStage 4.3.1 (5233) embeds Lua 5.2, not the Lua 5.5.1 used by this harness run. Its universal `LogicMainStage` framework binary contains the `Lua 5.2` version string plus `_ENV` and `bit32`, which Lua 5.1 lacks, and neither of Lua 5.1's `setfenv` or `loadstring`. Re-check with the command below; this Mac printed `Lua 5.2`, `_ENV` and `bit32`, each twice (once per architecture slice):

```sh
strings -a /Applications/MainStage.app/Contents/Frameworks/LogicMainStage.framework/Versions/A/LogicMainStage \
  | grep -xE 'Lua 5\.[0-9]+|_ENV|bit32|setfenv|loadstring' | sort | uniq -c
```

The profile stays Lua 5.1-compatible for older hosts. Hosted CI runs the Lua harness under Ubuntu's Lua 5.1 and 5.2 packages in addition to Homebrew's Lua 5.5 on macOS.

Recovery regressions exposed two defects: cancellation during reconnect could leave its helper running, and expired tagged snapshot replies could replace cached state. Reconnect now closes the owned helper on cancellation. Tagged snapshots require a pending refresh request before they can commit. Added synthetic checks cover cancellation during teardown/readiness/refresh, delayed replies around reconnect and requests expiring mid-snapshot, host goodbye/reinitialization, unchanged-route recovery after a topology notification, and mapped callback replay retaining its original observation time and stale connection scope. The integrated `make test` passes all 40 Python tests plus the Lua and native self-checks; `pip check` reports no broken requirements.

The smoke client now accepts `--reconnect` to restart only its owned helper once, requiring fresh responsive state for the same concert and a changed nonempty session before any optional Program Changes:

```sh
PYTHONPATH=src .venv/bin/python tests/live_check.py \
  --bridge "$PWD/build/bridge" --concert 'Exact Disposable Concert Name' --reconnect
```

Without `--program`, this sends no performance mutation. Its fake-client regression checks default refresh-only behavior, reconnect validation, fresh context for optional programs and stopping without retry. The command subsequently passed against MainStage in the live pass below.

### Second-Mac live pass — 2026-09-19

MainStage 4.3.1 (5233), installed from the App Store, was tested on the same macOS 26.6.2 arm64 environment. One disposable Keyboard Minimalist concert, `MCP Validation 2026-09-19`, contained the `Classic Electric Piano` patch. The owned default profile was version 2.3 / wire protocol 2. Two temporary Apple IAC buses provided the route; local endpoint identities were recorded before setup. No personal concert was edited.

| Check | Observed result |
| --- | --- |
| Owned default profile installation and doctor | Installed successfully; dedicated buses verified |
| Real MCP stdio refresh and `--reconnect` | Protocol 2026-07-28; fresh responsive selection/list/capabilities before and after reconnect; same concert and different nonempty client session |
| Actual owned-helper crash | Python `Bridge` client killed only its native child; bridge/transport became unavailable and the prior session/selection remained cached and stale; one reconnect restored fresh state with a new session and the same pinned route |
| MainStage quit/relaunch | Same Python/native bridge remained running; quitting MainStage left cached state stale and host responsiveness false; relaunching the same disposable concert restored fresh state with a new Lua/client session |
| Explicit CC90 screen-control mapping | One raw channel-16 CC90/value-32 send changed the mapped knob and channel-strip volume from 0 to -18 dB in MainStage's accessibility state; raw tool correctly reported `observed: false` |
| Mapped-parameter feedback | Fresh subsequent refresh still returned `mapped_parameter: null`; guarded `--value 64` probe refused before calling the setter because no current callback baseline existed |

The recovery checks sent no Program Change, CC or named action. The helper-crash and host-restart checks used the Python/native bridge directly; the refresh/reconnect smoke check used real MCP stdio. Old-context rejection after a crash and concurrent crash/refresh were not separately exercised live in this pass. Neither the CC90 transport receipt nor the visible fader change establishes an audio effect or parameter acknowledgement. Manual control/hardware feedback and patch-change invalidation remain untested; see [parameter probe](PARAMETER_PROBE.md).

The owned-helper crash check is reusable and read-only unless explicitly enabled:

```sh
PYTHONPATH=src .venv/bin/python tests/live_recovery_check.py \
  --bridge "$PWD/build/bridge" --concert 'Exact Disposable Concert Name' --crash-helper
```

It checks exact concert, fresh state and a pinned route before killing its own child, reconnects once and never retries a performance mutation. Synthetic coverage also verifies its read-only default and wrong-concert refusal. `tests/live_parameter_check.py` supplies the separate guarded, one-write callback probe documented in the mapping recipe. The final integrated `make test` passed all 43 Python tests, the Lua harness and native parser self-test; `git diff --check` passed.

Local evidence is in ignored `build/live-validation/`: `live-refresh-reconnect.jsonl`, `live-helper-crash.jsonl`, `host-restart.jsonl`, `raw-mapping-result.jsonl` and `live-parameter-probe.stderr`. These logs contain local context and are not distributed. Cleanup removed the owned profile and temporary buses, restored the IAC device's original offline state and original bus entity/source/destination IDs, and compared the complete CoreMIDI inventory equal to its pre-test baseline. MainStage and the test helpers were stopped; MainStage remains installed. No custom driver, global MIDI restart or logout was used.

## Recovery live validation recipe

The original-machine combined candidate passed one helper-restart case: `mainstage_reconnect` stopped and restarted its owned native helper, restored a responsive snapshot on the same pinned route, returned a different opaque client session, and rejected the held pre-reconnect session before any MIDI send. The September 19 pass above additionally completed host quit/relaunch and actual helper-crash recovery. The list below retains the complete recipe; its other cases and the post-crash old-context mutation check remain pending live evidence.

For the unchecked steps, use the disposable one-concert setup above and record the exact macOS/MainStage versions and endpoint listing before and after each case.

1. Refresh and record the returned session/revision and visible selection. Fully quit MainStage, confirm cached state becomes stale after the responsiveness deadline, relaunch it with the same disposable concert, select a patch and refresh. Require a non-stale snapshot and a new session before any mutation.
2. Terminate only the native helper child of the test MCP server. Require `bridge_running: false`, `transport_connected: false` and the prior complete snapshot still present but stale. Call `mainstage_reconnect`; require a fresh non-stale snapshot, a changed session and unchanged endpoint listing. Submit the old session/revision with an otherwise harmless, unmapped test CC and require rejection before any MIDI send.
3. Cause a CoreMIDI topology notification by attaching or removing an unrelated external device without editing the two dedicated IAC buses. Require the native helper to disconnect conservatively. Call reconnect and require success only when every pinned route identity is unchanged.
4. On a disposable MIDI configuration only, replace or recreate one named bridge bus. Require reconnect to fail with `endpoint identity changed`; do not accept a same-name replacement. Run `doctor` and review assignments before deliberately restarting the whole MCP server, which establishes a new in-memory pin.
5. Repeat a refresh concurrently with helper termination to exercise a late callback. Require the old complete snapshot to remain stale until a response correlated to the new helper commits, and require no Program Change, CC or toggle replay.
6. Open two disposable concerts, including two with the same display name, and switch focus while refreshing. Record the callbacks, but do not interpret a matching name/session/revision as durable concert identity or enable mutations from ambiguous state.

## Experimental named-action evidence and remaining recipe

The CC86 `MasterMute` binding produced OFF → ON → OFF, and CC87 `PlayStop` produced STOP → PLAY → STOP. Repeating CC80, CC86 and CC87 on the other temporary bus produced no UI change. These checks establish narrow binding effect/reversal and port isolation; tool results remain `observed: false`. Panic is still unverified. Reserved-name `PreviousPatch` and explicit `action_mainstage='PreviousSet'` attempts were live no-ops, so all navigation names and CC81–84 were removed.

1. Quit MainStage, uninstall the owned default profile, reinstall with `--experimental-actions`, then fully relaunch MainStage with only the disposable concert open. Refresh and require exactly `action_panic`, `action_master_mute`, and `action_play_stop` in addition to the default capabilities.
2. With master output already at a safe level, invoke `master_mute` twice with a fresh refresh before each call. Observe the MainStage master-mute UI change once per call and restore its original state. The MCP result must remain `observed: false`.
3. In a silent concert with a visible playback target, invoke `play_stop` twice with fresh context and observe one play then one stop transition. Restore the initial transport state. Do not claim audio playback from the button state alone.
4. For `panic`, first prove only that one press/release reaches the declared route. A host effect requires a controlled sustained-note scenario and independent evidence that the note stops; transport success or silence alone is insufficient causal proof.
5. Repeat CC80 and CC85–87 on another IAC bus. No action may occur there. If it does, the binding fails isolation and must remain disabled.
6. Force or simulate a missing release acknowledgement and confirm the client receives `may_have_triggered: true`, does not retry, and refreshes before any later mutation.

Promote one capability at a time only after its effect, reverse/restoration behavior, and other-bus isolation are recorded on MainStage 4.3.1. Toggle actions still have no action-state feedback; selection callbacks can establish the resulting patch/set but cannot prove the MIDI command was the cause if that state was reached concurrently.

## Experimental mapped-parameter live result

On September 18, the combined profile advertised the opt-in CC90 slot, but no explicit MainStage screen-control mapping was established. One unmapped write of value 32 returned `sent: true`, `observed: false`, and `timed_out: true`, leaving cached state explicitly stale. A subsequent refresh returned healthy responsive state with `mapped_parameter: null`. This validates conservative ambiguity and recovery handling. September 19 added the explicit mapping and raw-CC UI effect recorded above, but still no parameter callback. The probe remains default-off; follow [the mapping recipe](PARAMETER_PROBE.md) before making any stronger claim.

Independent implementation reviews and additional adversarial checks found no unresolved blockers within this implemented scope. The 0.1.0a1 baseline was built as an sdist and a wheel built from that sdist; the source distribution includes Swift, Makefile, Lua checks and documentation, while the wheel includes the Lua profile. Users still build the native executable separately. The same source-distribution and wheel content checks are part of the 0.1.0a2 packaging workflow.

## Findings that limit support

- A MIDI rescan did not reliably restore profile feedback; fully quitting/relaunching MainStage with one concert did. Multiple concerts and profile reload behavior were not isolated experimentally.
- A selector experiment reached a patch inside a set, but callback indices did not reliably address patches outside sets. That selector is not enabled or exposed.
- Controller feedback did not reliably represent metronome state. The mapped-parameter probe exposes no live feedback evidence yet, and no action-state API is exposed.
- A separate original CoreMIDI driver prototype compiled and passed synthetic checks but was not discovered after rescan or CoreMIDI restart. It was removed. Its source is now preserved under [experiments/isolated-driver](../experiments/isolated-driver/README.md), outside the supported package and default build; no driver binary is distributed.
- Adding/removing IAC buses regenerated pre-existing endpoint IDs on this machine. Test cleanup restored the recorded endpoint IDs. The distributed installer never edits MIDI configuration; manual setup remains an explicit limitation.
- Session/revision guards are preflight checks, not atomic host transactions. A user can switch context between checking state and MIDI execution. The exposed session is connection-scoped; neither it nor concert names are durable concert identities.

The unchecked recovery cases above, Intel execution, MainStage versions other than 4.3.1, macOS versions beyond the two recorded builds, multiple concerts, sleep/wake, sustained load and signed distribution remain unverified. Bounds are tested synthetically; a large live concert was not tested. Update 2026-09-19: the initial published commit's [hosted offline CI run passed](https://github.com/jp7312/Mainstage-MCP/actions/runs/35414789330). This does not establish live MainStage compatibility on the runner.

One refresh timed out after routing changes while entering Perform mode and recovered only after a full MainStage relaunch; that sequence does not isolate the cause. A later fresh Perform-mode refresh and bank/program selection succeeded, ruling out a general claim that Perform mode is unsupported.

The temporary buses and owned profile were removed after both test rounds; the earlier diagnostic setting remains absent. Existing endpoint names and IDs were compared to their recorded baseline. No personal concert, vendor script, binary, local installation manifest or raw device log is part of the source release.

## Bank/program targeting live result

The candidate `mainstage_select_bank_program` tool selected Alpha with bank 0/program 0 and Gamma with MSB 1, LSB 0/program 0 in the disposable concert. Both same-program/different-bank cases matched the MainStage UI and selection callbacks, with all three transport messages acknowledged. A later fresh, responsive read in Perform mode also selected Alpha from Gamma with bank 0/program 0 and matched both UI and callback. The API still reports the complete target as `observed: false` and `bank_observed: false` because callback feedback contains the program but no bank. The tool holds the existing mutation lock while sending exactly CC0 (Bank MSB), CC32 (Bank LSB), then Program Change. Automated checks also cover strict argument rejection, exact order/channel/value serialization, weak program-only observation, and a timeout after only the MSB was acknowledged.

The candidate deliberately does not enable `patchselector`. On MainStage 4.3.1 that flag is global to the matched IAC parent, and a live experiment showed that selector indices addressed a patch inside a set but did not reliably address an orphan patch. Callback list indices are therefore neither Program Change assignments nor durable patch IDs. Patch labels can duplicate, callback feedback contains no bank, and no stable host identity has been found. Exact bank/program values must come from the user's concert assignments.

### Exact live check

Use only a disposable concert and the dedicated `MS Bridge Input` / `MS Bridge Output` buses:

1. Confirm the installed profile's `controller_info()` has no `patchselector` field. Launch MainStage fresh with only the disposable concert open.
2. In the concert's Program Changes settings, choose `MS Bridge Input` and one test channel. Assign two disposable patches the same Program Change number but different Bank MSB/LSB pairs. Record both triples and the initial selected patch.
3. Build and start this copy, then call `mainstage_refresh`. Save its returned `session` and `revision`; verify `stale: false`, `host_responsive: true`, and the expected concert name.
4. Call `mainstage_select_bank_program` with the first patch's exact `bank_msb`, `bank_lsb`, `program`, channel, session and revision. Require `sent: true`, `messages_attempted: 3`, `messages_confirmed: 3`, `observed: false`, `bank_observed: false`, and `program_observed: true`. Require the returned state's patch/set names and the MainStage UI to show the first patch.
5. Refresh for a new revision and repeat with the second patch's bank pair and the same program. Require the second patch in both callback state and UI. This same-program/different-bank check is the evidence that the bank messages affect MainStage assignment selection.
6. Refresh and test boundary values used by real assignments, including 0 and 127 where practical. Send a boolean, -1, and 128 for each byte field and channels 0 and 17 through the MCP tool; every call must fail before any MIDI message is logged.
7. After every timeout, cancellation, or result with fewer than three confirmed messages, compare `messages_attempted` with `messages_confirmed`, inspect `may_have_changed_bank` and `may_have_selected`, refresh manually, and do not retry automatically. A missing acknowledgement does not prove that MainStage received nothing.
8. Restore the original selection and concert Program Changes settings, quit MainStage, remove the owned test profile, and confirm the two IAC buses and unrelated MIDI assignments are unchanged.

Record the exact MainStage/macOS builds, assignments, tool results and observed UI selections when reproducing this check. Keep treating bank state as unobservable even when the UI selection is correct.

## Offline concert inspection candidate

`mainstage-mcp inspect-concert PATH` is a separate experimental, read-only CLI. It accepts only the observed MainStage 4.3.1 (5233) plist versions `Version=57057` and `VersionPatches=40014`, bounds individual and aggregate plist reads plus hierarchy size/depth, rejects symlinked package components and unsafe child names, and treats `.cst` plug-in state as opaque. Handcrafted hierarchy, route, assignment, traversal and version-rejection checks pass. Two independent reviews also matched its read-only output for the validation fixture and synthetic fixture to their raw plists. This is not a hosted live plug-in-context test. The CLI is not an MCP tool and does not create, edit or promise compatibility with another concert format version.
