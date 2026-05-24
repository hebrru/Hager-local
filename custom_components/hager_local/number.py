"""Number platform for Hager Local."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .entity import HagerLocalEntity, async_add_wallbox_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager number entities."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [
                HagerLedIntensityNumber(coordinator, wallbox_key),
                HagerSolarHoldingTimeNumber(coordinator, wallbox_key),
            ],
        )
    )


class HagerLedIntensityNumber(HagerLocalEntity, NumberEntity):
    """Control the main LED intensity of the wallbox."""

    _attr_translation_key = "led_intensity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_led_intensity"

    @property
    def available(self) -> bool:
        """Return whether the LED intensity can be controlled."""
        return super().available and self.wallbox.led_intensity is not None

    @property
    def native_value(self) -> float | None:
        """Return the current LED intensity."""
        if self.wallbox.led_intensity is None:
            return None
        return float(self.wallbox.led_intensity)

    async def async_set_native_value(self, value: float) -> None:
        """Set the LED intensity."""
        try:
            await self.coordinator.api.async_set_led_intensity(self.wallbox, value)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err


class HagerSolarHoldingTimeNumber(HagerLocalEntity, NumberEntity):
    """Control the solar holding time in minutes."""

    _attr_translation_key = "solar_holding_time"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_solar_holding_time"

    @property
    def available(self) -> bool:
        """Return whether the holding time can be controlled."""
        return super().available and self.wallbox.solar_holding_time is not None

    @property
    def native_value(self) -> float | None:
        """Return the current holding time."""
        if self.wallbox.solar_holding_time is None:
            return None
        return float(self.wallbox.solar_holding_time)

    async def async_set_native_value(self, value: float) -> None:
        """Set the solar holding time."""
        try:
            await self.coordinator.api.async_set_solar_holding_time(self.wallbox, value)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err
