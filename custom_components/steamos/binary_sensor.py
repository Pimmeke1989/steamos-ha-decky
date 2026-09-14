"""Binary sensor: is a game running?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SteamOSGameRunningSensor(entry.runtime_data)])


class SteamOSGameRunningSensor(SteamOSEntity, BinarySensorEntity):
    _attr_translation_key = "game_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "game_running")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.game is not None
