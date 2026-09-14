"""Update entity: is there a newer release of the plugin on GitHub?

Purely informational — the plugin is installed through Decky, so there is no
``install`` support. Checks the latest GitHub release every 6 hours.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity

_LOGGER = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/Pimmeke1989/steamos-ha-decky/releases/latest"
CHECK_INTERVAL = timedelta(hours=6)


class ReleaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Latest release on GitHub: {"version": "0.2.0", "url": ..., "notes": ...}."""

    def __init__(self, hass: HomeAssistant, url: str = RELEASES_URL) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} releases", update_interval=CHECK_INTERVAL)
        self._url = url
        self._session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            resp = await self._session.get(
                self._url,
                headers={"Accept": "application/vnd.github+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise UpdateFailed(f"GitHub unreachable: {err}") from err
        if resp.status == 404:
            return {}  # no release published yet
        if resp.status >= 400:
            raise UpdateFailed(f"GitHub returned {resp.status}")
        body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("tag_name"):
            return {}
        return {
            "version": str(body["tag_name"]).lstrip("v"),
            "url": body.get("html_url"),
            "notes": body.get("body"),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from . import update as update_mod  # patchable URL for tests

    releases = ReleaseCoordinator(hass, update_mod.RELEASES_URL)
    await releases.async_refresh()  # never raises; a failure just makes the entity unavailable
    async_add_entities([SteamOSPluginUpdate(entry.runtime_data, releases)])


class SteamOSPluginUpdate(SteamOSEntity, UpdateEntity):
    _attr_translation_key = "plugin"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES
    _attr_title = "SteamOS HA Decky plugin"

    def __init__(self, coordinator: SteamOSCoordinator, releases: ReleaseCoordinator) -> None:
        super().__init__(coordinator, "plugin_update")
        self._releases = releases

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._releases.async_add_listener(self._on_release))

    @callback
    def _on_release(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        # Needs the plugin version (from the WebSocket hello) and a successful GitHub check.
        return self.coordinator.data.plugin_version is not None and self._releases.last_update_success

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.plugin_version

    @property
    def latest_version(self) -> str | None:
        data = self._releases.data or {}
        # No release yet → report installed as latest so the entity shows "up to date".
        return data.get("version") or self.installed_version

    @property
    def release_url(self) -> str | None:
        return (self._releases.data or {}).get("url")

    async def async_release_notes(self) -> str | None:
        return (self._releases.data or {}).get("notes")
