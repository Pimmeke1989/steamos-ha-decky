"""In-memory state of the Steam Machine as seen by Home Assistant.

The state has four top-level sections (see docs/api.md):

- ``status``: "gaming" | "disconnected"
- ``game``:   {"title", "appid", "shortcut", "started_at"} or None
- ``perf``:   {"fps", "frametime_ms"} or None
- ``sys``:    {"cpu_temp", ...} — filled in M2; None/absent keys mean "not available"

``diff()`` produces the partial ``update`` message: only sections that changed
are included, and a section that went away is sent explicitly as ``null``.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

STATUS_GAMING = "gaming"
STATUS_DISCONNECTED = "disconnected"

SECTIONS = ("status", "game", "perf", "sys")


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class State:
    def __init__(self) -> None:
        self.status: str = STATUS_DISCONNECTED
        self.game: dict[str, Any] | None = None
        self.perf: dict[str, Any] | None = None
        self.sys: dict[str, Any] | None = None

    # -- snapshot ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "state",
            "status": self.status,
            "game": copy.deepcopy(self.game),
            "perf": copy.deepcopy(self.perf),
            "sys": copy.deepcopy(self.sys),
            "ts": now_iso(),
        }

    def _sections(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "game": copy.deepcopy(self.game),
            "perf": copy.deepcopy(self.perf),
            "sys": copy.deepcopy(self.sys),
        }

    # -- mutations return an update message (or None if nothing changed) ----

    def set_status(self, status: str) -> dict[str, Any] | None:
        before = self._sections()
        self.status = status
        if status == STATUS_DISCONNECTED:
            # Without a frontend we cannot know what runs; drop game + perf.
            self.game = None
            self.perf = None
        return self.diff(before)

    def set_game(
        self, appid: int | None, title: str | None, shortcut: bool = False
    ) -> dict[str, Any] | None:
        before = self._sections()
        if appid is None and not title:
            self.game = None
            self.perf = None
        else:
            same = self.game is not None and self.game.get("appid") == appid
            self.game = {
                "title": (title or "").strip() or f"App {appid}",
                "appid": appid,
                "shortcut": bool(shortcut),
                "started_at": self.game["started_at"] if same else now_iso(),
            }
        return self.diff(before)

    def set_perf(
        self, fps: float | None, frametime_ms: float | None, focus: str | None = None
    ) -> dict[str, Any] | None:
        before = self._sections()
        if fps is None and frametime_ms is None:
            self.perf = None
        else:
            self.perf = {
                "fps": None if fps is None else round(float(fps), 1),
                "frametime_ms": None if frametime_ms is None else round(float(frametime_ms), 2),
                "focus": focus,
            }
        return self.diff(before)

    def set_sys(self, values: dict[str, Any] | None) -> dict[str, Any] | None:
        before = self._sections()
        self.sys = copy.deepcopy(values) if values else None
        return self.diff(before)

    def diff(self, before: dict[str, Any]) -> dict[str, Any] | None:
        after = self._sections()
        changed = {k: after[k] for k in SECTIONS if before.get(k) != after[k]}
        if not changed:
            return None
        changed["type"] = "update"
        changed["ts"] = now_iso()
        return changed
