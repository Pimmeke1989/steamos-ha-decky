"""SteamOS integration: connects a Steam Machine running the SteamOS HA Decky plugin."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import artwork as artwork_mod
from .artwork import ArtworkCoordinator, parse_overrides
from .client import SteamOSClient
from .const import CONF_API_KEY, CONF_ARTWORK_OVERRIDES, CONF_TOKEN, DOMAIN, SERVICE_REFRESH_ARTWORK
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.NOTIFY,
    Platform.SENSOR,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: SteamOSConfigEntry) -> bool:
    client = SteamOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    coordinator = SteamOSCoordinator(hass, entry, client)
    entry.runtime_data = coordinator

    api_key = (entry.options.get(CONF_API_KEY) or "").strip()
    if api_key:
        art = ArtworkCoordinator(
            hass,
            entry.entry_id,
            api_key,
            parse_overrides(entry.options.get(CONF_ARTWORK_OVERRIDES, "")),
            base_url=artwork_mod.SGDB_BASE_URL,
        )
        await art.async_load()
        coordinator.artwork = art

        @callback
        def _follow_title() -> None:
            game = coordinator.data.game if coordinator.data.available else None
            art.async_set_title(game.get("title") if game else None)

        entry.async_on_unload(coordinator.async_add_listener(_follow_title))

    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SteamOSConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: SteamOSConfigEntry) -> None:
    """Revoke our token on the plugin when the entry is deleted (best effort)."""
    client = SteamOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data.get(CONF_TOKEN),
    )
    await client.unpair()


async def _async_options_updated(hass: HomeAssistant, entry: SteamOSConfigEntry) -> None:
    """Options (API key, overrides) changed → reload so the artwork module is (re)built."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_ARTWORK):
        return

    async def _refresh_artwork(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            art = entry.runtime_data.artwork
            if art is not None:
                await art.async_refresh_current()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_ARTWORK, _refresh_artwork)
