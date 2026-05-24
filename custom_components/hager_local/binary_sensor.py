"""Binary sensor platform for Hager Local."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    HagerEmcEntity,
    HagerEmcEntityDescriptionMixin,
    HagerLocalEntity,
    HagerMeterEntity,
    HagerMeterEntityDescriptionMixin,
    async_add_emc_entities,
    async_add_meter_entities,
    HagerWallboxEntityDescriptionMixin,
    async_add_wallbox_entities,
)


@dataclass(kw_only=True)
class HagerBinarySensorDescription(
    HagerWallboxEntityDescriptionMixin, BinarySensorEntityDescription
):
    """Describe a Hager binary sensor."""


@dataclass(kw_only=True)
class HagerEmcBinarySensorDescription(
    HagerEmcEntityDescriptionMixin, BinarySensorEntityDescription
):
    """Describe a Hager EMC binary sensor."""


@dataclass(kw_only=True)
class HagerMeterBinarySensorDescription(
    HagerMeterEntityDescriptionMixin, BinarySensorEntityDescription
):
    """Describe a Hager monitoring meter binary sensor."""


BINARY_SENSOR_DESCRIPTIONS: tuple[HagerBinarySensorDescription, ...] = (
    HagerBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda wallbox: None,
    ),
    HagerBinarySensorDescription(
        key="cable_locked_state",
        translation_key="cable_locked_state",
        value_fn=lambda wallbox: wallbox.lock_cable,
    ),
    HagerBinarySensorDescription(
        key="fallback_charge_allowed_state",
        translation_key="fallback_charge_allowed_state",
        value_fn=lambda wallbox: wallbox.charge_in_fallback_mode_allowed,
    ),
    HagerBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda wallbox: wallbox.properties.get("chargingActive"),
    ),
    HagerBinarySensorDescription(
        key="car_connected",
        translation_key="car_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda wallbox: wallbox.properties.get("type2Plugged"),
    ),
    HagerBinarySensorDescription(
        key="activated",
        translation_key="activated",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: wallbox.evse_parameters.get("activated"),
    ),
)


EMC_BINARY_SENSOR_DESCRIPTIONS: tuple[HagerEmcBinarySensorDescription, ...] = (
    HagerEmcBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda emc: None,
    ),
)


METER_BINARY_SENSOR_DESCRIPTIONS: tuple[HagerMeterBinarySensorDescription, ...] = (
    HagerMeterBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda meter: None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager binary sensors."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [
                HagerBinarySensorEntity(coordinator, wallbox_key, description)
                for description in BINARY_SENSOR_DESCRIPTIONS
            ],
        )
    )
    entry.async_on_unload(
        async_add_emc_entities(
            coordinator,
            async_add_entities,
            lambda emc_key: [
                HagerEmcBinarySensorEntity(coordinator, emc_key, description)
                for description in EMC_BINARY_SENSOR_DESCRIPTIONS
            ],
        )
    )
    entry.async_on_unload(
        async_add_meter_entities(
            coordinator,
            async_add_entities,
            lambda meter_key: [
                HagerMeterBinarySensorEntity(coordinator, meter_key, description)
                for description in METER_BINARY_SENSOR_DESCRIPTIONS
            ],
        )
    )


class HagerBinarySensorEntity(HagerLocalEntity, BinarySensorEntity):
    """Representation of a Hager binary sensor."""

    entity_description: HagerBinarySensorDescription

    def __init__(
        self,
        coordinator,
        wallbox_key: str,
        description: HagerBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, wallbox_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.wallbox.device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if self.entity_description.key == "online":
            return self.is_online()

        value = self.entity_description.value_fn(self.wallbox)
        if value is None:
            return None
        return bool(value)


class HagerEmcBinarySensorEntity(HagerEmcEntity, BinarySensorEntity):
    """Representation of a Hager EMC binary sensor."""

    entity_description: HagerEmcBinarySensorDescription

    def __init__(self, coordinator, emc_key: str, description: HagerEmcBinarySensorDescription) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, emc_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.emc.device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if self.entity_description.key == "online":
            return self.is_online()

        value = self.entity_description.value_fn(self.emc)
        if value is None:
            return None
        return bool(value)


class HagerMeterBinarySensorEntity(HagerMeterEntity, BinarySensorEntity):
    """Representation of a Hager monitoring meter binary sensor."""

    entity_description: HagerMeterBinarySensorDescription

    def __init__(
        self,
        coordinator,
        meter_key: str,
        description: HagerMeterBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, meter_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.meter.device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if self.entity_description.key == "online":
            return self.is_online()

        value = self.entity_description.value_fn(self.meter)
        if value is None:
            return None
        return bool(value)
