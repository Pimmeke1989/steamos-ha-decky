"""Optional artwork module: looks up game art on SteamGridDB *by title*.

Only active when the config entry has a SteamGridDB API key. Follows the game
title from the main coordinator; on a change it searches SteamGridDB, picks the
best grid / hero / logo / icon and caches the URLs for 30 days so a game costs
at most five requests, ever.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SGDB_BASE_URL = "https://www.steamgriddb.com/api/v2"
ASSET_TYPES = ("grid", "hero", "logo", "icon")
ASSET_QUERY: dict[str, dict[str, str]] = {
    "grid": {"dimensions": "600x900", "types": "static", "nsfw": "false"},
    "hero": {"dimensions": "1920x620", "types": "static", "nsfw": "false"},
    "logo": {"types": "static", "nsfw": "false"},
    "icon": {"types": "static", "nsfw": "false"},
}
CACHE_TTL_S = 30 * 24 * 3600
STORAGE_VERSION = 1

_STRIP = re.compile(r"[™®©]|\s*\((steam|gog|epic|pc|windows)\)\s*$", re.IGNORECASE)


class SteamGridDBError(Exception):
    """Request failed."""


class SteamGridDBAuthError(SteamGridDBError):
    """API key rejected."""


def normalize_title(title: str) -> str:
    return " ".join(_STRIP.sub("", title).split()).strip().lower()


def shorten_title(title: str) -> str | None:
    """'Hades II: Something' → 'Hades II'; None when there is nothing to shorten."""
    for sep in (":", " - ", " – "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head and head.lower() != title.lower():
                return head
    return None


class SteamGridDBClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, base_url: str = SGDB_BASE_URL) -> None:
        self._session = session
        self._api_key = api_key
        self._base = base_url.rstrip("/")

    async def _get(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        try:
            resp = await self._session.get(
                f"{self._base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise SteamGridDBError(str(err)) from err
        if resp.status in (401, 403):
            raise SteamGridDBAuthError("API key rejected")
        if resp.status == 404:
            return []
        if resp.status == 429:
            retry = resp.headers.get("Retry-After")
            raise SteamGridDBError(f"rate limited (retry after {retry or '?'} s)")
        if resp.status >= 400:
            raise SteamGridDBError(f"http {resp.status}")
        body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("success", True):
            raise SteamGridDBError(str(body.get("errors") if isinstance(body, dict) else body))
        data = body.get("data", [])
        return data if isinstance(data, list) else []

    async def validate(self) -> None:
        await self._get("/search/autocomplete/portal")

    async def search(self, title: str) -> list[dict[str, Any]]:
        return await self._get(f"/search/autocomplete/{aiohttp.helpers.quote(title, safe='')}")

    async def assets(self, game_id: int, asset_type: str) -> list[dict[str, Any]]:
        path = {"grid": "grids", "hero": "heroes", "logo": "logos", "icon": "icons"}[asset_type]
        return await self._get(f"/{path}/game/{game_id}", ASSET_QUERY[asset_type])


@dataclass
class ArtworkData:
    title: str | None = None  # title we looked up (None = no game)
    match_name: str | None = None
    sgdb_id: int | None = None
    urls: dict[str, str] = field(default_factory=dict)  # asset type → url
    overridden: bool = False
    error: str | None = None
    updated: Any = None  # datetime of the last change


class ArtworkCoordinator(DataUpdateCoordinator[ArtworkData]):
    """Holds the artwork for the current game; entities listen to it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        api_key: str,
        overrides: dict[str, int],
        *,
        base_url: str = SGDB_BASE_URL,
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} artwork", update_interval=None)
        self.client = SteamGridDBClient(async_get_clientsession(hass), api_key, base_url)
        self.overrides = {normalize_title(k): v for k, v in overrides.items()}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.artwork.{entry_id}")
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.data = ArtworkData()

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._cache = {k: v for k, v in stored.items() if isinstance(v, dict)}

    @callback
    def async_set_title(self, title: str | None) -> None:
        """Called on every main-coordinator update; only reacts to a changed title."""
        if title == self.data.title:
            return
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = self.hass.async_create_task(self._async_lookup(title))

    async def async_refresh_current(self) -> None:
        """``steamos.refresh_artwork``: drop the cache entry for the current title and look it up again."""
        if self.data.title:
            self._cache.pop(normalize_title(self.data.title), None)
            await self._store.async_save(self._cache)
            await self._async_lookup(self.data.title, force=True)

    async def _async_lookup(self, title: str | None, force: bool = False) -> None:
        async with self._lock:
            if title is None:
                # No game: keep the last artwork, but the match sensor says so.
                self.data.title = None
                self.data.match_name = None
                self.async_set_updated_data(self.data)
                return
            key = normalize_title(title)
            entry = self._cache.get(key)
            if entry and not force and time.time() - entry.get("ts", 0) < CACHE_TTL_S:
                self._apply(title, entry)
                return
            try:
                entry = await self._fetch(title, key)
            except SteamGridDBError as err:
                _LOGGER.warning("SteamGridDB lookup for %r failed: %s", title, err)
                self.data.title = title
                self.data.match_name = None
                self.data.error = str(err)
                self.async_set_updated_data(self.data)
                return
            if entry is None:
                _LOGGER.info("SteamGridDB has no match for %r", title)
                entry = {"sgdb_id": None, "name": None, "urls": {}, "overridden": False, "ts": time.time()}
            self._cache[key] = entry
            await self._store.async_save(self._cache)
            self._apply(title, entry)

    async def _fetch(self, title: str, key: str) -> dict[str, Any] | None:
        overridden = key in self.overrides
        if overridden:
            game_id: int | None = self.overrides[key]
            name: str | None = title
        else:
            results = await self.client.search(title)
            if not results and (short := shorten_title(title)):
                results = await self.client.search(short)
            if not results:
                return None
            game_id = int(results[0]["id"])
            name = str(results[0].get("name") or title)
        assert game_id is not None
        urls: dict[str, str] = {}
        for asset_type in ASSET_TYPES:
            candidates = await self.client.assets(game_id, asset_type)
            best = max(candidates, key=lambda c: c.get("score", 0), default=None)
            if best and best.get("url"):
                urls[asset_type] = str(best["url"])
        return {"sgdb_id": game_id, "name": name, "urls": urls, "overridden": overridden, "ts": time.time()}

    def _apply(self, title: str, entry: dict[str, Any]) -> None:
        data = self.data
        changed = entry.get("urls", {}) != data.urls
        data.title = title
        data.match_name = entry.get("name")
        data.sgdb_id = entry.get("sgdb_id")
        data.urls = dict(entry.get("urls", {}))
        data.overridden = bool(entry.get("overridden"))
        data.error = None
        if changed or data.updated is None:
            data.updated = dt_util.utcnow()
        self.async_set_updated_data(data)


def parse_overrides(text: str) -> dict[str, int]:
    """'Title = 12345' per line → {title: 12345}; bad lines are ignored."""
    result: dict[str, int] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        title, _, value = line.partition("=")
        try:
            result[title.strip()] = int(value.strip())
        except ValueError:
            continue
    return result
