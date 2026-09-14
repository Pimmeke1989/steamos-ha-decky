"""Sensors: status, running game, performance and system statistics."""

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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import STATUS_DISCONNECTED, STATUS_GAMING
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator, SteamOSData
from .entity import SteamOSEntity

GAME_NONE = "none"


@dataclass(frozen=True, kw_only=True)
class SteamOSSensorDescription(SensorEntityDescription):
    """Describes a sensor fed from one key of the ``sys`` or ``perf`` section."""

    section: str  # "sys" | "perf"
    source_key: str
    value_fn: Callable[[Any], Any] | None = None


def _timestamp(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


SENSORS: tuple[SteamOSSensorDescription, ...] = (
    SteamOSSensorDescription(
        key="fps",
        translation_key="fps",
        section="perf",
        source_key="fps",
        native_unit_of_measurement="FPS",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:speedometer",
    ),
    SteamOSSensorDescription(
        key="frametime",
        translation_key="frametime",
        section="perf",
        source_key="frametime_ms",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SteamOSSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        section="sys",
        source_key="cpu_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="gpu_temperature",
        translation_key="gpu_temperature",
        section="sys",
        source_key="gpu_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="gpu_memory_temperature",
        translation_key="gpu_memory_temperature",
        section="sys",
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
        section="sys",
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
        section="sys",
        source_key="cpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:cpu-64-bit",
    ),
    SteamOSSensorDescription(
        key="cpu_frequency",
        translation_key="cpu_frequency",
        section="sys",
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
        section="sys",
        source_key="mem_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:memory",
    ),
    SteamOSSensorDescription(
        key="gpu_usage",
        translation_key="gpu_usage",
        section="sys",
        source_key="gpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:expansion-card",
    ),
    SteamOSSensorDescription(
        key="vram_usage",
        translation_key="vram_usage",
        section="sys",
        source_key="vram_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:expansion-card-variant",
    ),
    SteamOSSensorDescription(
        key="gpu_power",
        translation_key="gpu_power",
        section="sys",
        source_key="gpu_watt",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SteamOSSensorDescription(
        key="fan_speed",
        translation_key="fan_speed",
        section="sys",
        source_key="fan_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:fan",
    ),
    SteamOSSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        section="sys",
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
    entities: list[SensorEntity] = [SteamOSStatusSensor(coordinator), SteamOSGameSensor(coordinator)]
    entities.extend(SteamOSValueSensor(coordinator, description) for description in SENSORS)
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
        perf = self.coordinator.data.perf or {}
        focus = perf.get("focus")
        return {
            "appid": game.get("appid"),
            "shortcut": game.get("shortcut"),
            "started_at": game.get("started_at"),
            "steam_ui_focused": None if focus is None else focus == "steam",
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
    """One numeric value from the ``sys`` or ``perf`` section."""

    entity_description: SteamOSSensorDescription

    def __init__(self, coordinator: SteamOSCoordinator, description: SteamOSSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _raw(self, data: SteamOSData) -> Any:
        section = data.perf if self.entity_description.section == "perf" else data.sys
        if not section:
            return None
        return section.get(self.entity_description.source_key)

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
