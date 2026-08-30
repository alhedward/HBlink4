# Live parrot dashboard activity

When the local parrot service is enabled, HBlink4 emits informational dashboard events for the parrot lifecycle without creating routed or assumed TX streams.

The public and administrator Local DMR Services cards show these states:

- Ready
- Recording
- Preparing playback
- Playing back
- Test complete
- Test cancelled

During a recording the widget also consumes the normal HBlink4 `stream_update` events, so it can show the same RF-quality data already used by Last Heard: average/peak BER and average/min/max RSSI. Final lifecycle events retain the last RF-quality snapshot along with source radio ID, originating repeater ID, timeslot, duration and packet count.

The parrot lifecycle events are:

- `parrot_recording_started`
- `parrot_recording_complete`
- `parrot_playback_started`
- `parrot_playback_complete`
- `parrot_recording_discarded`
- `parrot_playback_cancelled`

These events are monitoring-only. They do not alter routing, call statistics, talkgroup ACLs or the local-only isolation of the parrot service. Event payloads do not contain DMR packet/audio contents or credentials.

HBlink4 currently exposes HomeBrew RF-quality metadata (BER/RSSI). The Python parrot does not decode AMBE to PCM, so it does not claim RMS/clipping metrics unless a future verified server-side source for those values is added.
