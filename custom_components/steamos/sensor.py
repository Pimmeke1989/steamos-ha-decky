"""Sensors for the SteamOS integration (M1: status only)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import STATUS_DISCONNECTED, STATUS_GAMING
from .coordinator import SteamOSConfigEntry
from .entity import SteamOSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SteamOSStatusSensor(entry.runtime_data)])


class SteamOSStatusSensor(SteamOSEntity, SensorEntity):
    """gaming / disconnected — the one entity that is always available."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_GAMING, STATUS_DISCONNECTED]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        return data.status if data.connected else STATUS_DISCONNECTED

    @property
    def icon(self) -> str:
        return "mdi:gamepad-variant" if self.native_value == STATUS_GAMING else "mdi:gamepad-variant-outline"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        return {
            "websocket_connected": data.connected,
            "plugin_version": data.plugin_version,
        }
