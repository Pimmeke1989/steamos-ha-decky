"""mDNS advertisement of the plugin's HTTP/WebSocket API.

Tries, in order:

1. ``python-zeroconf`` if it can be imported (vendored in py_modules or present
   in Decky's Python runtime);
2. ``avahi-publish-service`` as a child process (avahi-daemon runs on SteamOS);
3. nothing — Home Assistant can still be configured manually with host + port.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from . import API_VERSION, PLUGIN_VERSION, SERVICE_TYPE

log = logging.getLogger("steamos_ha.discovery")


def _txt_records(machine_id: str, hostname: str, model: str) -> dict[str, str]:
    return {
        "id": machine_id,
        "name": hostname,
        "model": model,
        "api": str(API_VERSION),
        "plugin": PLUGIN_VERSION,
    }


class Discovery:
    def __init__(self, port: int, machine_id: str, hostname: str, model: str) -> None:
        self.port = port
        self.machine_id = machine_id
        self.hostname = hostname
        self.model = model
        self.backend: str = "none"
        self._zc: Any = None
        self._info: Any = None
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if await self._start_zeroconf():
            self.backend = "zeroconf"
        elif await self._start_avahi():
            self.backend = "avahi"
        else:
            self.backend = "none"
            log.warning("No mDNS backend available; add the device manually in Home Assistant")
        log.info("Discovery backend: %s", self.backend)

    async def stop(self) -> None:
        if self._zc is not None:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._zc.unregister_service, self._info)
                await asyncio.get_running_loop().run_in_executor(None, self._zc.close)
            except Exception as err:  # noqa: BLE001
                log.debug("zeroconf shutdown: %s", err)
            self._zc = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 3)
            except TimeoutError:
                self._proc.kill()
            self._proc = None

    # -- backends ------------------------------------------------------------

    async def _start_zeroconf(self) -> bool:
        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf  # type: ignore
        except Exception:  # noqa: BLE001
            return False
        try:
            txt = {k: v.encode() for k, v in _txt_records(self.machine_id, self.hostname, self.model).items()}
            name = f"{self.hostname}.{SERVICE_TYPE}"
            self._info = ServiceInfo(
                SERVICE_TYPE,
                name,
                port=self.port,
                properties=txt,
                server=f"{self.hostname}.local.",
            )
            loop = asyncio.get_running_loop()
            self._zc = await loop.run_in_executor(None, lambda: Zeroconf(ip_version=IPVersion.V4Only))
            await loop.run_in_executor(None, self._zc.register_service, self._info)
            return True
        except Exception as err:  # noqa: BLE001
            log.warning("zeroconf registration failed: %s", err)
            self._zc = None
            return False

    async def _start_avahi(self) -> bool:
        txt = [f"{k}={v}" for k, v in _txt_records(self.machine_id, self.hostname, self.model).items()]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "avahi-publish-service",
                self.hostname,
                SERVICE_TYPE.rstrip("."),
                str(self.port),
                *txt,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False
        except Exception as err:  # noqa: BLE001
            log.warning("avahi-publish-service failed to start: %s", err)
            return False
        await asyncio.sleep(0.5)
        if self._proc.returncode is not None:
            log.warning("avahi-publish-service exited early (code %s)", self._proc.returncode)
            self._proc = None
            return False
        return True


def read_machine_id() -> str:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
                if value:
                    return value[:12]
        except OSError:
            continue
    return socket.gethostname()[:12]


def read_model() -> str:
    try:
        with open("/sys/class/dmi/id/product_name", encoding="utf-8") as fh:
            value = fh.read().strip()
            return value or "SteamOS device"
    except OSError:
        return "SteamOS device"
