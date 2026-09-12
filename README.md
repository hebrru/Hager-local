# Hager Local

<p align="left"><img src="custom_components/hager_local/brand/icon@2x.png" alt="Icône indépendante Hager Local" width="96" height="96"></p>

> **Projet communautaire indépendant, non affilié au fabricant.** Cette icône est une création originale pour cette intégration, pas un logo officiel. Les noms des fabricants désignent uniquement les appareils compatibles.

[Sources et variantes du logo](custom_components/hager_local/brand/) · [Publication 0.2.20b5](https://github.com/hebrru/Hager-local/releases/tag/0.2.20b5)

[![Open your Home Assistant instance and open this repository inside HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&owner=hebrru&repository=Hager-local)

Home Assistant custom integration to monitor and control a Hager `witty solar` charger through a Hager `Flow EMC` installation and a `myHager / flow` account.

This project is focused on `witty solar` control and monitoring from Home Assistant.

[![Buy Me a Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://www.buymeacoffee.com/hebrru)

## 0.2.20b2 - public telemetry beta

Adds **22 read-only entities** from data already fetched by the integration:
per-phase power/energy, installer current limits, configured solar ratio and
fallback current, reference-voltage/key diagnostics, a charging-state summary,
a received-or-cached weekly profile and eight reported status flags.

Includes a privacy-minimized Home Assistant diagnostic report for community
support, plus offline regression tests and English/French labels. Inactive
phase L2/L3 measurements are registered disabled by default on single-phase setups.

**Scope:** this update adds no new charging command or network endpoint. It
retains the experimental cloud current control introduced in b1. An editable
weekly planner, solar/minimum/fallback-current setters, access/phase setters,
session history, WebSocket updates and direct Modbus control are **not included**.
The received profile may contain cached values and is not a live acknowledgement.

This is a community integration, not an official Hager product. The stable
release remains `0.2.19`; hardware validation of the beta current command is
still pending. Back up before installing and restart Home Assistant afterwards.

[What is included, known limitations and sharing instructions](docs/TELEMETRY_BETA.md).

## Current control inherited from 0.2.20b1

This prerelease adds **Charge current limit** (**Limite de courant de charge**),
with 1 A steps inside the live installer limits and the supported 6-32 A range.
It uses the **Hager Flow cloud**, not direct Modbus. It never changes installer
protection limits, starts charging or enables Boost implicitly.

Commands require fresh online data and a matching non-pending cloud readback.
Hardware behaviour is **not yet validated**; this is not an unattended solar or
battery-protection controller. The stable release remains `0.2.19`.

A bounded, read-only Modbus diagnostic tool and offline tests are also included.
Direct Modbus current writes remain disabled: no valid register response has
yet been obtained from the investigated Witty Solar setup.

See [current-control safeguards, Modbus findings and validation instructions](docs/CURRENT_CONTROL_AND_MODBUS.md).

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
