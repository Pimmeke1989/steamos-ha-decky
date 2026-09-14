"""Notify entity + ``steamos.notify`` action: toasts on the TV while in Gaming Mode."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import NotInGamingMode, SteamOSError
from .const import DOMAIN
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity

SERVICE_NOTIFY = "notify"
ATTR_TITLE = "title"
ATTR_MESSAGE = "message"
ATTR_DURATION = "duration"
ATTR_ICON = "icon"

# Icons the plugin frontend knows how to draw (react-icons); anything else falls back to "home".
ICONS = ["home", "bell", "info", "alert", "check", "door", "phone", "message", "washer", "car", "clock", "sun"]
DEFAULT_DURATION = 6

NOTIFY_SCHEMA = {
    vol.Optional(ATTR_TITLE): cv.string,
    vol.Required(ATTR_MESSAGE): cv.string,
    vol.Optional(ATTR_DURATION, default=DEFAULT_DURATION): vol.All(vol.Coerce(float), vol.Range(min=1, max=60)),
    vol.Optional(ATTR_ICON, default="home"): vol.In(ICONS),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SteamOSNotifyEntity(entry.runtime_data)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_NOTIFY, NOTIFY_SCHEMA, "async_notify_extended")


class SteamOSNotifyEntity(SteamOSEntity, NotifyEntity):
    _attr_translation_key = "toast"

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "notify")

    @property
    def available(self) -> bool:
        """Always available: a failed send raises a clear error instead of being skipped silently."""
        return True

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        await self._send({"title": title or "Home Assistant", "message": message, "duration": DEFAULT_DURATION})

    async def async_notify_extended(
        self, message: str, title: str | None = None, duration: float = DEFAULT_DURATION, icon: str = "home"
    ) -> None:
        """``steamos.notify``: like send_message, plus duration (s) and icon."""
        await self._send({"title": title or "Home Assistant", "message": message, "duration": duration, "icon": icon})

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            await self.coordinator.client.send_notify(payload)
        except NotInGamingMode as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_in_gaming_mode") from err
        except SteamOSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="notify_failed", translation_placeholders={"error": str(err)}
            ) from err
