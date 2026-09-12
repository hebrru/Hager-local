"""Number platform for Hager Local."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .current_control import (
    CurrentControlError,
    configured_current,
    current_bounds,
    current_update_pending,
    validate_live_configuration,
)
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
                HagerChargeCurrentLimitNumber(coordinator, wallbox_key),
            ],
        )
    )


class HagerChargeCurrentLimitNumber(HagerLocalEntity, NumberEntity):
    """Operating current ceiling; never an installer/protection setting."""

    _attr_translation_key = "charge_current_limit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX  # Avoid a write for every movement of a slider.
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator, wallbox_key: str) -> None:
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_charge_current_limit"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        try:
            configuration = validate_live_configuration(self.wallbox.properties)
            return not current_update_pending(configuration)
        except CurrentControlError:
            return False

    @property
    def native_min_value(self) -> float:
        try:
            return float(current_bounds(self.wallbox.properties).minimum)
        except CurrentControlError:
            return 6.0  # Entity is unavailable; no permissive assumed upper limit.

    @property
    def native_max_value(self) -> float:
        try:
            return float(current_bounds(self.wallbox.properties).maximum)
        except CurrentControlError:
            return 6.0

    @property
    def native_value(self) -> float | None:
        try:
            return float(configured_current(self.wallbox.properties))
        except CurrentControlError:
            return None

    @property
    def extra_state_attributes(self) -> dict:
        attributes = dict(super().extra_state_attributes or {})
        attributes.update({
            "control_transport": "Hager Flow cloud",
            "experimental": True,
            "confirmation": "cloud_configuration_readback_not_measured_car_current",
            "installer_limit_a": self.wallbox.properties.get("upperCurrentLimit"),
            "update_pending": self.wallbox.properties.get("maxChargeCurrentWatingFor"),
        })
        return attributes

    async def async_set_native_value(self, value: float) -> None:
        if not self.available:
            raise HomeAssistantError("Current control unavailable; check the wallbox and refresh")
        try:
            await self.coordinator.api.async_set_max_charge_current(self.wallbox, value)
        except HagerApiError as err:
            self.async_write_ha_state()  # Only actually observed data, never the request.
            raise HomeAssistantError(str(err)) from err
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


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
