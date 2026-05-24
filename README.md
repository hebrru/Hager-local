# Hager Local

[![Open your Home Assistant instance and open this repository inside HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&owner=hebrru&repository=Hager-local)

Home Assistant custom integration to monitor and control a Hager `witty solar` charger through a Hager `Flow EMC` installation and a `myHager / flow` account.

This project is focused on `witty solar` control and monitoring from Home Assistant.

[![Buy Me a Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://www.buymeacoffee.com/hebrru)

## What It Exposes

- A dedicated device for the Hager `witty solar` charger
- A dedicated device for the Hager `Flow EMC`
- A dedicated device for the photovoltaic meter when exposed by Hager
- UI-based config flow for Home Assistant
- Charger status, online state, car connected state, current charging state
- Charging power, solar power, grid power, home power and energy counters when available
- `BOOST` control from Home Assistant
- Solar charging mode selection:
  `Boost`, `Solar only`, `Solar minimum`, `Solar delayed`
- Wallbox settings from Home Assistant:
  cable lock, fallback charging, LED intensity and solar holding time

## HACS Installation

1. Open HACS in Home Assistant.
2. Open `Custom repositories`.
3. Add `https://github.com/hebrru/Hager-local`.
4. Select the category `Integration`.
5. Install `Hager Local`.
6. Restart Home Assistant.
7. Add the integration and sign in with your `myHager / flow` account.

You can also use the badge at the top of this README to open the repository directly in HACS.

## Notes

- This integration is intended for Hager `witty solar` setups linked to a `Flow EMC`.
- The repository already includes `hacs.json` for HACS custom-repository installs.
- The integration uses the `myHager / flow` web account flow instead of the Hager developer API.
- The integration keeps a local cache to make startup and temporary Hager API failures more robust.

## Support

If this project helps you, you can support it here:

[![Buy Me a Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://www.buymeacoffee.com/hebrru)
