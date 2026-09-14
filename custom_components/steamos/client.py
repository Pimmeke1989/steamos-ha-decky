"""HTTP + WebSocket client for the SteamOS HA Decky plugin."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import SUPPORTED_API

_LOGGER = logging.getLogger(__name__)

WS_PING_INTERVAL = 30
WS_PONG_TIMEOUT = 10
BACKOFF_MIN = 1
BACKOFF_MAX = 60


class SteamOSError(Exception):
    """Base error."""


class CannotConnect(SteamOSError):
    """Host unreachable."""


class InvalidAuth(SteamOSError):
    """Token rejected."""


class NotInGamingMode(SteamOSError):
    """The Steam Machine is not in Gaming Mode."""


class PairingError(SteamOSError):
    """Pairing failed; ``code`` carries the plugin's error string."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnsupportedApi(SteamOSError):
    """Plugin speaks a newer API than we do."""


@dataclass(slots=True)
class DeviceInfo:
    machine_id: str
    name: str
    model: str
    plugin_version: str
    api: int
    paired: bool
    mac: str | None = None


class SteamOSClient:
    """Thin client around the plugin's REST + WebSocket API."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str | None = None) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ helpers

    @property
    def _netloc(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"http://{self._netloc}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self._netloc}/api/ws"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
                **kwargs,
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise CannotConnect(str(err)) from err
        if resp.status == 401:
            raise InvalidAuth
        return resp

    # ------------------------------------------------------------------- REST

    async def get_info(self) -> DeviceInfo:
        resp = await self._request("GET", "/api/info")
        data = await resp.json()
        info = DeviceInfo(
            machine_id=str(data.get("id", "")),
            name=str(data.get("name", "SteamOS")),
            model=str(data.get("model", "SteamOS device")),
            plugin_version=str(data.get("plugin", "?")),
            api=int(data.get("api", 1)),
            paired=bool(data.get("paired", False)),
            mac=data.get("mac") or None,
        )
        if info.api > SUPPORTED_API:
            raise UnsupportedApi(f"plugin API {info.api} > supported {SUPPORTED_API}")
        return info

    async def pair_start(self) -> None:
        resp = await self._request("POST", "/api/pair/start")
        if resp.status == 409:
            raise NotInGamingMode
        if resp.status >= 400:
            raise PairingError(f"http_{resp.status}")

    async def pair(self, code: str, client_name: str) -> str:
        resp = await self._request("POST", "/api/pair", json={"code": code, "client": client_name})
        data = await resp.json(content_type=None) if resp.content_length != 0 else {}
        if resp.status == 200 and data.get("token"):
            self.token = data["token"]
            return self.token
        raise PairingError(str(data.get("error") or f"http_{resp.status}"))

    async def unpair(self) -> None:
        try:
            await self._request("DELETE", "/api/pair")
        except SteamOSError:
            pass

    async def get_state(self) -> dict[str, Any]:
        resp = await self._request("GET", "/api/state")
        return await resp.json()

    async def notify_http(self, payload: dict[str, Any]) -> None:
        resp = await self._request("POST", "/api/notify", json=payload)
        if resp.status == 409:
            raise NotInGamingMode
        if resp.status >= 400:
            raise SteamOSError(f"notify failed: http {resp.status}")

    async def power_http(self, action: str) -> None:
        resp = await self._request("POST", "/api/power", json={"action": action})
        if resp.status == 409:
            raise NotInGamingMode
        if resp.status >= 400:
            raise SteamOSError(f"power action failed: http {resp.status}")

    # -------------------------------------------------------------- WebSocket

    async def run(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[None] | None],
        on_disconnect: Callable[[], Awaitable[None] | None],
    ) -> None:
        """Keep a WebSocket connection alive forever (until cancelled)."""
        backoff = BACKOFF_MIN
        while True:
            try:
                await self._run_once(on_message)
                backoff = BACKOFF_MIN
            except asyncio.CancelledError:
                raise
            except InvalidAuth:
                _LOGGER.error("SteamOS plugin rejected the token; re-pair the integration")
                await _maybe_await(on_disconnect())
                backoff = BACKOFF_MAX
            except (TimeoutError, aiohttp.ClientError, OSError) as err:
                _LOGGER.debug("WebSocket connection to %s failed: %s", self.ws_url, err)
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected WebSocket error: %s", err)
            await _maybe_await(on_disconnect())
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _run_once(self, on_message: Callable[[dict[str, Any]], Awaitable[None] | None]) -> None:
        try:
            async with self._session.ws_connect(
                self.ws_url,
                headers=self._headers(),
                heartbeat=WS_PING_INTERVAL,
                timeout=aiohttp.ClientWSTimeout(ws_receive=WS_PING_INTERVAL + WS_PONG_TIMEOUT, ws_close=10),
            ) as ws:
                self._ws = ws
                _LOGGER.debug("WebSocket connected to %s", self.ws_url)
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            if not isinstance(data, dict):
                                continue
                            if data.get("type") == "result":
                                self._resolve(data)
                                continue
                            await _maybe_await(on_message(data))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                finally:
                    self._ws = None
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.set_exception(CannotConnect("websocket closed"))
                    self._pending.clear()
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise InvalidAuth from err
            raise

    async def send_notify(self, payload: dict[str, Any]) -> None:
        """Send a notification over the WebSocket, falling back to HTTP."""
        await self._command("notify", payload, self.notify_http, payload)

    async def send_power(self, action: str) -> None:
        """suspend / shutdown / reboot over the WebSocket, falling back to HTTP."""
        await self._command("power", {"action": action}, self.power_http, action)

    async def _command(self, msg_type: str, payload: dict[str, Any], http_fallback, *fallback_args: Any) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            await http_fallback(*fallback_args)
            return
        self._msg_id += 1
        msg_id = self._msg_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await ws.send_json({"type": msg_type, "id": msg_id, **payload})
            result = await asyncio.wait_for(fut, 10)
        except (TimeoutError, aiohttp.ClientError) as err:
            self._pending.pop(msg_id, None)
            raise CannotConnect(str(err)) from err
        if not result.get("ok"):
            error = result.get("error") or "unknown"
            if error == "not_in_gaming_mode":
                raise NotInGamingMode
            raise SteamOSError(f"{msg_type} failed: {error}")

    def _resolve(self, data: dict[str, Any]) -> None:
        fut = self._pending.pop(data.get("id"), None)  # type: ignore[arg-type]
        if fut and not fut.done():
            fut.set_result(data)


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if result is not None:
        await result
