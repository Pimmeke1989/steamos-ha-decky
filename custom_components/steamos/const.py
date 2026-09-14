"""Constants for the SteamOS integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "steamos"
MANUFACTURER: Final = "Valve"
DEFAULT_PORT: Final = 8570
SUPPORTED_API: Final = 1

CONF_TOKEN: Final = "token"
CONF_MACHINE_ID: Final = "machine_id"
CONF_MODEL: Final = "model"
CONF_MAC: Final = "mac"
CONF_HAS_BATTERY: Final = "has_battery"
CONF_WOL_BROADCAST: Final = "wol_broadcast"
DEFAULT_WOL_BROADCAST: Final = "255.255.255.255"

STATUS_GAMING: Final = "gaming"
STATUS_DISCONNECTED: Final = "disconnected"

CLIENT_NAME: Final = "Home Assistant"

# Artwork module (config entry options)
CONF_API_KEY: Final = "api_key"
CONF_ARTWORK_OVERRIDES: Final = "artwork_overrides"
SERVICE_REFRESH_ARTWORK: Final = "refresh_artwork"
