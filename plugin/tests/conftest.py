"""Test setup: fake ``decky`` module + py_modules on sys.path."""

from __future__ import annotations

import logging
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN_DIR, "py_modules"))
sys.path.insert(0, PLUGIN_DIR)


class _FakeDecky(types.ModuleType):
    def __init__(self, tmpdir: str) -> None:
        super().__init__("decky")
        self.logger = logging.getLogger("decky")
        self.DECKY_PLUGIN_SETTINGS_DIR = tmpdir
        self.DECKY_PLUGIN_LOG_DIR = tmpdir
        self.DECKY_PLUGIN_RUNTIME_DIR = tmpdir
        self.DECKY_USER_HOME = tmpdir
        self.emitted: list[tuple[str, tuple]] = []

    async def emit(self, event: str, *args):  # noqa: D401
        self.emitted.append((event, args))


def pytest_configure() -> None:
    import tempfile

    sys.modules["decky"] = _FakeDecky(tempfile.mkdtemp(prefix="decky-test-"))


@pytest.fixture(autouse=True)
def _sockets_when_ha_plugin_present(request):
    """pytest-homeassistant-custom-component (if installed) blocks sockets; our server needs loopback."""
    try:
        request.getfixturevalue("socket_enabled")
    except Exception:  # noqa: BLE001
        pass
