# steamos-ha-decky

Connects a Steam Machine (or any SteamOS device running [Decky Loader](https://decky.xyz)) to Home Assistant.

Two parts, one repo:

| Part | Where | What it does |
|---|---|---|
| **Decky plugin** "SteamOS HA" | `plugin/` | Runs a small local API on the Steam Machine, reports status / running game / stats, shows Home Assistant notifications as toasts in Gaming Mode. |
| **Home Assistant integration** `steamos` | `custom_components/steamos/` | Finds the plugin via mDNS, pairs with a code shown on screen, keeps a WebSocket open and exposes everything as one device. |

No MQTT broker, no cloud, and the Steam Machine never holds Home Assistant credentials.

> **Status: milestone 5 of 6.** Working today: discovery, pairing, status, running game,
> system statistics (temperatures, load, power, fan), FPS via MangoHud, on-screen
> notifications and optional game artwork via SteamGridDB. Not yet tested on a real
> Steam Machine — see `docs/design.md` for what to verify.

## How it works

```
Steam Machine (Gaming Mode)                     Home Assistant
┌──────────────────────────────┐                ┌─────────────────────────┐
│ Decky frontend (Steam UI)    │                │ steamos integration     │
│  · heartbeat every 5 s       │                │  · zeroconf discovery   │
│  · running game via          │   mDNS + WS    │  · config flow + pairing│
│    SteamClient API           │ ◄────────────► │  · push coordinator     │
│  · toasts                    │  :8570 /api/ws │  · sensor.*_status      │
├──────────────────────────────┤                │  · notify.*             │
│ Decky backend (Python)       │                └─────────────────────────┘
│  · aiohttp HTTP + WebSocket  │
│  · mDNS _steamos-ha._tcp     │
│  · hwmon / proc / MangoHud   │
└──────────────────────────────┘
```

Only Gaming Mode counts. When the Steam Machine is in Desktop Mode, asleep or off, the
integration shows **Disconnected** and every other entity becomes unavailable.

## Install

### 1. Decky plugin (on the Steam Machine)

1. Install [Decky Loader](https://decky.xyz) if you haven't.
2. Download `SteamOS-HA-<version>.zip` from the [releases page](https://github.com/Pimmeke1989/steamos-ha-decky/releases).
3. In Gaming Mode open the Quick Access Menu → Decky → ⚙ → *Developer* → *Install plugin from zip*
   (enable developer mode once under Decky settings if the option is missing).
4. The plugin shows up as **Home Assistant** in the Quick Access Menu.

### 2. Home Assistant integration

Via HACS: add `https://github.com/Pimmeke1989/steamos-ha-decky` as a custom repository
(category *Integration*), install **SteamOS**, restart Home Assistant.

Manual: copy `custom_components/steamos` into your `config/custom_components/` and restart.

### 3. Pair

1. Put the Steam Machine in Gaming Mode.
2. Home Assistant should discover it ("Steam Machine found" under *Settings → Devices & services*).
   If not, add the **SteamOS** integration manually with the hostname or IP and port `8570`.
3. Confirm; a 6-digit code appears on the TV and in the plugin's panel. Type it in Home Assistant.
4. Optionally enter a [SteamGridDB API key](https://www.steamgriddb.com/profile/preferences/api)
   for artwork entities. Leave it empty to skip; you can add or remove it later under
   *Configure* on the integration.

That's it. Re-pairing later: remove the integration entry, or press *Koppeling verwijderen* in the plugin.

## Entities

All entities hang off one device. Everything except the status sensor and the notify
entity becomes `unavailable` while the Steam Machine is not in Gaming Mode.

| Entity | Description |
|---|---|
| `sensor.<name>_status` | `gaming` or `disconnected`; always available |
| `sensor.<name>_game` | Title of the running game, or `none`; attributes `appid`, `shortcut`, `started_at` |
| `binary_sensor.<name>_game_running` | On while a game runs |
| `event.<name>_game` | `game_started` / `game_stopped` with `title`, `appid`, `shortcut` |
| `sensor.<name>_cpu_temperature`, `_gpu_temperature`, `_gpu_memory_temperature`*, `_ssd_temperature`* | °C from hwmon (`k10temp`, `amdgpu`, `nvme`) |
| `sensor.<name>_cpu_usage`, `_memory_usage`, `_gpu_usage`, `_vram_usage` | % |
| `sensor.<name>_cpu_frequency`* | GHz, average of all cores |
| `sensor.<name>_gpu_power` | W |
| `sensor.<name>_fan_speed` | rpm |
| `sensor.<name>_last_boot`* | timestamp (diagnostic) |
| `sensor.<name>_fps`, `_frametime`* | averaged over the last second of MangoHud's log; only while a game runs |
| `notify.<name>_on_screen_notification` | `notify.send_message` shows a toast; fails with a clear error outside Gaming Mode |
| `image.<name>_cover`, `_hero_banner`, `_logo`, `_icon` | artwork of the running game via SteamGridDB (only with an API key) |
| `sensor.<name>_artwork_match` | which SteamGridDB game was matched (diagnostic); attributes `sgdb_id`, `overridden`, `error` |

\* disabled by default; enable in the entity settings.

A metric the machine does not expose (no matching hwmon node) simply stays unavailable.
The plugin samples every 2 s and only sends values that moved by more than a small
threshold (0.5 °C, 1 %, 0.5 W, 50 rpm), so the recorder stays quiet.

Example automation:

```yaml
alias: Washing machine done → toast on TV
triggers:
  - trigger: state
    entity_id: sensor.washing_machine_status
    to: "finished"
conditions:
  - condition: state
    entity_id: sensor.steam_machine_status
    state: gaming
actions:
  - action: notify.send_message
    target: { entity_id: notify.steam_machine_on_screen_notification }
    data: { title: Washing machine, message: The laundry is done }
```

## Actions

`notify.send_message` on the notify entity is the simple form. `steamos.notify` adds a
duration (1–60 s) and an icon (`home`, `bell`, `info`, `alert`, `check`, `door`, `phone`,
`message`, `washer`, `car`, `clock`, `sun`):

```yaml
action: steamos.notify
target: { entity_id: notify.steam_machine_on_screen_notification }
data: { title: Doorbell, message: Someone is at the door, duration: 10, icon: door }
```

`steamos.refresh_artwork` forgets the cached SteamGridDB result for the current game and
looks it up again.

## Game artwork (optional)

With a SteamGridDB API key the integration looks the running game up **by title** — not by
appid, because non-Steam shortcuts get random ids — and exposes the best-scored cover
(600×900), hero banner (1920×620), logo and icon as `image` entities. Results are cached
for 30 days, so a game costs at most five API calls. When no game runs the last artwork
stays put and `sensor.<name>_artwork_match` shows `none`, so a dashboard can decide for
itself whether to keep showing the cover. Titles that match the wrong game can be pinned
under *Configure* with one `Game title = SteamGridDB game id` per line.

## FPS via MangoHud

Gaming Mode already runs MangoHud's `mangoapp` for the performance overlay. When a game
starts, the plugin

1. finds the config file `mangoapp` uses (`MANGOHUD_CONFIGFILE` of the running process,
   else `~/.config/MangoHud/MangoHud.conf`), and makes sure it contains
   `output_folder=<log dir>` and `log_interval=250`;
2. asks `mangoapp` to reload its config and start a log session (a control message on
   MangoHud's own message queue — send-only, so the overlay keeps every frame);
3. tails the newest CSV in the log dir once a second and averages `fps` / `frametime`.

When the game stops the session is stopped and the CSV deleted. Log dir default:
`~/.local/share/steamos-ha/mangohud`. If anything in this chain fails, the FPS line in the
plugin panel shows what went wrong and the sensors stay unavailable; the other sensors are
not affected. `enabled`, `log_dir`, `config_path` and `log_interval_ms` live under
`mangohud` in `~/homebrew/settings/SteamOS HA/settings.json`.

## Development

```bash
# Decky plugin frontend
cd plugin && pnpm install && pnpm build        # → plugin/dist/index.js

# Backend tests (no Steam Machine needed; decky is stubbed)
pip install aiohttp pytest pytest-asyncio ruff
python -m pytest -q && ruff check .
```

Deploy to a Steam Machine for testing: copy the `plugin/` folder (with `dist/`) to
`~/homebrew/plugins/SteamOS HA/` and reload plugins from Decky's settings, or use the
zip built by CI. The plugin logs to `~/homebrew/logs/SteamOS HA/`.

`scripts/steamos-inventory.sh` prints everything the plugin relies on (hwmon names,
Decky user, steamos-manager D-Bus, MangoHud paths) — useful when something doesn't show up.

The API between plugin and integration is documented in [`docs/api.md`](docs/api.md); the
overall design in [`docs/design.md`](docs/design.md).

## License

MIT — see [LICENSE](LICENSE).
