"""FPS from gamescope's stats pipe.

gamescope-session starts gamescope with ``-T $XDG_RUNTIME_DIR/gamescope.XXXX/stats.pipe``.
gamescope writes short ``key=value`` lines into that FIFO a few times per second::

    fps=60.000000
    focus=-708029406        # focused appid as a signed 32-bit int, or "steam"

Nothing on SteamOS reads the pipe anymore (it predates mangoapp), so we can be
its reader without taking anything away from anyone. Verified on a Steam Deck
(SteamOS 3.8) — see docs/design.md.

The FIFO is opened O_RDWR so that gamescope's open/write/close cycle never
leaves us at EOF, and O_NONBLOCK so the asyncio loop can watch the fd.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("steamos_ha.gamescope")

PIPE_GLOB = "/run/user/*/gamescope.*/stats.pipe"
WINDOW_S = 1.0  # average fps samples over the last second
STALE_S = 5.0  # no fps line for this long → report None
PROC_ROOT = "/proc"


def find_stats_pipe(proc_root: str = PROC_ROOT, pipe_glob: str = PIPE_GLOB) -> str | None:
    """Path of the running gamescope's stats pipe (``-T <path>``), else the newest one on disk."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        entries = []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue
        if not args or b"gamescope" not in os.path.basename(args[0]):
            continue
        for i, arg in enumerate(args[:-1]):
            if arg in (b"-T", b"--stats-path") and args[i + 1]:
                path = args[i + 1].decode("utf-8", "replace")
                if os.path.exists(path):
                    return path
    candidates = glob.glob(pipe_glob)
    if not candidates:
        return None
    return max(candidates, key=lambda p: os.path.getmtime(os.path.dirname(p)))


def signed32(value: str) -> int | None:
    try:
        n = int(value)
    except ValueError:
        return None
    return n + 2**32 if n < 0 else n


class GamescopeStats:
    """Tails gamescope's stats pipe and reports fps / frametime once per WINDOW_S."""

    def __init__(
        self,
        on_perf: Callable[[float | None, float | None, str | None], Awaitable[None]],
        *,
        pipe_override: str = "",
    ) -> None:
        self._on_perf = on_perf
        self.pipe_override = pipe_override
        self.pipe_path: str | None = None
        self.last_error: str | None = None
        self.active = False
        self.focus: str | None = None
        self._fd: int | None = None
        self._buf = b""
        self._samples: list[tuple[float, float]] = []  # (monotonic, fps)
        self._last_line = 0.0
        self._task: asyncio.Task | None = None
        self._reported: tuple[Any, Any, Any] | None = None

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if self.active:
            return
        self.last_error = None
        path = os.path.expanduser(self.pipe_override) if self.pipe_override else find_stats_pipe()
        if not path:
            self.last_error = "gamescope stats pipe not found (not in Gaming Mode?)"
            log.warning(self.last_error)
            return
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as err:
            self.last_error = f"cannot open {path}: {err}"
            log.warning(self.last_error)
            return
        self.pipe_path = path
        self._fd = fd
        self._buf = b""
        self._samples.clear()
        self._last_line = 0.0
        loop = asyncio.get_running_loop()
        loop.add_reader(fd, self._on_readable)
        self._task = loop.create_task(self._report_loop())
        self.active = True
        log.info("Reading gamescope stats from %s", path)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._fd is not None:
            try:
                asyncio.get_running_loop().remove_reader(self._fd)
            except Exception:  # noqa: BLE001
                pass
            os.close(self._fd)
            self._fd = None
        was_active = self.active
        self.active = False
        self._samples.clear()
        self._reported = None
        if was_active:
            await self._on_perf(None, None, None)
            log.info("Stopped reading gamescope stats")

    # -- reading ---------------------------------------------------------------

    def _on_readable(self) -> None:
        if self._fd is None:
            return
        try:
            chunk = os.read(self._fd, 4096)
        except BlockingIOError:
            return
        except OSError as err:
            self.last_error = f"read error: {err}"
            log.warning(self.last_error)
            return
        if not chunk:
            return
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._handle_line(raw.decode("utf-8", "replace").strip())

    def _handle_line(self, line: str) -> None:
        key, sep, value = line.partition("=")
        if not sep:
            return
        now = time.monotonic()
        if key == "fps":
            try:
                fps = float(value)
            except ValueError:
                return
            self._samples.append((now, fps))
            self._last_line = now
        elif key == "focus":
            self.focus = value

    def current(self) -> tuple[float | None, float | None]:
        """Average fps over the last WINDOW_S seconds (None when stale)."""
        now = time.monotonic()
        if not self._samples or now - self._last_line > STALE_S:
            return None, None
        self._samples = [(t, f) for t, f in self._samples if now - t <= WINDOW_S + 0.5]
        if not self._samples:
            return None, None
        fps = sum(f for _, f in self._samples) / len(self._samples)
        frametime = 1000.0 / fps if fps > 0 else None
        return fps, frametime

    async def _report_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(WINDOW_S)
                fps, frametime = self.current()
                key = (
                    None if fps is None else round(fps, 1),
                    None if frametime is None else round(frametime, 2),
                    self.focus,
                )
                if key != self._reported:
                    self._reported = key
                    await self._on_perf(fps, frametime, self.focus)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("gamescope stats: %s", err)
