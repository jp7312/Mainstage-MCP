# Isolated CoreMIDI driver spike

Original C implementation of a user-space CoreMIDI CFPlugIn, interface version 2. No borrowed vendor driver code, DriverKit, kernel extension, custom IPC, MIDI monitor, or background process. Run `sh build.sh` here to build and ad-hoc sign a universal arm64/x86_64 macOS 11+ bundle and run the isolated routing check. This does **not** install or load the driver.

`SIGN_IDENTITY='identity name' sh build.sh` selects a local development signing identity for a controlled test; the default remains ad-hoc signing. The build never installs the bundle.

The factory accepts the registered `kMIDIDriverTypeID`, matching Apple's SampleUSBMIDIDriver. MIDIServer then requests version 2 through QueryInterface. The bundle self-check loads the built CFPlugIn in the test process and verifies its plist registration, exported factory, version-2 QueryInterface behavior, and release lifecycle without calling `Start` or changing MIDI setup.

Identity is `org.mainstage-mcp.driver`, manufacturer `MainStage MCP`, model/device name `MainStage MCP Bridge`. The bundle factory UUID is `612823DD-EEF0-4D1A-96C6-275238588734`.

| Entity / source / destination name | Routing |
| --- | --- |
| MainStage MCP Input | Writes to this destination arrive at this source (bridge commands to MainStage). |
| MainStage MCP Output | Writes to this destination arrive at this source (MainStage feedback to bridge). |

Each destination forwards only to its corresponding source using `MIDIReceived`; CoreMIDI handles distribution to clients. MainStage's profile must ignore the feedback bus to prevent feedback. This is MIDI 1.0, suitable for the existing PC/CC/SysEx protocol. Version 3/MIDI 2.0 is deliberately not advertised.

`Start` reuses the sole persisted device provided by CoreMIDI and never replaces entities or unique IDs. Unexpected device counts, identity, or topology fail closed. First start creates only its own device. `Stop` disables sends and marks its own device offline, retaining topology and IDs. Destination refCons are restored on every `Start`. Scheduling is delegated to CoreMIDI with advance time zero; the driver has no queues or realtime allocations.

## Evidence and lifecycle limits

Apple's current Xcode SDK `CoreMIDI/MIDIDriver.h` documents the supported user installation location as `~/Library/Audio/MIDI Drivers`, the CFPlugIn plist fields, the v2 lifecycle, CoreMIDI framework linking, and which calls are permitted on the MIDI server thread. [Apple's driver interface reference](https://developer.apple.com/documentation/coremidi/mididriverinterface) lists the callbacks.

An installer can copy this one bundle into that user directory after checking for an existing bundle with conflicting contents, and uninstall only that owned bundle. Driver loading into MIDIServer, restart behavior, persisted-device handling after removal, and MainStage discovery still require live verification. Merely removing the file does not guarantee an already-loaded driver unloads or its persisted MIDI device disappears. Do not edit MIDI preferences directly to clean it up.

For controlled cleanup, `build/remove_device --check` performs a read-only preflight. After the bundle is removed and its device is offline, `build/remove_device --remove-offline-owned-device` explicitly removes that one device through the documented configuration-editor API. It refuses online devices, duplicate owned devices, unexpected manufacturer/model, unexpected two-bus topology/names, or missing unique IDs. It neither stops MIDI nor writes preferences. Its MIDI client initialization can cause ordinary CoreMIDI initialization; if the installed driver makes the device online, the helper refuses removal. Unload/restart coordination therefore remains a prerequisite, not something this helper guarantees. Do not run concurrently with reinstalls or other MIDI setup changes: CoreMIDI does not provide a compare-and-remove transaction.

Apple documents [`MIDISetupRemoveDevice`](https://developer.apple.com/documentation/coremidi/midisetupremovedevice(_:)) for configuration editors removing offline devices explicitly declared permanently missing; it is **not** restricted to drivers. `MIDIDeviceDispose` documentation also directs drivers to use it for already-added devices. Thus removal of this explicitly retired offline device uses the published API, not an undocumented client call. There is no guarantee that removal unloads driver code. No preference-based uninstall switch was added.

[`MIDIRestart`](https://developer.apple.com/documentation/coremidi/midirestart()) stops/restarts MIDI I/O and asks drivers to rescan. Its documentation does not promise loading newly copied plug-ins or unloading deleted ones. A global MIDI restart interrupts other users of MIDI; do not perform one silently or kill MIDIServer as an installation shortcut. A logout/reboot is a conservative manual lifecycle test after all music sessions are saved.

Ad-hoc signing here is for local development, not a trusted public binary release. For public distribution, use a real publisher-owned bundle identifier (finalize before users persist device IDs), Developer ID signing and a notarized distribution artifact. [Apple's Developer ID guidance](https://developer.apple.com/developer-id/) explicitly covers plug-ins; [the custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) covers packaging and submission. No signing identity or notarization credentials were requested or used here. The chosen `org.mainstage-mcp` identifier is provisional, not a claim to a registered domain.

## Verified here

- Both architectures compile with `-Wall -Wextra -Werror`; plist validates; ad-hoc signature passes strict verification.
- A bundle-level CFPlugIn self-check loads the built bundle and exercises its static registration, exported factory, requested version-2 interface, QueryInterface, and release path. It does not call `Start` or create a MIDI client.
- An isolated C self-check exercises COM query/refcount behavior, independent routing of both buses, invalid bus/null input rejection, propagation of delivery status, and blocked sends after stop. It mocks `MIDIReceived`, never creates a MIDI client, and never mutates MIDI setup.
- A second mock lifecycle check exercises first creation, reuse without new entities, refCons, offline transition, failed creation cleanup, rejection of unexpected owned devices/models, online removal refusal, read-only preflight, explicit offline removal, and already-absent cleanup. All exercised MIDI functions/properties are local test substitutes. The real removal helper was compiled but never executed here.
- The checks above did not install the bundle or test driver loading, CoreMIDI topology creation, restart persistence, full SysEx transport, or MainStage profile discovery. A separate earlier live installation attempt failed discovery, as recorded below; successful live operation remains unproven.

The previous live install did not discover the driver. Source inspection has not identified a metadata, export, or ABI mismatch: the plist matches Apple's documented static registration shape, the factory symbol is exported, and the bundle-level self-check exercises factory creation and version-2 QueryInterface successfully. These checks do not establish that MIDIServer will load an ad-hoc-signed user driver on the tested macOS release. The next live gate is a controlled install with all music applications closed, followed by a normal login-session restart if the running MIDIServer does not load a newly copied plug-in. `MIDIRestart` only rescans drivers and is not documented to discover newly installed bundles. If a login-session restart still produces no owned device, collect MIDIServer trust/loading diagnostics and test a development-certificate signature before changing driver lifecycle code.

## Minimum live acceptance

Snapshot every existing device/entity/endpoint unique ID and owner; install the driver; verify exactly one new `MIDIGetDevice` device under the expected owner with two entities and four endpoints. Verify both loopbacks, PC/CC plus multi-packet SysEx, simultaneous clients, and MainStage profile initialization under `MIDI Device Profiles/MainStage MCP/MainStage MCP Bridge.device/config.lua`. Restart normally and ensure all seven new IDs and all existing IDs are unchanged. Validate safe uninstall/reinstall and quarantine/notarization behavior on a clean user account before calling the transport releasable.
