<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
    <img src="custom_components/steamos/brand/logo@2x.png" alt="SteamOS for Home Assistant" width="480">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Pimmeke1989/steamos-ha-decky/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Pimmeke1989/steamos-ha-decky?display_name=tag&sort=semver"></a>
  <a href="https://github.com/Pimmeke1989/steamos-ha-decky/actions/workflows/plugin.yml"><img alt="Decky plugin CI" src="https://github.com/Pimmeke1989/steamos-ha-decky/actions/workflows/plugin.yml/badge.svg"></a>
  <a href="https://github.com/Pimmeke1989/steamos-ha-decky/actions/workflows/integration.yml"><img alt="Home Assistant integration CI" src="https://github.com/Pimmeke1989/steamos-ha-decky/actions/workflows/integration.yml/badge.svg"></a>
  <a href="https://hacs.xyz"><img alt="HACS custom repository" src="https://img.shields.io/badge/HACS-custom%20repository-41BDF5"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/github/license/Pimmeke1989/steamos-ha-decky"></a>
</p>

<h1 align="center">SteamOS for Home Assistant</h1>

<p align="center">
  Your Steam Machine (or Steam Deck) as a device in Home Assistant: see what's being played,
  watch temperatures and FPS, get notifications on the TV, and switch the machine on or off.
</p>

---

## What it does

Once installed, your Steam Machine shows up in Home Assistant as one device with a set of
sensors and buttons. In plain terms, it lets you:

- **See if the machine is in use.** A status sensor says whether it is in Gaming Mode or not,
  and a game sensor shows the title of whatever is running right now (also for non-Steam
  games you added as shortcuts).
- **Keep an eye on the hardware.** CPU and GPU temperature, CPU/GPU/memory/VRAM usage,
  GPU power draw, fan speed, SSD temperature, and the FPS and frame time of the game that is
  running.
- **Put messages on the TV.** Send a notification from any automation and it pops up as a
  toast in the Steam UI — "the laundry is done", "someone is at the door", "dinner in
  10 minutes".
- **Control power.** Buttons for *Sleep*, *Shut down*, *Restart* and *Turn on*
  (Wake-on-LAN). No extra YAML needed.
- **Show the game's artwork.** Optionally, with a free SteamGridDB key, the cover and icon
  of the running game become image entities for your dashboard.
- **React to events.** A `game_started` / `game_stopped` event and an on/off "game running"
  sensor make automations easy: dim the lights when a game starts, bring them back when it
  stops.

Everything stays on your own network. There is no cloud service, no MQTT broker, and the
Steam Machine never stores a Home Assistant password or token — pairing works the other way
round, with a code shown on screen.

## How it works, in one paragraph

Two small pieces of software talk to each other. On the Steam Machine runs a
[Decky Loader](https://decky.xyz) plugin called **SteamOS HA**; it reads the hardware
sensors, watches which game is running, and offers all of that on a tiny local API. In
Home Assistant runs the **SteamOS** integration; it finds the plugin automatically on the
network, pairs with it once, and from then on gets every change pushed instantly. Only
Gaming Mode counts: when the machine is in Desktop Mode, asleep or switched off, Home
Assistant shows it as *Disconnected* and the other sensors become unavailable (the *Turn on*
button keeps working, of course).

## Installation

You need three things: the plugin on the Steam Machine, the integration in Home Assistant,
and a one-time pairing. Ten minutes, tops.

### Step 1 — the plugin on the Steam Machine

1. Install [Decky Loader](https://decky.xyz) if you haven't already (it is the plugin
   system for Gaming Mode; installation is a one-time thing in Desktop Mode).
2. Download `SteamOS-HA-v<version>.zip` from the
   [latest release](https://github.com/Pimmeke1989/steamos-ha-decky/releases/latest).
   You can do this on the Steam Machine itself in Desktop Mode, or on any computer and copy
   the file over.
3. In Gaming Mode, open the Quick Access Menu (the ··· button), go to the Decky tab (the
   plug icon), press the ⚙ gear, and choose **Developer → Install plugin from zip**. If you
   don't see a *Developer* entry, enable *Developer mode* in Decky's settings first.
4. Pick the zip. The plugin appears in the Decky tab as **Home Assistant**. Open it once: it
   shows the address it is listening on and whether discovery is working.

### Step 2 — the integration in Home Assistant

Via **HACS** (recommended, so you get updates):

1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/Pimmeke1989/steamos-ha-decky`, category *Integration*.
3. Search for **SteamOS**, install it, and restart Home Assistant.

Manually: copy the folder `custom_components/steamos` from this repository into the
`custom_components` folder of your Home Assistant configuration and restart.

### Step 3 — pairing

1. Make sure the Steam Machine is in Gaming Mode.
2. Home Assistant should find it on its own: under *Settings → Devices & services* a card
   says "Steam Machine found" (or whatever the machine is called). Press **Add**. If nothing
   shows up, press *Add integration*, search for *SteamOS* and enter the machine's IP
   address; the port is `8570`.
3. A 6-digit code appears on the TV (and in the plugin panel). Type it into Home Assistant.
4. Optionally paste a [SteamGridDB API key](https://www.steamgriddb.com/profile/preferences/api)
   for game artwork. Leave it empty to skip; you can add or remove it any time later under
   *Configure*.

Done. The device and all its entities are there right away.

Need to pair again later (new Home Assistant, reset, or just curious)? Remove the
integration entry in Home Assistant, or press **Remove pairing** in the plugin panel, and
run Step 3 again.

### Updating

New versions of the integration arrive through HACS like any other. For the plugin, the
`update` entity in Home Assistant tells you when a newer release exists; install the new zip
the same way as in Step 1 (installing over the old one is fine, the pairing is kept).

## What you get in Home Assistant

All entities belong to one device. Entities marked with * are disabled by default — enable
them in the entity settings if you want them. Everything except *Status*, the notification
entity and *Turn on* becomes `unavailable` while the machine is not in Gaming Mode.

| Entity | What it tells you |
|---|---|
| `sensor.<name>_status` | `gaming` or `disconnected` |
| `sensor.<name>_game` | Title of the running game, or `none`. Attributes: `appid`, `shortcut`, `started_at` |
| `binary_sensor.<name>_game_running` | On while a game runs |
| `event.<name>_game` | `game_started` / `game_stopped`, with the title in the event data |
| `sensor.<name>_cpu_temperature`, `_gpu_temperature`, `_gpu_memory_temperature`*, `_ssd_temperature`* | °C |
| `sensor.<name>_cpu_usage`, `_memory_usage`, `_gpu_usage`, `_vram_usage` | % |
| `sensor.<name>_cpu_frequency`* | GHz, average of all cores |
| `sensor.<name>_gpu_power` | W |
| `sensor.<name>_fan_speed` | rpm |
| `sensor.<name>_fps`, `_frametime`* | Frames per second and milliseconds per frame of the running game, averaged over the last second |
| `sensor.<name>_last_boot`* | When the machine last started |
| `notify.<name>_on_screen_notification` | Send a message here and it pops up on the TV |
| `button.<name>_sleep`, `_shut_down`, `_restart`* | Same as picking those from the Steam power menu |
| `button.<name>_turn_on` | Sends a Wake-on-LAN packet |
| `image.<name>_cover`, `_icon` | Artwork of the running game (only with a SteamGridDB key) |
| `sensor.<name>_artwork_match` | Which SteamGridDB entry was used (diagnostic) |
| `update.<name>_plugin` | Tells you when a newer plugin release exists |

A metric your machine doesn't expose simply stays unavailable. Sensor values are only sent
when they actually change by a meaningful amount, so your database doesn't fill up with
noise.

## Things you can do with it

**A message on the TV when the washing machine is done:**

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

**The same, but with a longer display time and an icon.** The `steamos.notify` action adds
a duration (1–60 s) and an icon: `home`, `bell`, `info`, `alert`, `check`, `door`, `phone`,
`message`, `washer`, `car`, `clock` or `sun`.

```yaml
action: steamos.notify
target: { entity_id: notify.steam_machine_on_screen_notification }
data: { title: Doorbell, message: Someone is at the door, duration: 10, icon: door }
```

**Dim the lights when a game starts, restore them when it stops:**

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.steam_machine_game_running
actions:
  - if:
      - condition: state
        entity_id: binary_sensor.steam_machine_game_running
        state: "on"
    then:
      - action: light.turn_on
        target: { entity_id: light.living_room }
        data: { brightness_pct: 20 }
    else:
      - action: scene.turn_on
        target: { entity_id: scene.living_room_evening }
```

**Put the machine to sleep when everyone leaves the house:** a `button.press` on
`button.steam_machine_sleep` in your "away" automation. And **wake it before a gaming
evening** with `button.steam_machine_turn_on`.

## Power buttons and Wake-on-LAN

*Sleep*, *Shut down* and *Restart* do exactly what the same options in the Steam power
menu do. They only work while the machine is in Gaming Mode, since that is where the Steam
UI lives.

*Turn on* works differently: Home Assistant sends a Wake-on-LAN "magic packet" to the
machine's network card. The plugin tells the integration its MAC address during pairing, so
there is nothing to configure. Two things to know:

- **Wake-on-LAN needs a cable.** A Steam Machine on Ethernet wakes from sleep, and from
  fully off as well if Wake-on-LAN is enabled in its firmware settings. Over Wi-Fi it
  generally does not work; that is a limitation of Wi-Fi hardware, not of this integration.
- If Home Assistant runs on a different network segment (a VLAN, a Docker network), set the
  broadcast address of the Steam Machine's network (for example `192.168.1.255`) under
  *Configure* on the integration.

## Game artwork (optional)

With a free [SteamGridDB](https://www.steamgriddb.com) API key, the integration looks up the
running game **by its title** and exposes the highest-rated cover (600×900) and icon as
image entities. It searches by title rather than by Steam's app id on purpose: games you
added as non-Steam shortcuts get a random id that means nothing to anyone.

Results are cached for 30 days, so a game costs a handful of requests once and nothing after
that. When no game runs, the last artwork stays in place and `sensor.<name>_artwork_match`
shows `none`, so your dashboard can decide for itself whether to keep showing the cover.

If a title matches the wrong game, pin it under *Configure* with one line per game:
`Game title = SteamGridDB game id`. The `steamos.refresh_artwork` action throws away the
cached result for the current game and looks it up again.

## Frequently asked questions

**Home Assistant doesn't find the Steam Machine.**
Check that it is in Gaming Mode and that the *mDNS* line in the plugin panel says
`zeroconf` (not *unavailable*). Some
routers block mDNS between Wi-Fi and wired devices or between VLANs; in that case add the
integration manually with the IP address (port `8570`). It works just as well, you only
lose the automatic discovery.

**The status says Disconnected but the machine is on.**
Then it is not in Gaming Mode. That is by design: the plugin's front-end only exists inside
the Steam UI, so Desktop Mode counts as "not available for gaming".

**A temperature or fan sensor is missing.**
Not every device exposes every sensor. The plugin reads whatever the hardware offers; a
sensor that isn't there stays unavailable rather than showing a wrong number.

**FPS shows nothing while a game is running.**
Open the plugin panel: the *FPS* line says whether it could find gamescope's stats pipe.
FPS is only reported while a game is in the foreground.

**Can I use this with a Steam Deck?**
Yes. Everything except Wake-on-LAN (the Deck is on Wi-Fi) works the same, and the Deck is
where most of the testing happened.

**Is anything sent to the internet?**
Only the optional SteamGridDB artwork lookups (with your own key) and the check for a newer
plugin release on GitHub. Everything else is local.

## For developers

Building, testing, releasing and the design behind the two halves are described in
[`docs/development.md`](docs/development.md). The API between the plugin and the
integration is in [`docs/api.md`](docs/api.md), and the design decisions and on-device
findings in [`docs/design.md`](docs/design.md). Issues and pull requests are welcome.

## License

MIT — see [LICENSE](LICENSE). The release zip bundles
[python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) (LGPL-2.1) and
[ifaddr](https://github.com/ifaddr/ifaddr) (MIT) unmodified as pure-Python packages under
`py_modules/`, because Decky's runtime has no zeroconf and avahi is switched off on SteamOS.
