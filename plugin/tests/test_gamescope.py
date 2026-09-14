"""gamescope stats-pipe reader: discovery, FIFO reading the way gamescope writes, staleness."""

from __future__ import annotations

import asyncio
import os

import pytest

from steamos_ha import gamescope
from steamos_ha.gamescope import GamescopeStats, find_stats_pipe, signed32


def _write_like_gamescope(path: str, line: str) -> None:
    """gamescope opens the FIFO for writing, writes one line, closes it."""
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def test_find_stats_pipe_from_cmdline(tmp_path):
    proc = tmp_path / "proc"
    pipe = tmp_path / "run" / "gamescope.abc" / "stats.pipe"
    pipe.parent.mkdir(parents=True)
    os.mkfifo(pipe)
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cmdline").write_bytes(b"gamescope\0-w\x001280\0-T\0" + str(pipe).encode() + b"\0-O\0*,eDP-1\0")
    (proc / "456").mkdir()
    (proc / "456" / "cmdline").write_bytes(b"steam\0-silent\0")
    assert find_stats_pipe(str(proc), str(tmp_path / "nothing" / "*")) == str(pipe)
    # no gamescope process → newest pipe on disk
    assert find_stats_pipe(str(tmp_path / "noproc"), str(tmp_path / "run" / "gamescope.*" / "stats.pipe")) == str(pipe)
    assert find_stats_pipe(str(tmp_path / "noproc"), str(tmp_path / "nothing" / "*")) is None


def test_signed32():
    assert signed32("-708029406") == 3586937890
    assert signed32("413091") == 413091
    assert signed32("steam") is None


async def test_reads_fps_and_focus(tmp_path, monkeypatch):
    monkeypatch.setattr(gamescope, "WINDOW_S", 0.1)
    pipe = tmp_path / "stats.pipe"
    os.mkfifo(pipe)
    received: list[tuple] = []

    async def on_perf(fps, ft, focus):
        received.append((fps, ft, focus))

    stats = GamescopeStats(on_perf, pipe_override=str(pipe))
    await stats.start()
    assert stats.active and stats.pipe_path == str(pipe)

    for line in ("fps=60.000000", "focus=-708029406", "fps=59.988003", "fps=60.000000"):
        _write_like_gamescope(str(pipe), line)
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)
    assert received, "no perf reported"
    fps, ft, focus = received[-1]
    assert fps == pytest.approx(60.0, abs=0.05)
    assert ft == pytest.approx(16.67, abs=0.05)
    assert focus == "-708029406"

    # staleness → None
    monkeypatch.setattr(gamescope, "STALE_S", 0.2)
    await asyncio.sleep(0.5)
    assert received[-1][0] is None

    await stats.stop()
    assert not stats.active


async def test_missing_pipe_is_reported(tmp_path):
    async def on_perf(*_):
        pass

    stats = GamescopeStats(on_perf, pipe_override=str(tmp_path / "nope"))
    await stats.start()
    assert not stats.active and "cannot open" in (stats.last_error or "")
