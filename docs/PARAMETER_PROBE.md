# Experimental mapped-parameter probe

This probe tests one explicit MainStage screen-control mapping on channel 16 CC90. It is disabled by default and does not enumerate plug-ins, parameters, mappings, or units.

## Evidence and boundary

MainStage 4.3.1 includes Lua device profiles whose `controller_midi_out(midiEvent, name, valueString, color)` callbacks receive controller values plus optional label/display strings. Apple's bundled Arturia KeyLab 49 profile routes selected CC feedback through this callback, and its Axiom Pro profile treats the label as display text. Those examples support the callback shape and a 7-bit CC value; they do not establish a stable parameter identifier or engineering unit.

Apple documents controller feedback as values scaled to 0–127, with optional label and value text for Lua-script devices. The same Logic control-surface documentation says feedback is unavailable on IAC, which is a material warning for this project's IAC transport rather than evidence that MainStage behaves differently. MainStage release notes separately document screen-control and controller-feedback behavior. Only the live test below can decide whether this exact MainStage/IAC/profile combination works:

- [Apple controller feedback parameters](https://support.apple.com/guide/logicpro/expert-view-parameters-ctls71c30e80/mac)
- [Apple MainStage release notes](https://support.apple.com/101568)

The callback's `name` and `valueString` have already produced unrelated action labels in a metronome experiment. This probe therefore treats them as opaque `label` and `display`, never as identity or parsed units. `rawUnit` is always `midi_7bit`; text such as `-3.0 dB` remains only display text.

## Current live status

On September 19, 2026, a disposable concert had an explicit Round Knob assigned to **MCP Parameter 1** on MS Bridge Input, channel 16, Absolute, CC90, with MIDI Through set to **Do not pass through** and **Send Value to** MS Bridge Output. The screen control was mapped to Classic Electric Piano > Volume. The opt-in profile advertised `mapped_parameter_1`, and refresh returned fresh responsive state, but `mapped_parameter` was initially `null`.

One raw `mainstage_send_cc` call sent CC90 value 32 on channel 16 and returned `sent: true`, `observed: false`, as required for the transport-only raw tool. MainStage accessibility state showed the knob change from `.7086614` / `+0.0 dB` to `.2519685` / `-18.0 dB`, and the channel-strip fader showed `-18 dB`. This is bounded evidence that the explicit mapping affected the MainStage UI. It is not mapped-feedback evidence or plug-in acknowledgement. The following refresh was healthy but still returned `mapped_parameter: null`.

The guarded one-shot probe below was then invoked with `--value 64`. It rejected the attempt before calling the setter because no current mapped-feedback baseline existed. Custom-knob drag and scroll failed with `AXError.noValue`, while accessibility value-setting reported that the knob was not settable, so manual screen-control, plug-in UI, and hardware-change callback paths remain untested. Callback observation, the guarded setter's UI effect, hardware feedback, and patch-invalidation gates are still pending. The feature remains experimental and default-off; the result does not establish that IAC feedback is categorically unavailable.

Separately, on September 18, 2026, the combined profile advertised the opt-in slot before an explicit screen-control mapping could be established. Sending unmapped value 32 returned `sent: true`, `observed: false`, and `timed_out: true`; cached state became explicitly stale. A later refresh was responsive and healthy with `mapped_parameter: null`. That historical attempt confirms conservative timeout/recovery behavior only.

## Narrow live recipe

Use a disposable concert and preserve its original state. Install the profile with `--experimental-mapped-parameter`, then fully relaunch MainStage. This adds one declared device item, **MCP Parameter 1**, on the dedicated input/output buses at channel 16 CC90.

1. In Layout mode, assign one continuous screen control to MCP Parameter 1 and configure MainStage's value feedback to the dedicated output. In Edit mode, map that screen control to one obvious disposable parameter.
2. Refresh. Require `mapped_parameter_1` in capabilities. Capability alone is not success; `mapped_parameter` may still be absent.
3. Move the screen control in MainStage, the mapped plug-in control, and any assigned hardware control separately. Each accepted callback must increase `sequence`; record raw value, opaque label/display, selection revision, and whether all three change paths produce feedback.
4. Run the guarded probe with a value different from the baseline:

   ```sh
   .venv/bin/python tests/live_parameter_check.py \
     --bridge build/bridge \
     --concert 'Exact disposable concert name' \
     --input 'MS Bridge Input' \
     --output 'MS Bridge Output' \
     --value 64
   ```

   The probe requires fresh responsive state, the exact concert name, the capability, and a current non-stale mapped-feedback baseline for the same session and selection revision. It prints the baseline, calls `mainstage_set_mapped_parameter_1` exactly once with that context, and prints the result. It refuses an absent or stale baseline and a value equal to the baseline without mutation. It neither retries nor restores automatically. Success requires `sent: true`, `observed: true`, and a strictly newer same-context callback with the requested raw value. Check the UI/plugin separately because this observation confirms only screen-control feedback.
5. Switch patches. The previous value must become stale because its selection revision no longer matches. It becomes current only after a new callback for the new revision.
6. Remove or disable the mapping and send once more. A transport receipt or replayed callback must end with `observed: false`; do not retry automatically.

Test values near 0, 64, and 127 and one mapped parameter whose display contains an obvious unit. Record the display verbatim, but do not convert or claim the unit. Keep the feature disabled until a valid callback passes the bounded gates above; absence of a callback in one setup is not a categorical result for IAC.
