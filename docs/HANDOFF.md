# Agent handoff

Updated 2026-09-19. Start here on another machine; no previous conversation or original workspace is required for the supported code or the preserved driver experiment.

## Goal and current state

Build a useful, publicly reusable MainStage MCP integration, starting from investigation of Arturia's KeyLab integration. The current result is a **source-built developer alpha**, version 0.1.0a2: ten local stdio MCP tools, an original Lua profile, a Swift/CoreMIDI helper and a separate read-only concert-inspection CLI. There is no general MainStage object model, arbitrary concert editor, OSC server, or production-ready automatic MIDI setup.

Repository: https://github.com/jp7312/Mainstage-MCP. Initial published implementation commit: `43ec4f12fdf399a3aaf401c5ad929c99ddf965dd`. Its [first hosted CI run](https://github.com/jp7312/Mainstage-MCP/actions/runs/35414789330) passed. No binary distribution, PyPI upload, signing or notarization was performed. The original source ZIP describes that initial release; subsequent handoff documentation and experimental driver sources are in Git.

## Reading order and code map

1. [README](../README.md): actual tool surface and limitations.
2. [Setup](SETUP.md): build, manual IAC preparation, owned profile install/remove, doctor and MCP-client setup.
3. [Validation](VALIDATION.md): completed evidence versus unexecuted acceptance recipes.
4. [Research findings](RESEARCH.md): original artifacts, discovery mechanics, failed approaches and other host interfaces.
5. [Bridge contract](../CONTRACT.md): framing, freshness, correlation, capabilities and mutation semantics.
6. [Roadmap](ROADMAP.md) and [parameter probe](PARAMETER_PROBE.md): remaining gates.
7. [Isolated-driver experiment](../experiments/isolated-driver/README.md) and its [discovery report](../experiments/isolated-driver/REPORT.md): original source and runnable isolated checks, outside the supported package and default build.

| File | Responsibility |
| --- | --- |
| `src/mainstage_mcp/server.py` | Official MCP SDK integration, state reducer, helper lifecycle, validation and ten tools |
| `src/mainstage_mcp/profile.lua` | MainStage callbacks, cached snapshots, versioned SysEx, declared controls |
| `native/bridge.swift` | CoreMIDI identity/routing, parser, JSONL commands and send receipts |
| `src/mainstage_mcp/installation.py` | Profile rendering, conflict/ownership checks, reversible installation |
| `src/mainstage_mcp/concert_inspector.py` | Bounded read-only parser for one observed private concert format |
| `src/mainstage_mcp/__main__.py` | CLI entry point |
| `tests/` | Python regression tests, Lua harness and opt-in live smoke client |

## Reproduce before changing anything

On macOS with Xcode Command Line Tools, Python 3.11+ and Lua:

```sh
git clone https://github.com/jp7312/Mainstage-MCP.git
cd Mainstage-MCP
make bootstrap
make build
make test
# Optional local CoreMIDI-only check; does not prove MainStage behavior:
make loopback
```

The integrated alpha passed 35 Python tests, Lua checks, native self-test and local loopback; hosted CI runs the offline checks. Live testing was on macOS 26.4 (25E246), arm64, MainStage 4.3.1 (5233), Python 3.13 and MCP SDK 2.2.0. The workflow uses Python 3.11 on `macos-latest`; that is build/test evidence, not a second live MainStage configuration. Consult the run logs for its exact runner version.

Live setup is deliberately manual: follow SETUP.md, use two dedicated IAC buses, install the owned profile, fully relaunch MainStage and open one disposable concert. Begin with `tests/live_check.py` without `--program`, which only reads/refreshes state. Never reuse endpoint IDs, profile manifests or machine paths from someone else's logs. `doctor` and `build/bridge --list` discover the local route.

## Non-negotiable behavior established by tests

- `sent` is a CoreMIDI receipt, not MainStage action completion. Toggle tools always report `observed: false`; a matching selected program is weaker than proving command causality. Bank feedback is unavailable.
- Guard mutations with fresh opaque session/revision. These are preflight checks, not an atomic host transaction or durable concert identity. Only one concert/server owner is supported.
- Never automatically retry an ambiguous mutation, press/release or partial bank sequence. Keep partial delivery visible and require explicit reconciliation.
- Commit snapshots atomically; keep the last complete snapshot explicitly stale during failure. A refresh proves responsiveness, not exhaustive rename notifications.
- Reconnect only the owned helper, retain its in-process route-identity pin and invalidate old client sessions. Same-name endpoints are not necessarily the same device. Starting a new server creates a new pin.
- Keep default-off features default-off until their individual live gates pass. Callback replay cannot count as new parameter feedback.
- Preserve other MIDI profiles and configuration. Installer ownership is recorded and checked; it never creates IAC buses. No vendor code is shipped.

## Best next experiments

| Priority | Experiment | Acceptance / stop condition |
| --- | --- | --- |
| 1 | Reproduce setup and read-only refresh on another Mac | Record exact OS/MainStage versions, local endpoint identities and profile handshake; no compatibility claim before this passes |
| 2 | Establish one explicit CC90 screen-control mapping | Follow PARAMETER_PROBE.md; require a genuinely newer callback, UI effect and patch-change invalidation; a timeout or capability advertisement is insufficient |
| 3 | Complete recovery matrix | MainStage quit/relaunch, actual helper crash, unrelated topology change, changed-route refusal and sleep/wake; never replay commands |
| 4 | Validate panic separately | Controlled sustained-note scenario plus independent evidence and other-bus isolation; silence/receipt alone is insufficient |
| 5 | Resolve isolated-driver discovery | Build isolated checks, then controlled installation and a fresh login session; inspect loading/trust evidence before changing lifecycle code |
| 6 | Expand offline-inspector evidence | New disposable concert with a distinctive Audio Unit and explicit bus route; compare saved copies, UI and manually exported Plug-In Info; do not parse/edit opaque state yet |

Do not spend another round renaming standalone virtual ports, treating callback indices as assignments, blindly adding navigation action strings, or repeating MIDI rescans as proof of new driver loading. RESEARCH.md records why those approaches failed or remain unsupported. Richer editing through Logic Remote is a separate unexplored research track, not an unfinished function in this MCP.

## Original-machine cleanup and portability

Both live test rounds ended with the owned profile and temporary buses removed, diagnostic preference restored/absent, existing endpoint names and IDs compared with the recorded baseline, and test processes stopped. No driver remains installed from these tests. This describes the original machine, not a state check on a future machine.

Excluded intentionally: proprietary Arturia/Apple scripts, app binaries/disassembly, personal concerts, raw device IDs/logs, local manifests, credentials and generated binaries. Official source URLs and hashes are in RESEARCH.md; obtain vendor artifacts independently. Recreate the synthetic concert through MainStage using VALIDATION.md rather than transferring personal data. The isolated-driver source and its tests are now included so that investigation does not depend on the original local workspace.

Temporary live-test permissions from the original conversation are not permission to log out, restart global MIDI services or alter another machine. A future operator must arrange those disruptive driver experiments explicitly. No fresh-login driver test was completed.

## Suggested prompt for a successor

> Read docs/HANDOFF.md, docs/RESEARCH.md and docs/VALIDATION.md. Reproduce the offline checks, then select one bounded unfinished experiment. Preserve the distinction between source evidence, synthetic tests and live effects. Keep experimental capabilities disabled until their acceptance recipe passes. Update validation with exact versions, observations and cleanup; do not infer a general MainStage API from a working MIDI command.
