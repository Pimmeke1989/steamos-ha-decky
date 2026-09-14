"""Base entity for the SteamOS integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MACHINE_ID, CONF_MODEL, DOMAIN, MANUFACTURER
from .coordinator import SteamOSCoordinator


class SteamOSEntity(CoordinatorEntity[SteamOSCoordinator]):
    """Entity bound to the single Steam Machine device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SteamOSCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        machine_id = entry.data[CONF_MACHINE_ID]
        self._attr_unique_id = f"{machine_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, machine_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=entry.data.get(CONF_MODEL),
            sw_version=coordinator.data.plugin_version,
            configuration_url=f"http://{entry.data[CONF_HOST]}:{entry.data['port']}/api/info",
        )

    @property
    def available(self) -> bool:
        """Most entities are only meaningful while the Steam Machine is in Gaming Mode."""
        return self.coordinator.data.available
