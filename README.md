# steamos-ha-decky

Connects a Steam Machine (or any SteamOS device running [Decky Loader](https://decky.xyz)) to Home Assistant.

Two parts, one repo:

| Part | Where | What it does |
|---|---|---|
| **Decky plugin** "SteamOS HA" | `plugin/` | Runs a small local API on the Steam Machine, reports status / running game / stats, shows Home Assistant notifications as toasts in Gaming Mode. |
| **Home Assistant integration** `steamos` | `custom_components/steamos/` | Finds the plugin via mDNS, pairs with a code shown on screen, keeps a WebSocket open and exposes everything as one device. |

No MQTT broker, no cloud, and the Steam Machine never holds Home Assistant credentials.

> **Status: 0.1.0, feature-complete for the first release** — discovery, pairing, status,
> running game, system statistics, FPS via gamescope, on-screen notifications, optional
> artwork via SteamGridDB and a plugin update check. Tested end-to-end on a Steam Deck;
> `docs/design.md` lists what is still open for the Steam Machine itself.

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
│  · hwmon / proc / gamescope  │
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
| `sensor.<name>_fps`, `_frametime`* | from gamescope's stats pipe, averaged over the last second; only while a game runs |
| `notify.<name>_on_screen_notification` | `notify.send_message` shows a toast; fails with a clear error outside Gaming Mode |
| `image.<name>_cover`, `_icon` | artwork of the running game via SteamGridDB (only with an API key) |
| `sensor.<name>_artwork_match` | which SteamGridDB game was matched (diagnostic); attributes `sgdb_id`, `overridden`, `error` |
| `update.<name>_plugin` | compares the running plugin version with the latest GitHub release (diagnostic; install is manual) |

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
(600×900) and icon as `image` entities. Results are cached for 30 days, so a game costs
at most three API calls. When no game runs the last artwork
stays put and `sensor.<name>_artwork_match` shows `none`, so a dashboard can decide for
itself whether to keep showing the cover. Titles that match the wrong game can be pinned
under *Configure* with one `Game title = SteamGridDB game id` per line.

## FPS via gamescope

gamescope (the Gaming Mode compositor) is started with `-T …/stats.pipe` and writes
`fps=59.988003` a few times per second plus `focus=<appid>` / `focus=steam` into that FIFO.
Nothing on SteamOS reads it anymore, so the plugin opens it while a game runs and averages
the samples over the last second. No config files are touched and MangoHud is not involved
(its log reports mangoapp's own redraw rate, not the game's — verified on a Steam Deck).
The pipe is found from the running gamescope's command line; `fps.stats_pipe` in
`~/homebrew/settings/SteamOS HA/settings.json` overrides it. If the pipe cannot be found
the FPS line in the plugin panel says so and the sensors stay unavailable.

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

### Releasing

1. `python scripts/set-version.py 0.2.0` — writes the version into `plugin/package.json`,
   `custom_components/steamos/manifest.json` and `steamos_ha/__init__.py`.
2. Commit, then create the tag `v0.2.0` (GitHub Desktop: *Repository → Create tag*, or
   `git tag v0.2.0`) and push it. Alternatively create the release with that tag in the
   GitHub UI — that pushes the tag too.
3. The *Decky plugin* workflow runs tests, builds the frontend, checks that tag and
   versions agree, and attaches `SteamOS-HA-v0.2.0.zip` to the GitHub release with
   generated release notes. HACS picks up the new version from the tag; the `update`
   entity in Home Assistant shows it.

Local plugin testing needs the vendored deps once: `bash scripts/vendor-plugin-deps.sh`
(installs zeroconf + ifaddr into `plugin/py_modules`, git-ignored; CI does the same for the zip).

`scripts/steamos-inventory.sh` prints everything the plugin relies on (hwmon names,
Decky user, steamos-manager D-Bus, gamescope/MangoHud processes) and `scripts/fps-probe.py`
checks the possible FPS sources — useful when something doesn't show up.

The API between plugin and integration is documented in [`docs/api.md`](docs/api.md); the
overall design in [`docs/design.md`](docs/design.md).

## License

MIT — see [LICENSE](LICENSE). The release zip bundles [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf)
(LGPL-2.1) and [ifaddr](https://github.com/ifaddr/ifaddr) (MIT) unmodified, as pure-Python
packages under `py_modules/`, because Decky's runtime has no zeroconf and avahi is off on SteamOS.
