"""System statistics from sysfs and procfs — no psutil, no subprocesses.

hwmon devices are found by *name* (``/sys/class/hwmon/hwmon*/name``), the way
Valve's Inkterface does it, so the code does not depend on hwmon numbering:

    k10temp          CPU temperature (temp1_input, "Tctl"); the Steam Deck APU has no
                     k10temp and reports the SoC temperature via acpitz instead
    amdgpu           GPU: temp*_input with labels edge/junction/mem, power1_average,
                     and ``device/gpu_busy_percent`` / ``device/mem_busy_percent``
    nvme             SSD temperature
    steamdeck_hwmon  fan1_input (name to be confirmed on the Steam Machine)

Every reader returns ``None`` when the source is missing, so a metric that does
not exist on this machine simply never becomes an entity in Home Assistant.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("steamos_ha.sysstats")

# Minimum change before a new value is reported (keeps HA's recorder quiet).
THRESHOLDS: dict[str, float] = {
    "cpu_temp": 0.5,
    "gpu_temp": 0.5,
    "gpu_mem_temp": 0.5,
    "ssd_temp": 0.5,
    "cpu_load": 1.0,
    "cpu_ghz": 0.1,
    "mem_pct": 1.0,
    "gpu_load": 1.0,
    "vram_pct": 1.0,
    "gpu_watt": 0.5,
    "fan_rpm": 50.0,
}

FAN_HWMON_CANDIDATES = ("steamdeck_hwmon", "jupiter", "galileo", "nct6775", "it87", "asus", "oxp_platform")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_float(path: str, scale: float = 1.0) -> float | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return float(raw) * scale
    except ValueError:
        return None


class SysStats:
    def __init__(self, sys_root: str = "/sys", proc_root: str = "/proc") -> None:
        self.sys_root = sys_root
        self.proc_root = proc_root
        self._hwmon: dict[str, str] | None = None
        self._gpu_temp_nodes: dict[str, str] | None = None
        self._prev_cpu: tuple[float, float] | None = None
        self._cpu_load: float | None = None
        self._cpu_last_ts = 0.0

    # ---------------------------------------------------------------- hwmon

    def hwmon(self, name: str) -> str | None:
        """Path of the hwmon directory whose ``name`` file matches, or None."""
        if self._hwmon is None:
            self._hwmon = {}
            for path in sorted(glob.glob(os.path.join(self.sys_root, "class/hwmon/hwmon*"))):
                node_name = _read(os.path.join(path, "name"))
                if node_name and node_name not in self._hwmon:
                    self._hwmon[node_name] = path
            log.info("hwmon devices: %s", ", ".join(sorted(self._hwmon)) or "none")
        return self._hwmon.get(name)

    def rescan(self) -> None:
        self._hwmon = None
        self._gpu_temp_nodes = None

    def _hwmon_value(self, name: str, field: str, scale: float = 1.0) -> float | None:
        path = self.hwmon(name)
        return _read_float(os.path.join(path, field), scale) if path else None

    def _gpu_temps(self) -> dict[str, str]:
        """Map label (edge/junction/mem) → temp*_input path for the amdgpu hwmon."""
        if self._gpu_temp_nodes is None:
            self._gpu_temp_nodes = {}
            path = self.hwmon("amdgpu")
            if path:
                for label_file in sorted(glob.glob(os.path.join(path, "temp*_label"))):
                    label = (_read(label_file) or "").lower()
                    input_file = label_file.replace("_label", "_input")
                    if label and os.path.exists(input_file):
                        self._gpu_temp_nodes[label] = input_file
                if not self._gpu_temp_nodes:
                    # No labels: fall back to Inkterface's numbering (temp2 = junction, temp3 = mem)
                    for label, idx in (("edge", 1), ("junction", 2), ("mem", 3)):
                        candidate = os.path.join(path, f"temp{idx}_input")
                        if os.path.exists(candidate):
                            self._gpu_temp_nodes[label] = candidate
        return self._gpu_temp_nodes

    # -------------------------------------------------------------- readers

    def cpu_temp(self) -> float | None:
        for name in ("k10temp", "zenpower", "coretemp", "acpitz"):
            value = self._hwmon_value(name, "temp1_input", 0.001)
            if value is not None:
                return value
        return None

    def gpu_temp(self) -> float | None:
        temps = self._gpu_temps()
        for label in ("junction", "edge"):
            if label in temps:
                return _read_float(temps[label], 0.001)
        return None

    def gpu_mem_temp(self) -> float | None:
        temps = self._gpu_temps()
        return _read_float(temps["mem"], 0.001) if "mem" in temps else None

    def ssd_temp(self) -> float | None:
        return self._hwmon_value("nvme", "temp1_input", 0.001)

    def fan_rpm(self) -> float | None:
        for name in FAN_HWMON_CANDIDATES:
            value = self._hwmon_value(name, "fan1_input")
            if value is not None:
                return value
        return None

    def gpu_watt(self) -> float | None:
        value = self._hwmon_value("amdgpu", "power1_average", 0.000001)
        if value is None:
            value = self._hwmon_value("amdgpu", "power1_input", 0.000001)
        return value

    def gpu_load(self) -> float | None:
        return self._hwmon_value("amdgpu", "device/gpu_busy_percent")

    def vram_pct(self) -> float | None:
        value = self._hwmon_value("amdgpu", "device/mem_busy_percent")
        if value is not None:
            return value
        total = self._hwmon_value("amdgpu", "device/mem_info_vram_total")
        used = self._hwmon_value("amdgpu", "device/mem_info_vram_used")
        if total and used is not None:
            return used / total * 100.0
        return None

    def cpu_load(self) -> float | None:
        """Busy percentage since the previous call (first call returns None)."""
        now = time.monotonic()
        if now - self._cpu_last_ts < 0.5:
            return self._cpu_load
        line = _read(os.path.join(self.proc_root, "stat"))
        if not line or not line.startswith("cpu "):
            return None
        parts = line.split("\n", 1)[0].split()[1:]
        if len(parts) < 7:
            return None
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            return None
        busy = vals[0] + vals[1] + vals[2] + vals[5] + vals[6]
        total = busy + vals[3] + (vals[4] if len(vals) > 4 else 0)
        if self._prev_cpu is not None:
            d_busy = busy - self._prev_cpu[0]
            d_total = total - self._prev_cpu[1]
            if d_total > 0:
                self._cpu_load = max(0.0, min(100.0, d_busy / d_total * 100.0))
        self._prev_cpu = (busy, total)
        self._cpu_last_ts = now
        return self._cpu_load

    def cpu_ghz(self) -> float | None:
        freqs = []
        for path in glob.glob(os.path.join(self.sys_root, "devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
            value = _read_float(path, 0.000001)  # kHz → GHz
            if value:
                freqs.append(value)
        if not freqs:
            text = _read(os.path.join(self.proc_root, "cpuinfo")) or ""
            for line in text.splitlines():
                if line.lower().startswith("cpu mhz"):
                    try:
                        freqs.append(float(line.split(":", 1)[1]) / 1000.0)
                    except (IndexError, ValueError):
                        continue
        return sum(freqs) / len(freqs) if freqs else None

    def mem_pct(self) -> float | None:
        text = _read(os.path.join(self.proc_root, "meminfo")) or ""
        total = avail = None
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                total = float(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = float(line.split()[1])
        if total and avail is not None:
            return (total - avail) / total * 100.0
        return None

    def boot_time(self) -> str | None:
        raw = _read(os.path.join(self.proc_root, "uptime"))
        if not raw:
            return None
        try:
            uptime = float(raw.split()[0])
        except (IndexError, ValueError):
            return None
        boot = datetime.now(UTC) - timedelta(seconds=uptime)
        # round to whole seconds so the value is stable between samples
        return boot.replace(microsecond=0).astimezone().isoformat()

    # --------------------------------------------------------------- sample

    def sample(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "cpu_temp": self.cpu_temp(),
            "gpu_temp": self.gpu_temp(),
            "gpu_mem_temp": self.gpu_mem_temp(),
            "ssd_temp": self.ssd_temp(),
            "cpu_load": self.cpu_load(),
            "cpu_ghz": self.cpu_ghz(),
            "mem_pct": self.mem_pct(),
            "gpu_load": self.gpu_load(),
            "vram_pct": self.vram_pct(),
            "gpu_watt": self.gpu_watt(),
            "fan_rpm": self.fan_rpm(),
            "boot_time": self.boot_time(),
        }
        return {k: _round(k, v) for k, v in raw.items()}


def _round(key: str, value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if key in ("cpu_load", "mem_pct", "gpu_load", "vram_pct", "fan_rpm"):
        return int(round(value))
    return round(float(value), 1)


def apply_thresholds(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return ``current`` with small changes replaced by the previously sent value."""
    if not previous:
        return current
    result: dict[str, Any] = {}
    for key, value in current.items():
        old = previous.get(key)
        threshold = THRESHOLDS.get(key)
        if (
            threshold is not None
            and isinstance(value, int | float)
            and isinstance(old, int | float)
            and abs(value - old) < threshold
        ):
            result[key] = old
        else:
            result[key] = value
    return result
