"""Integration tests run the real plugin backend against the real HA integration."""

from __future__ import annotations

import logging
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "py_modules"))
sys.path.insert(0, ROOT)

# The plugin backend package (steamos_ha) does not import decky, but main.py does.
if "decky" not in sys.modules:
    fake = types.ModuleType("decky")
    fake.logger = logging.getLogger("decky")  # type: ignore[attr-defined]
    sys.modules["decky"] = fake


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _allow_loopback_sockets(socket_enabled):
    """The plugin backend runs as a real aiohttp server on 127.0.0.1 during tests."""
    yield
