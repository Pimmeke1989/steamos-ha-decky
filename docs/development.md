# Development

Everything a contributor needs that does not belong on the front page.

## Repository layout

| Path | What |
|---|---|
| `plugin/` | The Decky plugin: `main.py` (backend entry), `py_modules/steamos_ha/` (server, discovery, stats, gamescope reader), `src/` (React front-end), `tests/`. |
| `custom_components/steamos/` | The Home Assistant integration (HACS reads this path). |
| `tests/` | End-to-end tests: a real plugin `Server` in-process + a fake SteamGridDB, driven through Home Assistant. |
| `docs/api.md` | The HTTP/WebSocket contract between the two halves. |
| `docs/design.md` | Design decisions, milestones, what was verified on real hardware. |
| `scripts/` | Version bump, dependency vendoring, on-device inventory and FPS probe. |

## Building and testing

```bash
# Decky plugin front-end
cd plugin && pnpm install && pnpm build        # → plugin/dist/index.js

# Plugin backend tests (no Steam Machine needed; the decky module is stubbed)
pip install aiohttp pytest pytest-asyncio ruff
python -m pytest plugin/tests -q

# Home Assistant integration tests (also runs the plugin server in-process)
pip install pytest-homeassistant-custom-component wakeonlan
python -m pytest tests -q

ruff check .
```

Local plugin testing needs the vendored deps once: `bash scripts/vendor-plugin-deps.sh`
installs zeroconf + ifaddr into `plugin/py_modules` (git-ignored; CI does the same when
it builds the zip). They are vendored because Decky's Python has no zeroconf and avahi is
switched off on SteamOS.

## Trying a build on a device

Copy the `plugin/` folder (with `dist/` and the vendored `py_modules/`) to
`~/homebrew/plugins/SteamOS HA/` on the device and reload plugins from Decky's settings,
or install the zip that CI builds. The plugin logs to `~/homebrew/logs/SteamOS HA/`.

When something does not show up, `scripts/steamos-inventory.sh` prints everything the
plugin relies on: hwmon names, the power-supply nodes behind the battery entities, the
Decky user, and steamos-manager on D-Bus. Run it over SSH in Gaming Mode
(`ssh deck@<ip>`), because that is the mode the plugin cares about.

## Releasing

1. `python scripts/set-version.py 0.3.0` writes the version into `plugin/package.json`,
   `custom_components/steamos/manifest.json` and `steamos_ha/__init__.py`.
2. Commit, then create the tag `v0.3.0` (GitHub Desktop: *Repository → Create tag*, or
   `git tag v0.3.0`) and push it. Creating the release in the GitHub UI with that tag
   pushes the tag as well.
3. The *Decky plugin* workflow runs the tests, builds the front-end, checks that tag and
   versions agree, and attaches `SteamOS-HA-v0.3.0.zip` to the GitHub release with
   generated notes. HACS picks the new version up from the tag, and the `update` entity in
   Home Assistant shows it.

## How the pieces talk

The plugin is the server. It advertises `_steamos-ha._tcp.local.` over mDNS and serves a
small HTTP + WebSocket API on port 8570. Home Assistant is the client: it pairs once with
a 6-digit code, then keeps a WebSocket open and receives pushes. The full contract, with
every message shape, is in [`api.md`](api.md).

Status is derived from a heartbeat: the plugin's front-end (which only exists in Gaming
Mode) pings the backend every 5 s, and the backend flips to `disconnected` after 15 s of
silence. That is why Desktop Mode shows as disconnected even though the backend process
keeps running.

FPS comes from gamescope's stats pipe (`-T …/stats.pipe`), which nothing else on modern
SteamOS reads. MangoHud logging was tried first and dropped: it reports mangoapp's own
redraw rate, not the game's. See `design.md` for the on-device findings.
