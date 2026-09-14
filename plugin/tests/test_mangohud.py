"""MangoHud module: CSV parsing, config editing, control-message layout, session flow."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import os
import struct

import pytest

from steamos_ha import mangohud
from steamos_ha.mangohud import MangoHudSession, ensure_config, parse_tail

HEADER = (
    "os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
    "SteamOS Holo,AMD Custom APU,AMD Custom GPU,16GB,6.11,Mesa,\n"
    "--------------------------------------\n"
    "fps,frametime,cpu_load,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,"
    "gpu_vram_used,gpu_power,ram_used,swap_used,process_rss,elapsed\n"
)


def _row(fps: float, elapsed_ns: int) -> str:
    return f"{fps:.1f},{1000 / fps:.2f},30,90,60,65,2500,1000,4.2,98,6.1,0,2.0,{elapsed_ns}\n"


def test_parse_tail_with_header_and_window():
    samples = [30, 40, 50, 60, 118, 120, 122, 120, 118]
    text = HEADER + "".join(_row(fps, i * 250_000_000) for i, fps in enumerate(samples))
    fps, frametime = parse_tail(text)
    # last second = last 5 rows (elapsed 1.0 s window incl. endpoints): 118,120,122,120,118
    assert fps == pytest.approx(119.6, abs=0.01)
    assert frametime == pytest.approx(1000 / 119.6, abs=0.1)


def test_parse_tail_without_header_uses_default_columns():
    text = "3,0.3,garbage\n" + "".join(_row(60, i * 250_000_000) for i in range(3))
    # The first partial line starts with a digit but is bogus; window fallback is the last 4 rows.
    fps, frametime = parse_tail(text)
    assert fps is not None and 40 < fps <= 60


def test_parse_tail_empty():
    assert parse_tail("") == (None, None)
    assert parse_tail(HEADER) == (None, None)


def test_ensure_config_adds_and_replaces(tmp_path):
    cfg = tmp_path / "mangohud.conf"
    cfg.write_text("no_display\nfps_limit=60\noutput_folder=/old\n")
    assert ensure_config(str(cfg), "/new/logs", 250) is True
    text = cfg.read_text()
    assert "output_folder=/new/logs\n" in text and "log_interval=250\n" in text
    assert "fps_limit=60" in text and "no_display" in text
    assert text.count("output_folder") == 1
    # idempotent
    assert ensure_config(str(cfg), "/new/logs", 250) is False
    # missing file gets created
    new = tmp_path / "sub" / "conf"
    assert ensure_config(str(new), "/x", 100) is True
    assert new.read_text() == "output_folder=/x\nlog_interval=100\n"


def test_ctrl_message_layout_round_trip():
    """Send a control message through a real SysV queue and read it back as mangoapp would."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    libc.msgrcv.restype = ctypes.c_ssize_t
    libc.msgrcv.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_long, ctypes.c_int]
    queue = mangohud._CtrlQueue()
    msgid = queue._open()
    if msgid is None:
        pytest.skip("SysV IPC not available in this sandbox")
    size = struct.calcsize(mangohud.CTRL_STRUCT)
    long_size = struct.calcsize("@l")
    assert size == long_size + 4 + 4 + 1 + 1 + 64 + 1  # packed, no padding
    buf = ctypes.create_string_buffer(size)
    while libc.msgrcv(msgid, buf, size - long_size, 0, 0o4000) >= 0:
        pass  # drain leftovers from earlier runs (the queue is system-wide)
    assert queue.send(log_session=1, name="steamos-ha") is True
    got = libc.msgrcv(msgid, buf, size - long_size, 2, 0o4000)
    assert got == size - long_size
    msg_type, ctrl_msg_type, version, no_display, log_session, name, reload = struct.unpack(
        mangohud.CTRL_STRUCT, buf.raw
    )
    assert (msg_type, ctrl_msg_type, version) == (2, 1, 1)
    assert no_display == 0 and log_session == 1 and reload == 0
    assert name.rstrip(b"\0") == b"steamos-ha"


async def test_session_start_tail_stop(tmp_path, monkeypatch):
    """Full session flow with the queue and process lookup stubbed out."""
    sent: list[dict] = []
    monkeypatch.setattr(mangohud._CtrlQueue, "send", lambda self, **kw: sent.append(kw) or True)
    monkeypatch.setattr(mangohud, "find_mangoapp_pid", lambda: None)
    monkeypatch.setattr(mangohud, "CONFIG_FALLBACK", str(tmp_path / "MangoHud.conf"))
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))

    received: list[tuple] = []

    async def on_perf(fps, ft):
        received.append((fps, ft))

    log_dir = str(tmp_path / "logs")
    session = MangoHudSession(log_dir, interval_ms=250, on_perf=on_perf)
    await session.start()
    assert session.active
    assert os.path.isdir(log_dir)
    assert (tmp_path / "MangoHud.conf").read_text() == f"output_folder={log_dir}\nlog_interval=250\n"
    assert sent[0]["reload_config"] == 1 and sent[1]["log_session"] == 1

    # mangoapp "writes" a log
    csv = tmp_path / "logs" / "steamos-ha_2026-09-14.csv"
    csv.write_text(HEADER + "".join(_row(60, i * 250_000_000) for i in range(8)))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if received:
            break
    assert received and received[-1][0] == pytest.approx(60.0)

    await session.stop()
    assert not session.active
    assert sent[-1]["log_session"] == 2
    assert received[-1] == (None, None)
    assert not csv.exists()  # cleaned up


def _fast_sleep(real_sleep):
    async def _sleep(seconds, *args, **kwargs):
        await real_sleep(min(seconds, 0.02), *args, **kwargs)

    return _sleep
