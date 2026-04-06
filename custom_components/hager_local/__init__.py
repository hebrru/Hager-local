"""The Hager Local integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .api import HagerApiClient
from .const import PLATFORMS
from .coordinator import HagerDataUpdateCoordinator

LOGGER = logging.getLogger(__name__)
UNSUPPORTED_ENTITY_SUFFIXES: tuple[str, ...] = ()


@dataclass(slots=True)
class HagerLocalRuntimeData:
    """Runtime data for Hager Local."""

    api: HagerApiClient
    coordinator: HagerDataUpdateCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Hager Local integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hager Local from a config entry."""
    api = HagerApiClient(hass, entry)
    coordinator = HagerDataUpdateCoordinator(hass, entry, api)
    cached_snapshot = await coordinator.async_load_cached_snapshot()
    api.prime_cached_snapshot(cached_snapshot)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except ConfigEntryNotReady:
        if cached_snapshot is None:
            raise

        LOGGER.warning(
            "Hager Local could not complete the startup refresh for %s; using cached data",
            entry.title,
        )
        coordinator.async_set_updated_data(cached_snapshot)
        hass.async_create_task(coordinator.async_refresh())

    entry.runtime_data = HagerLocalRuntimeData(api=api, coordinator=coordinator)
    _async_remove_unsupported_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Hager Local config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data = None
    return unload_ok


def _async_remove_unsupported_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove stale entities for unsupported Hager write paths."""
    registry = er.async_get(hass)
    for entity_entry in list(registry.entities.values()):
        if entity_entry.config_entry_id != entry.entry_id:
            continue
        if entity_entry.platform != "hager_local":
            continue
        unique_id = entity_entry.unique_id or ""
        if unique_id.endswith(UNSUPPORTED_ENTITY_SUFFIXES):
            registry.async_remove(entity_entry.entity_id)
