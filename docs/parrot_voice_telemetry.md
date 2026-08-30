# TG9990 parrot voice telemetry

HBlink4 can optionally transmit a short spoken RF-quality report after a successful local TG9990 parrot echo. The feature is independent of the parrot itself: the echo service can remain enabled while voice telemetry is disabled.

## Runtime behaviour

After the recorded call is echoed to the originating repeater/hotspot, HBlink4 can speak the average BER, average RSSI and originating timeslot. A typical report is:

> Bit Error Rate zero point four percent. Received Signal Strength Indication minus seventy-two D B M. Timeslot two. Seventy threes.

Unavailable RF metrics are spoken as `unavailable`; values are never invented. BER uses two decimal places below 1 percent and one decimal place otherwise. RSSI is rounded to the nearest whole dBm.

The report is local-only, just like the parrot echo. It uses TG9990, the originating repeater/hotspot, the originating timeslot and the Colour Code recovered from the recorded call. If the RF slot becomes active while the report is running, the report stops and yields the slot. A completed echo remains successful and is not reclassified as a cancelled parrot test merely because the optional voice report was interrupted.

## Configuration

The `parrot` block supports these independent voice controls:

```json
{
  "voice_telemetry_enabled": true,
  "voice_telemetry_source_id": 9990,
  "voice_telemetry_pause_seconds": 0.45
}
```

`voice_telemetry_enabled` enables the spoken report. `voice_telemetry_source_id` is the 24-bit DMR source ID used for generated report packets. `voice_telemetry_pause_seconds` is the pause after the echo and before the report begins; valid range is 0 to 5 seconds.

## Audio asset architecture

Production HBlink4 does not run text-to-speech or an AMBE encoder. The committed `hblink4/parrot_voice_assets.tar.gz` contains pre-encoded AMBE+2 prompt fragments. At runtime HBlink4 selects the required fragments, converts the canonical AMBE bit order to the DMR over-the-air interleave, inserts DMR sync/embedded signalling, builds Link Control, and emits a normal HomeBrew DMRD voice stream.

The vocabulary contains fixed phrases plus whole-number clips from 0 through 200. This covers the BER/RSSI ranges required by the service while keeping numbers natural-sounding.

## Rebuilding the assets

`scripts/generate_parrot_voice_assets.py` is a build-time tool. It uses Australian-English gTTS (`lang=en`, `tld=com.au`) to generate source speech, converts it to 8 kHz, 16-bit signed little-endian mono PCM, and invokes OpenDMR to produce canonical 9-byte AMBE+2 frames.

The encoder is pinned to OpenDMR commit:

`d28164b39ba4d91ad5948ff22707937f8944f70f`

A typical build environment needs gTTS, num2words, ffmpeg, a C/C++ build toolchain and the pinned OpenDMR source. None of those are production runtime dependencies.

OpenDMR and its integrated codec sources have their own upstream licences and provenance. Regenerated assets should retain the pinned revision and source-generation metadata in the archive manifest, and any change to the encoder/toolchain should be reviewed separately.

## DMR framing

OpenDMR emits canonical/DVSI 72-bit AMBE frames in A+B+C order. HomeBrew DMR voice payloads carry the channel-coded voice bits in the DMR interleaved order. `hblink4/parrot_voice.py` applies the standard 36-dibit DMR AMBE interleave schedule before placing three 20 ms AMBE frames into each 60 ms voice burst.

Regression coverage includes the standard DMR silence reference: canonical `49400f09a0e0000000` must interleave to `acaa40200044408080`. The generated call also verifies Voice Header/Terminator Link Control, Colour Code, timeslot, HomeBrew packet shape and A-F voice sequencing.

## Dashboard lifecycle

Dashboard version 1.3.2 adds a `Voice report` phase. The expected lifecycle is:

`Ready -> Recording -> Preparing playback -> Playing back -> Voice report -> Test complete -> Ready`

Terminal Complete/Cancelled activity expires after eight seconds through both the server-backed recovery state and the WebSocket-driven UI, preventing a stale final state from remaining on the card.
