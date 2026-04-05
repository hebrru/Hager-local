"""Coordinator for Hager Local."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HagerAccountSnapshot,
    HagerApiClient,
    HagerApiConnectionError,
    HagerApiError,
    HagerAuthenticationError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, OPTION_SCAN_INTERVAL

LOGGER = logging.getLogger(__name__)


class HagerDataUpdateCoordinator(DataUpdateCoordinator[HagerAccountSnapshot]):
    """Coordinate Hager API updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: HagerApiClient) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.entry = entry

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

    async def _async_update_data(self) -> HagerAccountSnapshot:
        """Fetch data from Hager."""
        try:
            return await self.api.async_get_overview()
        except HagerAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HagerApiConnectionError as err:
            raise UpdateFailed(f"Unable to communicate with Hager: {err}") from err
        except HagerApiError as err:
            raise UpdateFailed(f"Unexpected Hager API error: {err}") from err
