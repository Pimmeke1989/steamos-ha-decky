"""SysStats against a fake /sys + /proc tree."""

from __future__ import annotations

import os

from steamos_ha.sysstats import SysStats, apply_thresholds


def _w(root: str, rel: str, content: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def make_tree(tmp_path, *, labels: bool = True):
    sysr = str(tmp_path / "sys")
    procr = str(tmp_path / "proc")
    # hwmon numbering deliberately shuffled
    _w(sysr, "class/hwmon/hwmon0/name", "nvme\n")
    _w(sysr, "class/hwmon/hwmon0/temp1_input", "41000\n")
    _w(sysr, "class/hwmon/hwmon1/name", "k10temp\n")
    _w(sysr, "class/hwmon/hwmon1/temp1_input", "61250\n")
    _w(sysr, "class/hwmon/hwmon2/name", "amdgpu\n")
    _w(sysr, "class/hwmon/hwmon2/temp1_input", "60000\n")
    _w(sysr, "class/hwmon/hwmon2/temp2_input", "67000\n")
    _w(sysr, "class/hwmon/hwmon2/temp3_input", "70000\n")
    if labels:
        _w(sysr, "class/hwmon/hwmon2/temp1_label", "edge\n")
        _w(sysr, "class/hwmon/hwmon2/temp2_label", "junction\n")
        _w(sysr, "class/hwmon/hwmon2/temp3_label", "mem\n")
    _w(sysr, "class/hwmon/hwmon2/power1_average", "98500000\n")
    _w(sysr, "class/hwmon/hwmon2/device/gpu_busy_percent", "92\n")
    _w(sysr, "class/hwmon/hwmon2/device/mem_busy_percent", "71\n")
    _w(sysr, "class/hwmon/hwmon3/name", "steamdeck_hwmon\n")
    _w(sysr, "class/hwmon/hwmon3/fan1_input", "2310\n")
    for i, khz in enumerate((3900000, 4100000)):
        _w(sysr, f"devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", f"{khz}\n")
    _w(procr, "stat", "cpu  1000 0 500 8000 100 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 0 0 0\n")
    _w(procr, "meminfo", "MemTotal:       16000000 kB\nMemFree:        4000000 kB\nMemAvailable:    7344000 kB\n")
    _w(procr, "uptime", "9876.54 12345.67\n")
    return sysr, procr


def test_sample_reads_everything(tmp_path):
    sysr, procr = make_tree(tmp_path)
    stats = SysStats(sysr, procr)
    s = stats.sample()
    assert s["cpu_temp"] == 61.2  # 61.25 rounds half-even → 61.2
    assert s["gpu_temp"] == 67.0  # junction preferred over edge
    assert s["gpu_mem_temp"] == 70.0
    assert s["ssd_temp"] == 41.0
    assert s["gpu_watt"] == 98.5
    assert s["gpu_load"] == 92 and s["vram_pct"] == 71
    assert s["fan_rpm"] == 2310
    assert s["cpu_ghz"] == 4.0
    assert s["mem_pct"] == 54  # (16000000-7344000)/16000000
    assert s["cpu_load"] is None  # needs two samples
    assert s["boot_time"] and "T" in s["boot_time"]

    # second /proc/stat sample → load = busy delta / total delta
    _w(procr, "stat", "cpu  1500 0 700 8300 100 0 0 0 0 0\n")
    stats._cpu_last_ts = 0  # bypass the 0.5 s guard
    s2 = stats.sample()
    assert s2["cpu_load"] == 70  # (700 busy) / (700 busy + 300 idle)


def test_missing_nodes_give_none(tmp_path):
    stats = SysStats(str(tmp_path / "nope"), str(tmp_path / "nope"))
    s = stats.sample()
    assert all(v is None for v in s.values())


def test_gpu_temp_fallback_without_labels(tmp_path):
    sysr, procr = make_tree(tmp_path, labels=False)
    stats = SysStats(sysr, procr)
    assert stats.gpu_temp() == 67.0  # temp2 = junction (Inkterface convention)
    assert stats.gpu_mem_temp() == 70.0


def test_apply_thresholds():
    prev = {"cpu_temp": 61.2, "gpu_load": 92, "fan_rpm": 2310, "boot_time": "x"}
    cur = {"cpu_temp": 61.5, "gpu_load": 93, "fan_rpm": 2340, "boot_time": "x"}
    out = apply_thresholds(prev, cur)
    assert out["cpu_temp"] == 61.2  # 0.3 < 0.5 → keep old
    assert out["gpu_load"] == 93  # 1 >= 1 → new
    assert out["fan_rpm"] == 2310  # 30 < 50 → keep old
    assert apply_thresholds(None, cur) == cur
    assert apply_thresholds(prev, {"cpu_temp": None})["cpu_temp"] is None
