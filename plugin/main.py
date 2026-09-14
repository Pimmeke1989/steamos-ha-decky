"""SteamOS ↔ Home Assistant — Decky plugin backend entrypoint.

Runs an aiohttp HTTP/WebSocket server that Home Assistant connects to, advertises
it over mDNS, and tracks whether the Gaming Mode frontend is alive (heartbeat).

Everything the Steam client knows (running game, toasts, the pairing code on
screen) goes through the TypeScript frontend; everything else lives here.
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

import decky  # provided by Decky Loader

from steamos_ha import HEARTBEAT_TIMEOUT_S, PLUGIN_VERSION
from steamos_ha.discovery import Discovery, read_machine_id, read_model
from steamos_ha.server import Server
from steamos_ha.settings import Settings
from steamos_ha.state import STATUS_DISCONNECTED, STATUS_GAMING, State

log = decky.logger


class Plugin:
    # ------------------------------------------------------------------ lifecycle
    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.settings = Settings(os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json"))
        self.state = State()
        self.machine_id = read_machine_id()
        self.hostname = socket.gethostname() or "steammachine"
        self.model = read_model()
        self.last_heartbeat: float = 0.0
        self.pairing_code: str | None = None
        self.server_error: str | None = None

        self.server = Server(
            self.settings,
            self.state,
            machine_id=self.machine_id,
            hostname=self.hostname,
            model=self.model,
            show_code=self._show_pairing_code,
            notify=self._notify_frontend,
        )
        self.discovery = Discovery(self.settings.port, self.machine_id, self.hostname, self.model)

        try:
            await self.server.start(self.settings.port)
        except OSError as err:
            self.server_error = f"Port {self.settings.port} unavailable: {err}"
            log.error(self.server_error)
        else:
            await self.discovery.start()

        self._watchdog = self.loop.create_task(self._heartbeat_watchdog())
        log.info(
            "SteamOS HA %s ready (id=%s, host=%s, model=%s)", PLUGIN_VERSION, self.machine_id, self.hostname, self.model
        )

    async def _unload(self) -> None:
        log.info("Unloading")
        watchdog = getattr(self, "_watchdog", None)
        if watchdog:
            watchdog.cancel()
        await self.discovery.stop()
        await self.server.stop()

    async def _uninstall(self) -> None:
        pass

    async def _migration(self) -> None:
        pass

    # ---------------------------------------------------------- frontend -> backend
    async def heartbeat(self) -> dict[str, Any]:
        """Called every few seconds by the frontend while Gaming Mode is up."""
        self.last_heartbeat = self.loop.time()
        if self.state.status != STATUS_GAMING:
            await self.server.broadcast(self.state.set_status(STATUS_GAMING))
            log.info("Frontend alive → status gaming")
        return await self.get_status()

    async def set_running_app(self, appid: int | None = None, name: str | None = None, shortcut: bool = False) -> None:
        """Frontend reports the running app (None/None when nothing runs)."""
        before = self.state.game
        update = self.state.set_game(appid, name, shortcut)
        if update is None:
            return
        after = self.state.game
        if before and (not after or after.get("appid") != before.get("appid")):
            await self.server.broadcast(_event("game_stopped", before, update["ts"]))
        if after and (not before or after.get("appid") != before.get("appid")):
            await self.server.broadcast(_event("game_started", after, update["ts"]))
        await self.server.broadcast(update)

    async def get_status(self) -> dict[str, Any]:
        """Everything the QAM panel shows."""
        return {
            "version": PLUGIN_VERSION,
            "status": self.state.status,
            "port": self.settings.port,
            "server_error": self.server_error,
            "discovery": self.discovery.backend,
            "paired": self.settings.paired,
            "clients": self.settings.public_clients(),
            "connected": self.server.connected_clients,
            "pairing_code": self.pairing_code,
            "game": self.state.game,
            "hostname": self.hostname,
            "machine_id": self.machine_id,
        }

    async def get_settings(self) -> dict[str, Any]:
        return {"port": self.settings.port, "mangohud": dict(self.settings.mangohud)}

    async def set_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        old_port = self.settings.port
        self.settings.update(changes or {})
        if self.settings.port != old_port:
            await self._restart_server()
        return await self.get_settings()

    async def unpair_all(self) -> dict[str, Any]:
        self.settings.revoke_all()
        log.info("All Home Assistant tokens revoked")
        return await self.get_status()

    async def cancel_pairing(self) -> None:
        self.server.pairing = None
        await self._show_pairing_code(None)

    # ---------------------------------------------------------- backend -> frontend
    async def _show_pairing_code(self, code: str | None) -> None:
        self.pairing_code = code
        await decky.emit("pairing_code", code)

    async def _notify_frontend(self, payload: dict[str, Any]) -> bool:
        if self.state.status != STATUS_GAMING:
            return False
        await decky.emit("notify", payload)
        return True

    # ---------------------------------------------------------------- internals
    async def _heartbeat_watchdog(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                if self.state.status == STATUS_GAMING and self.loop.time() - self.last_heartbeat > HEARTBEAT_TIMEOUT_S:
                    log.info("No heartbeat for %.0fs → status disconnected", HEARTBEAT_TIMEOUT_S)
                    await self.server.broadcast(self.state.set_status(STATUS_DISCONNECTED))
                    await self._show_pairing_code(None)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("watchdog: %s", err)

    async def _restart_server(self) -> None:
        await self.discovery.stop()
        await self.server.stop()
        self.server_error = None
        try:
            await self.server.start(self.settings.port)
        except OSError as err:
            self.server_error = f"Port {self.settings.port} unavailable: {err}"
            log.error(self.server_error)
            return
        self.discovery = Discovery(self.settings.port, self.machine_id, self.hostname, self.model)
        await self.discovery.start()


def _event(name: str, game: dict[str, Any], ts: str) -> dict[str, Any]:
    return {
        "type": "event",
        "event": name,
        "game": {k: game.get(k) for k in ("title", "appid", "shortcut")},
        "ts": ts,
    }
