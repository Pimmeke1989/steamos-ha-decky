#!/usr/bin/env bash
# steamos-inventory.sh — leest uit wat we nodig hebben voor de Decky-plugin + HA-integratie.
# Alleen lezen, verandert niets. Draai in Desktop Mode (Konsole) of via SSH:
#   bash steamos-inventory.sh > inventory.txt 2>&1
set +e

section() { printf '\n==== %s ====\n' "$1"; }
show() { printf '%s: ' "$1"; shift; "$@" 2>&1 | head -n 20; }
catf() { [ -r "$1" ] && printf '%s = %s\n' "$1" "$(tr -d '\n' < "$1")" || printf '%s = (niet leesbaar)\n' "$1"; }

section "Systeem"
catf /etc/os-release
catf /sys/class/dmi/id/product_name
catf /sys/class/dmi/id/sys_vendor
catf /sys/class/dmi/id/bios_version
show "kernel" uname -r
show "user" id
show "hostname" uname -n
show "uptime" cat /proc/uptime
show "python3" python3 --version
show "python3 pad" which python3

section "Steam / SteamOS"
show "steamos versie" bash -c "grep -E '^(VERSION_ID|BUILD_ID|VARIANT_ID)=' /etc/os-release"
for d in "$HOME/.steam/steam" "$HOME/.local/share/Steam"; do
  [ -d "$d" ] && echo "Steam dir: $d -> $(readlink -f "$d")"
done
ls "$HOME/.steam/steam/package/"*.manifest 2>/dev/null | head -n 3
grep -m1 '"version"' "$HOME/.steam/steam/package/steam_client_steamos.manifest" 2>/dev/null
[ -r "$HOME/.steam/steam/logs/console_log.txt" ] && echo "console_log.txt: aanwezig ($(wc -l < "$HOME/.steam/steam/logs/console_log.txt") regels)"
grep -a 'Game process added\|Game process removed' "$HOME/.steam/steam/logs/console_log.txt" 2>/dev/null | tail -n 3
show "steamos-session-select" which steamos-session-select
show "steamos-readonly" steamos-readonly status

section "Sessie / mode-detectie"
show "loginctl sessies" loginctl list-sessions --no-legend
for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
  echo "-- sessie $s:"; loginctl show-session "$s" -p Type -p Desktop -p Class -p State -p Active 2>/dev/null
done
show "gamescope procs" pgrep -a gamescope
show "gamescope-session unit" systemctl --user status gamescope-session 2>&1
show "XDG_CURRENT_DESKTOP" printenv XDG_CURRENT_DESKTOP
show "XDG_SESSION_TYPE" printenv XDG_SESSION_TYPE
show "WAYLAND_DISPLAY" printenv WAYLAND_DISPLAY
show "DISPLAY" printenv DISPLAY

section "Decky Loader"
show "plugin_loader.service" systemctl status plugin_loader --no-pager
show "plugin_loader proces" pgrep -a -f PluginLoader
ls -la "$HOME/homebrew" 2>/dev/null
ls "$HOME/homebrew/plugins" 2>/dev/null
[ -r "$HOME/homebrew/services/PluginLoader" ] && echo "PluginLoader binary aanwezig"
cat "$HOME/homebrew/services/.loader.version" 2>/dev/null
cat "$HOME/homebrew/settings/loader.json" 2>/dev/null | head -n 30

section "hwmon (temperaturen, fan, GPU)"
for h in /sys/class/hwmon/hwmon*; do
  n=$(cat "$h/name" 2>/dev/null)
  echo "-- $h  name=$n"
  for f in "$h"/temp*_input "$h"/temp*_label "$h"/fan*_input "$h"/fan*_label "$h"/freq*_input "$h"/freq*_label "$h"/power*_average "$h"/power*_input "$h"/in*_input; do
    [ -r "$f" ] && printf '   %s = %s\n' "$(basename "$f")" "$(cat "$f" 2>/dev/null)"
  done
done

section "GPU (DRM / amdgpu)"
for c in /sys/class/drm/card*; do
  [ -d "$c/device" ] || continue
  echo "-- $c"
  catf "$c/device/vendor"; catf "$c/device/device"
  catf "$c/device/gpu_busy_percent"
  catf "$c/device/mem_busy_percent"
  catf "$c/device/mem_info_vram_total"
  catf "$c/device/mem_info_vram_used"
  catf "$c/device/power_dpm_force_performance_level"
done
for s in /sys/class/drm/*/status; do printf '%s = %s\n' "$s" "$(cat "$s" 2>/dev/null)"; done
show "lspci VGA" bash -c "lspci 2>/dev/null | grep -i -E 'vga|display|3d'"

section "CPU / RAM"
grep -m1 'model name' /proc/cpuinfo
grep -c '^processor' /proc/cpuinfo
grep -E 'MemTotal|MemAvailable' /proc/meminfo
head -n1 /proc/stat

section "steamos-manager (D-Bus)"
show "unit (user)" systemctl --user status steamos-manager --no-pager
show "unit (system)" systemctl status steamos-manager --no-pager
show "busctl user names" bash -c "busctl --user list 2>/dev/null | grep -i steam"
show "introspect" busctl --user introspect com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 2>&1
show "DeviceModel" busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.Manager2 DeviceModel
show "TdpLimit" busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.TdpLimit1 TdpLimit
show "HdmiCecState" busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.HdmiCec1 HdmiCecState

section "MangoHud / mangoapp"
show "mangohud" which mangohud
show "mangoapp" which mangoapp
show "mangohudctl" which mangohudctl
show "mangohud versie" bash -c "mangohud --version 2>/dev/null || pacman -Q mangohud 2>/dev/null"
show "mangoapp procs" pgrep -a mangoapp
ls -la "$HOME/.config/MangoHud/" 2>/dev/null
cat "$HOME/.config/MangoHud/MangoHud.conf" 2>/dev/null | head -n 40
show "MANGOHUD env (deze shell)" bash -c "printenv | grep -i mango"
for pid in $(pgrep -x mangoapp 2>/dev/null); do
  echo "-- mangoapp pid $pid environment (MANGOHUD*):"
  tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep -i mangohud
  cfg=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep '^MANGOHUD_CONFIGFILE=' | cut -d= -f2-)
  if [ -n "$cfg" ]; then echo "-- inhoud van $cfg:"; cat "$cfg" 2>/dev/null; ls -la "$cfg" 2>/dev/null; fi
done
for pid in $(pgrep -x gamescope 2>/dev/null | head -n 1); do
  echo "-- gamescope pid $pid environment (MANGOHUD*):"
  tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep -i mangohud
done
show "sysv msg queues" ipcs -q
ls -la /tmp/mangoapp* /tmp/MangoHud* 2>/dev/null

section "Audio"
show "wpctl status" bash -c "wpctl status 2>/dev/null | head -n 40"
show "default sink volume" wpctl get-volume @DEFAULT_AUDIO_SINK@

section "Netwerk / mDNS"
show "ip" bash -c "ip -brief addr 2>/dev/null"
show "mac" bash -c "cat /sys/class/net/*/address 2>/dev/null | paste -sd ' '"
show "wol" bash -c "for i in /sys/class/net/e*; do echo \$(basename \$i): \$(ethtool \$(basename \$i) 2>/dev/null | grep -i wake-on); done"
show "avahi-daemon" systemctl status avahi-daemon --no-pager
show "python zeroconf" python3 -c "import zeroconf, sys; print(zeroconf.__version__)"
show "python aiohttp" python3 -c "import aiohttp; print(aiohttp.__version__)"
show "python dbus libs" python3 -c "
for m in ('dbus','dbus_next','jeepney','pydbus','gi'):
    try:
        __import__(m); print(m, 'ok')
    except Exception as e:
        print(m, 'nee')"
show "luisterende poorten" bash -c "ss -ltnp 2>/dev/null | head -n 30"

section "Power / polkit"
show "systemctl suspend (kan?)" bash -c "systemctl suspend --dry-run 2>&1 | head -n 3"
ls -la /etc/polkit-1/rules.d/ 2>/dev/null
show "rfkill" rfkill list

echo
echo "==== klaar ===="
