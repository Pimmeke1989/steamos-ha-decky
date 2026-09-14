"""Backend tests: pairing, auth, WebSocket, heartbeat/status transitions."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from steamos_ha.server import Server
from steamos_ha.settings import Settings
from steamos_ha.state import STATUS_DISCONNECTED, STATUS_GAMING, State

pytestmark = pytest.mark.asyncio


class Harness:
    def __init__(self, tmp_path) -> None:
        self.settings = Settings(os.path.join(tmp_path, "settings.json"))
        self.state = State()
        self.codes: list[str | None] = []
        self.notified: list[dict] = []
        self.server = Server(
            self.settings,
            self.state,
            machine_id="abc123def456",
            hostname="steammachine",
            model="Steam Machine",
            show_code=self._show_code,
            notify=self._notify,
        )

    async def _show_code(self, code):
        self.codes.append(code)

    async def _notify(self, payload):
        self.notified.append(payload)
        return True


@pytest.fixture
async def harness(tmp_path):
    h = Harness(tmp_path)
    async with TestClient(TestServer(h.server.app)) as client:
        h.client = client  # type: ignore[attr-defined]
        yield h


async def pair(h: Harness) -> str:
    h.state.set_status(STATUS_GAMING)
    resp = await h.client.post("/api/pair/start")
    assert resp.status == 202
    code = h.server.pairing.code
    resp = await h.client.post("/api/pair", json={"code": code, "client": "HA test"})
    assert resp.status == 200
    return (await resp.json())["token"]


async def test_info_is_public(harness: Harness):
    resp = await harness.client.get("/api/info")
    assert resp.status == 200
    data = await resp.json()
    assert data["id"] == "abc123def456"
    assert data["paired"] is False
    assert data["status"] == STATUS_DISCONNECTED


async def test_pair_requires_gaming_mode(harness: Harness):
    resp = await harness.client.post("/api/pair/start")
    assert resp.status == 409


async def test_pair_flow_and_auth(harness: Harness):
    token = await pair(harness)
    assert harness.settings.paired
    assert harness.codes[0] is not None and harness.codes[-1] is None  # shown, then hidden
    # token is stored hashed only
    raw = json.load(open(harness.settings.path))
    assert token not in json.dumps(raw)

    resp = await harness.client.get("/api/state")
    assert resp.status == 401
    resp = await harness.client.get("/api/state", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 200
    assert (await resp.json())["status"] == STATUS_GAMING

    resp = await harness.client.delete("/api/pair", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 204
    resp = await harness.client.get("/api/state", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 401


async def test_wrong_code_and_lockout(harness: Harness):
    harness.state.set_status(STATUS_GAMING)
    await harness.client.post("/api/pair/start")
    for _ in range(4):
        resp = await harness.client.post("/api/pair", json={"code": "000000"})
        assert resp.status == 403
    resp = await harness.client.post("/api/pair", json={"code": "000000"})
    assert resp.status == 429
    resp = await harness.client.post("/api/pair", json={"code": "000000"})
    assert resp.status == 409  # session gone


async def test_websocket_hello_state_and_updates(harness: Harness):
    token = await pair(harness)
    async with harness.client.ws_connect("/api/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
        hello = await ws.receive_json()
        assert hello["type"] == "hello" and hello["api"] == 1
        state = await ws.receive_json()
        assert state["type"] == "state" and state["status"] == STATUS_GAMING

        await ws.send_json({"type": "ping"})
        assert (await ws.receive_json())["type"] == "pong"

        # game starts → event + update
        update = harness.state.set_game(1145350, "Hades II", False)
        await harness.server.broadcast({"type": "event", "event": "game_started", "game": {"title": "Hades II"}})
        await harness.server.broadcast(update)
        event = await ws.receive_json()
        assert event["type"] == "event" and event["event"] == "game_started"
        upd = await ws.receive_json()
        assert upd["type"] == "update" and upd["game"]["title"] == "Hades II"
        assert "sys" not in upd  # unchanged sections are not sent

        # notify over the socket
        await ws.send_json({"type": "notify", "id": 7, "title": "Test", "message": "<b>hoi</b>"})
        result = await ws.receive_json()
        assert result == {"type": "result", "id": 7, "ok": True, "error": None}
        assert harness.notified[-1]["message"] == "&lt;b&gt;hoi&lt;/b&gt;"

        # status drops → update with status + game null
        await harness.server.broadcast(harness.state.set_status(STATUS_DISCONNECTED))
        upd = await ws.receive_json()
        assert upd["status"] == STATUS_DISCONNECTED and upd["game"] is None


async def test_updates_are_coalesced(harness: Harness):
    token = await pair(harness)
    async with harness.client.ws_connect("/api/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
        await ws.receive_json()
        await ws.receive_json()
        harness.server._last_send = asyncio.get_running_loop().time()  # force the 1 s window
        await harness.server.broadcast(harness.state.set_perf(60, 16.7))
        await harness.server.broadcast(harness.state.set_perf(61, 16.4))
        await harness.server.broadcast(harness.state.set_perf(62, 16.1))
        upd = await ws.receive_json()
        assert upd["perf"]["fps"] == 62
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive_json(), 0.3)


async def test_notify_http_requires_gaming(harness: Harness):
    token = await pair(harness)
    harness.state.set_status(STATUS_DISCONNECTED)
    resp = await harness.client.post(
        "/api/notify", json={"title": "x", "message": "y"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status == 409
    harness.state.set_status(STATUS_GAMING)
    resp = await harness.client.post(
        "/api/notify",
        json={"title": "x", "message": "y", "duration": 999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 204
    assert harness.notified[-1]["duration"] == 60.0


async def test_plugin_heartbeat_transitions(tmp_path, monkeypatch):
    """main.Plugin: heartbeat → gaming, watchdog → disconnected."""
    import decky  # fake, from conftest
    import main as plugin_main

    from steamos_ha import discovery

    monkeypatch.setattr(decky, "DECKY_PLUGIN_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(plugin_main, "HEARTBEAT_TIMEOUT_S", 0.2)

    async def _no_discovery(self):
        self.backend = "none"

    monkeypatch.setattr(discovery.Discovery, "start", _no_discovery)

    plugin = plugin_main.Plugin()
    # use an ephemeral port to avoid clashes
    orig_init = plugin_main.Settings.__init__

    def _init(self, path):
        orig_init(self, path)
        self.data["port"] = 0

    monkeypatch.setattr(plugin_main.Settings, "__init__", _init)

    await plugin._main()
    try:
        assert plugin.state.status == STATUS_DISCONNECTED
        status = await plugin.heartbeat()
        assert status["status"] == STATUS_GAMING
        await plugin.set_running_app(413150, "Stardew Valley", False)
        assert plugin.state.game["title"] == "Stardew Valley"

        # watchdog fires after HEARTBEAT_TIMEOUT_S without heartbeats
        plugin.last_heartbeat = plugin.loop.time() - 10
        deadline = plugin.loop.time() + 7
        while plugin.state.status == STATUS_GAMING and plugin.loop.time() < deadline:
            await asyncio.sleep(0.1)
        assert plugin.state.status == STATUS_DISCONNECTED
        assert plugin.state.game is None
    finally:
        await plugin._unload()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
