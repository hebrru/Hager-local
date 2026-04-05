"""Shared entity helpers for Hager Local."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HagerEmcSnapshot, HagerMeterSnapshot, HagerWallboxSnapshot
from .const import DEFAULT_STATUS_STALE_MINUTES, DOMAIN, OPTION_STATUS_STALE_MINUTES
from .coordinator import HagerDataUpdateCoordinator


def parse_hager_datetime(value: Any) -> datetime | None:
    """Parse a Hager UTC timestamp."""
    if not value or not isinstance(value, str):
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def nested_get(value: Any, *path: str) -> Any:
    """Safely traverse nested dictionaries."""
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_parsed_datetime(*values: Any) -> datetime | None:
    """Return the first valid timestamp from a list of candidates."""
    for value in values:
        parsed = parse_hager_datetime(value)
        if parsed is not None:
            return parsed
    return None


@dataclass(slots=True)
class HagerWallboxEntityDescriptionMixin:
    """Shared helper for custom entity descriptions."""

    value_fn: Callable[[HagerWallboxSnapshot], Any]


@dataclass(slots=True)
class HagerEmcEntityDescriptionMixin:
    """Shared helper for EMC entity descriptions."""

    value_fn: Callable[[HagerEmcSnapshot], Any]


@dataclass(slots=True)
class HagerMeterEntityDescriptionMixin:
    """Shared helper for meter entity descriptions."""

    value_fn: Callable[[HagerMeterSnapshot], Any]


def _status_indicates_online(status: Any) -> bool | None:
    """Return whether a Hager textual status looks online."""
    if not isinstance(status, str):
        return None

    normalized = status.strip().casefold()
    if not normalized:
        return None
    if normalized in {"offline", "disconnected", "decommissioned", "removed"}:
        return False
    return normalized in {"ok", "online", "connected", "paired", "ready", "active"}


def _is_recent_timestamp(
    coordinator: HagerDataUpdateCoordinator,
    *timestamps: Any,
) -> bool | None:
    """Return whether one of the timestamps is still considered fresh."""
    received_at = first_parsed_datetime(*timestamps)
    if received_at is None:
        return None

    stale_after = timedelta(
        minutes=int(
            coordinator.entry.options.get(
                OPTION_STATUS_STALE_MINUTES,
                DEFAULT_STATUS_STALE_MINUTES,
            )
        )
    )
    return datetime.now(UTC) - received_at <= stale_after


class HagerLocalEntity(CoordinatorEntity[HagerDataUpdateCoordinator], Entity):
    """Base entity for Hager Local."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HagerDataUpdateCoordinator, wallbox_key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.wallbox_key = wallbox_key

    @property
    def wallbox(self) -> HagerWallboxSnapshot:
        """Return the current wallbox snapshot."""
        return self.coordinator.data.wallboxes[self.wallbox_key]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        wallbox = self.wallbox
        mac_address = wallbox.media_parameters.get("macAddress")
        return DeviceInfo(
            identifiers={(DOMAIN, wallbox.device_id)},
            connections=(
                {(CONNECTION_NETWORK_MAC, str(mac_address).lower())}
                if mac_address
                else None
            ),
            manufacturer="Hager",
            model=wallbox.device_reference or "Witty Solar",
            name=wallbox.display_name,
            serial_number=wallbox.serial_number,
            suggested_area=wallbox.installation_name,
            via_device=(DOMAIN, wallbox.emc_hardware_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose stable context attributes."""
        wallbox = self.wallbox
        attrs: dict[str, Any] = {
            "installation_id": wallbox.installation_id,
            "installation_name": wallbox.installation_name,
            "emc_link_id": wallbox.emc_link_id,
            "emc_hardware_id": wallbox.emc_hardware_id,
            "evse_id": wallbox.evse_id,
            "wallbox_id": wallbox.wallbox_id,
            "configuration_id": wallbox.configuration_id,
            "media": wallbox.media,
        }
        if wallbox.device_reference:
            attrs["device_reference"] = wallbox.device_reference
        mac_address = wallbox.media_parameters.get("macAddress")
        if mac_address:
            attrs["mac_address"] = mac_address
        location = wallbox.installation.get("locationName")
        if location:
            attrs["installation_location"] = location
        return attrs

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.wallbox_key in self.coordinator.data.wallboxes

    def is_online(self) -> bool:
        """Return if the wallbox appears online."""
        wallbox = self.wallbox
        device_connected = nested_get(wallbox.properties, "deviceState", "deviceConnected")
        if device_connected is False:
            return False

        received_at = first_parsed_datetime(
            wallbox.evse.get("lastKnownDeviceStatusTimestamp"),
            wallbox.properties.get("lastKnownDeviceStatusTimestamp"),
            nested_get(wallbox.properties, "deviceState", "timestamp"),
            nested_get(wallbox.properties, "deviceState", "lastUpdate"),
        )

        if received_at is None:
            if device_connected is not None:
                return bool(device_connected)
            return wallbox.configuration is not None

        stale_after = timedelta(
            minutes=int(
                self.coordinator.entry.options.get(
                    OPTION_STATUS_STALE_MINUTES,
                    DEFAULT_STATUS_STALE_MINUTES,
                )
            )
        )
        return datetime.now(UTC) - received_at <= stale_after


class HagerEmcEntity(CoordinatorEntity[HagerDataUpdateCoordinator], Entity):
    """Base entity for a Hager Flow EMC."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HagerDataUpdateCoordinator, emc_key: str) -> None:
        """Initialize the EMC entity."""
        super().__init__(coordinator)
        self.emc_key = emc_key

    @property
    def emc(self) -> HagerEmcSnapshot:
        """Return the current EMC snapshot."""
        return self.coordinator.data.emcs[self.emc_key]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        emc = self.emc
        return DeviceInfo(
            identifiers={(DOMAIN, emc.device_id)},
            manufacturer="Hager",
            model=emc.product_name or "Flow EMC",
            name=emc.display_name,
            serial_number=emc.serial_number,
            suggested_area=emc.installation_name,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose stable EMC context attributes."""
        emc = self.emc
        attrs: dict[str, Any] = {
            "installation_id": emc.installation_id,
            "installation_name": emc.installation_name,
            "emc_link_id": emc.emc_link_id,
            "emc_hardware_id": emc.device_id,
            "meter_count": emc.meter_count,
            "controlled_device_count": emc.controlled_device_count,
            "storage_count": emc.storage_count,
        }
        if emc.short_id:
            attrs["short_id"] = emc.short_id
        if emc.product_name:
            attrs["product_name"] = emc.product_name
        location = emc.installation.get("locationName")
        if location:
            attrs["installation_location"] = location
        return attrs

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.emc_key in self.coordinator.data.emcs

    def is_online(self) -> bool | None:
        """Return if the EMC appears online."""
        recent = _is_recent_timestamp(
            self.coordinator,
            self.emc.last_status_timestamp,
            self.emc.installation.get("updatedAt"),
        )
        if recent is not None:
            return recent
        return _status_indicates_online(self.emc.device_status)


class HagerMeterEntity(CoordinatorEntity[HagerDataUpdateCoordinator], Entity):
    """Base entity for a Hager monitoring meter."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HagerDataUpdateCoordinator, meter_key: str) -> None:
        """Initialize the meter entity."""
        super().__init__(coordinator)
        self.meter_key = meter_key

    @property
    def meter(self) -> HagerMeterSnapshot:
        """Return the current meter snapshot."""
        return self.coordinator.data.meters[self.meter_key]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        meter = self.meter
        return DeviceInfo(
            identifiers={(DOMAIN, meter.device_id)},
            manufacturer="Hager",
            model=meter.device_type or meter.media or "Meter",
            name=meter.display_name,
            serial_number=meter.meter_id,
            suggested_area=meter.installation_name,
            via_device=(DOMAIN, meter.emc_device_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose stable meter context attributes."""
        meter = self.meter
        attrs: dict[str, Any] = {
            "installation_id": meter.installation_id,
            "installation_name": meter.installation_name,
            "emc_hardware_id": meter.emc_device_id,
            "meter_id": meter.meter_id,
        }
        if meter.device_type:
            attrs["device_type"] = meter.device_type
        if meter.media:
            attrs["media"] = meter.media
        if meter.modbus_address is not None:
            attrs["modbus_address"] = meter.modbus_address
        if meter.wiring_mode:
            attrs["wiring_mode"] = meter.wiring_mode
        location = meter.installation.get("locationName")
        if location:
            attrs["installation_location"] = location
        return attrs

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.meter_key in self.coordinator.data.meters

    def is_online(self) -> bool | None:
        """Return if the meter appears online."""
        recent = _is_recent_timestamp(
            self.coordinator,
            self.meter.last_status_timestamp,
            self.meter.meter.get("updatedAt"),
        )
        if recent is not None:
            return recent
        return _status_indicates_online(self.meter.device_status)


@callback
def async_add_wallbox_entities(
    coordinator: HagerDataUpdateCoordinator,
    async_add_entities: Callable[[list[Entity]], None],
    factory: Callable[[str], list[Entity]],
) -> Callable[[], None]:
    """Add entities for all current and future wallboxes."""
    known_keys: set[str] = set()

    @callback
    def _add_missing_entities() -> None:
        new_entities: list[Entity] = []
        for wallbox_key in coordinator.data.wallboxes:
            if wallbox_key in known_keys:
                continue
            known_keys.add(wallbox_key)
            new_entities.extend(factory(wallbox_key))

        if new_entities:
            async_add_entities(new_entities)

    _add_missing_entities()
    return coordinator.async_add_listener(_add_missing_entities)


@callback
def async_add_emc_entities(
    coordinator: HagerDataUpdateCoordinator,
    async_add_entities: Callable[[list[Entity]], None],
    factory: Callable[[str], list[Entity]],
) -> Callable[[], None]:
    """Add entities for all current and future EMC devices."""
    known_keys: set[str] = set()

    @callback
    def _add_missing_entities() -> None:
        new_entities: list[Entity] = []
        for emc_key in coordinator.data.emcs:
            if emc_key in known_keys:
                continue
            known_keys.add(emc_key)
            new_entities.extend(factory(emc_key))

        if new_entities:
            async_add_entities(new_entities)

    _add_missing_entities()
    return coordinator.async_add_listener(_add_missing_entities)


@callback
def async_add_meter_entities(
    coordinator: HagerDataUpdateCoordinator,
    async_add_entities: Callable[[list[Entity]], None],
    factory: Callable[[str], list[Entity]],
) -> Callable[[], None]:
    """Add entities for all current and future monitoring meters."""
    known_keys: set[str] = set()

    @callback
    def _add_missing_entities() -> None:
        new_entities: list[Entity] = []
        for meter_key in coordinator.data.meters:
            if meter_key in known_keys:
                continue
            known_keys.add(meter_key)
            new_entities.extend(factory(meter_key))

        if new_entities:
            async_add_entities(new_entities)

    _add_missing_entities()
    return coordinator.async_add_listener(_add_missing_entities)
