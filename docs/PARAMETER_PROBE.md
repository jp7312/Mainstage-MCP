# Experimental mapped-parameter probe

This probe tests one explicit MainStage screen-control mapping on channel 16 CC90. It is disabled by default and does not enumerate plug-ins, parameters, mappings, or units.

## Evidence and boundary

MainStage 4.3.1 includes Lua device profiles whose `controller_midi_out(midiEvent, name, valueString, color)` callbacks receive controller values plus optional label/display strings. Apple's bundled Arturia KeyLab 49 profile routes selected CC feedback through this callback, and its Axiom Pro profile treats the label as display text. Those examples support the callback shape and a 7-bit CC value; they do not establish a stable parameter identifier or engineering unit.

Apple documents controller feedback as values scaled to 0–127, with optional label and value text for Lua-script devices. The same Logic control-surface documentation says feedback is unavailable on IAC, which is a material warning for this project's IAC transport rather than evidence that MainStage behaves differently. MainStage release notes separately document screen-control and controller-feedback behavior. Only the live test below can decide whether this exact MainStage/IAC/profile combination works:

- [Apple controller feedback parameters](https://support.apple.com/guide/logicpro/expert-view-parameters-ctls71c30e80/mac)
- [Apple MainStage release notes](https://support.apple.com/101568)

The callback's `name` and `valueString` have already produced unrelated action labels in a metronome experiment. This probe therefore treats them as opaque `label` and `display`, never as identity or parsed units. `rawUnit` is always `midi_7bit`; text such as `-3.0 dB` remains only display text.

## Current live status

The combined profile advertised the opt-in slot, but an explicit screen-control mapping could not be established in the tested MainStage layout. Sending unmapped value 32 returned `sent: true`, `observed: false`, and `timed_out: true`; cached state became explicitly stale. A later refresh was responsive and healthy with `mapped_parameter: null`. This confirms conservative timeout/recovery behavior only. It is not mapped-feedback evidence, so the feature remains experimental and default-off.

## Narrow live recipe

Use a disposable concert and preserve its original state. Install the profile with `--experimental-mapped-parameter`, then fully relaunch MainStage. This adds one declared device item, **MCP Parameter 1**, on the dedicated input/output buses at channel 16 CC90.

1. In Layout mode, assign one continuous screen control to MCP Parameter 1 and configure MainStage's value feedback to the dedicated output. In Edit mode, map that screen control to one obvious disposable parameter.
2. Refresh. Require `mapped_parameter_1` in capabilities. Capability alone is not success; `mapped_parameter` may still be absent.
3. Move the screen control in MainStage, the mapped plug-in control, and any assigned hardware control separately. Each accepted callback must increase `sequence`; record raw value, opaque label/display, selection revision, and whether all three change paths produce feedback.
4. Call `mainstage_set_mapped_parameter_1` once with a value different from the baseline. Success requires `sent: true`, `observed: true`, and a strictly newer callback with that raw value. Check the UI/plugin separately because this observation confirms the screen-control callback only.
5. Switch patches. The previous value must become stale because its selection revision no longer matches. It becomes current only after a new callback for the new revision.
6. Remove or disable the mapping and send once more. A transport receipt or replayed callback must end with `observed: false`; do not retry automatically.

Test values near 0, 64, and 127 and one mapped parameter whose display contains an obvious unit. Record the display verbatim, but do not convert or claim the unit. If IAC never produces a valid callback, keep the feature disabled and record that transport as the blocker.
