"""Push coordinator: holds the last state received over the WebSocket."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .artwork import ArtworkCoordinator
from .client import SteamOSClient
from .const import CONF_HAS_BATTERY, CONF_MAC, DOMAIN, STATUS_DISCONNECTED, STATUS_GAMING

_LOGGER = logging.getLogger(__name__)


@dataclass
class SteamOSData:
    """Normalised state as the entities see it."""

    status: str = STATUS_DISCONNECTED
    game: dict[str, Any] | None = None
    sys: dict[str, Any] = field(default_factory=dict)
    plugin_version: str | None = None
    mac: str | None = None
    os_version: str | None = None
    connected: bool = False

    @property
    def available(self) -> bool:
        return self.connected and self.status == STATUS_GAMING


SteamOSConfigEntry = ConfigEntry["SteamOSCoordinator"]


class SteamOSCoordinator(DataUpdateCoordinator[SteamOSData]):
    """Receives push updates from the plugin; never polls."""

    config_entry: SteamOSConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SteamOSConfigEntry, client: SteamOSClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=None,
        )
        self.client = client
        self.artwork: ArtworkCoordinator | None = None
        self._task: asyncio.Task | None = None
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []
        self.data = SteamOSData()

    # ------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        self._task = self.config_entry.async_create_background_task(
            self.hass, self.client.run(self._on_message, self._on_disconnect), name=f"{DOMAIN}-ws"
        )

    async def async_stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # -------------------------------------------------------------- messages

    async def _on_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        data = self.data
        if mtype == "hello":
            data.plugin_version = msg.get("plugin")
            data.mac = msg.get("mac") or None
            data.os_version = msg.get("os_version") or None
            data.connected = True
            # Keep the entry in step with the machine: the MAC decides whether Wake-on-LAN
            # can work, the battery flag whether the battery entities exist at all (a
            # change there needs a reload, which HA does for us on an entry update).
            fixed = {}
            if data.mac and self.config_entry.data.get(CONF_MAC) != data.mac:
                fixed[CONF_MAC] = data.mac
            if "battery" in msg and self.config_entry.data.get(CONF_HAS_BATTERY) != bool(msg["battery"]):
                fixed[CONF_HAS_BATTERY] = bool(msg["battery"])
            if fixed:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, **fixed}
                )
            return
        if mtype == "state":
            data.connected = True
            data.status = msg.get("status", STATUS_DISCONNECTED)
            data.game = msg.get("game")
            data.sys = msg.get("sys") or {}
            self.async_set_updated_data(data)
            return
        if mtype == "update":
            if "status" in msg:
                data.status = msg["status"] or STATUS_DISCONNECTED
            if "game" in msg:
                data.game = msg["game"]
            if "sys" in msg:
                data.sys = msg["sys"] or {}
            self.async_set_updated_data(data)
            return
        if mtype == "event":
            for listener in self._event_listeners:
                listener(msg)
            return
        if mtype in ("pong", "error"):
            return
        _LOGGER.debug("Unhandled message type %s", mtype)

    async def _on_disconnect(self) -> None:
        data = self.data
        if data.connected or data.status != STATUS_DISCONNECTED:
            data.connected = False
            data.status = STATUS_DISCONNECTED
            data.game = None
            self.async_set_updated_data(data)

    @callback
    def async_add_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._event_listeners.append(listener)

        @callback
        def _remove() -> None:
            self._event_listeners.remove(listener)

        return _remove
