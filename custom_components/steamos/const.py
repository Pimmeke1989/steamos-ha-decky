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

STATUS_GAMING: Final = "gaming"
STATUS_DISCONNECTED: Final = "disconnected"

CLIENT_NAME: Final = "Home Assistant"
