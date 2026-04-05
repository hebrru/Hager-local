"""Button platform for Hager Local."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import HagerApiError
from .const import CHARGING_MODE_BOOST
from .entity import HagerLocalEntity, async_add_wallbox_entities


@dataclass(kw_only=True)
class HagerButtonDescription(ButtonEntityDescription):
    """Describe a Hager button."""

    press_fn: Callable[[object, object], Awaitable[None]]


BUTTON_DESCRIPTIONS: tuple[HagerButtonDescription, ...] = (
    HagerButtonDescription(
        key="boost",
        translation_key="boost",
        press_fn=lambda api, wallbox: api.async_set_charging_mode(
            wallbox, CHARGING_MODE_BOOST
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hager buttons."""
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_add_wallbox_entities(
            coordinator,
            async_add_entities,
            lambda wallbox_key: [
                HagerButtonEntity(coordinator, wallbox_key, description)
                for description in BUTTON_DESCRIPTIONS
            ],
        )
    )


class HagerButtonEntity(HagerLocalEntity, ButtonEntity):
    """Representation of a Hager action button."""

    entity_description: HagerButtonDescription

    def __init__(self, coordinator, wallbox_key: str, description: HagerButtonDescription) -> None:
        """Initialize the button."""
        super().__init__(coordinator, wallbox_key)
        self.entity_description = description
        self._attr_unique_id = f"{self.wallbox.device_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self.entity_description.press_fn(self.coordinator.api, self.wallbox)
            await self.coordinator.async_request_refresh()
        except HagerApiError as err:
            raise HomeAssistantError(str(err)) from err
