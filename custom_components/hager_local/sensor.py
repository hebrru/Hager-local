"""Sensor platform for Hager Local."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
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
    first_parsed_datetime,
    nested_get,
)


@dataclass(kw_only=True)
class HagerSensorDescription(HagerWallboxEntityDescriptionMixin, SensorEntityDescription):
    """Describe a Hager sensor."""


@dataclass(kw_only=True)
class HagerEmcSensorDescription(HagerEmcEntityDescriptionMixin, SensorEntityDescription):
    """Describe a Hager EMC sensor."""


@dataclass(kw_only=True)
class HagerMeterSensorDescription(HagerMeterEntityDescriptionMixin, SensorEntityDescription):
    """Describe a Hager meter sensor."""


def _as_float(value: Any) -> float | None:
    """Convert a Hager number-like value to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_values(*values: Any) -> float | None:
    """Return the sum of all numeric values when at least one is present."""
    numbers = [number for value in values if (number := _as_float(value)) is not None]
    if not numbers:
        return None
    return sum(numbers)


def _first_not_none(*values: Any) -> Any:
    """Return the first value that is not None."""
    for value in values:
        if value is not None:
            return value
    return None


def _as_str(value: Any) -> str | None:
    """Convert a non-empty value to a string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


SENSOR_DESCRIPTIONS: tuple[HagerSensorDescription, ...] = (
    HagerSensorDescription(
        key="charging_mode",
        translation_key="charging_mode",
        value_fn=lambda wallbox: _as_str(wallbox.charging_mode),
    ),
    HagerSensorDescription(
        key="led_intensity_level",
        translation_key="led_intensity_level",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _as_float(wallbox.led_intensity),
    ),
    HagerSensorDescription(
        key="solar_holding_time_minutes",
        translation_key="solar_holding_time_minutes",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _as_float(wallbox.solar_holding_time),
    ),
    HagerSensorDescription(
        key="authentication_mode",
        translation_key="authentication_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: _as_str(wallbox.authentication_mode),
    ),
    HagerSensorDescription(
        key="phases_management",
        translation_key="phases_management",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: _as_str(wallbox.phases_management),
    ),
    HagerSensorDescription(
        key="total_power",
        translation_key="total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _sum_values(
            wallbox.properties.get("pmPowerL1"),
            wallbox.properties.get("pmPowerL2"),
            wallbox.properties.get("pmPowerL3"),
        ),
    ),
    HagerSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _as_float(
            nested_get(wallbox.properties, "wallboxCurrentOverview", "SUN")
        ),
    ),
    HagerSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _as_float(
            nested_get(wallbox.properties, "wallboxCurrentOverview", "NET")
        ),
    ),
    HagerSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda wallbox: _sum_values(
            wallbox.properties.get("pmEnergyL1"),
            wallbox.properties.get("pmEnergyL2"),
            wallbox.properties.get("pmEnergyL3"),
        ),
    ),
    HagerSensorDescription(
        key="max_charge_current",
        translation_key="max_charge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda wallbox: _as_float(wallbox.properties.get("maxChargeCurrent")),
    ),
    HagerSensorDescription(
        key="min_charge_current",
        translation_key="min_charge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda wallbox: _as_float(wallbox.properties.get("minChargeCurrent")),
    ),
    HagerSensorDescription(
        key="phase_count",
        translation_key="phase_count",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wallbox: wallbox.properties.get("numberPhases"),
    ),
    HagerSensorDescription(
        key="status_code",
        translation_key="status_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: _first_not_none(
            wallbox.properties.get("status"),
            wallbox.evse.get("deviceStatusCode"),
        ),
    ),
    HagerSensorDescription(
        key="control_pilot_state",
        translation_key="control_pilot_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: wallbox.properties.get("cpState"),
    ),
    HagerSensorDescription(
        key="session_status",
        translation_key="session_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: _first_not_none(
            wallbox.properties.get("sessionStatus"),
            wallbox.evse.get("lastKnownDeviceStatus"),
            wallbox.evse.get("deviceStatus"),
        ),
    ),
    HagerSensorDescription(
        key="last_status_timestamp",
        translation_key="last_status_timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wallbox: first_parsed_datetime(
            wallbox.evse.get("lastKnownDeviceStatusTimestamp"),
            wallbox.properties.get("lastKnownDeviceStatusTimestamp"),
            nested_get(wallbox.properties, "deviceState", "timestamp"),
            nested_get(wallbox.properties, "deviceState", "lastUpdate"),
        ),
    ),
)


EMC_SENSOR_DESCRIPTIONS: tuple[HagerEmcSensorDescription, ...] = (
    HagerEmcSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda emc: _as_float(emc.grid_power),
    ),
    HagerEmcSensorDescription(
        key="home_power",
        translation_key="home_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda emc: _as_float(emc.home_power),
    ),
    HagerEmcSensorDescription(
        key="device_status",
        translation_key="device_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda emc: _as_str(emc.device_status),
    ),
    HagerEmcSensorDescription(
        key="installation_status",
        translation_key="installation_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda emc: _as_str(emc.installation_status),
    ),
    HagerEmcSensorDescription(
        key="product_name",
        translation_key="product_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda emc: _as_str(emc.product_name),
    ),
    HagerEmcSensorDescription(
        key="short_id",
        translation_key="short_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda emc: _as_str(emc.short_id),
    ),
    HagerEmcSensorDescription(
        key="meter_count",
        translation_key="meter_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda emc: emc.meter_count,
    ),
    HagerEmcSensorDescription(
        key="controlled_device_count",
        translation_key="controlled_device_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda emc: emc.controlled_device_count,
    ),
    HagerEmcSensorDescription(
        key="storage_count",
        translation_key="storage_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda emc: emc.storage_count,
    ),
    HagerEmcSensorDescription(
        key="last_status_timestamp",
        translation_key="last_status_timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda emc: first_parsed_datetime(
            emc.last_status_timestamp,
            emc.installation.get("updatedAt"),
        ),
    ),
)


METER_SENSOR_DESCRIPTIONS: tuple[HagerMeterSensorDescription, ...] = (
    HagerMeterSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda meter: _as_float(meter.current_power),
    ),
    HagerMeterSensorDescription(
        key="peak_power",
        translation_key="peak_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda meter: _as_float(meter.peak_power),
    ),
    HagerMeterSensorDescription(
        key="device_status",
        translation_key="device_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda meter: _as_str(meter.device_status),
    ),
    HagerMeterSensorDescription(
        key="status_code",
        translation_key="status_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda meter: meter.status_code,
    ),
    HagerMeterSensorDescription(
        key="modbus_address",
        translation_key="modbus_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda meter: meter.modbus_address,
    ),
    HagerMeterSensorDescription(
        key="wiring_mode",
        translation_key="wiring_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda meter: _as_str(meter.wiring_mode),
    ),
    HagerMeterSensorDescription(
        key="last_status_timestamp",
        translation_key="last_status_timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda meter: first_parsed_datetime(
            meter.last_status_timestamp,
            meter.meter.get("updatedAt"),
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [
                HagerSensorEntity(coordinator, wallbox_key, description)
                for description in SENSOR_DESCRIPTIONS
            ],
        )
    )
    entry.async_on_unload(
        async_add_emc_entities(
            coordinator,
            async_add_entities,
            lambda emc_key: [
                HagerEmcSensorEntity(coordinator, emc_key, description)
                for description in EMC_SENSOR_DESCRIPTIONS
            ],
        )
    )
    entry.async_on_unload(
        async_add_meter_entities(
            coordinator,
            async_add_entities,
            lambda meter_key: [
                HagerMeterSensorEntity(coordinator, meter_key, description)
                for description in METER_SENSOR_DESCRIPTIONS
            ],
        )
    )


class HagerSensorEntity(HagerLocalEntity, SensorEntity):
    """Representation of a Hager sensor."""

    entity_description: HagerSensorDescription

    def __init__(
        self,
        coordinator,
        wallbox_key: str,
        description: HagerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, wallbox_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.wallbox.device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.wallbox)


class HagerEmcSensorEntity(HagerEmcEntity, SensorEntity):
    """Representation of a Hager EMC sensor."""

    entity_description: HagerEmcSensorDescription

    def __init__(self, coordinator, emc_key: str, description: HagerEmcSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, emc_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.emc.device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.emc)


class HagerMeterSensorEntity(HagerMeterEntity, SensorEntity):
    """Representation of a Hager monitoring meter sensor."""

    entity_description: HagerMeterSensorDescription

    def __init__(
        self,
        coordinator,
        meter_key: str,
        description: HagerMeterSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, meter_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.meter.device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.meter)
