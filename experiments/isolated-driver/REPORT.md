# Isolated transport discovery report

## Finding

No source, bundle-registration, exported-symbol, or COM ABI defect is currently demonstrated. Apple's installed SDK `CoreMIDI/MIDIDriver.h` documents `~/Library/Audio/MIDI Drivers` and the plist registration keys used here. Apple's original [SampleUSBMIDIDriver](https://developer.apple.com/library/archive/samplecode/SampleUSBMIDIDriver/) also accepts `kMIDIDriverTypeID` in its factory and selects v2 through QueryInterface, matching this implementation.

The new bundle-level self-check proves that the built plugin can be found by its registered driver type, can resolve and call `MainStageMCPDriverFactory`, and returns a v2 interface with a valid release lifecycle. It runs in the test process and deliberately does not call `Start`, create a MIDI client, or alter MIDI setup. The existing mock checks still prove only routing and lifecycle logic with substituted CoreMIDI calls.

The prior live result remains: an ad-hoc-signed bundle copied into the documented user driver directory was not discovered after Audio MIDI Setup rescan or [`MIDIRestart`](https://developer.apple.com/documentation/coremidi/midirestart()). Apple's SDK describes `MIDIRestart` as asking already loaded drivers to rescan hardware; it does not promise to enumerate newly installed plugin bundles. Therefore that result does not yet distinguish a startup-only discovery boundary from host trust rejection.

The bundle passes strict code-signature verification but is ad-hoc signed and is not notarized. MIDIServer on this machine carries `com.apple.security.cs.disable-library-validation`, so its signature does not by itself prove that ad-hoc plugins are rejected. Apple documents [Developer ID](https://developer.apple.com/developer-id/) signing and [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) for distribution; a local development-certificate build is a diagnostic comparison, not release evidence.

## Exact next live gate

1. Save work, quit MainStage and other MIDI clients, and snapshot every existing device/entity/endpoint unique ID and owner.
2. Build and verify the plugin; install the one bundle at `~/Library/Audio/MIDI Drivers/MainStageMCP.plugin` without modifying IAC or MIDI preferences.
3. With the bundle present, log out and back in so the per-user MIDIServer LaunchAgent starts fresh. Do not treat another `MIDIRestart` as a plugin-discovery test.
4. Verify exactly one new device owned by `org.mainstage-mcp.driver`, with the expected two entities and four endpoints, before testing bytes or MainStage.
5. If absent, collect MIDIServer loading/trust diagnostics from that fresh login and repeat once with a local Apple Development signature. Do not change driver lifecycle code without a concrete load or `Start` error.
6. Only after discovery, test independent two-way PC/CC/SysEx transport, simultaneous clients, MainStage profile matching, restart persistence, stable new and pre-existing IDs, and controlled removal.

A login-session restart is the narrow next requirement because MIDIServer is a per-user LaunchAgent. No Apple source reviewed here promises hot loading of a newly copied CFPlugIn. A full machine restart is not yet justified; use it only if a fresh login still leaves an ambiguous lifecycle state.

## Installation tooling decision

No automatic installer/uninstaller was added. Safe file ownership is tractable, but actual load/unload behavior and persisted-device cleanup are not yet known. Removing the bundle cannot promise to unload code or retire its device, so exposing automation before the live lifecycle gate would overstate safety. The guarded `remove_device` helper remains source-only and refuses online or mismatched devices.
