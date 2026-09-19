# MainStage and Arturia research findings

Investigation: 2026-09-18; consolidated 2026-09-19. Application inspected: MainStage 4.3.1 (5233), arm64. This records historical observations, not a claim about future builds. Current implementation/effect evidence takes precedence over early hypotheses; see [validation](VALIDATION.md). Vendor code and binary excerpts are intentionally not reproduced here.

## Original Arturia artifact

- [KeyLab mk3 resources](https://www.arturia.com/products/hybrid-synths/keylab-mk3/resources)
- [MainStage integration ZIP](https://dl.arturia.net/products/keylab-49-mk3/daw-integration-script/KeyLab_mk3_Mainstage_Daw_Integration_Script_1_1_0.zip)
- [MainStage integration guide](https://dl.arturia.net/products/keylab-49-mk3/daw-integration-guide/KeyLab_mk3_MainStage_Daw_Integration_Guide_1_0_EN.pdf)
- [Installation FAQ](https://support.arturia.com/hc/en-us/articles/15637916754204-KeyLab-mk3-DAW-Integration)

The resource listing identified 1.1.0 (19 May 2026), but the extracted directory said 1_0_0, the Lua header 1.4, and `controller_info().version` 1.0. These are not interchangeable version identities. Recorded SHA-256:

```text
ZIP:       23e190033f81ebf17fefdf943c2f6882df4568b8058cb11582039b61abf8bb44
49 config: c75258ca14b187fe35614f3155466b56fbbfd9721bb3dc7483edb2f6b49a05c9
```

The ZIP contained a guide and disk image. Read-only extraction exposed 49/61/88 `.device/config.lua` files (1,521 lines each, differing in model/header) and an installer app. Readable Lua was sufficient; no installer execution was needed. Arturia's documented user root is `~/Music/Audio Music Apps/MIDI Device Profiles/Arturia/`; its guide warns against simultaneously installing its MainStage and Logic integrations.

The profile contains no OSC, HTTP, socket or MCP implementation. It translates host callbacks to hardware MIDI/SysEx. Its instrument deep-edit behavior is vendor-specific evidence, not a general third-party Audio Unit API.

## Lua interface recovered from profiles

MainStage supplies `controller_select_patch(programchangenumber, patchname, setname, concertname, patchlist, currentSetIndex, currentPatchIndex)`. List entries include `IsPatch`, `Label`, `SetIndex`; Apple's VAX77 additionally uses `PatchIndex`. Source conventions are zero-based selection indices and a one-based Lua list. They do not imply MIDI assignments or durable identities.

Other relevant declarations/callbacks are `controller_info`, `controller_initialize(applicationName, deviceNewlyDetected)`, `controller_finalize`, `controller_midi_in(midiEvent, portName)`, `controller_midi_out(midiEvent, name, valueString, color)`, `controller_select_patch_done`, `controller_timer_trigger` and `settriggertimer(ms)`. Incoming event bytes begin at index 0. The downloaded mk3 profile does not implement `controller_midi_out`; older bundled Arturia and Axiom Pro profiles supplied that evidence. Exact callback ordering is not generally established.

Installed primary-source examples can be inspected locally under:

```text
/Applications/MainStage.app/Contents/Frameworks/MACore.framework/Versions/A/Resources/MIDI Device Scripts/
```

Useful profiles: `Infinite Response/VAX77.device`, `VAXMIDI.device`, `Arturia/KeyLab 49.device`, `M-Audio/Axiom Pro 49.device`, `KORG INC/KONTROL49.device`, and `EDIROL/PCR.device`. They demonstrate return-value MIDI, explicit `inport`/`outport`, pass-through and deferred callbacks. These are shipped private interface examples, not a stable public SDK promise.

## Arturia display and controls

The hardware envelope is `F0 00 20 6B 7F 42 <payload> F7`. Source-observed payload prefixes: `00 02 05` connection, `00 02 09` touch reporting, `00 02 04` display. Display operations 0x31/32/33/34 implement list setup/refresh, append/position, prepend and current-patch heading. Five visible rows and 31-byte labels are display choices, not host-list limits. The bridge uses its own protocol instead.

An offline restricted Lua replay established that the original profile suppresses repeated `(program,set,patch)` triples, suppresses rename-only changes to those same indices, and can suppress the initial `(0,0,0)` selection because its cache starts at zero. These are function-input observations, not proof of host callback scheduling. UTF-8 truncation/high-bit bytes were not hardware-tested. The replay on Lua 5.5 needed an in-memory compatibility adjustment to an unused debugging helper that assigns a loop variable; originals were unchanged.

Source-declared Arturia controls on its DAW port:

| Control | Decimal CC / declaration |
| --- | --- |
| Knobs 1–8 | 91, 92, 94, 95, 96, 97, 102, 103; Relative2C |
| Faders 1–8 / master | 105–112 / 113; absolute |
| Previous/next patch / set | 49/50 / 51/52; reserved item names |
| Metronome / panic / play-stop | 27 / 20 / 21; Metronome / PanicFull / PlayStop |
| Record / tap tempo / undo / redo | 22 / 23 / 43 / 44; Record / TapTempo / Undo / Redo |

These are Arturia declarations, not this project's bindings or a verified general action registry. A marketed stop button is declared PanicFull. This project reserves channel 16 CC80, optional CC85–87, and optional CC90; consult CONTRACT.md.

## Profile discovery: failed approaches and working route

**Standalone virtual endpoints failed.** Matching endpoint names, Manufacturer and Model did not initialize the profile. Bounded MainStageCore inspection found that this discovery path resolves an endpoint's UniqueID by scanning `MIDIGetDevice` devices and their entities/endpoints. Entity-less app-created endpoints do not acquire the internal device object needed for Lua initialization. Adding an unrelated external-device description does not satisfy that scan. This was observed in this build; ordinary MIDI transport through virtual endpoints is a separate issue.

**Driver-backed IAC succeeded.** Generic loading tries manufacturer + device name, then manufacturer + model name, searching `.device/config.lua` roots. It normalizes trailing periods/whitespace: raw `Apple Inc.` became directory `Apple Inc`. The model was localized (`Sterownik IAC` on the original machine); do not hard-code that name elsewhere. Prefer local metadata and the installer. Driver ownership is `com.apple.AppleMIDIIACDriver` and is inherited by child endpoints; it is stronger evidence than a localized label.

MainStage groups all IAC entities under the parent driver device. Profile-wide flags can therefore affect unrelated buses. Explicitly scope declared controls and callback routes; test the opposite bus as well as the intended bus. Use the concert's Program Changes device/channel settings for assignment-based selection. An External Instrument channel strip is not a prerequisite for host patch selection.

**Rescan was insufficient to reliably restore profile feedback.** A full MainStage quit/relaunch with one disposable concert restored it. Do not infer multiple-concert or hot-reload support from a single successful refresh. Endpoint creation and setting metadata are separate operations, so complete setup before launching MainStage.

The preference domain is `com.apple.mainstage3`; `LUA_DEBUG` levels found in shipped code are 0 off, 1 errors, 2 verbose, 3 calls, 4 active sense, 5 dumps. Debug output includes script-search/loading messages through NSLog/unified logging. Preserve and restore the prior preference if using it; it is not needed for normal operation. Binary loader implementation was in LogicMainStage, while scripts were in MACore resources. No binary patches were made.

Useful symbol search anchors for another installed build are `WsLuaDeviceInitialize`, `WsLuaCleanupCoreMIDINames` and `WsMIDIHasPatchSelector`; old instruction addresses are build-specific. Connected external-endpoint metadata via `kMIDIPropertyConnectionUniqueID` was another source-observed lead: associating an external device description with an already driver-backed endpoint might alter profile matching. That route was not validated and does not remove the initial driver-device requirement.

For reproducible reinspection of the **same 4.3.1 (5233) arm64 build**, the original notes recorded these disassembler addresses (not portable offsets or patch instructions):

| Image | Address / observation |
| --- | --- |
| LogicMainStage | `0x810a48`: WsLuaDeviceInitialize; `0x811538`: generic device/model match; `0x811890`: profile-directory search |
| LogicMainStage | `0x807cd8`: root enumeration; `0x812e90`: name cleanup, with period trimming at `0x81303c–0x813094` and whitespace trimming at `0x813098–0x8130d4` |
| MainStageCore | `0x6c998–0x6c9ac`: endpoint/device gate calling device-scan helper `0x65fd8`; `0x6ce60`: missing-object skip; `0x6cfd8`: Lua initialization call |
| MainStageCore | `0x6dd74`: metadata helper; `0x6de70`: connection UniqueID inspection; `0x6dfdc`: connected-object resolution |

Reacquire the application independently and verify its build before interpreting these. Raw disassembly is not distributed.

## Patch selector: why it is excluded

Bundled VAX profiles declare `patchselector = true`. Early inference from comments suggested a channel-16 CC32/CC0/Program Change 127 sequence; that failed. Bounded inspection of this build instead found a full status-byte comparison with `0xB0` (channel 1), CC0 storing a set value, and CC32 triggering selection. The selection branch did not require Program Change. `controller_midi_in` runs before it and can consume an event.

A corrected channel-1 CC0 → CC32 experiment reached a patch inside a set, but did not reliably address an orphan patch with callback indices. The flag belongs to the shared IAC parent and has no per-port field. Consuming selector CCs on unrelated ports was only an isolation experiment, not a supported routing design; it could also intercept legitimate traffic. The release has no `patchselector` and no select-by-name/index tool.

The same-build inspection trail was MainStageCore `0x67664–0x67770` for the flag predicate, `0x69a20–0x69a54` for callback-before-selector ordering, `0x69a58–0x69a60` for status `0xB0`, `0x69a64–0x69a6c` / `0x69d98–0x69da8` for CC0 storage, and `0x69a70–0x69b60` / `0x69dac–0x69e04` for the CC32 trigger. Bounds checks around `0x69ad0–0x69b5c` used values directly without a +1 conversion. The failed initial trial was `BF 20 00`, `BF 00 01`, `CF 7F`; the revised proposed set-1/patch-0 trial was `B0 00 01` then `B0 20 00`. These are historical experiment bytes, **not a supported selection recipe**; orphan addressing remained unreliable.

Do not confuse that private selector experiment with the released standard Bank Select tool: the latter sends CC0 → CC32 → Program Change on an explicit channel against user-assigned bank/program triples. Two patches with the same program and different banks passed live. Callback state exposes no selected bank, so the complete target remains unobserved by the API.

Reserved-name PreviousPatch and explicit `action_mainstage='PreviousSet'` also produced live no-ops despite relevant action strings existing. No loader/cache/version workaround was established; navigation names were removed. Metronome, master mute and play/stop did produce visible effects and reverse transitions with other-bus isolation. Panic remains unverified.

## IAC identities and the isolated driver

Adding/removing temporary buses in Audio MIDI Setup regenerated existing endpoint IDs on the original machine. Those IDs were restored and checked during cleanup, but modifying another driver's endpoint identities is not a portable installation method. The released installer neither creates buses nor edits MIDI preferences.

The inspected CoreMIDI SDK restricts non-driver `MIDIDeviceAddEntity` use to external devices; `MIDISetupAddDevice` is driver-only. Configuration-editor endpoint-count APIs do not provide a documented way to append an IAC bus while guaranteeing existing identities. Do not replace manual setup with unsupported calls against Apple's driver.

The preserved [isolated driver](../experiments/isolated-driver/README.md) exposes an independently owned two-bus device in original C source. Universal compilation, signature verification, mock routing/lifecycle and real CFPlugIn factory/v2 QueryInterface checks passed. The bundle check never calls Start or creates a MIDI client. A prior ad-hoc-signed user-directory installation was not discovered after rescan/MIDIRestart and was removed. This is a failed discovery test, not proof of a code-signing or ABI defect.

Apple's [SampleUSBMIDIDriver](https://developer.apple.com/library/archive/samplecode/SampleUSBMIDIDriver/) supports the factory type/v2 interface approach used here. [MIDIRestart](https://developer.apple.com/documentation/coremidi/midirestart()) rescans loaded drivers; it does not promise hot discovery of newly copied bundles. The next live gate is installation followed by a **fresh login session**, then device-owner enumeration. If discovery still fails, collect loading/trust diagnostics and compare a local development signature before changing driver lifecycle logic. No logout or fresh-login test was performed. Signing/notarization, persisted-device cleanup, stable IDs and clean-machine removal remain unproven. The provisional bundle identifier is not a publisher-ownership claim.

## Parameter feedback: evidence versus promise

Bundled Lua profiles and [Apple controller feedback documentation](https://support.apple.com/guide/logicpro/expert-view-parameters-ctls71c30e80/mac) support screen-control values scaled to 0–127 with optional labels/display text. The Logic documentation also warns that feedback is unavailable on IAC; that is a material question for this MainStage route, not proof that MainStage is exempt.

The metronome experiment produced unrelated action labels rather than reliable action-state feedback. Labels/display strings must remain opaque; they are not parameter identity or engineering units. The explicit CC90 probe counts only new `controller_midi_out` callbacks; a refresh replay retains sequence and observation time.

No mapping was established during the GUI attempt: the layout canvas could not be manipulated successfully through the available accessibility automation (`AXError.noValue`). This is an automation limitation, not proof that manual mapping is impossible. An unmapped write timed out honestly and recovered on refresh. Next: manually establish one screen control and follow [PARAMETER_PROBE.md](PARAMETER_PROBE.md). Keep the capability default-off until then.

## Other host interfaces and offline format

| Interface | Established evidence | What remains unestablished |
| --- | --- | --- |
| Screen-control mappings | MIDI values/actions and Send Value to feedback | Full object/parameter enumeration |
| Scripter | JavaScript MIDI event processing and plug-in controls | Node-style sockets, concert editing or arbitrary host object API |
| AppleScript actions | MainStage invokes scripts with context; bundled Music scripts read concert name | Incoming rich MainStage scripting dictionary |
| Logic Remote | Apple's app can create/select patches, choose settings, control mixer/Smart Controls | Public third-party protocol/SDK; no protocol capture or implementation attempted |
| OSC | Release notes mention TouchOSC feedback; TouchOSC also supports MIDI | Native general MainStage OSC server/address API |
| Concert packages | Saved plist hierarchy, names, assignments and routes can be read | Safe writer or full opaque plug-in-state interpretation |

No `.sdef`, `.scriptSuite` or `.scriptTerminology` was found in the inspected MainStage bundle; scripting-definition plist keys were absent and `sdef` failed with -10827. This is supporting negative evidence, not proof that all Apple Events are impossible. `exportPlugInInfo:` and an Export Plug-In Info UI label are private/UI implementation details, not an external insertion API. No relevant NativeProvidedInterfaces declaration was found in the installed bundles checked.

The read-only inspector accepts only observed document version 57057 / patch version 40014. It reads hierarchy, PC/bank assignments, channel names/UUIDs and raw bus routes, and reports opaque `.cst` filenames. `.cst` containers carried an OCuA marker; their contents are not parsed. Limits include 2 MiB per plist, 64 MiB total, 4,096 hierarchy nodes, 16,384 channels and depth 32, plus path/symlink/input validation. Synthetic fixtures and comparison with a disposable saved concert passed; private-format compatibility or editing safety did not follow from that. It remains a CLI, not an arbitrary-path MCP tool.

Relevant primary references for continued research:

- [MainStage user guide](https://help.apple.com/pdf/mainstage/en_US/mainstage-user-guide.pdf)
- [MainStage release notes](https://support.apple.com/101568) (including version-specific SysEx/controller fixes)
- [Apple MIDI Device Scripts/control surfaces](https://support.apple.com/guide/logicpro/ctls718dd5b2/mac) (Logic documentation is not automatically MainStage behavior)
- [Scripter API](https://support.apple.com/guide/logicpro/lgce3905a48c/mac)
- [Logic Remote for MainStage](https://support.apple.com/guide/logicremote-mainstage-ipad/chsff8c943/ipados)
- [Hexler Logic OSC setup](https://hexler.net/touchosc/manual/setup-logic) (documents Logic, not equivalent MainStage support)
- [CoreMIDI services](https://developer.apple.com/documentation/coremidi/midi-services), installed SDK `MIDISetup.h`, `MIDIServices.h`, `MIDIDriver.h`

## Prototype mistakes already corrected in the release

The first throwaway adapter hand-implemented an older MCP dispatcher, could retain misleading connected state after MainStage quit, mixed selection/list generations, and lacked machine-readable native send receipts. It was replaced by the pinned official SDK and the current bounded protocol, atomic snapshots, distinct transport/responsiveness/staleness fields, correlated requests/deadlines, send results and conservative observation rules. Do not resurrect the earlier prototype as a simpler implementation. Its defects explain the current contract; regression coverage belongs with changes to that contract.

OSC/HTTP wrappers could be added outside MainStage for a concrete client need, but they would expose the same demonstrated host capabilities. Neither an MCP wrapper nor another network protocol creates an editing API that has not been found.
