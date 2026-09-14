#!/usr/bin/env python3
"""Probe possible FPS sources on a SteamOS device — read-only, changes nothing.

Run in Gaming Mode while a game runs:  python3 fps-probe.py

1. Peeks at gamescope's frame messages to mangoapp with MSG_COPY (non-destructive):
   does the kernel support it, and how often is a message visible?
2. Lists GAMESCOPE_* / fps-ish properties on the gamescope X root window (:0 and :1).
3. Says who has gamescope's stats.pipe open (without reading it).
"""

import ctypes
import ctypes.util
import glob
import os
import struct
import subprocess
import time

MSG_COPY = 0o40000
IPC_NOWAIT = 0o4000
DURATION_S = 10.0

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
libc.ftok.restype = ctypes.c_int
libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.msgget.restype = ctypes.c_int
libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
libc.msgrcv.restype = ctypes.c_ssize_t
libc.msgrcv.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_long, ctypes.c_int]


def section(title):
    print(f"\n==== {title} ====")


def decode_v1(mtext: bytes):
    """mangoapp_msg_v1 without the leading msg_type (packed)."""
    if len(mtext) < 24:
        return None
    version, pid, visible = struct.unpack_from("<IIQ", mtext, 0)
    info = {"version": version, "pid": pid, "visible_frametime_ns": visible}
    if len(mtext) >= 50:
        fsr, sharp, app_ft, latency, w, h = struct.unpack_from("<BBQQII", mtext, 16)
        info.update(app_frametime_ns=app_ft, latency_ns=latency, out=f"{w}x{h}")
    if len(mtext) >= 54:
        (refresh, hdr, focused) = struct.unpack_from("<H??", mtext, 42)
        info.update(refresh=refresh, hdr=hdr, steam_focused=focused)
    if len(mtext) >= 94:
        info["engine"] = mtext[46:86].split(b"\0", 1)[0].decode("utf-8", "replace")
    return info


section("1. MSG_COPY peek on the mangoapp queue")
key = libc.ftok(b"mangoapp", 65)
msgid = libc.msgget(key, 0o666)
print(f"ftok key={key & 0xFFFFFFFF:#x} msgid={msgid}")
if msgid < 0:
    print("queue not found (is Gaming Mode / mangoapp running?)")
else:
    buf = ctypes.create_string_buffer(1024)
    seen = 0
    polls = 0
    enosys = False
    frametimes = []
    last_sig = None
    t_end = time.monotonic() + DURATION_S
    while time.monotonic() < t_end:
        polls += 1
        n = libc.msgrcv(msgid, buf, 1024 - 8, 0, IPC_NOWAIT | MSG_COPY)
        if n < 0:
            err = ctypes.get_errno()
            if err == 38:  # ENOSYS
                enosys = True
                break
            if err != 42:  # ENOMSG is normal
                print(f"msgrcv errno {err} ({os.strerror(err)})")
                break
        else:
            mtype = struct.unpack_from("<q", buf.raw, 0)[0]
            mtext = buf.raw[8 : 8 + n]
            sig = (mtype, mtext)
            if sig != last_sig:
                last_sig = sig
                seen += 1
                if mtype == 1:
                    info = decode_v1(mtext)
                    if info and info["visible_frametime_ns"]:
                        frametimes.append(info["visible_frametime_ns"])
                    if seen <= 3:
                        print(f"sample: type={mtype} len={n} {info}")
        time.sleep(0.002)
    if enosys:
        print("MSG_COPY not supported by this kernel (ENOSYS)")
    else:
        print(f"polls={polls} distinct messages seen={seen} in {DURATION_S:.0f}s")
        if frametimes:
            avg = sum(frametimes) / len(frametimes)
            print(f"visible_frametime avg={avg / 1e6:.2f} ms → {1e9 / avg:.1f} fps ({len(frametimes)} samples)")

section("2. gamescope X root window properties")
for disp in (":0", ":1"):
    try:
        out = subprocess.run(
            ["xprop", "-root"], env={**os.environ, "DISPLAY": disp}, capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        print("xprop not installed")
        break
    lines = [
        ln for ln in out.stdout.splitlines() if "GAMESCOPE" in ln or "FPS" in ln.upper() or "REFRESH" in ln.upper()
    ]
    print(f"-- DISPLAY={disp}: {len(lines)} matching properties")
    for ln in lines[:60]:
        print("  " + ln[:160])

section("3. gamescope stats.pipe")
for pipe in glob.glob("/run/user/*/gamescope.*/stats.pipe"):
    st = os.stat(pipe)
    print(f"{pipe}  mode={oct(st.st_mode)}")
    readers = []
    for fd in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            if os.readlink(fd) == pipe:
                pid = fd.split("/")[2]
                with open(f"/proc/{pid}/comm") as fh:
                    readers.append(f"{pid} {fh.read().strip()}")
        except OSError:
            continue
    print("open by: " + (", ".join(sorted(set(readers))) or "nobody visible"))

section("4. mangoapp")
try:
    out = subprocess.run(["pgrep", "-a", "mangoapp"], capture_output=True, text=True)
    print(out.stdout.strip() or "not running")
except FileNotFoundError:
    pass
print("done")
