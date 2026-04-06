"""Switch platform for Hager Local."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .const import CHARGING_MODE_BOOST
from .entity import HagerLocalEntity, async_add_wallbox_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager switches."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [
                HagerBoostSwitch(coordinator, wallbox_key),
                HagerLockCableSwitch(coordinator, wallbox_key),
                HagerFallbackChargeSwitch(coordinator, wallbox_key),
            ],
        )
    )


class HagerBoostSwitch(HagerLocalEntity, SwitchEntity):
    """Temporary boost mode toggle shown in the Flow dashboard."""

    _attr_translation_key = "boost"

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_boost"

    @property
    def available(self) -> bool:
        """Return whether boost mode can be controlled."""
        return super().available and self.wallbox.configuration is not None

    @property
    def is_on(self) -> bool:
        """Return whether boost mode is enabled."""
        return self.wallbox.charging_mode == CHARGING_MODE_BOOST or bool(
            self.wallbox.properties.get("chargeFull")
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Enable boost mode."""
        try:
            await self.coordinator.api.async_set_boost_mode(self.wallbox, True)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs) -> None:
        """Disable boost mode."""
        try:
            await self.coordinator.api.async_set_boost_mode(self.wallbox, False)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err


class HagerLockCableSwitch(HagerLocalEntity, SwitchEntity):
    """Toggle the cable lock persistence behavior."""

    _attr_translation_key = "lock_cable"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_lock_cable"

    @property
    def available(self) -> bool:
        """Return whether the switch can be controlled."""
        return super().available and self.wallbox.lock_cable is not None

    @property
    def is_on(self) -> bool:
        """Return whether cable lock persistence is enabled."""
        return bool(self.wallbox.lock_cable)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable cable lock persistence."""
        try:
            await self.coordinator.api.async_set_lock_cable(self.wallbox, True)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs) -> None:
        """Disable cable lock persistence."""
        try:
            await self.coordinator.api.async_set_lock_cable(self.wallbox, False)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err


class HagerFallbackChargeSwitch(HagerLocalEntity, SwitchEntity):
    """Toggle charging behavior when Flow is disconnected."""

    _attr_translation_key = "charge_in_fallback_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, wallbox_key: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_charge_in_fallback_mode"

    @property
    def available(self) -> bool:
        """Return whether the switch can be controlled."""
        return super().available and self.wallbox.charge_in_fallback_mode_allowed is not None

    @property
    def is_on(self) -> bool:
        """Return whether fallback charging is allowed."""
        return bool(self.wallbox.charge_in_fallback_mode_allowed)

    async def async_turn_on(self, **kwargs) -> None:
        """Allow charging when Flow is disconnected."""
        try:
            await self.coordinator.api.async_set_charge_in_fallback_mode(self.wallbox, True)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs) -> None:
        """Disallow charging when Flow is disconnected."""
        try:
            await self.coordinator.api.async_set_charge_in_fallback_mode(self.wallbox, False)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err
