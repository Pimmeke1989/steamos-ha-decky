"""FPS via MangoHud logging.

In Gaming Mode gamescope runs ``mangoapp`` (the built-in performance overlay).
We never read its frame-data queue — that would steal frames from the overlay —
but we can *send* it control messages (msg type 2) to start and stop a log
session, and then tail the CSV it writes.

Flow at ``game_started``:

1. Find the config file mangoapp reads: ``MANGOHUD_CONFIGFILE`` in the
   environment of the running ``mangoapp`` process (gamescope-session creates
   it as ``/tmp/mangohud.XXXXXXXX``), else ``~/.config/MangoHud/MangoHud.conf``,
   unless the user configured a path in the plugin settings.
2. Make sure it contains ``output_folder=<our log dir>`` and
   ``log_interval=<ms>``; Steam rewrites this file when the overlay level
   changes, so we check every time.
3. Send ``reload_config`` and ``log_session=start`` over the SysV message queue
   (same key mangoapp uses: ``ftok("mangoapp", 65)``). ``mangohudctl`` is used as
   a fallback when the queue is not reachable.
4. Tail the newest ``*.csv`` in the log dir once a second and average the
   ``fps`` / ``frametime`` columns over the last second.

At ``game_stopped``: ``log_session=stop`` and delete the CSV.

Everything is best effort and logged: if any step fails, FPS simply stays
``null`` and the plugin log says which step did not work.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import glob
import logging
import os
import shutil
import struct
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("steamos_ha.mangohud")

CTRL_MSG_TYPE = 2  # message queue type for control messages
CTRL_VERSION = 1
CTRL_MSGID = 1  # mangoapp_ctrl_msgid1_v1
DEFAULT_LOG_INTERVAL_MS = 250
TAIL_BYTES = 64 * 1024
WINDOW_S = 1.0
CONFIG_FALLBACK = "~/.config/MangoHud/MangoHud.conf"

# Packed struct with a native ``long`` first (8 bytes on x86_64) and no padding.
_LONG = "q" if struct.calcsize("@l") == 8 else "l"
CTRL_STRUCT = f"<{_LONG}IIBB64sB"


# ---------------------------------------------------------------- SysV queue


class _CtrlQueue:
    """Send-only access to mangoapp's SysV message queue."""

    def __init__(self) -> None:
        self._libc: Any = None
        self._msgid: int | None = None

    def _open(self) -> int | None:
        if self._msgid is not None:
            return self._msgid
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            libc.ftok.restype = ctypes.c_int
            libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
            libc.msgget.restype = ctypes.c_int
            libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
            libc.msgsnd.restype = ctypes.c_int
            libc.msgsnd.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        except OSError as err:
            log.warning("libc not available for SysV IPC: %s", err)
            return None
        # mangoapp/gamescope use a *relative* path here; when the file does not
        # exist ftok returns -1 and both sides end up with the same key.
        key = libc.ftok(b"mangoapp", 65)
        msgid = libc.msgget(key, 0o666 | 0o1000)  # IPC_CREAT = 0o1000
        if msgid < 0:
            log.warning("msgget failed (errno %s)", ctypes.get_errno())
            return None
        self._libc = libc
        self._msgid = msgid
        return msgid

    def send(self, *, no_display: int = 0, log_session: int = 0, reload_config: int = 0, name: str = "") -> bool:
        msgid = self._open()
        if msgid is None:
            return False
        # struct mangoapp_ctrl_msgid1_v1 (packed):
        #   long msg_type; uint32 ctrl_msg_type; uint32 version;
        #   uint8 no_display; uint8 log_session; char log_session_name[64]; uint8 reload_config;
        payload = struct.pack(
            CTRL_STRUCT,
            CTRL_MSG_TYPE,
            CTRL_MSGID,
            CTRL_VERSION,
            no_display,
            log_session,
            name.encode("utf-8")[:63],
            reload_config,
        )
        buf = ctypes.create_string_buffer(payload, len(payload))
        rc = self._libc.msgsnd(msgid, buf, len(payload) - struct.calcsize(_LONG), 0o4000)  # IPC_NOWAIT
        if rc != 0:
            log.warning("msgsnd failed (errno %s)", ctypes.get_errno())
            self._msgid = None
            return False
        return True


# ------------------------------------------------------------ config helpers


def find_mangoapp_pid() -> int | None:
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8") as fh:
                if fh.read().strip() == "mangoapp":
                    return int(entry)
        except OSError:
            continue
    return None


def config_path_from_process(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            environ = fh.read().split(b"\0")
    except OSError:
        return None
    for item in environ:
        if item.startswith(b"MANGOHUD_CONFIGFILE="):
            return item.split(b"=", 1)[1].decode("utf-8", "replace") or None
    return None


def ensure_config(path: str, log_dir: str, interval_ms: int) -> bool:
    """Make sure ``output_folder`` and ``log_interval`` are set; returns True if the file changed."""
    wanted = {"output_folder": log_dir, "log_interval": str(int(interval_ms))}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as err:
        log.warning("Cannot read MangoHud config %s: %s", path, err)
        return False
    out: list[str] = []
    seen: set[str] = set()
    changed = False
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            if key in seen:
                changed = True
                continue
            seen.add(key)
            new_line = f"{key}={wanted[key]}"
            if line.strip() != new_line:
                changed = True
            out.append(new_line)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            out.append(f"{key}={value}")
            changed = True
    if not changed:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
    except OSError as err:
        log.warning("Cannot write MangoHud config %s: %s", path, err)
        return False
    return True


# ------------------------------------------------------------- CSV parsing


def parse_tail(text: str) -> tuple[float | None, float | None]:
    """Average fps / frametime (ms) over the last WINDOW_S seconds of a MangoHud CSV tail.

    The tail may start mid-line and may or may not include the column header.
    MangoHud writes a header block (os,cpu,gpu,…), a dashed separator, a column
    header line (fps,frametime,…,elapsed) and then one row per interval.
    """
    lines = text.splitlines()
    columns: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        if line.startswith("fps,") or ",fps," in f",{line},":
            if "frametime" in line:
                columns = [c.strip() for c in line.split(",")]
                rows = []
                continue
        if not line or line.startswith("-") or not line[0].isdigit():
            continue
        rows.append(line.split(","))
    if not rows:
        return None, None
    if columns is None:
        # No header in this tail: assume MangoHud's default order (fps first, frametime second).
        fps_i, ft_i, el_i = 0, 1, None
    else:
        try:
            fps_i = columns.index("fps")
            ft_i = columns.index("frametime")
        except ValueError:
            return None, None
        el_i = columns.index("elapsed") if "elapsed" in columns else None
    # keep the rows inside the window (elapsed is in nanoseconds), else the last 4
    selected: list[list[str]] = []
    if el_i is not None:
        try:
            last_elapsed = float(rows[-1][el_i])
            for row in reversed(rows):
                if len(row) <= max(fps_i, ft_i, el_i):
                    continue
                if last_elapsed - float(row[el_i]) > WINDOW_S * 1e9:
                    break
                selected.append(row)
        except (ValueError, IndexError):
            selected = []
    if not selected:
        selected = [r for r in rows[-4:] if len(r) > max(fps_i, ft_i)]
    fps_vals: list[float] = []
    ft_vals: list[float] = []
    for row in selected:
        try:
            fps_vals.append(float(row[fps_i]))
            ft_vals.append(float(row[ft_i]))
        except (ValueError, IndexError):
            continue
    if not fps_vals:
        return None, None
    return sum(fps_vals) / len(fps_vals), sum(ft_vals) / len(ft_vals)


def newest_csv(log_dir: str) -> str | None:
    files = glob.glob(os.path.join(log_dir, "*.csv"))
    if not files:
        return None
    return max(files, key=lambda p: os.path.getmtime(p))


def read_tail(path: str, size: int = TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            fh.seek(max(0, end - size))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


# ----------------------------------------------------------------- session


class MangoHudSession:
    """Start/stop MangoHud logging around a game and tail the result."""

    def __init__(
        self,
        log_dir: str,
        *,
        config_override: str = "",
        interval_ms: int = DEFAULT_LOG_INTERVAL_MS,
        on_perf: Callable[[float | None, float | None], Awaitable[None]],
    ) -> None:
        self.log_dir = log_dir
        self.config_override = config_override
        self.interval_ms = interval_ms
        self._on_perf = on_perf
        self._queue = _CtrlQueue()
        self._task: asyncio.Task | None = None
        self.active = False
        self.last_error: str | None = None
        self.config_path: str | None = None
        self.csv_path: str | None = None

    # -- control ---------------------------------------------------------------

    def _ctrl(self, **kwargs: Any) -> bool:
        if self._queue.send(**kwargs):
            return True
        # Fallback: mangohudctl (if installed)
        ctl = shutil.which("mangohudctl")
        if not ctl:
            return False
        ok = True
        for key, value in (("log_session", kwargs.get("log_session")), ("no_display", kwargs.get("no_display"))):
            if not value:
                continue
            arg = "true" if value == 1 else "false"
            ok &= os.system(f"{ctl} set {key} {arg} >/dev/null 2>&1") == 0
        if kwargs.get("reload_config"):
            ok &= os.system(f"{ctl} reload >/dev/null 2>&1") == 0
        return ok

    def resolve_config_path(self) -> str | None:
        if self.config_override:
            return os.path.expanduser(self.config_override)
        pid = find_mangoapp_pid()
        if pid is not None:
            path = config_path_from_process(pid)
            if path:
                return path
            log.info("mangoapp (pid %s) has no MANGOHUD_CONFIGFILE; using %s", pid, CONFIG_FALLBACK)
        else:
            log.info("No mangoapp process found; using %s", CONFIG_FALLBACK)
        return os.path.expanduser(CONFIG_FALLBACK)

    async def start(self) -> None:
        if self.active:
            return
        self.last_error = None
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            os.chmod(self.log_dir, 0o777)
        except OSError as err:
            self.last_error = f"log dir {self.log_dir}: {err}"
            log.warning(self.last_error)
            return
        self.config_path = await asyncio.to_thread(self.resolve_config_path)
        changed = self.config_path and await asyncio.to_thread(
            ensure_config, self.config_path, self.log_dir, self.interval_ms
        )
        if changed:
            log.info("Updated %s (output_folder, log_interval)", self.config_path)
            if not self._ctrl(reload_config=1):
                self.last_error = "could not reach mangoapp (reload_config)"
                log.warning(self.last_error)
        await asyncio.sleep(0.5)
        if not self._ctrl(log_session=1, name="steamos-ha"):
            self.last_error = "could not reach mangoapp (log_session start)"
            log.warning(self.last_error)
            return
        self.active = True
        self.csv_path = None
        self._task = asyncio.get_running_loop().create_task(self._tail_loop())
        log.info("MangoHud log session started (dir %s)", self.log_dir)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self.active:
            self._ctrl(log_session=2)
            self.active = False
            log.info("MangoHud log session stopped")
        await self._on_perf(None, None)
        await asyncio.to_thread(self._cleanup)

    def _cleanup(self) -> None:
        for path in glob.glob(os.path.join(self.log_dir, "*.csv")):
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- tailing ---------------------------------------------------------------

    async def _tail_loop(self) -> None:
        misses = 0
        while True:
            try:
                await asyncio.sleep(1.0)
                path = await asyncio.to_thread(newest_csv, self.log_dir)
                if not path:
                    misses += 1
                    if misses == 10:
                        self.last_error = f"no MangoHud log appeared in {self.log_dir}"
                        log.warning(self.last_error)
                    continue
                misses = 0
                self.csv_path = path
                fps, frametime = parse_tail(await asyncio.to_thread(read_tail, path))
                await self._on_perf(fps, frametime)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("MangoHud tail: %s", err)
