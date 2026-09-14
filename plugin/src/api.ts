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
  hostname: string;
  ip: string | null;
  mac: string | null;
  os_version: string | null;
  battery: boolean;
  last_power: PowerResult | null;
  machine_id: string;
}

/** What became of the last Sleep / Shut down / Restart request. */
export interface PowerResult {
  action: string;
  ok: boolean | null; // null = handed to the Steam UI, no answer yet
  detail: string;
  at: string;
}

export interface PluginSettings {
  port: number;
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
export const powerResult = callable<[action: string, ok: boolean, detail: string], void>("power_result");
