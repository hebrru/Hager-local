# Changelog

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
