# Developer setup

## Prepare the source build

Use macOS with MainStage, Xcode Command Line Tools (`swiftc`), Python 3.11 or newer, and Lua. From the repository root:

```sh
make bootstrap
make build
make test
```

`make test` runs native parser checks, a Lua profile harness, and Python tests. These use synthetic input; passing does not validate MainStage or your MIDI routing. `make loopback` separately exercises CoreMIDI loopback when services are available, without establishing MainStage compatibility.

## Prepare dedicated IAC buses manually

Save your open concerts and quit MainStage before installing the profile. In Audio MIDI Setup, show MIDI Studio and inspect the IAC Driver. The default configuration requires two distinct, online buses named exactly:

- `MS Bridge Input`: bridge commands sent to MainStage.
- `MS Bridge Output`: MainStage profile feedback sent to the bridge.

The installer requires these buses to already exist on the same Apple IAC parent device and refuses ambiguous names. It never creates, renames, enables, removes, or repairs MIDI devices.

**Existing rigs:** adding or removing IAC buses has been observed to regenerate existing endpoint IDs, potentially breaking assignments in other applications. Record your existing MIDI configuration and assignments before changing it, and verify them afterward. Dedicated bus names do not isolate the parent device: MainStage loads the profile for the shared IAC manufacturer/model. The supplied profile reserves CC80 on channel 16 for metronome toggle, scoped to the configured input bus; coexistence with every other IAC workflow is not established. An existing profile for that parent is a conflict; do not overwrite it to force installation.

## Install and inspect

From the repository root:

```sh
.venv/bin/mainstage-mcp install --bridge "$PWD/build/bridge" \
  --input 'MS Bridge Input' --output 'MS Bridge Output'
.venv/bin/mainstage-mcp doctor --bridge "$PWD/build/bridge" \
  --input 'MS Bridge Input' --output 'MS Bridge Output'
```

Installation discovers the localized IAC manufacturer/model and writes one `config.lua` beneath `~/Music/Audio Music Apps/MIDI Device Profiles`. It records ownership, a checksum, and endpoint identities in `~/Library/Application Support/MainStage MCP/installation.json`. Repeating installation with unchanged configuration is a no-op. Conflicting profiles, changed owned files, or changed MIDI identities are errors, not permission to overwrite them.

Install and uninstall share a persistent empty `installation.json.lock` file. Lock ownership is released when its process exits; file existence does not mean an operation is running. Do not delete it; keeping one inode avoids unlink/reopen races.

For a disposable live action experiment only, add `--experimental-actions` to the install command. This additionally reserves channel-16 CC85–87 and advertises panic, master mute and play/stop. The group remains disabled by default: master mute and play/stop have narrow binding-level evidence, while panic remains unverified. Navigation actions are not exposed after live no-op results. Switching modes requires uninstalling the owned profile first; the installer refuses to rewrite it in place.

`doctor` performs read-only static checks. Its `runtime_handshake_tested: false` explicitly means it has not established a live MainStage connection. Diagnostics may include local paths/device metadata; review them before sharing.

After installation, fully quit and relaunch MainStage, then open only one disposable test concert and select a patch to generate initial feedback. A MIDI rescan alone did not restore profile feedback in the live check; a clean relaunch with one concert did. That check did not isolate profile reload from multiple-open-concert effects, so use one open concert for this alpha. Run the MCP server through your client and call `mainstage_refresh`. Inspect `host_responsive`, `stale`, `session`, `revision`, and selection/list contents. Test Program Changes only against known assignments in that test concert.

## Configure a stdio MCP client

Use this generic configuration shape, adapting the outer schema to your MCP host. Replace both `/absolute/path/to/mainstage-mcp` placeholders with the actual checkout path; JSON does not expand `$PWD` or `~`.

```json
{
  "mcpServers": {
    "mainstage": {
      "command": "/absolute/path/to/mainstage-mcp/.venv/bin/mainstage-mcp",
      "args": [
        "serve",
        "--bridge", "/absolute/path/to/mainstage-mcp/build/bridge",
        "--input", "MS Bridge Input",
        "--output", "MS Bridge Output"
      ]
    }
  }
}
```

The host launches the server over local stdio. Keep stdout reserved for MCP; logs belong on stderr. Use only one server/bridge owner per bus pair. The server accepts `--timeout` in seconds (default 3, maximum 30).

Read or refresh state first. Pass its opaque, connection-scoped `session` and `revision` as `expected_session` and `expected_revision` for each mutation. If context changes, inspect fresh state and explicitly choose again. A helper reconnect changes this session even when MainStage's internal profile session survives. MIDI program/control/value fields are 0–127; channels are 1–16. A successful transport send is not proof of a MainStage effect. Do not automatically retry a command whose send outcome is ambiguous.

## Map controls explicitly

For raw CC, assign the intended MIDI bus/channel/controller to a MainStage screen control and map that control to your desired parameter or action using MainStage's own interface. Verify the effect manually before invoking `mainstage_send_cc`. The raw-CC tool does not discover these mappings, enumerate plugin parameters, verify action completion, or return mapped-parameter feedback. The default profile supplies one preconfigured action: metronome toggle, using CC80 on channel 16 of the configured input bus. Reserve that binding and avoid sending it accidentally through raw CC.

`mainstage_toggle_metronome` requires the negotiated `metronome_toggle` capability and current session/revision. It sends CC80/channel 16 value 127 followed by 0. The binding was live-tested against the MainStage UI, but the tool always reports `observed: false`: no reliable on/off feedback or action confirmation exists. Never automatically retry a toggle after a timeout or partial send. Refresh state before another mutation.

`mainstage_trigger_action` also requires current context and the exact `action_*` capability advertised by the installed profile. It sends one press/release pair and always reports `observed: false`. The generic metronome name is supported by the default profile; all other names require `--experimental-actions`. Treat `sent: true` as CoreMIDI transport confirmation only.

For a disposable mapped-parameter experiment only, install with `--experimental-mapped-parameter`. This opts the profile into one channel-16 CC90 slot and the `mapped_parameter_1` capability; it remains off by default. Follow [the bounded live recipe](PARAMETER_PROBE.md). Capability means the slot is declared, not that a concert mapping or feedback route exists.

Both experimental flags may be supplied together. They are independently recorded in the ownership manifest, and changing either requires uninstalling the prior owned profile first.

Program Change uses the MIDI program assigned in your concert, not the position in `mainstage_list_patches`. Confirm numbering in MainStage against the API's 0–127 range; do not infer assignments from names or indices. `mainstage_select_program` sends no Bank Select messages. `mainstage_select_bank_program` sends explicit MSB/LSB/program values and passed one same-program/different-bank disposable-concert check, but it still cannot observe the selected bank or discover assignments.

## Inspect one concert offline

`mainstage-mcp inspect-concert PATH` is an experimental, read-only CLI for the exact observed MainStage 4.3.1 (5233) plist versions `Version=57057` and `VersionPatches=40014`. It reports bounded hierarchy, routes and assignment metadata while treating channel-setting and plug-in state as opaque. It rejects other versions and malformed XML plists as concert-format errors; CLI rejection exits 2 without a traceback. It is not exposed through MCP.

## Upgrade

Save your concerts, quit MainStage, and stop the MCP server. Use the currently installed version to run `.venv/bin/mainstage-mcp uninstall` before replacing the checkout or updating its profile. Confirm removal succeeded; modified owned files are preserved and require inspection. Then update the source, run `make bootstrap`, `make build` and `make test`, and repeat the install and doctor commands above. Relaunch MainStage with one test concert and verify a fresh handshake before resuming use.

The installer does not overwrite an existing owned profile as an upgrade mechanism. Changed owned files are refused; an intact recorded installation can remain a no-op even when the source template has changed. Uninstall/reinstall ensures the new profile and server agree. Do not delete the ownership manifest to bypass a conflict, and do not recreate IAC buses for a software upgrade.

## Recover and remove

If the native helper crashes or reports disconnected after an unrelated MIDI topology notification, call `mainstage_reconnect`. It restarts only the helper, requires the same persistent endpoint identities pinned when this MCP server started, requests one correlated refresh, and never replays a mutation. Its returned session is new; inspect the returned state and explicitly choose any later mutation again.

If reconnect reports that an endpoint identity changed, stop the MCP server, inspect `doctor`, MIDI configuration and assignments, then deliberately restart the server only after accepting the new configuration. A fresh server establishes a fresh in-memory route pin; the pin is not persisted across server processes. A MainStage restart can create a new profile context; refresh before choosing another mutation. Cached state may remain visible but explicitly stale when the host is unavailable.

Use one open concert for this alpha. Concert names, callback indices and Lua session tokens are not durable concert identities, and multiple open concerts with identical or switching contexts cannot be distinguished safely from available evidence.

To remove the owned profile, quit MainStage and run:

```sh
.venv/bin/mainstage-mcp uninstall
```

Removal deletes the recorded profile only if its checksum still matches, then removes the ownership manifest and empty owned profile directories. Modified files are preserved and reported. IAC buses and MIDI configuration remain untouched. If you remove the dedicated buses manually later, recheck other MIDI identities and assignments. Keep the checkout until uninstall has completed; afterward its virtual environment/build files can be removed with the checkout.
