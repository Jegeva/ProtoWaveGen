# UART

Back to [usage overview](../USAGE.md).

`type: "uart"` — `UartTransport`, `protocols/uart.py`. Asynchronous
serial: start bit, LSB-first data bits, optional parity, stop bit(s).
Full duplex gets independent `tx`/`rx` lines; half duplex shares one
`data` line. Push-pull — not wired for floating markers today (`send()`
uses plain `decode_payload`, no `"bin"` datatype, no `DriverTracker`).

## Constructor params

```json
"params": {
  "baudrate": 9600, "data_bits": 8, "parity": "none",
  "stop_bits": 1, "duplex": "full", "flow_control": "none"
}
```

- `baudrate` (required).
- `data_bits` — `5`-`9` (default `8`).
- `parity` — `"none"` (default), `"even"`, `"odd"`, `"mark"`, `"space"`.
- `stop_bits` — `1` (default), `1.5`, or `2`.
- `duplex` — `"full"` (default, independent `tx`/`rx`) or `"half"`
  (shared `data` line — **required** by LIN, see below).
- `flow_control` — `"none"` (default) or `"rts_cts"` (adds `rts`/`cts`
  lines, modeled as a short assert/release bracket around the frame — not
  a full flow-control state machine).

## Operations

- **`send`** — `channel="tx"` (or `"rx"`, or `"data"` in half duplex),
  `data`, `datatype="bytes"`, `driver=None` (an explicit label for the
  `"driver"` annotation track — needed in half duplex to say who's
  talking), `pre_delay_bits=0`, `inter_byte_gap_bits=0`, `labels=None`.

## Example — `examples/uart_basic.json`

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "uart0",
      "type": "uart",
      "params": { "baudrate": 9600, "data_bits": 8, "parity": "none", "stop_bits": 1, "duplex": "full" },
      "operations": [
        { "op": "send", "channel": "tx", "data": "Hello", "datatype": "text", "driver": "device-a" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/uart_basic.svg" },
    { "type": "sigrok", "path": "output/uart_basic.sr" },
    { "type": "vcd", "path": "output/uart_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/uart_basic.json
```

Override the payload from the CLI:

```bash
.venv/bin/python -m protowavegen --config examples/uart_basic.json \
    --data-hex deadbeef
```

---

## Stacked devices

Every device below needs `"stack_on": "<uart node id>"` and must be
declared after that UART node in the `protocols` list.

### LIN — `type: "lin"`

`lin.py`. LIN bus reuses UART byte framing with its own sync/PID/checksum
fields on top. **The underlying UART node must be configured
`"duplex": "half"`** — LIN is a single shared wire.

Operations: `send_frame(frame_id, data, datatype="bytes", checksum="enhanced"|"classic")`
— plain-decode only (no `"bin"`, no floating markers).

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "lin_uart",
      "type": "uart",
      "params": { "baudrate": 19200, "duplex": "half" },
      "operations": []
    },
    {
      "id": "lin0",
      "type": "lin",
      "stack_on": "lin_uart",
      "operations": [
        { "op": "send_frame", "frame_id": 16, "data": [1, 2, 3], "checksum": "enhanced" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/lin_basic.svg" },
    { "type": "sigrok", "path": "output/lin_basic.sr" },
    { "type": "vcd", "path": "output/lin_basic.vcd" }
  ]
}
```

### Modbus RTU — `type: "modbus_rtu"`

`modbus.py`. Byte-oriented UART framing with its own address/function-code/
real-CRC16 layer (`checksums.crc16_modbus`).

`params`: `silence_char_times` (default `3.5`, the inter-frame silence
threshold in character-times).

Operations: `read_holding_registers(slave, start_addr, count)`,
`write_single_register(slave, addr, value)`. **Neither takes `datatype`.**

```json
{
  "samplerate": 1000000,
  "protocols": [
    { "id": "uart0", "type": "uart", "params": { "baudrate": 19200 }, "operations": [] },
    {
      "id": "modbus0", "type": "modbus_rtu", "stack_on": "uart0",
      "operations": [
        { "op": "read_holding_registers", "slave": 1, "start_addr": 0, "count": 10 },
        { "op": "write_single_register", "slave": 1, "addr": 16, "value": 4660 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/modbus_rtu_basic.svg" },
    { "type": "sigrok", "path": "output/modbus_rtu_basic.sr" },
    { "type": "vcd", "path": "output/modbus_rtu_basic.vcd" }
  ]
}
```

### DMX512 — `type: "dmx512"`

`dmx512.py`. Lighting-control protocol: break + mark-after-break +
channel bytes, same shape as LIN's framing. The underlying UART node
should be configured 250000 baud, 8 data bits, no parity, 2 stop bits,
**`"duplex": "full"`**.

`params`: `break_us` (default `100`), `mab_us` (default `12`,
mark-after-break).

Operations: `send_frame(channels, start_code=0)` — `channels` is always a
plain int list, **no `datatype` param at all**.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "uart0", "type": "uart", "params": { "baudrate": 250000, "data_bits": 8, "parity": "none", "stop_bits": 2 }, "operations": [] },
    {
      "id": "dmx0", "type": "dmx512", "stack_on": "uart0",
      "operations": [
        { "op": "send_frame", "channels": [10, 20, 30, 255] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/dmx512_basic.svg" },
    { "type": "sigrok", "path": "output/dmx512_basic.sr" },
    { "type": "vcd", "path": "output/dmx512_basic.vcd" }
  ]
}
```
