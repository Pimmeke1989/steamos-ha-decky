"""Image entities for the artwork module (only created when a SteamGridDB key is configured)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .artwork import ASSET_TYPES, ArtworkCoordinator
from .coordinator import SteamOSConfigEntry
from .entity import SteamOSEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.artwork is None:
        return
    async_add_entities(SteamOSArtworkImage(hass, coordinator, coordinator.artwork, kind) for kind in ASSET_TYPES)


class SteamOSArtworkImage(CoordinatorEntity[ArtworkCoordinator], ImageEntity):
    """One asset type (grid / hero / logo / icon) of the running game."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, main, artwork: ArtworkCoordinator, kind: str) -> None:
        CoordinatorEntity.__init__(self, artwork)
        ImageEntity.__init__(self, hass)
        self._kind = kind
        self._attr_translation_key = f"artwork_{kind}"
        # Same device / unique-id scheme as the other entities.
        helper = SteamOSEntity(main, f"artwork_{kind}")
        self._attr_unique_id = helper.unique_id
        self._attr_device_info = helper.device_info
        self._last_url: str | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        url = self.coordinator.data.urls.get(self._kind)
        if url != self._last_url:
            self._last_url = url
            self._cached_image = None  # ImageEntity caches the bytes; a new URL must be fetched again
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return self.coordinator.data.urls.get(self._kind) is not None

    @property
    def image_url(self) -> str | None:
        return self.coordinator.data.urls.get(self._kind)

    @property
    def image_last_updated(self) -> datetime | None:
        return self.coordinator.data.updated

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        return {"game": data.match_name, "sgdb_id": data.sgdb_id}
