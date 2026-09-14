"""Notify entity: shows a toast on the TV while the Steam Machine is in Gaming Mode."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import NotInGamingMode, SteamOSError
from .const import DOMAIN
from .coordinator import SteamOSConfigEntry
from .entity import SteamOSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SteamOSNotifyEntity(entry.runtime_data)])


class SteamOSNotifyEntity(SteamOSEntity, NotifyEntity):
    _attr_translation_key = "toast"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "notify")

    @property
    def available(self) -> bool:
        """Always available: a failed send raises a clear error instead of being skipped silently."""
        return True

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        payload = {"title": title or "Home Assistant", "message": message, "duration": 6}
        try:
            await self.coordinator.client.send_notify(payload)
        except NotInGamingMode as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_in_gaming_mode"
            ) from err
        except SteamOSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="notify_failed", translation_placeholders={"error": str(err)}
            ) from err
