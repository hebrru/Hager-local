"""Additional read-only entities using the existing coordinator snapshot."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory

from .entity import HagerLocalEntity
from .telemetry_data import bounded_number, charge_summary, finite_number, flag, nested, profile_days, profile_summary

# No additional endpoint and no setter exists in this module.
READINGS = (
    ("installer_upper_current", "upperCurrentLimit", "configuration", UnitOfElectricCurrent.AMPERE, None, None, 0, 1000),
    ("installer_lower_current", "lowerCurrentLimit", "configuration", UnitOfElectricCurrent.AMPERE, None, None, 0, 1000),
    ("solar_ratio_configured", "minCurrentSunMode", "parameters", PERCENTAGE, None, None, 0, 100),
    ("fallback_current_configured", "fallbackMaxCurrent", "parameters", UnitOfElectricCurrent.AMPERE, None, None, 0, 1000),
    ("reference_voltage", "diagPhaseVoltage", "configuration", UnitOfElectricPotential.VOLT, None, None, 0, 1000),
    ("key_position_code", "keyState", "configuration", None, None, None, 0, 65535),
)
FLAGS = (
    ("device_connection_reported", ("deviceState", "deviceConnected")),
    ("device_working_reported", ("deviceState", "deviceWorking")),
    ("current_limit_pending", ("maxChargeCurrentWatingFor",)),
    ("rfid_reader_reported", ("rfidReaderEnabled",)),
    ("authentication_required_reported", ("authRequired",)),
    ("automatic_phase_switch_reported", ("autoPhaseSwitchEnabled",)),
    ("physical_cable_lock_reported", ("cableLock",)),
    ("charge_cancelled_reported", ("chargingCanceled",)),
)


class HagerTelemetrySensor(HagerLocalEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, wallbox_key, specification, enabled=True):
        super().__init__(coordinator, wallbox_key)
        key, self.field, self.source, unit, device_class, state_class, self.minimum, self.maximum = specification
        self._attr_unique_id = f"{self.wallbox.device_id}_{key}"
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_registry_enabled_default = enabled

    @property
    def native_value(self):
        source = self.wallbox.evse_parameters if self.source == "parameters" else self.wallbox.properties
        return bounded_number(source.get(self.field), self.minimum, self.maximum)


class HagerTelemetryBinarySensor(HagerLocalEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, wallbox_key, key, path):
        super().__init__(coordinator, wallbox_key)
        self.path = path
        self._attr_unique_id = f"{self.wallbox.device_id}_{key}"
        self._attr_translation_key = key

    @property
    def is_on(self):
        return flag(nested(self.wallbox.properties, *self.path))


class HagerChargeSummarySensor(HagerLocalEntity, SensorEntity):
    _attr_translation_key = "charge_summary"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["offline", "fault", "charging", "connected_waiting", "disconnected", "unknown"]

    def __init__(self, coordinator, wallbox_key):
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_charge_summary"

    @property
    def native_value(self):
        return charge_summary(self.wallbox.properties)


class HagerReceivedProfileSensor(HagerLocalEntity, SensorEntity):
    _attr_translation_key = "profile_received"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["inactive", "solar_only", "minimum", "delayed", "unknown"]

    def __init__(self, coordinator, wallbox_key):
        super().__init__(coordinator, wallbox_key)
        self._attr_unique_id = f"{self.wallbox.device_id}_profile_received"

    @property
    def native_value(self):
        return profile_summary(self.wallbox.evse_parameters)

    @property
    def extra_state_attributes(self):
        result = dict(super().extra_state_attributes or {})
        result.update({
            "days": profile_days(self.wallbox.evse_parameters),
            "profile_may_be_cached": True,
            "read_only": True,
            "time_basis": "Hager installation local time",
            "note": "Received or remembered profile; not proof of a live acknowledged plan",
        })
        return result


def telemetry_sensors(coordinator, wallbox_key):
    entities = [HagerTelemetrySensor(coordinator, wallbox_key, specification) for specification in READINGS]
    wallbox = coordinator.data.wallboxes[wallbox_key]
    phase_count = finite_number(wallbox.properties.get("numberPhases"))
    for phase in (1, 2, 3):
        enabled = phase == 1 or (phase_count is not None and phase <= phase_count)
        entities.append(HagerTelemetrySensor(coordinator, wallbox_key, (
            f"phase_l{phase}_power", f"pmPowerL{phase}", "configuration", UnitOfPower.WATT,
            SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, -100000, 100000,
        ), enabled))
        entities.append(HagerTelemetrySensor(coordinator, wallbox_key, (
            f"phase_l{phase}_energy", f"pmEnergyL{phase}", "configuration", UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, 0, 1e15,
        ), enabled))
    entities.extend([HagerChargeSummarySensor(coordinator, wallbox_key), HagerReceivedProfileSensor(coordinator, wallbox_key)])
    return entities


def telemetry_binary_sensors(coordinator, wallbox_key):
    return [HagerTelemetryBinarySensor(coordinator, wallbox_key, key, path) for key, path in FLAGS]
