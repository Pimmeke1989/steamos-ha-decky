import { callable } from "@decky/api";

export interface GameInfo {
  title: string;
  appid: number | null;
  shortcut: boolean;
  started_at: string;
}

export interface PluginStatus {
  version: string;
  status: "gaming" | "disconnected";
  port: number;
  server_error: string | null;
  discovery: "zeroconf" | "avahi" | "none";
  paired: boolean;
  clients: { name: string; created: string }[];
  connected: number;
  pairing_code: string | null;
  game: GameInfo | null;
  perf: { fps: number | null; frametime_ms: number | null } | null;
  hostname: string;
  machine_id: string;
  mangohud: {
    enabled: boolean;
    active: boolean;
    last_error: string | null;
    config_path: string | null;
    csv_path: string | null;
    log_dir: string;
  };
}

export interface PluginSettings {
  port: number;
  mangohud: { enabled: boolean; log_dir: string; config_path: string; log_interval_ms: number };
}

export interface NotifyPayload {
  title: string;
  message: string;
  duration: number;
  icon: string;
}

export const heartbeat = callable<[], PluginStatus>("heartbeat");
export const getStatus = callable<[], PluginStatus>("get_status");
export const setRunningApp = callable<
  [appid: number | null, name: string | null, shortcut: boolean],
  void
>("set_running_app");
export const getSettings = callable<[], PluginSettings>("get_settings");
export const setSettings = callable<[changes: Partial<PluginSettings>], PluginSettings>("set_settings");
export const unpairAll = callable<[], PluginStatus>("unpair_all");
export const cancelPairing = callable<[], void>("cancel_pairing");
