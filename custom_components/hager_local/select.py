"""Select platform for Hager Local."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .const import (
    CHARGING_MODE_BOOST,
    CHARGING_MODE_SOLAR_DELAYED,
    CHARGING_MODE_SOLAR_MINIMUM,
    CHARGING_MODE_SOLAR_ONLY,
)
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
            lambda wallbox_key: [HagerChargingStrategySelect(coordinator, wallbox_key)],
        )
    )


class HagerChargingStrategySelect(HagerLocalEntity, SelectEntity):
    """Select the wallbox charging strategy."""

    _attr_translation_key = "charging_strategy"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_charging_strategy"
        # Keep the full menu stable in Home Assistant even if Hager temporarily
        # omits the remembered solar profile during startup refreshes.
        self._attr_options = [
            CHARGING_MODE_BOOST,
            CHARGING_MODE_SOLAR_ONLY,
            CHARGING_MODE_SOLAR_MINIMUM,
            CHARGING_MODE_SOLAR_DELAYED,
        ]

    @property
    def available(self) -> bool:
        """Return whether the charging strategy can be controlled."""
        return (
            super().available
            and self.wallbox.configuration is not None
            and self.current_option is not None
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected charging strategy."""
        return self.wallbox.charging_mode

    async def async_select_option(self, option: str) -> None:
        """Change the charging strategy."""
        try:
            await self.coordinator.api.async_set_charge_strategy(self.wallbox, option)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err
