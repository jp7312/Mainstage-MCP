# MainStage MCP

Source-built developer alpha for local MainStage inspection and MIDI control through MCP. A Python stdio server owns a Swift/CoreMIDI bridge; an original Lua device profile reports MainStage selection and patch/set information.

**Validation:** the 0.1.0a1 baseline passed a live MCP stdio round trip on MainStage 4.3.1 with one original test concert: profile handshake, selection/list feedback, assigned Program Changes and the metronome binding, including other-bus isolation. The 0.1.0a2 candidate adds the features below. Same-program/different-bank targeting, explicit helper reconnect and two experimental action bindings have narrow live evidence; mapped-parameter feedback and concert inspection remain offline checked unless the validation document records a later result. This is not a compatibility matrix. No stable-release, signing, notarization, or cross-machine compatibility claim is made.

## Scope

| Tool | Behavior |
| --- | --- |
| `mainstage_get_state` | Cached selection, session/revision, transport status, responsiveness and staleness |
| `mainstage_list_patches` | Cached patch/set list with the same freshness metadata |
| `mainstage_refresh` | Correlated profile response and atomic cached snapshot |
| `mainstage_reconnect` | Explicitly restart a crashed/disconnected native helper only if its pinned endpoint identities still match, then refresh |
| `mainstage_select_program` | Send an assigned MIDI program, 0–127; report `sent` and `observed` separately |
| `mainstage_select_bank_program` | Send explicit Bank MSB, Bank LSB, then Program Change; report transport, program-only observation and unobservable bank separately |
| `mainstage_send_cc` | Advanced raw CC, 0–127, with effects determined by your concert mappings |
| `mainstage_toggle_metronome` | Capability-gated press/release; always returns `observed: false` because action state feedback is unavailable |
| `mainstage_trigger_action` | Capability-gated named press/release; metronome is verified, other names require explicit experimental installation |
| `mainstage_set_mapped_parameter_1` | Experimental CC90 slot; reports only newer matching screen-control callback feedback |

The separate `mainstage-mcp inspect-concert PATH` CLI reads one exact, tested `.concert` plist format offline. It is experimental and read-only, and it is not an MCP filesystem tool.

Mutation tools require the opaque, connection-scoped session and revision from your last state read. Reconnect changes the session even if the MainStage profile did not restart, invalidating held mutation context. It never retries a mutation. List indices are not Program Change assignments or durable patch IDs. Refresh proves the profile responded; it replays callback-derived data and cannot guarantee every host rename was reported. An observed program match is not proof the command caused the selection.

The profile advertises capabilities during its handshake; every named action requires its own `action_*` capability. The default profile reserves CC80 on channel 16 for the verified metronome route (127 then 0). `--experimental-actions` additionally reserves CC85–87 for panic, master mute and play/stop. Master mute and play/stop have narrow binding-level live evidence; panic remains unverified, so the group stays disabled by default. Navigation candidates were removed after live no-op results. Do not automatically retry an ambiguous action.

Program-only selection sends no Bank Select messages. The bank/program tool sends CC0, CC32 and Program Change once in that order, using explicit 0–127 values. MainStage's concert assignments determine its effect. The callback reports the selected program but no bank, so `observed` remains false for the complete bank/program target even when `program_observed` is true. This is assignment control, not durable patch identity or list-index selection.

There is no arbitrary concert editing, plugin enumeration, full parameter introspection, or open-ended action registry. The optional mapped-parameter probe covers one explicit screen-control slot and does not prove a plug-in effect. Create and verify your own MainStage mappings before using raw CC.

## Build and setup

Requires macOS, MainStage for live use, Xcode Command Line Tools/Swift, Python 3.11 or newer, and Lua for tests. The Python MCP SDK is pinned to `mcp==2.2.0`.

```sh
make bootstrap
make build
make test
# Optional: requires functioning CoreMIDI services; does not test MainStage.
make loopback
```

Follow [setup and removal](docs/SETUP.md) before starting the server. Installation requires two manually prepared, dedicated IAC buses. The installer never creates buses or edits MIDI setup. IAC profile matching applies to the shared parent device; adding/removing buses can change existing MIDI endpoint IDs and affect other assignments.

```sh
.venv/bin/mainstage-mcp serve --bridge "$PWD/build/bridge" \
  --input 'MS Bridge Input' --output 'MS Bridge Output'
```

Run one server/bridge owner for a bus pair. After a helper crash or an unrelated topology notification, `mainstage_reconnect` can recover explicitly if the pinned endpoints are unchanged. If an endpoint identity changed, the tool refuses to retarget; inspect `doctor` and assignments before deliberately restarting the whole server.

See [validation](docs/VALIDATION.md) for tested behavior, [the roadmap](docs/ROADMAP.md) for integration gates, and [the bridge contract](CONTRACT.md) for protocol boundaries. Original project source is under the [MIT license](LICENSE).
