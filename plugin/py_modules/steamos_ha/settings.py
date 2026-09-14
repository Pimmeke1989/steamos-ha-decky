"""Persistent plugin settings (JSON file in Decky's settings dir).

Stored shape::

    {
      "port": 8570,
      "clients": [
        {"token_sha256": "...", "name": "Home Assistant (woonkamer)", "created": "2026-09-14T20:00:00+02:00"}
      ],
      "mangohud": {"enabled": true, "log_dir": "", "config_path": "", "log_interval_ms": 250}
    }
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from typing import Any

from . import DEFAULT_PORT

_DEFAULTS: dict[str, Any] = {
    "port": DEFAULT_PORT,
    "clients": [],
    "mangohud": {"enabled": True, "log_dir": "", "config_path": "", "log_interval_ms": 250},
}


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Settings:
    def __init__(self, path: str) -> None:
        self.path = path
        self.data: dict[str, Any] = json.loads(json.dumps(_DEFAULTS))
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            merged = json.loads(json.dumps(_DEFAULTS))
            merged.update(loaded)
            if isinstance(loaded.get("mangohud"), dict):
                merged["mangohud"] = {**_DEFAULTS["mangohud"], **loaded["mangohud"]}
            self.data = merged

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- accessors ---------------------------------------------------------

    @property
    def port(self) -> int:
        try:
            return int(self.data.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @port.setter
    def port(self, value: int) -> None:
        self.data["port"] = int(value)
        self.save()

    @property
    def clients(self) -> list[dict[str, Any]]:
        clients = self.data.get("clients")
        if not isinstance(clients, list):
            clients = []
            self.data["clients"] = clients
        return clients

    @property
    def paired(self) -> bool:
        return len(self.clients) > 0

    def public_clients(self) -> list[dict[str, Any]]:
        """Client list without secrets, for the plugin UI."""
        return [{"name": c.get("name", "?"), "created": c.get("created", "")} for c in self.clients]

    def issue_token(self, client_name: str) -> str:
        """Create a new bearer token for a client; only the hash is stored."""
        token = secrets.token_urlsafe(32)
        self.clients.append(
            {"token_sha256": hash_token(token), "name": client_name[:80], "created": _now_iso()}
        )
        self.save()
        return token

    def token_valid(self, token: str | None) -> bool:
        if not token:
            return False
        digest = hash_token(token)
        return any(secrets.compare_digest(digest, c.get("token_sha256", "")) for c in self.clients)

    def revoke_token(self, token: str) -> bool:
        digest = hash_token(token)
        before = len(self.clients)
        self.data["clients"] = [c for c in self.clients if c.get("token_sha256") != digest]
        changed = len(self.data["clients"]) != before
        if changed:
            self.save()
        return changed

    def revoke_all(self) -> None:
        self.data["clients"] = []
        self.save()

    @property
    def mangohud(self) -> dict[str, Any]:
        mh = self.data.get("mangohud")
        if not isinstance(mh, dict):
            mh = dict(_DEFAULTS["mangohud"])
            self.data["mangohud"] = mh
        return mh

    def update(self, changes: dict[str, Any]) -> None:
        """Apply a partial update coming from the plugin UI (never touches clients)."""
        if "port" in changes:
            port = int(changes["port"])
            if 1024 <= port <= 65535:
                self.data["port"] = port
        if isinstance(changes.get("mangohud"), dict):
            self.mangohud.update(
                {k: v for k, v in changes["mangohud"].items() if k in _DEFAULTS["mangohud"]}
            )
        self.save()
