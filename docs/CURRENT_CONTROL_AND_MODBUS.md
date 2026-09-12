# Current control and Modbus investigation - 0.2.20b1

## Release status

This is an experimental prerelease, not a validated hardware driver. It adds a
Home Assistant number named **Charge current limit** / **Limite de courant de
charge**. Its transport is the existing Hager Flow cloud API. It does **not** add
local Modbus charging control and does not remove the Flow EMC dependency.

The current-control write has not yet been confirmed on a physical Witty Solar.
Offline tests validate software behaviour with synthetic responses; they are not
proof that a particular firmware will apply the command or that a vehicle will
draw the requested current. Do not use this beta as an unattended safety control
or as a guarantee that home batteries will never supply the car.

## What the number does

The control sets `maxChargeCurrent`, an operating ceiling, in whole amperes. It
can reduce and increase that ceiling within the live configuration limits. It
does not start a session, enable Boost or demand that the vehicle consume that
current. Flow, the vehicle and the electrical installation retain their own
constraints.

The implementation currently accepts only the observed Witty Solar API type 6.
The selectable interval is the intersection of:

- The supported 6-32 A range for this implementation.
- The live `lowerCurrentLimit`, `minChargeCurrent` and `upperCurrentLimit`.

An installation limited to 16 A will remain limited to 16 A. Missing, non-finite,
zero, inconsistent or unsupported-model bounds do not fall back to an assumed
32 A limit. The number becomes unavailable. A fresh API read is required again
when a command is requested, even when a valid cached value is displayed.

The only field in the write payload is `maxChargeCurrent`. The integration does
not change `upperCurrentLimit`, `lowerCurrentLimit`, `minChargeCurrent`, circuit
protection, phases, solar mode, fallback settings or the configured protection
rating. It does not bypass the physical rotary selector or Flow load management.

An operation requires an online, working device, rejects an already-pending
current update, skips an unchanged value and rejects concurrent current commands
for the same wallbox rather than queuing stale requests. The write is sent once.
It is followed by up to five reads, two seconds apart, within an overall
40-second timeout. Success requires the requested value to be reported with
`maxChargeCurrentWatingFor` explicitly false (this spelling is Hager's).

A 204 HTTP response alone is not success. A missing or mismatched readback,
connection failure or timeout reports an error. It never substitutes the desired
value in the cache, retries an uncertain write or restores a higher previous
limit. Check the actual reported setting after an error before trying again.
A matching cloud setting is not a measurement of the current taken by the car.

No automated charging schedule, no startup write and no Tesla/Zendure automation
is created by installing this update. Prefer deliberate manual tests first; avoid
rapid cloud-control loops. Network/cloud latency cannot guarantee instantaneous
solar matching or zero home-battery discharge.

## Installation and first validation

Use the `0.2.20b1` GitHub prerelease (not the stable `0.2.19` release). Back up the
existing `custom_components/hager_local` directory before replacing integration
files. Restart Home Assistant to load changed Python modules. A simple integration
reload may continue to use modules already imported into the running process.
The old sensors and controls keep their unique IDs. The new number has suffix
`_charge_current_limit`; its exact entity ID is assigned by Home Assistant.

Before connecting the control to an automation, validate it manually on the exact
firmware, within the existing electrical limits, and check both the reported
setting and actual charging behaviour. Do not raise electrical protection limits
or disconnect the Flow controller to make a test work.

## Modbus: what is and is not established

Hager's Witty Solar manual documents a Modbus TCP / LAN connection to Flow EMC.
That establishes the physical communication path, not an open third-party
control contract or compatibility with every Witty model's register table.

During a read-only investigation on 2026-09-12, TCP port 502 at the address
reported by Flow accepted connections. An identification request and selected
FC03 reads with unit 1 were reset by the peer. Two additional FC03 reads with
units 0 and 255 were also reset. **No valid Modbus register response was read.**
No write function was sent, no pairing was changed and Flow was not stopped.

The available community register map describes **Witty Plus**, not a verified
Witty Solar map. Its addresses were used only as limited read probes, not as
write targets. Compatibility, the precise cause of the connection resets and
coexistence with the existing Flow controller remain unconfirmed. A single-client
server or a firmware/network restriction are possibilities, not diagnoses.

Further local control work needs a register/units specification for the exact
Solar hardware and firmware, successful read-only identification and limits, and
a documented way to avoid two controllers fighting over the same current setpoint
or weakening the existing load management. Until then, this release deliberately
contains no Modbus current-write implementation.

## Read-only diagnostic tool

`tools/modbus_probe.py` uses Python's standard library. It sends exactly one
FC03 read to an explicit host, unit and register address, then closes the
connection. There is no scan and no Modbus write function. Requests are bounded
to 1-16 registers; headers, transaction IDs, units and byte counts are validated.
An open TCP connection is reported separately from a valid Modbus reply and a
successful register read. A Modbus exception is not reported as register data.

Example using a documentation-only IP (replace it with an authorized LAN device
and a register documented for that exact model):

```shell
python tools/modbus_probe.py --host 192.0.2.10 --unit 1 --address 0x1001 --count 1
```

The command exits with status 1 if it could not read registers. No account token
or password is needed. Review register output before sharing it: some registers
may contain serial numbers or other device identifiers. Do not expose port 502
to the Internet.

## Software tests

```shell
python -m unittest discover -s tests -v
python -m compileall -q custom_components tools tests
```

The suite covers numerical boundaries, installer caps, pending/offline states,
readback, timeouts/cancellation, no optimistic success, no unintended write
fields, concurrent-command rejection, the number entity binding and read-only
Modbus framing. HA binding tests compile selected production classes/methods
against synthetic dependencies. They are not full Home Assistant runtime tests.
GitHub Actions runs the offline suite without access to any physical charger.

## Sources

- Hager Witty Solar manual, sections 03.02 and 08.01:
  https://assets.hager.com/step-content/P/HA_41357490/Document/std.lang.all/6LE009239A_XEV1Kxx2S_EVCS_witty-solar_manual_EN_web.pdf
- Hager Flow portal, current-setting UI and API field mapping inspected 2026-09-12:
  https://flow.hager.com/
- Witty Plus community Modbus map (not proof of Solar compatibility):
  https://github.com/evcc-io/evcc/issues/33327
- Home Assistant number entity contract:
  https://developers.home-assistant.io/docs/core/entity/number/

No private configuration, account credentials, real device identifiers or vendor
JavaScript bundles are included in this repository or its diagnostic examples.
