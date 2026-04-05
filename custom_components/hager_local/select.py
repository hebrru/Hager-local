"""Select platform for Hager Local."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .const import CHARGING_MODE_OPTIONS
from .entity import HagerLocalEntity, async_add_wallbox_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager selects."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [HagerChargeModeSelect(coordinator, wallbox_key)],
        )
    )


class HagerChargeModeSelect(HagerLocalEntity, SelectEntity):
    """Representation of the Hager charging mode selector."""

    _attr_translation_key = "charging_strategy"
    _attr_options = CHARGING_MODE_OPTIONS

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_charging_strategy"

    @property
    def current_option(self) -> str | None:
        """Return the current charging mode."""
        return self.wallbox.charging_mode

    async def async_select_option(self, option: str) -> None:
        """Change the wallbox charging mode."""
        if option not in CHARGING_MODE_OPTIONS:
            raise HomeAssistantError(f"Unsupported Hager charging mode: {option}")

        try:
            await self.coordinator.api.async_set_charging_mode(self.wallbox, option)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err
