# Design notes

The full design document (scope, architecture, data sources, entities, roadmap)
lives as a shared page; this file keeps the decisions that shape the code.

## Decisions

| Topic | Decision |
|---|---|
| Transport | The Decky plugin is the **server** (aiohttp HTTP + WebSocket, advertised via mDNS); Home Assistant is the client. Local push, no MQTT, no HA credentials on the Steam Machine. |
| Scope | **Gaming Mode only.** Desktop Mode, sleep and off all show `disconnected`. |
| Game detection | Frontend (`SteamClient.GameSessions.RegisterForAppLifetimeNotifications` + `appStore.GetAppOverviewByAppID`), reported to the backend. |
| Status | Frontend heartbeat every 5 s; backend watchdog → `disconnected` after 15 s. |
| FPS | Tail MangoHud's log CSV (session toggled at game start/stop). Not the mangoapp message queue: reading it would steal frames from the built-in overlay. |
| System stats | sysfs/procfs read by name (`k10temp`, `amdgpu`, `nvme`, `steamdeck_hwmon`), the way Valve's Inkterface does. No `psutil`. |
| Artwork | Optional module inside the same integration, SteamGridDB looked up **by title** (non-Steam shortcuts have meaningless appids). No API key → no artwork entities, no external calls. |
| Controls in v1 | Notifications only (toast via Decky's toaster). Power, TDP, mode switching etc. later via `steamos-manager` D-Bus. |
| Repo | One repo. HACS reads `custom_components/`; the plugin is packaged from `plugin/` as a release zip. |

## Milestones

| | Milestone | Contents | Status |
|---|---|---|---|
| M0 | Inventory | Run `scripts/steamos-inventory.sh` on the Steam Machine; confirm hwmon names, Decky user, MangoHud paths. | open |
| M1 | Skeleton + connection | Server, pairing, WebSocket, heartbeat, QAM panel, discovery, config flow, status sensor, notify entity. | **done** |
| M2 | System stats + game | hwmon/proc sampler, temperature/load/power sensors, game sensor, binary_sensor, event entity. | **done** |
| M3 | FPS | MangoHud log session on/off, tailer, FPS + frametime sensors. | **done** (needs on-device confirmation of config path / log dir) |
| M4 | Notifications polish | `steamos.notify` action with duration/icon, 409 behaviour, tests. | |
| M5 | Artwork module | SteamGridDB client, optional API key in config/options flow, cache, image entities, overrides. | |
| M6 | Finish | HACS metadata, release zip, README, translations, diagnostics, CI. | |

## To verify on the device (M0)

| Question | Assumption | Fallback in code |
|---|---|---|
| Decky Loader runs on the Steam Machine; user | yes, `deck` | paths via Decky's own constants |
| hwmon names | `k10temp`, `amdgpu`, `nvme`, `steamdeck_hwmon` | lookup by name; missing metric → no entity |
| Which `temp*` is junction/edge | temp2 = junction (Inkterface) | read `temp*_label` |
| MangoHud config file mangoapp reads, log dir, `mangohudctl` present | unknown | settings in plugin UI; FPS unavailable until it works |
| `RegisterForAppLifetimeNotifications` / `GetAppOverviewByAppID` still work | yes | `console_log.txt` tailer as reserve |
| `python-zeroconf` importable in Decky's Python | no | `avahi-publish-service` subprocess, else manual add |
| Port 8570 free and reachable | yes | configurable |
| `aiohttp` importable in the plugin backend | yes (Decky Loader ships it) | vendor into `py_modules/` |
