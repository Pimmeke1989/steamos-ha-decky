"""Binary sensors: is a game running, and is the battery charging?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_HAS_BATTERY
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[BinarySensorEntity] = [SteamOSGameRunningSensor(entry.runtime_data)]
    if entry.data.get(CONF_HAS_BATTERY):
        entities.append(SteamOSBatteryChargingSensor(entry.runtime_data))
    async_add_entities(entities)


class SteamOSGameRunningSensor(SteamOSEntity, BinarySensorEntity):
    _attr_translation_key = "game_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "game_running")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.game is not None


class SteamOSBatteryChargingSensor(SteamOSEntity, BinarySensorEntity):
    """Only exists on a machine that has a battery."""

    _attr_translation_key = "battery_charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "battery_charging")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.sys.get("battery_charging") is not None

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.sys.get("battery_charging"))
