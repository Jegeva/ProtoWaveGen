# DCF77 — `type: "dcf77"`

`dcf77.py`. DCF77 European longwave time signal: one demodulated `data`
line. Confirmed against sigrok's own `dcf77` decoder: it triggers on
*rising* edges and measures the following *high* period's duration to
classify each bit (40-160ms -> 0, 161-260ms -> 1) — the opposite envelope
sense from every other pulse-timed protocol in this repo (mark/low vs.
space/high), matched here rather than chosen freely.

59 bits per minute, one per second, BCD fields: bit 0 (start of minute,
always 0), bits 1-14 (special bits, always 0 — civil warning/weather
forecast data isn't modeled), bit 15 (call bit), bits 16-19 (summer-time
announcement/CEST/CET/leap-second flags), bit 20 (start of encoded time,
always 1), bits 21-28 (minute BCD + even parity), bits 29-35 (hour BCD +
even parity), bits 36-58 (day/weekday/month/year BCD + even parity over
the whole date block). Bit 59 is never transmitted — a full extra silent
second in its place marks the new-minute boundary.

Operations: `send_minute(minute, hour, day, weekday, month, year,
call_bit=False, summer_time_announce=False, cest=False, cet=True,
leap_second_announce=False)`. No `datatype`.

**Needs 2+ back-to-back `send_minute()` calls for a meaningful decode**:
sigrok's decoder only starts annotating real fields once it sees the
~2000ms new-minute gap (confirmed empirically — a single isolated minute
never gets that gap). This mirrors real DCF77 behavior (a receiver locks
onto a continuously-transmitting signal) rather than being a workaround.

Real-time duration is a non-issue for this tool despite the 1-minute-per-
call span — `CaptureBuilder`'s edge list is sparse and scales with edge
count (~118/minute), not real-time span; two minutes is only 120,000
samples at a 1kHz samplerate (already 10x the pulses' own resolution).

```json
{
  "samplerate": 1000,
  "protocols": [
    {
      "id": "dcf0", "type": "dcf77",
      "operations": [
        { "op": "send_minute", "minute": 30, "hour": 14, "day": 5, "weekday": 4, "month": 3, "year": 26 },
        { "op": "send_minute", "minute": 31, "hour": 14, "day": 5, "weekday": 4, "month": 3, "year": 26 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/dcf77_basic.svg" },
    { "type": "sigrok", "path": "output/dcf77_basic.sr" },
    { "type": "vcd", "path": "output/dcf77_basic.vcd" }
  ]
}
```
