"""Coordinator for Hager Local."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HagerAccountSnapshot,
    HagerApiClient,
    HagerApiConnectionError,
    HagerEmcSnapshot,
    HagerApiError,
    HagerAuthenticationError,
    HagerMeterSnapshot,
    HagerWallboxSnapshot,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, OPTION_SCAN_INTERVAL

LOGGER = logging.getLogger(__name__)
CACHE_VERSION = 1


def _serialize_snapshot(snapshot: HagerAccountSnapshot) -> dict[str, Any]:
    """Convert a normalized snapshot into JSON-serializable storage data."""
    return {
        "account_id": snapshot.account_id,
        "account_email": snapshot.account_email,
        "installations": snapshot.installations,
        "emcs": {
            key: {
                "installation": item.installation,
                "emc_device_link": item.emc_device_link,
                "sub_devices": item.sub_devices,
                "overview": item.overview,
                "status": item.status,
            }
            for key, item in snapshot.emcs.items()
        },
        "meters": {
            key: {
                "installation": item.installation,
                "emc_device_link": item.emc_device_link,
                "meter": item.meter,
                "overview": item.overview,
                "status": item.status,
                "meter_group_size": item.meter_group_size,
            }
            for key, item in snapshot.meters.items()
        },
        "wallboxes": {
            key: {
                "installation": item.installation,
                "emc_device_link": item.emc_device_link,
                "evse": item.evse,
                "configuration": item.configuration,
            }
            for key, item in snapshot.wallboxes.items()
        },
        "fetched_at": snapshot.fetched_at.isoformat(),
    }


def _parse_cached_timestamp(value: Any) -> datetime:
    """Parse a cached timestamp and normalize it to UTC."""
    if not isinstance(value, str):
        return datetime.now(UTC)

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _deserialize_snapshot(data: Any) -> HagerAccountSnapshot | None:
    """Rebuild a normalized snapshot from cached storage data."""
    if not isinstance(data, dict):
        return None

    installations = data.get("installations")
    raw_emcs = data.get("emcs")
    raw_meters = data.get("meters")
    raw_wallboxes = data.get("wallboxes")

    if not isinstance(installations, dict):
        return None
    if not isinstance(raw_emcs, dict):
        return None
    if not isinstance(raw_meters, dict):
        return None
    if not isinstance(raw_wallboxes, dict):
        return None

    emcs: dict[str, HagerEmcSnapshot] = {}
    for key, item in raw_emcs.items():
        if not isinstance(item, dict):
            continue

        installation = item.get("installation")
        emc_device_link = item.get("emc_device_link")
        sub_devices = item.get("sub_devices")
        overview = item.get("overview")
        status = item.get("status")
        if not isinstance(installation, dict):
            continue
        if not isinstance(emc_device_link, dict):
            continue
        if not isinstance(sub_devices, dict):
            continue
        if overview is not None and not isinstance(overview, dict):
            continue
        if status is not None and not isinstance(status, dict):
            continue

        emcs[str(key)] = HagerEmcSnapshot(
            installation=installation,
            emc_device_link=emc_device_link,
            sub_devices=sub_devices,
            overview=overview,
            status=status,
        )

    meters: dict[str, HagerMeterSnapshot] = {}
    for key, item in raw_meters.items():
        if not isinstance(item, dict):
            continue

        installation = item.get("installation")
        emc_device_link = item.get("emc_device_link")
        meter = item.get("meter")
        overview = item.get("overview")
        status = item.get("status")
        meter_group_size = item.get("meter_group_size")
        if not isinstance(installation, dict):
            continue
        if not isinstance(emc_device_link, dict):
            continue
        if not isinstance(meter, dict):
            continue
        if overview is not None and not isinstance(overview, dict):
            continue
        if status is not None and not isinstance(status, dict):
            continue
        if not isinstance(meter_group_size, int):
            continue

        meters[str(key)] = HagerMeterSnapshot(
            installation=installation,
            emc_device_link=emc_device_link,
            meter=meter,
            overview=overview,
            status=status,
            meter_group_size=meter_group_size,
        )

    wallboxes: dict[str, HagerWallboxSnapshot] = {}
    for key, item in raw_wallboxes.items():
        if not isinstance(item, dict):
            continue

        installation = item.get("installation")
        emc_device_link = item.get("emc_device_link")
        evse = item.get("evse")
        configuration = item.get("configuration")
        if not isinstance(installation, dict):
            continue
        if not isinstance(emc_device_link, dict):
            continue
        if not isinstance(evse, dict):
            continue
        if configuration is not None and not isinstance(configuration, dict):
            continue

        wallboxes[str(key)] = HagerWallboxSnapshot(
            installation=installation,
            emc_device_link=emc_device_link,
            evse=evse,
            configuration=configuration,
        )

    return HagerAccountSnapshot(
        account_id=str(data.get("account_id") or ""),
        account_email=str(data["account_email"]) if data.get("account_email") else None,
        installations=installations,
        emcs=emcs,
        meters=meters,
        wallboxes=wallboxes,
        fetched_at=_parse_cached_timestamp(data.get("fetched_at")),
    )


class HagerDataUpdateCoordinator(DataUpdateCoordinator[HagerAccountSnapshot]):
    """Coordinate Hager API updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: HagerApiClient) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.entry = entry
        self._store = Store(hass, CACHE_VERSION, f"{DOMAIN}.{entry.entry_id}")

        scan_interval = timedelta(
            seconds=int(entry.options.get(OPTION_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        )

        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )

    async def async_load_cached_snapshot(self) -> HagerAccountSnapshot | None:
        """Load the last successful snapshot from local storage."""
        payload = await self._store.async_load()
        snapshot = _deserialize_snapshot(payload)
        if payload is not None and snapshot is None:
            LOGGER.warning("Ignoring invalid cached Hager Local snapshot for %s", self.entry.title)
        return snapshot

    async def _async_save_cached_snapshot(self, snapshot: HagerAccountSnapshot) -> None:
        """Persist the last successful snapshot for startup fallback."""
        try:
            await self._store.async_save(_serialize_snapshot(snapshot))
        except Exception:  # pylint: disable=broad-except
            LOGGER.exception("Unable to persist the Hager Local cache for %s", self.entry.title)

    async def _async_update_data(self) -> HagerAccountSnapshot:
        """Fetch data from Hager."""
        try:
            snapshot = await self.api.async_get_overview()
        except HagerAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HagerApiConnectionError as err:
            raise UpdateFailed(f"Unable to communicate with Hager: {err}") from err
        except HagerApiError as err:
            raise UpdateFailed(f"Unexpected Hager API error: {err}") from err

        await self._async_save_cached_snapshot(snapshot)
        return snapshot
