"""Privacy-minimized Home Assistant diagnostics for community support."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .telemetry_data import finite_number, public_diagnostic_view


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Do not serialize entry.data, raw API payloads, names or identifiers."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator if runtime is not None else None
    snapshot = coordinator.data if coordinator is not None else None
    wallboxes = snapshot.wallboxes.values() if snapshot is not None else ()
    return {
        "integration_version": "0.2.20b2",
        "transport": "Hager Flow cloud",
        "snapshot_available": snapshot is not None,
        "coordinator_update_succeeded": bool(coordinator.last_update_success) if coordinator is not None else None,
        "scan_interval_seconds": finite_number(entry.options.get("scan_interval")),
        "wallboxes": [
            {"device_index": index, **public_diagnostic_view(wallbox.properties, wallbox.evse_parameters)}
            for index, wallbox in enumerate(wallboxes, start=1)
        ],
        "note": "Received/cached data, not a live hardware test. Review even this reduced report before sharing.",
    }
