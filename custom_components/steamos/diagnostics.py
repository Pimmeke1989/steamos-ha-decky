"""Diagnostics support (token is redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_TOKEN
from .coordinator import SteamOSConfigEntry

TO_REDACT = {CONF_TOKEN, CONF_API_KEY}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SteamOSConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    art = coordinator.artwork
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "data": asdict(coordinator.data),
        "artwork": {**asdict(art.data), "updated": str(art.data.updated)} if art else None,
    }
