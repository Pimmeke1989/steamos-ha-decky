"""Event entity: game_started / game_stopped."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity

EVENT_TYPES = ["game_started", "game_stopped"]


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SteamOSGameEvent(entry.runtime_data)])


class SteamOSGameEvent(SteamOSEntity, EventEntity):
    _attr_translation_key = "game"
    _attr_event_types = EVENT_TYPES
    _attr_icon = "mdi:controller"

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "game_event")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._handle_event))

    @callback
    def _handle_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event not in EVENT_TYPES:
            return
        game = msg.get("game") or {}
        self._trigger_event(
            event,
            {"title": game.get("title"), "appid": game.get("appid"), "shortcut": game.get("shortcut")},
        )
        self.async_write_ha_state()
