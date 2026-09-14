"""HTTP + WebSocket server that Home Assistant connects to.

Routes (see docs/api.md):

    GET    /api/info          public
    POST   /api/pair/start    public   ask the plugin UI to show a pairing code
    POST   /api/pair          public   {"code": "483921", "client": "..."} -> {"token": "..."}
    DELETE /api/pair          auth     revoke the calling token
    GET    /api/state         auth
    POST   /api/notify        auth     {"title","message","duration","icon"}
    POST   /api/power         auth     {"action": "suspend" | "shutdown" | "reboot"}
    GET    /api/ws            auth     WebSocket

The server itself knows nothing about Decky: it gets callbacks for the things
that need the frontend (showing the pairing code, showing a toast).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import WSMsgType, web

from . import API_VERSION, PLUGIN_VERSION
from .settings import Settings
from .state import STATUS_GAMING, State

log = logging.getLogger("steamos_ha.server")

PAIR_CODE_TTL_S = 300
PAIR_MAX_ATTEMPTS = 5
NOTIFY_MAX_LEN = 200
WS_SEND_MIN_INTERVAL_S = 1.0

ShowCodeCb = Callable[[str | None], Awaitable[None]]
NotifyCb = Callable[[dict[str, Any]], Awaitable[bool]]
PowerCb = Callable[[str], Awaitable[bool]]
POWER_ACTIONS = ("suspend", "shutdown", "reboot")


class PairingSession:
    def __init__(self) -> None:
        self.code = "".join(secrets.choice("0123456789") for _ in range(6))
        self.expires = time.monotonic() + PAIR_CODE_TTL_S
        self.attempts = 0

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires or self.attempts >= PAIR_MAX_ATTEMPTS

    @property
    def pretty(self) -> str:
        return f"{self.code[:3]} {self.code[3:]}"


class Server:
    def __init__(
        self,
        settings: Settings,
        state: State,
        *,
        machine_id: str,
        hostname: str,
        model: str,
        os_version: str | None = None,
        battery: bool = False,
        show_code: ShowCodeCb,
        notify: NotifyCb,
        power: PowerCb,
        mac: str | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.machine_id = machine_id
        self.hostname = hostname
        self.model = model
        self.os_version = os_version
        self.battery = battery
        self._show_code = show_code
        self._notify = notify
        self._power = power
        self.mac = mac

        self.pairing: PairingSession | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        self._pending: dict[str, Any] | None = None
        self._flush_task: asyncio.Task | None = None
        self._last_send = 0.0
        self.port_in_use: int | None = None

        self.app = web.Application(middlewares=[self._auth_middleware])
        self.app.add_routes(
            [
                web.get("/api/info", self.handle_info),
                web.post("/api/pair/start", self.handle_pair_start),
                web.post("/api/pair", self.handle_pair),
                web.delete("/api/pair", self.handle_unpair),
                web.get("/api/state", self.handle_state),
                web.post("/api/notify", self.handle_notify),
                web.post("/api/power", self.handle_power),
                web.get("/api/ws", self.handle_ws),
            ]
        )

    # -- lifecycle -------------------------------------------------------------

    async def start(self, port: int) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", port, reuse_address=True)
        await site.start()
        self.port_in_use = port
        log.info("Listening on port %s", port)

    async def stop(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close(code=1001, message=b"shutdown")
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self.port_in_use = None

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    # -- auth --------------------------------------------------------------------

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        public = request.path in ("/api/info", "/api/pair/start") or (
            request.path == "/api/pair" and request.method == "POST"
        )
        if not public:
            token = _bearer(request)
            if not self.settings.token_valid(token):
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    # -- handlers ------------------------------------------------------------------

    async def handle_info(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": self.machine_id,
                "name": self.hostname,
                "model": self.model,
                "os_version": self.os_version,
                "battery": self.battery,
                "plugin": PLUGIN_VERSION,
                "api": API_VERSION,
                "paired": self.settings.paired,
                "status": self.state.status,
                "mac": self.mac,
            }
        )

    async def handle_pair_start(self, request: web.Request) -> web.Response:
        if self.state.status != STATUS_GAMING:
            return web.json_response(
                {"error": "not_in_gaming_mode", "message": "Open Gaming Mode on the Steam Machine first"},
                status=409,
            )
        if self.pairing is None or self.pairing.expired:
            self.pairing = PairingSession()
        try:
            await self._show_code(self.pairing.pretty)
        except Exception as err:  # noqa: BLE001
            log.warning("Could not show pairing code: %s", err)
        log.info("Pairing started (code valid for %ss)", PAIR_CODE_TTL_S)
        return web.json_response({"expires_in": PAIR_CODE_TTL_S}, status=202)

    async def handle_pair(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        code = str(body.get("code", "")).replace(" ", "").strip()
        client = str(body.get("client", "Home Assistant")).strip() or "Home Assistant"
        if self.pairing is None or self.pairing.expired:
            self.pairing = None
            await self._hide_code()
            return web.json_response({"error": "no_pairing_session"}, status=409)
        self.pairing.attempts += 1
        if not secrets.compare_digest(code, self.pairing.code):
            if self.pairing.expired:
                self.pairing = None
                await self._hide_code()
                return web.json_response({"error": "too_many_attempts"}, status=429)
            return web.json_response({"error": "wrong_code"}, status=403)
        token = self.settings.issue_token(client)
        self.pairing = None
        await self._hide_code()
        log.info("Paired with %s", client)
        return web.json_response({"token": token, "id": self.machine_id, "name": self.hostname})

    async def handle_unpair(self, request: web.Request) -> web.Response:
        token = _bearer(request)
        if token:
            self.settings.revoke_token(token)
        return web.Response(status=204)

    async def handle_state(self, request: web.Request) -> web.Response:
        return web.json_response(self.state.snapshot())

    async def handle_notify(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        payload, error = _clean_notify(body)
        if error:
            return web.json_response({"error": error}, status=400)
        if self.state.status != STATUS_GAMING:
            return web.json_response({"error": "not_in_gaming_mode"}, status=409)
        ok = await self._notify(payload)
        if not ok:
            return web.json_response({"error": "frontend_unavailable"}, status=409)
        return web.Response(status=204)

    async def handle_power(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        action = str(body.get("action", ""))
        if action not in POWER_ACTIONS:
            return web.json_response({"error": "unknown_action"}, status=400)
        if self.state.status != STATUS_GAMING:
            return web.json_response({"error": "not_in_gaming_mode"}, status=409)
        ok = await self._power(action)
        if not ok:
            return web.json_response({"error": "frontend_unavailable"}, status=409)
        log.info("Power action %s requested by %s", action, request.remote)
        return web.Response(status=204)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, autoping=True)
        await ws.prepare(request)
        self._clients.add(ws)
        peer = request.remote
        log.info("WebSocket client connected from %s (%d total)", peer, len(self._clients))
        try:
            await ws.send_json(
                {
                    "type": "hello",
                    "api": API_VERSION,
                    "id": self.machine_id,
                    "name": self.hostname,
                    "model": self.model,
                    "os_version": self.os_version,
                    "battery": self.battery,
                    "plugin": PLUGIN_VERSION,
                    "mac": self.mac,
                }
            )
            await ws.send_json(self.state.snapshot())
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_ws_message(ws, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING):
                    break
        finally:
            self._clients.discard(ws)
            log.info("WebSocket client %s disconnected (%d left)", peer, len(self._clients))
        return ws

    async def _handle_ws_message(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            await ws.send_json({"type": "error", "error": "invalid_json"})
            return
        mtype = msg.get("type")
        if mtype == "ping":
            await ws.send_json({"type": "pong"})
        elif mtype == "notify":
            payload, error = _clean_notify(msg)
            msg_id = msg.get("id")
            if error:
                await ws.send_json({"type": "result", "id": msg_id, "ok": False, "error": error})
                return
            if self.state.status != STATUS_GAMING:
                await ws.send_json({"type": "result", "id": msg_id, "ok": False, "error": "not_in_gaming_mode"})
                return
            ok = await self._notify(payload)
            await ws.send_json(
                {"type": "result", "id": msg_id, "ok": ok, "error": None if ok else "frontend_unavailable"}
            )
        elif mtype == "power":
            msg_id = msg.get("id")
            action = str(msg.get("action", ""))
            if action not in POWER_ACTIONS:
                await ws.send_json({"type": "result", "id": msg_id, "ok": False, "error": "unknown_action"})
                return
            if self.state.status != STATUS_GAMING:
                await ws.send_json({"type": "result", "id": msg_id, "ok": False, "error": "not_in_gaming_mode"})
                return
            ok = await self._power(action)
            log.info("Power action %s via WebSocket: %s", action, "ok" if ok else "frontend unavailable")
            await ws.send_json(
                {"type": "result", "id": msg_id, "ok": ok, "error": None if ok else "frontend_unavailable"}
            )
        elif mtype == "get_state":
            await ws.send_json(self.state.snapshot())
        else:
            await ws.send_json({"type": "error", "error": "unknown_type"})

    # -- broadcasting --------------------------------------------------------------

    async def broadcast(self, message: dict[str, Any] | None) -> None:
        """Send a message to every client.

        ``update`` messages are coalesced so that at most one is sent per
        second; ``event`` and ``state`` messages go out immediately.
        """
        if not message or not self._clients:
            if message and message.get("type") == "update":
                # nobody listening; keep nothing pending
                self._pending = None
            return
        if message.get("type") != "update":
            await self._send_all(message)
            return
        if self._pending is None:
            self._pending = message
        else:
            self._pending.update({k: v for k, v in message.items() if k != "type"})
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.ensure_future(self._flush_later())

    async def _flush_later(self) -> None:
        wait = max(0.0, WS_SEND_MIN_INTERVAL_S - (time.monotonic() - self._last_send))
        if wait:
            await asyncio.sleep(wait)
        pending, self._pending = self._pending, None
        if pending:
            await self._send_all(pending)

    async def _send_all(self, message: dict[str, Any]) -> None:
        self._last_send = time.monotonic()
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _hide_code(self) -> None:
        try:
            await self._show_code(None)
        except Exception:  # noqa: BLE001
            pass


# -- helpers ---------------------------------------------------------------------


def _bearer(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _clean_text(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return " ".join(text.split())[:NOTIFY_MAX_LEN]


def _clean_notify(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    title = _clean_text(body.get("title"))
    message = _clean_text(body.get("message"))
    if not title and not message:
        return {}, "empty_notification"
    try:
        duration = float(body.get("duration", 6))
    except (TypeError, ValueError):
        duration = 6.0
    duration = min(max(duration, 1.0), 60.0)
    icon = str(body.get("icon") or "")[:64]
    return {"title": title, "message": message, "duration": duration, "icon": icon}, None
