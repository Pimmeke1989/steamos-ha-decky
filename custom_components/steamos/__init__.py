"""SteamOS integration: connects a Steam Machine running the SteamOS HA Decky plugin."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import SteamOSClient
from .const import CONF_TOKEN
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.NOTIFY, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SteamOSConfigEntry) -> bool:
    client = SteamOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    coordinator = SteamOSCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
