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
| FPS | **Dropped in 0.2.3.** Every source SteamOS exposes is wrong in a different way: MangoHud's log reports mangoapp's redraw rate, the mangoapp message queue is drained instantly (even `MSG_COPY` never sees a message), and gamescope's stats pipe gives the *compositor's* paint rate once per 300 composited frames. An approximation that never matched the on-screen overlay was worth less than no sensor at all. |
| Battery | `/sys/class/power_supply`: the first entry with `type=Battery` whose `scope` is not `Device` (connected controllers register as batteries too). Reported only by a machine that has one; `/api/info` carries a `battery` flag so Home Assistant knows whether to create the entities at all. |
| SteamOS version | `VERSION_ID` (+ `BUILD_ID`) from `/etc/os-release`, sent in `/api/info` and `hello`, shown as a diagnostic sensor. |
| System stats | sysfs/procfs read by name (`k10temp`, `amdgpu`, `nvme`, `steamdeck_hwmon`), the way Valve's Inkterface does. No `psutil`. |
| Artwork | Optional module inside the same integration, SteamGridDB looked up **by title** (non-Steam shortcuts have meaningless appids). No API key → no artwork entities, no external calls. |
| Controls in v1 | Notifications (toast via Decky's toaster) and power: sleep / shut down / restart through the frontend's `SteamClient.System`, turn on via Wake-on-LAN from Home Assistant (MAC reported by the plugin). TDP, mode switching etc. later via `steamos-manager` D-Bus. |
| Repo | One repo. HACS reads `custom_components/`; the plugin is packaged from `plugin/` as a release zip. |

## Milestones

| | Milestone | Contents | Status |
|---|---|---|---|
| M0 | Inventory | Run `scripts/steamos-inventory.sh` on the Steam Machine; confirm hwmon names, Decky user, MangoHud paths. | open |
| M1 | Skeleton + connection | Server, pairing, WebSocket, heartbeat, QAM panel, discovery, config flow, status sensor, notify entity. | **done** |
| M2 | System stats + game | hwmon/proc sampler, temperature/load/power sensors, game sensor, binary_sensor, event entity. | **done** |
| M3 | FPS | gamescope stats-pipe reader, FPS + frametime sensors. | **removed in 0.2.3** — no usable source, see below |
| M4 | Notifications polish | `steamos.notify` action with duration/icon, 409 behaviour, tests. | **done** |
| M5 | Artwork module | SteamGridDB client, optional API key in config/options flow, cache, image entities, overrides. | **done** |
| M6 | Finish | HACS metadata, release zip, README, translations, diagnostics, CI, `update` entity, version script. | **done** |

## Deviations from the original design (as built)

- The notify entity is always available (HA silently skips actions on unavailable entities; a clear error is more useful).
- `sensor.<name>_game` reports the literal `none` when nothing runs, so automations can compare against it.
- The artwork module first kept the last artwork when a game stopped, so a dashboard could decide for itself whether to keep showing the cover. Changed in 0.2.3: the image entities now go unavailable and `sensor.<name>_artwork_match` flips to `none`, because an entity that still shows yesterday's game reads as wrong rather than as deliberate. The cached lookup is untouched, so restarting the game restores the artwork without new API calls.
- FPS was built twice and then removed altogether in 0.2.3. MangoHud logging came first: mangoapp logs its own overlay redraw rate (multiples of the display period), not the game's frame rate, and `MSG_COPY` on the mangoapp queue never sees a message because mangoapp drains it instantly. gamescope's `stats.pipe` replaced it, but that reports the compositor's paint rate once per 300 composited frames — seconds apart, and drifting from the game whenever anything else drives the screen (the overlay itself, for one). Holding the last value fixed the flapping but not the accuracy, and a number that never agrees with what the user sees on screen is worse than no number. The `perf` section left the protocol with it.
- An `update` entity (GitHub releases) was added; installing stays manual through Decky.
- The first power implementation called `SteamClient.System.Suspend()` / `.Shutdown()`, which do not exist — the real names end in `PC` — so Sleep and Shut down silently did nothing while Home Assistant reported success. Each action now carries a list of candidate calls and the frontend uses the first one that is actually present (preferring `SteamClient.User.StartShutdown/StartRestart`, the graceful path the Steam power menu takes), and it reports the outcome back over `power_result` so a failure shows up in the plugin panel and the log instead of vanishing.
- Power controls were pulled forward from "later": `POST /api/power` / WS `power` route `suspend`, `shutdown` and `reboot` to the frontend (`SteamClient.System.Suspend/Shutdown/RestartPC`) instead of going through `steamos-manager`, so no D-Bus/polkit work on the plugin side. Turning on is Wake-on-LAN from Home Assistant (`wakeonlan`); the plugin exposes its MAC in `/api/info` and `hello`, the integration stores it in the config entry and refreshes it on every reconnect.
- Artwork was narrowed to cover (grid) and icon; hero banner and logo were dropped.

## Verified on a Steam Deck (SteamOS 3.8.16, Decky Loader 3.2.6) — 2026-09-14

| Question | Result on the Deck | Consequence |
|---|---|---|
| Decky Loader, user | runs as a service; user `deck`, home `/home/deck` | as assumed |
| hwmon names | `steamdeck_hwmon` (fan1), `nvme` (temp1), `amdgpu` (temp1 = `edge`, `power1_average`, `gpu_busy_percent`); **no `k10temp`**, CPU/SoC temp via `acpitz`; `mem_busy_percent` unreadable | `acpitz` added as CPU-temp fallback; VRAM via `mem_info_vram_*` fallback works |
| MangoHud | `mangohud`, `mangoapp`, `mangohudctl` present (0.8.3); `MANGOHUD_CONFIGFILE=/run/user/1000/gamescope.*/mangohud.config`; log session works but its `fps` column is mangoapp's redraw rate (17/33/50 ms frametimes at a 60 Hz panel) | MangoHud path dropped |
| gamescope stats pipe | `/run/user/1000/gamescope.*/stats.pipe` (from `-T`), no reader; emits `fps=…` and `focus=<appid or steam>`. Cadence comes from `paint_all()`: `if (++frameCounter == 300) { currentFrameRate = 300 * 1000.0f / (currentTime - lastSampledFrameTime); … }` — one line per 300 composites (≈5 s at 60 fps), carrying the compositor's own average | was the FPS source; dropped in 0.2.3 |
| gamescope X root properties (`:0`) | `GAMESCOPE_FPS_LIMIT`, `GAMESCOPE_DISPLAY_REFRESH_RATE_FEEDBACK`, `GAMESCOPE_DISPLAY_IS_EXTERNAL`, `GAMESCOPE_DISPLAY_HDR_ENABLED`, `GAMESCOPE_VRR_ENABLED`, `GAMESCOPE_FOCUSED_APP` | candidates for later sensors |
| Session detection | Gaming Mode: `loginctl` session `Desktop=gamescope`, `Type=wayland`; Desktop Mode: `Desktop=KDE` | heartbeat stays the primary signal; this is a possible backend cross-check |
| mDNS | `python-zeroconf` missing, `avahi-daemon` disabled | zeroconf + ifaddr vendored (pure Python) into the release zip |
| Python / aiohttp | system Python 3.13.5 with aiohttp 3.12; Decky's own runtime decides for the backend | vendored packages are pure Python so they load on any 3.11+ |
| steamos-manager | D-Bus `com.steampowered.SteamOSManager1` answers (TdpLimit, HdmiCecState, DeviceModel); `dbus`/`dbus_next`/`gi` importable | ready for the "later" control features |
| Port 8570 | free | — |
| Root filesystem | `steamos-readonly enabled` | nothing is written outside `/home` and `/run/user` |
| `hostname` command | absent on SteamOS | script uses `uname -n`; plugin uses `socket.gethostname()` |

Still open for the Steam Machine itself: its hwmon names (a desktop-class CPU should have `k10temp`; the GPU may expose `junction`/`mem`), and whether the fan sits under `steamdeck_hwmon` or another driver.
