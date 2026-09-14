"""End-to-end: HA config flow + entities against a live plugin backend server."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address

import pytest
from aiohttp.test_utils import TestServer

from custom_components.steamos.const import CONF_TOKEN, DOMAIN
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from steamos_ha.server import Server
from steamos_ha.settings import Settings
from steamos_ha.state import STATUS_DISCONNECTED, STATUS_GAMING, State


class FakePlugin:
    """The plugin backend without Decky: real Server, real State."""

    def __init__(self, tmp_path) -> None:
        self.settings = Settings(str(tmp_path / "settings.json"))
        self.state = State()
        self.code: str | None = None
        self.toasts: list[dict] = []
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
        self.code = code

    async def _notify(self, payload):
        self.toasts.append(payload)
        return True


class FakeSteamGridDB:
    """Minimal SteamGridDB v2 stand-in: one known game, one asset per type."""

    def __init__(self) -> None:
        from aiohttp import web

        self.requests: list[str] = []
        self.app = web.Application()
        self.latest_release: str | None = "v0.2.0"
        self.app.add_routes(
            [
                web.get("/search/autocomplete/{term}", self.search),
                web.get("/{kind}/game/{game_id}", self.assets),
                web.get("/releases/latest", self.releases),  # doubles as the GitHub releases endpoint
            ]
        )

    async def releases(self, request):
        from aiohttp import web

        if self.latest_release is None:
            return web.json_response({"message": "Not Found"}, status=404)
        return web.json_response(
            {"tag_name": self.latest_release, "html_url": "https://github.com/x/y/releases/tag/v0.2.0", "body": "Notes"}
        )

    async def _auth(self, request):
        from aiohttp import web

        if request.headers.get("Authorization") != "Bearer good-key":
            return web.json_response({"success": False, "errors": ["Unauthorized"]}, status=401)
        return None

    async def search(self, request):
        from aiohttp import web

        if (denied := await self._auth(request)) is not None:
            return denied
        term = request.match_info["term"]
        self.requests.append(f"search:{term}")
        if "hades" in term.lower():
            return web.json_response({"success": True, "data": [{"id": 5138, "name": "Hades II", "types": ["steam"]}]})
        return web.json_response({"success": True, "data": []})

    async def assets(self, request):
        from aiohttp import web

        if (denied := await self._auth(request)) is not None:
            return denied
        kind = request.match_info["kind"]
        self.requests.append(f"{kind}:{request.match_info['game_id']}")
        if kind == "icons":
            return web.json_response({"success": True, "data": []})
        return web.json_response(
            {
                "success": True,
                "data": [
                    {"id": 1, "score": 3, "url": f"https://cdn.example/{kind}-low.png"},
                    {"id": 2, "score": 9, "url": f"https://cdn.example/{kind}-best.png"},
                ],
            }
        )


@pytest.fixture
async def sgdb(monkeypatch):
    from custom_components.steamos import artwork as artwork_mod
    from custom_components.steamos import update as update_mod

    fake = FakeSteamGridDB()
    async with TestServer(fake.app) as srv:
        monkeypatch.setattr(artwork_mod, "SGDB_BASE_URL", f"http://127.0.0.1:{srv.port}")
        monkeypatch.setattr(update_mod, "RELEASES_URL", f"http://127.0.0.1:{srv.port}/releases/latest")
        yield fake


@pytest.fixture
async def plugin(tmp_path):
    fake = FakePlugin(tmp_path)
    fake.state.set_status(STATUS_GAMING)
    async with TestServer(fake.server.app) as srv:
        fake.host = "127.0.0.1"  # type: ignore[attr-defined]
        fake.port = srv.port  # type: ignore[attr-defined]
        fake.test_server = srv  # type: ignore[attr-defined]
        yield fake


async def _wait_for(hass: HomeAssistant, entity_id: str, state: str, timeout: float = 5) -> None:
    for _ in range(int(timeout / 0.05)):
        st = hass.states.get(entity_id)
        if st is not None and st.state == state:
            return
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    raise AssertionError(f"{entity_id} never became {state!r}; is {hass.states.get(entity_id)}")


async def test_zeroconf_pairing_and_entities(hass: HomeAssistant, plugin: FakePlugin, sgdb: FakeSteamGridDB) -> None:
    info = ZeroconfServiceInfo(
        ip_address=ip_address(plugin.host),
        ip_addresses=[ip_address(plugin.host)],
        hostname="steammachine.local.",
        name="steammachine._steamos-ha._tcp.local.",
        port=plugin.port,
        properties={"id": "abc123def456", "name": "steammachine", "model": "Steam Machine", "api": "1"},
        type="_steamos-ha._tcp.local.",
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "zeroconf_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pair"
    assert plugin.code is not None  # code is on screen

    # wrong code first
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"code": "000 000"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "wrong_code"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"code": plugin.code})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "artwork"

    # bad key is rejected, good key accepted
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"api_key": "bad"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"api_key": "invalid_api_key"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"api_key": "good-key"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options == {"api_key": "good-key"}
    assert entry.unique_id == "abc123def456"
    assert entry.data[CONF_HOST] == plugin.host and entry.data[CONF_PORT] == plugin.port
    assert plugin.settings.token_valid(entry.data[CONF_TOKEN])
    assert plugin.code is None  # hidden again
    await hass.async_block_till_done()

    # entities exist; WebSocket delivered "gaming"
    await _wait_for(hass, "sensor.steammachine_status", STATUS_GAMING)
    assert hass.states.get("notify.steammachine_on_screen_notification") is not None

    # notification → toast on the plugin side
    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": "notify.steammachine_on_screen_notification", "message": "Hoi", "title": "Test"},
        blocking=True,
    )
    assert plugin.toasts[-1]["message"] == "Hoi"

    # game starts → game sensor, binary sensor, event entity
    assert hass.states.get("sensor.steammachine_game").state == "none"
    assert hass.states.get("binary_sensor.steammachine_game_running").state == "off"
    update = plugin.state.set_game(1145350, "Hades II", False)
    await plugin.server.broadcast(
        {"type": "event", "event": "game_started", "game": {"title": "Hades II", "appid": 1145350, "shortcut": False}}
    )
    await plugin.server.broadcast(update)
    await _wait_for(hass, "sensor.steammachine_game", "Hades II")
    game = hass.states.get("sensor.steammachine_game")
    assert game.attributes["appid"] == 1145350 and game.attributes["shortcut"] is False
    assert hass.states.get("binary_sensor.steammachine_game_running").state == "on"
    ev = hass.states.get("event.steammachine_game")
    assert ev.attributes["event_type"] == "game_started" and ev.attributes["title"] == "Hades II"

    # artwork: looked up by title, best-scored asset wins, icon missing → unavailable
    await _wait_for(hass, "sensor.steammachine_artwork_match", "Hades II")
    match = hass.states.get("sensor.steammachine_artwork_match")
    assert match.attributes["sgdb_id"] == 5138 and match.attributes["assets"] == ["grid", "hero", "logo"]
    grid = hass.states.get("image.steammachine_cover")
    assert grid.state not in ("unknown", "unavailable")
    assert hass.states.get("image.steammachine_icon").state == "unavailable"
    assert entry.runtime_data.artwork.data.urls["grid"] == "https://cdn.example/grids-best.png"
    lookups = [r for r in sgdb.requests if r != "search:portal"]  # "portal" = API-key validation
    assert lookups == ["search:Hades II", "grids:5138", "heroes:5138", "logos:5138", "icons:5138"]

    # update entity: plugin 0.1.0 installed, release v0.2.0 on GitHub → update available
    upd = hass.states.get("update.steammachine_plugin")
    assert upd.state == "on"
    assert upd.attributes["installed_version"] == "0.1.0" and upd.attributes["latest_version"] == "0.2.0"

    # refresh action → cache dropped, looked up again
    await hass.services.async_call(DOMAIN, "refresh_artwork", {}, blocking=True)
    await hass.async_block_till_done()
    assert sgdb.requests.count("search:Hades II") == 2

    # system stats → value sensors; missing metric stays unavailable
    await plugin.server.broadcast(
        plugin.state.set_sys(
            {"cpu_temp": 61.2, "gpu_temp": 67.0, "cpu_load": 37, "mem_pct": 54, "gpu_load": 92,
             "gpu_watt": 98.5, "fan_rpm": None, "boot_time": "2026-09-14T18:02:11+02:00"}
        )
    )
    await _wait_for(hass, "sensor.steammachine_cpu_temperature", "61.2")
    assert hass.states.get("sensor.steammachine_gpu_temperature").state == "67.0"
    assert hass.states.get("sensor.steammachine_gpu_power").state == "98.5"
    assert hass.states.get("sensor.steammachine_cpu_usage").state == "37"
    assert hass.states.get("sensor.steammachine_fan_speed").state == "unavailable"
    assert hass.states.get("sensor.steammachine_fps").state == "unavailable"  # no perf yet

    await plugin.server.broadcast(plugin.state.set_perf(118.4, 8.45))
    await _wait_for(hass, "sensor.steammachine_fps", "118.4")

    # plugin reports desktop mode → sensor flips, notify raises, stats unavailable
    await plugin.server.broadcast(plugin.state.set_status(STATUS_DISCONNECTED))
    await _wait_for(hass, "sensor.steammachine_status", STATUS_DISCONNECTED)
    assert hass.states.get("sensor.steammachine_cpu_temperature").state == "unavailable"
    assert hass.states.get("sensor.steammachine_game").state == "unavailable"
    await _wait_for(hass, "sensor.steammachine_artwork_match", "none")  # no game → no match, art stays
    assert hass.states.get("image.steammachine_cover").state not in ("unknown", "unavailable")
    with pytest.raises(Exception, match="Gaming Mode"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": "notify.steammachine_on_screen_notification", "message": "x"},
            blocking=True,
        )

    # back to gaming
    await plugin.server.broadcast(plugin.state.set_status(STATUS_GAMING))
    await _wait_for(hass, "sensor.steammachine_status", STATUS_GAMING)

    # unload cleanly
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_manual_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_HOST: "127.0.0.1", CONF_PORT: 1}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_server_gone_marks_disconnected(hass: HomeAssistant, plugin: FakePlugin, tmp_path) -> None:
    """When the WebSocket drops, the status sensor shows disconnected (not unavailable)."""
    token = plugin.settings.issue_token("test")
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock = MockConfigEntry(
        domain=DOMAIN,
        unique_id="abc123def456",
        title="steammachine",
        data={
            CONF_HOST: plugin.host,
            CONF_PORT: plugin.port,
            CONF_TOKEN: token,
            "machine_id": "abc123def456",
            "model": "Steam Machine",
        },
    )
    mock.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock.entry_id)
    await _wait_for(hass, "sensor.steammachine_status", STATUS_GAMING)

    await plugin.server.stop()  # closes the WebSocket clients
    await plugin.test_server.close()  # and the listening socket
    await _wait_for(hass, "sensor.steammachine_status", STATUS_DISCONNECTED)
    # notify stays available but fails loudly
    with pytest.raises(Exception, match="could not be sent"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": "notify.steammachine_on_screen_notification", "message": "x"},
            blocking=True,
        )


async def test_without_api_key_no_artwork_entities(hass: HomeAssistant, plugin: FakePlugin) -> None:
    token = plugin.settings.issue_token("test")
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock = MockConfigEntry(
        domain=DOMAIN,
        unique_id="abc123def456",
        title="steammachine",
        data={CONF_HOST: plugin.host, CONF_PORT: plugin.port, CONF_TOKEN: token, "machine_id": "abc123def456"},
        options={},
    )
    mock.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock.entry_id)
    await _wait_for(hass, "sensor.steammachine_status", STATUS_GAMING)
    assert hass.states.get("image.steammachine_cover") is None
    assert hass.states.get("sensor.steammachine_artwork_match") is None

    # steamos.notify with duration + icon reaches the plugin
    await hass.services.async_call(
        DOMAIN,
        "notify",
        {"entity_id": "notify.steammachine_on_screen_notification", "message": "Deur", "duration": 12, "icon": "door"},
        blocking=True,
    )
    assert plugin.toasts[-1] == {"title": "Home Assistant", "message": "Deur", "duration": 12.0, "icon": "door"}

    assert await hass.config_entries.async_unload(mock.entry_id)
    await hass.async_block_till_done()
