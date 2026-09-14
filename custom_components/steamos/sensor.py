"""Sensors: status, running game, system statistics and diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_HAS_BATTERY, STATUS_DISCONNECTED, STATUS_GAMING
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator, SteamOSData
from .entity import SteamOSEntity

GAME_NONE = "none"


@dataclass(frozen=True, kw_only=True)
class SteamOSSensorDescription(SensorEntityDescription):
    """Describes a sensor fed from one key of the ``sys`` section."""

    source_key: str
    value_fn: Callable[[Any], Any] | None = None
    requires_battery: bool = False


def _timestamp(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


SENSORS: tuple[SteamOSSensorDescription, ...] = (
    SteamOSSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        source_key="cpu_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="gpu_temperature",
        translation_key="gpu_temperature",
        source_key="gpu_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="gpu_memory_temperature",
        translation_key="gpu_memory_temperature",
        source_key="gpu_mem_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SteamOSSensorDescription(
        key="ssd_temperature",
        translation_key="ssd_temperature",
        source_key="ssd_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SteamOSSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        source_key="cpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:cpu-64-bit",
    ),
    SteamOSSensorDescription(
        key="cpu_frequency",
        translation_key="cpu_frequency",
        source_key="cpu_ghz",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.GIGAHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SteamOSSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        source_key="mem_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:memory",
    ),
    SteamOSSensorDescription(
        key="gpu_usage",
        translation_key="gpu_usage",
        source_key="gpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:expansion-card",
    ),
    SteamOSSensorDescription(
        key="vram_usage",
        translation_key="vram_usage",
        source_key="vram_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:expansion-card-variant",
    ),
    SteamOSSensorDescription(
        key="gpu_power",
        translation_key="gpu_power",
        source_key="gpu_watt",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="fan_speed",
        translation_key="fan_speed",
        source_key="fan_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:fan",
    ),
    SteamOSSensorDescription(
        key="battery",
        translation_key="battery",
        source_key="battery_pct",
        requires_battery=True,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SteamOSSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        source_key="boot_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_timestamp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    has_battery = bool(entry.data.get(CONF_HAS_BATTERY))
    entities: list[SensorEntity] = [
        SteamOSStatusSensor(coordinator),
        SteamOSGameSensor(coordinator),
        SteamOSVersionSensor(coordinator),
    ]
    entities.extend(
        SteamOSValueSensor(coordinator, description)
        for description in SENSORS
        if has_battery or not description.requires_battery
    )
    if coordinator.artwork is not None:
        entities.append(SteamOSArtworkMatchSensor(coordinator))
    async_add_entities(entities)


class SteamOSStatusSensor(SteamOSEntity, SensorEntity):
    """gaming / disconnected — the one entity that is always available."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_GAMING, STATUS_DISCONNECTED]

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
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
        return {"websocket_connected": data.connected, "plugin_version": data.plugin_version}


class SteamOSVersionSensor(SteamOSEntity, SensorEntity):
    """Which SteamOS release the machine runs (diagnostic).

    Known as soon as the plugin is reachable, so unlike most entities this one does
    not need Gaming Mode — only a live connection.
    """

    _attr_translation_key = "os_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:linux"

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "os_version")

    @property
    def available(self) -> bool:
        return self.coordinator.data.connected and self.coordinator.data.os_version is not None

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.os_version


class SteamOSGameSensor(SteamOSEntity, SensorEntity):
    """Title of the running game, or ``none``."""

    _attr_translation_key = "game"
    _attr_icon = "mdi:controller"

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "game")

    @property
    def native_value(self) -> str:
        game = self.coordinator.data.game
        return game.get("title") or GAME_NONE if game else GAME_NONE

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        game = self.coordinator.data.game or {}
        return {
            "appid": game.get("appid"),
            "shortcut": game.get("shortcut"),
            "started_at": game.get("started_at"),
        }


class SteamOSArtworkMatchSensor(SteamOSEntity, SensorEntity):
    """Which SteamGridDB game the artwork module matched for the current title."""

    _attr_translation_key = "artwork_match"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:image-search"

    def __init__(self, coordinator: SteamOSCoordinator) -> None:
        super().__init__(coordinator, "artwork_match")
        self._artwork = coordinator.artwork

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        assert self._artwork is not None
        self.async_on_remove(self._artwork.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        assert self._artwork is not None
        return self._artwork.data.match_name or GAME_NONE

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        assert self._artwork is not None
        data = self._artwork.data
        return {
            "title": data.title,
            "sgdb_id": data.sgdb_id,
            "overridden": data.overridden,
            "error": data.error,
            "assets": sorted(data.urls),
        }


class SteamOSValueSensor(SteamOSEntity, SensorEntity):
    """One numeric value from the ``sys`` section."""

    entity_description: SteamOSSensorDescription

    def __init__(self, coordinator: SteamOSCoordinator, description: SteamOSSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _raw(self, data: SteamOSData) -> Any:
        return (data.sys or {}).get(self.entity_description.source_key)

    @property
    def available(self) -> bool:
        return super().available and self._raw(self.coordinator.data) is not None

    @property
    def native_value(self) -> Any:
        raw = self._raw(self.coordinator.data)
        if raw is None:
            return None
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(raw)
        return raw
