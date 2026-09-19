# Internal bridge contract (v2)

One native process owns a pair of explicitly selected driver-backed MIDI endpoints. Python owns that process. Local stdio only; logs to stderr. No automatic MIDI setup mutation during server startup.

## Python ↔ Swift

Commands are one JSON object per line, max 4096 bytes. `id` is a nonempty ASCII token `[A-Za-z0-9_-]`, max 64 characters. Commands:

- `{ "id": "r1", "command": "refresh" }`
- `{ "id": "c1", "command": "pc", "program": 0, "channel": 1 }`
- `{ "id": "c2", "command": "cc", "control": 7, "value": 64, "channel": 1 }`
- `{ "id": "q1", "command": "quit" }`

Validate integers strictly (not booleans), program/control/value 0–127, channel 1–16. Every accepted command emits `{"kind":"command_result","id":"...","ok":true,"status":0}` after its CoreMIDI send completes, or ok=false with status/error. This only confirms transport delivery. Refresh embeds the same id in Lua request.

Native transport events are `{"kind":"transport","connected":false,"reason":"..."}` or a connected event that also carries `"route"`, ordered as destination then source. Each route row is `[direction, name, endpoint_unique_id, entity_unique_id, device_unique_id, driver_owner]`. Python pins the first connected route for its process lifetime and refuses an explicit helper reconnect if any field changes. A fresh MCP server establishes a fresh pin; operators must use `doctor` before deliberately restarting against changed MIDI configuration. On endpoint identity changes, stop sending and report disconnected; reconnecting must never silently target a different device. Native endpoint listing is JSONL, with source/destination name, unique_id, entity, device, device_name, manufacturer, model and driver_owner metadata. Preserve existing --list and self-test CLI flags. IAC CLI flags stay --iac-input NAME --iac-output NAME, with unambiguous driver identity validation. Metadata listing is read only.

Python implements bank/program selection without extending the native protocol: while holding the existing mutation lock and deadline, it sends `cc` control 0 (MSB), `cc` control 32 (LSB), then `pc`, all on the same explicit channel. Each value is a strict integer 0–127 and the channel is 1–16; booleans are invalid. The sequence is never retried. `sent` is true only after all three native acknowledgements; `messages_attempted` and `messages_confirmed` expose ambiguous or partial delivery without treating a missing acknowledgement as proof that MainStage received nothing. A matching callback can set `program_observed`, but `observed` and `bank_observed` remain false because profile feedback contains no selected bank. A partial or ambiguous result must be refreshed and reconciled manually, not automatically retried.

## Lua ↔ Swift

SysEx `F0 7D` + ASCII `MSP2<TAB>kind<TAB>percent-encoded fields` + `F7`. Percent encode UTF-8 bytes, control characters and `%`. Native decodes to `{"kind":"...","fields":["..."]}`. Maximum frame 64 KiB; bound queues and snapshot totals too.

- `hello`: application, protocol_version (2), session, capabilities. Base: `selection,patch_list,raw_midi_cc`; the default profile also advertises `metronome_toggle,action_metronome`. Experimental installs may advertise `mapped_parameter_1`, `action_panic`, `action_master_mute`, and `action_play_stop`.
- `snapshot_begin`: session, revision, request_id (empty for unsolicited)
- `selection`: program, setIndex, patchIndex, concert, set, patch
- `item`: isPatch (0/1), setIndex, patchIndex, label
- `snapshot_end`: session, revision, request_id, itemCount
- `parameter`: session, selectionRevision, callbackSequence, mappingId, rawValue, minimum, maximum, rawUnit, label, display, source
- `goodbye`: session
- `error`: request_id, code, message

Input refresh frame: `MSP2<TAB>refresh<TAB>id`. Lua accepts only on configured input bus and consumes only our protocol on configured feedback bus. Refresh uses the host timer and replies with hello + a complete cached snapshot tagged with id; if no selection has ever been received, emit error id/no_snapshot. A correlated response proves the profile is responsive, not that host rename callbacks are exhaustive. Session changes on initialize. Revision changes when selection/list changes. Refresh alone need not change revision. A snapshot is atomic in Python; preserve the previous complete snapshot as explicitly stale until a replacement commits. Reject incomplete, malformed, oversized, wrong-session or mismatched end markers.

Python request ids include a random per-server prefix so delayed timer replies from a surviving profile cannot collide with a fresh server's counter. The MCP `session` field is an opaque connection-scoped context token derived from the native helper connection and the internal Lua wire session. It changes after explicit helper reconnect even when the Lua session/revision do not, so held mutation context is rejected. It is not a durable concert or host identity.

The opt-in `mapped_parameter_1` experiment declares exactly one screen-control slot: channel 16 CC90. Its parameter frame has fixed mappingId `mapped_parameter_1`, range 0–127, rawUnit `midi_7bit`, and source `screen_control_feedback`. `label` and `display` are opaque host callback strings; neither establishes parameter identity or engineering units. Callback sequence increases only for a new `controller_midi_out` callback. Refresh may replay the last frame with the same sequence before its correlated snapshot; replay is not a new observation. Python validates frames against the raw Lua session, exposes the opaque client session, preserves the original observation time, and marks feedback stale when its session or selection revision differs, its capability disappears, or host/transport state is stale.

The profile declares one verified action: Metronome, channel 16 CC80 momentary press127/release0, explicitly scoped to the configured input/output ports. MCP may invoke a name only when its exact capability is advertised by hello. Every named-action result includes `action`, `experimental`, `sent`, `observed: false`, and `messages_confirmed`; partial outcomes also include `may_have_triggered`. It confirms transport sends only, never claims the resulting host state, and never retries an ambiguous press.

Explicit `--experimental-actions` installation adds channel-16 momentary bindings: CC85 `PanicFull`, CC86 `MasterMute`, and CC87 `PlayStop`. CC86 and CC87 have narrow effect/reversal and other-bus isolation evidence; panic does not. Previous/next patch and set candidates are not exposed because both reserved-name and explicit MainStage-action live attempts were no-ops. The remaining group stays disabled by default until every binding passes. Unrelated MIDI passes through; the global patchselector is not enabled. Generic CC is explicit advanced MIDI control; never claim parameter feedback. More capabilities require live evidence and separate review of this contract.

Bank/program selection is standard outbound MIDI and has no profile capability flag. One same-program/different-bank disposable-concert check passed; support still depends on explicit assignments and bank state remains unobservable.

`mapped_parameter_1` is disabled unless installation explicitly opts in. Its capability means only that the profile declared the CC90 slot. It does not prove that a concert mapped the slot, that feedback is enabled, or that a plug-in accepted a value. The named setter reports `observed: true` only after a strictly newer callback with the expected session, selection revision, mapping ID, source, and raw value. A transport send or refresh replay never satisfies observation, and the result is screen-control feedback rather than plug-in acknowledgement. Do not retry automatically after an ambiguous result.

## Release gates

Original source only. No vendor scripts, binaries/disassembly, user concerts, machine-specific logs or absolute user paths. Installation must preserve other profiles and MIDI configuration, be owned/idempotent/reversible, and detect conflicts. An installation requiring manual dedicated buses must document that constraint honestly. No public GitHub/network publication in this task.
