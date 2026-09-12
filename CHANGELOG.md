# Changelog

## 0.2.20b1 - 2026-09-12 (prerelease)

### Added

- Experimental Witty Solar type 6 current-limit number with English/French names.
- Whole-ampere operating limits within fresh installer bounds, capped at 6-32 A.
- A single-field cloud write (`maxChargeCurrent`) with mandatory readback,
  pending/offline checks, bounded timeout and per-wallbox command serialization.
- Rejection of malformed/unknown bounds, duplicate writes and concurrent requests;
  no optimistic success, uncertain-write retry or automatic restoration.
- Read-only, single-request Modbus TCP diagnostic CLI (FC03 only).
- Offline unit/binding/protocol tests and a dedicated GitHub Actions workflow.
- Technical notes distinguishing cloud settings from actual vehicle current and
  Witty Plus register documentation from unverified Witty Solar compatibility.

### Validation status

- Physical current-setting behaviour is not yet confirmed. Keep this a beta.
- The investigated port 502 accepted TCP connections but reset the read probes;
  no valid register response was obtained and no Modbus write was attempted.
- Stable `0.2.19` remains available; no existing entity unique IDs are changed.

## 0.2.19

Expanded `witty solar` support with stable runtime controls and improved startup behavior.

### Added

- Solar charge mode selection from Home Assistant:
  `Boost`, `Solar only`, `Solar minimum`, `Solar delayed`
- Wallbox settings from Home Assistant:
  cable lock, fallback charging, LED intensity and solar holding time
- Home power sensor on the `Flow EMC`
- Local cache hydration to keep the last known wallbox profile across restarts

### Fixed

- Startup cases where entities could stay `unknown` until the integration was reloaded
- Grid and photovoltaic power sourcing to use the live Flow / E3DC status data
- Hager reauthentication handling for `reAuthToken`
- Witty Solar charge-mode payloads so `Solar minimum` and `Solar delayed` use the values accepted by Hager
- EVSE update payloads so generic wallbox setting changes work reliably from Home Assistant
- Switch handling so `BOOST` and `Flow disconnected` controls each keep the correct `turn_off` action

## 0.2.0

Initial public release of `Hager Local` for Home Assistant.

### Included

- Support for Hager `witty solar`
- Discovery of the linked `Flow EMC`
- Discovery of the photovoltaic meter when exposed by Hager
- `BOOST` control from Home Assistant
- Sensors for charger status, charging power, solar power, grid power and energy
- Login through the `myHager / flow` web account flow
- HACS custom repository support

### Support

[![Buy Me a Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://www.buymeacoffee.com/hebrru)
