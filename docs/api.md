# Plugin ↔ Home Assistant API (v1)

Source of truth for the contract between the Decky plugin (`plugin/`) and the
Home Assistant integration (`custom_components/steamos/`). Both sides ship
together and share the `api` number; a breaking change bumps it.

Base URL: `http://<host>:8570/api/`. JSON in and out; timestamps are ISO 8601
with offset. Every route except `GET /api/info` and the pairing routes requires
`Authorization: Bearer <token>`.

## mDNS

```
service:  _steamos-ha._tcp.local.
name:     <hostname>._steamos-ha._tcp.local.
port:     8570 (configurable in the plugin)
TXT:      id=<first 12 chars of /etc/machine-id>
          name=<hostname>
          model=<DMI product_name, e.g. "Steam Machine">
          api=1
          plugin=<plugin version>
```

## HTTP

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/info` | no | `{id, name, model, os_version, battery, plugin, api, paired, status, mac}`. `mac` = address of the interface that serves the API (or null); `os_version` = SteamOS release from `/etc/os-release`; `battery` = whether this machine has one, which decides whether Home Assistant creates the battery entities. |
| `POST` | `/api/pair/start` | no | Ask the plugin to show a 6-digit code on screen. `202 {"expires_in": 300}`; `409 not_in_gaming_mode` when the frontend isn't up. |
| `POST` | `/api/pair` | no | `{"code": "483921", "client": "Home Assistant"}` → `200 {"token", "id", "name"}`. Errors: `403 wrong_code`, `409 no_pairing_session`, `429 too_many_attempts` (5 tries, 5 minutes). |
| `DELETE` | `/api/pair` | yes | Revoke the calling token. `204`. |
| `GET` | `/api/state` | yes | Full state (same shape as the WebSocket `state` message). |
| `POST` | `/api/notify` | yes | `{"title","message","duration","icon"}` → `204`; `409 not_in_gaming_mode`; `400 empty_notification`. Fields are capped at 200 chars, HTML-escaped; duration clamped to 1–60 s. |
| `POST` | `/api/power` | yes | `{"action": "suspend" \| "shutdown" \| "reboot"}` → `204`; `400 unknown_action`; `409 not_in_gaming_mode` / `409 frontend_unavailable`. Executed by the frontend, which picks the first Steam function that exists (`User.StartShutdown`/`StartRestart`, else `System.SuspendPC`/`ShutdownPC`/`RestartPC`) and reports the outcome back over the `power_result` callable. Turning *on* is not an API call: Home Assistant sends a Wake-on-LAN packet to `mac`. |
| `GET` | `/api/ws` | yes | WebSocket upgrade. `401` (handshake) on a bad token. |

## WebSocket — server → Home Assistant

```jsonc
{ "type": "hello", "api": 1, "id": "3f9a1c2e7b04", "name": "steammachine",
  "model": "Steam Machine", "plugin": "0.2.3", "mac": "50:5a:65:71:dd:4b",
  "os_version": "3.8.16", "battery": false }

{ "type": "state",                        // full snapshot, right after hello
  "status": "gaming",                     // "gaming" | "disconnected"
  "game": { "title": "Hades II", "appid": 1145350, "shortcut": false,
            "started_at": "2026-09-14T20:41:07+02:00" },   // or null
  "sys":  { "cpu_temp": 61.2, "gpu_temp": 67.0, "gpu_mem_temp": 70.0, "ssd_temp": 41.0,
            "cpu_load": 37.5, "cpu_ghz": 3.9, "mem_pct": 54.1,
            "gpu_load": 92.0, "vram_pct": 71.0, "gpu_watt": 98.5,
            "fan_rpm": 2310,
            // battery_* only on a machine that has one (see "battery" in hello)
            "battery_pct": 83, "battery_charging": true,
            "boot_time": "2026-09-14T18:02:11+02:00" },   // or null
  "ts": "2026-09-14T20:45:00+02:00" }

{ "type": "update", "sys": { "cpu_temp": 63.1 }, "ts": "…" }
  // only changed sections; a section that went away is sent as null

{ "type": "event", "event": "game_started",   // | "game_stopped"
  "game": { "title": "Hades II", "appid": 1145350, "shortcut": false }, "ts": "…" }

{ "type": "pong" }
{ "type": "result", "id": 17, "ok": true, "error": null }
{ "type": "error", "error": "unknown_type" }
```

Rules:

- `update` carries only changed sections. The server coalesces updates so at
  most one goes out per second; `event` and `state` are immediate.
- On every (re)connect the server sends `hello` then `state`.
- `status: "disconnected"` also clears `game` (it is unknown without the frontend).
- Home Assistant refuses an `api` major higher than it knows and logs why.

## WebSocket — Home Assistant → server

```jsonc
{ "type": "ping" }                                   // every 30 s; no pong in 10 s → reconnect
{ "type": "get_state" }                              // ask for a fresh full state
{ "type": "notify", "id": 17, "title": "Wasmachine", "message": "Klaar", "duration": 6 }
{ "type": "power", "id": 18, "action": "suspend" }   // | "shutdown" | "reboot"
```

Every `notify` and `power` is answered with a `result` carrying the same `id`.
`ok: false` comes with `error`: `not_in_gaming_mode`, `frontend_unavailable`,
`empty_notification`, `unknown_action`.

## Status semantics

| Situation | Backend | Home Assistant |
|---|---|---|
| Gaming Mode, no game | heartbeat ok, `game: null` | `gaming`, game sensor `none` |
| Gaming Mode, game running | heartbeat ok | `gaming`, all sensors |
| Desktop Mode | no heartbeat for 15 s → `disconnected` | `disconnected`, others unavailable |
| Sleep / off / network gone | WebSocket drops | `disconnected`, others unavailable |
| Steam restarts | frontend reloads, heartbeat resumes | briefly `disconnected`, then `gaming` |

The frontend calls `heartbeat()` every 5 s; the backend's watchdog flips the
status to `disconnected` after 15 s of silence.
