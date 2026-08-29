# Local parrot / echo service

HBlink4 can provide a local DMR parrot service without a second HBLink process.
The service records a group-voice transmission to one configured talkgroup and
replays the original DMRD frames only to the repeater/hotspot that originated
the call.

## Configuration

```json
"parrot": {
  "enabled": true,
  "talkgroup": 9990,
  "delay_seconds": 2.0,
  "packet_interval_seconds": 0.06,
  "max_duration_seconds": 120.0
}
```

The section is optional and defaults to disabled.

- `talkgroup` is the canonical network-side TGID. The default is TG9990.
- `delay_seconds` is the pause after a proper voice terminator before playback.
- `packet_interval_seconds` is the DMRD playback cadence; 60 ms matches the
  classic HBLink playback service.
- `max_duration_seconds` bounds the in-memory recording. Overlong or incomplete
  calls are discarded rather than replayed.

## Routing and safety behaviour

The parrot is a local service, not a bridge:

- an enabled parrot TG is accepted from a locally authenticated repeater/hotspot
  even when that TG is absent from the normal repeater routing allow-list;
- its normal route cache is forced empty, so the call is not sent to another
  local repeater, outbound HomeBrew connection, or OpenBridge trunk;
- a numerically identical TG arriving from an external trunk does not invoke the
  local parrot;
- the recorded packets are replayed only to the originating repeater/hotspot;
- playback is cancelled if the origin disconnects or reuses the slot before the
  echo begins;
- recordings that end through timeout/fast-terminator handling are discarded;
  playback requires a proper voice terminator.

DMRD translation remains respected: a repeater may map a local RF TG to the
configured canonical parrot TG and still invoke the service.

## Operation

Key the configured TG, speak a short test transmission, and unkey normally.
After the configured delay the server should replay the transmission on the
same repeater/hotspot and timeslot. Logs use the `[PARROT]` prefix for recording,
playback, cancellation, and limit events.
