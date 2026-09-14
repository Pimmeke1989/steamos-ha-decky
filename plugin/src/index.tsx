import {
  addEventListener,
  definePlugin,
  removeEventListener,
  toaster,
} from "@decky/api";
import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  Router,
  staticClasses,
} from "@decky/ui";
import { FC, useEffect, useState } from "react";
import {
  FaBell,
  FaCar,
  FaCheckCircle,
  FaClock,
  FaComment,
  FaDoorOpen,
  FaExclamationTriangle,
  FaHome,
  FaInfoCircle,
  FaPhone,
  FaSun,
  FaTshirt,
} from "react-icons/fa";

import {
  cancelPairing,
  getStatus,
  heartbeat,
  NotifyPayload,
  PluginStatus,
  powerResult,
  setRunningApp,
  unpairAll,
} from "./api";

// Steam client globals available inside the Gaming Mode UI.
declare const SteamClient: any;
declare const appStore: any;

const HEARTBEAT_MS = 5000;
const SHORTCUT_APP_TYPE = 1073741824;

/**
 * Steam's own power functions, per action, in the order we prefer them.
 *
 * The names are not stable across SteamOS releases — an earlier version of this
 * plugin called `System.Suspend()` and `System.Shutdown()`, which simply do not
 * exist (the real ones end in `PC`), so the buttons did nothing at all. Listing
 * every candidate and calling the first one that is actually present keeps that
 * from happening again. `User.Start*` is preferred where it exists because it is
 * the graceful path the Steam power menu itself takes: Steam closes the game and
 * syncs cloud saves first.
 */
type SteamCall = [path: string, ...args: unknown[]];

const POWER_CALLS: Record<string, SteamCall[]> = {
  suspend: [["System.SuspendPC"], ["System.Suspend"]],
  shutdown: [["User.StartShutdown", false], ["System.ShutdownPC"]],
  reboot: [["User.StartRestart", false], ["System.RestartPC"]],
};

/** Calls SteamClient.<path>(...args); false when that function does not exist here. */
const callSteamClient = (path: string, args: unknown[]): boolean => {
  const parts = path.split(".");
  const name = parts.pop() as string;
  let owner: any = SteamClient;
  for (const part of parts) {
    owner = owner?.[part];
  }
  if (typeof owner?.[name] !== "function") return false;
  owner[name](...args);
  return true;
};

// Icon names accepted by the steamos.notify action in Home Assistant.
const ICONS: Record<string, JSX.Element> = {
  home: <FaHome />,
  bell: <FaBell />,
  info: <FaInfoCircle />,
  alert: <FaExclamationTriangle />,
  check: <FaCheckCircle />,
  door: <FaDoorOpen />,
  phone: <FaPhone />,
  message: <FaComment />,
  washer: <FaTshirt />,
  car: <FaCar />,
  clock: <FaClock />,
  sun: <FaSun />,
};

// ----------------------------------------------------------------- game tracking

function isShortcut(overview: any): boolean {
  if (!overview) return false;
  try {
    if (typeof overview.BIsShortcut === "function") return !!overview.BIsShortcut();
  } catch {
    /* ignore */
  }
  return overview.app_type === SHORTCUT_APP_TYPE;
}

function reportApp(appid: number | null, overview: any): void {
  const name: string | null = overview?.display_name ?? null;
  setRunningApp(appid, name, isShortcut(overview)).catch(() => undefined);
}

function reportMainRunningApp(): void {
  const app = (Router as any).MainRunningApp;
  if (app && app.appid !== undefined) {
    const appid = Number(app.appid);
    let overview: any = null;
    try {
      overview = appStore.GetAppOverviewByAppID(appid);
    } catch {
      /* ignore */
    }
    reportApp(appid, overview ?? { display_name: app.display_name });
  } else {
    setRunningApp(null, null, false).catch(() => undefined);
  }
}

// --------------------------------------------------------------------- QAM panel

const Content: FC = () => {
  const [status, setStatus] = useState<PluginStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = () => getStatus().then((s) => alive && setStatus(s)).catch(() => undefined);
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!status) {
    return (
      <PanelSection title="Home Assistant">
        <PanelSectionRow>
          <Field label="Status">Loading…</Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const connection = status.server_error
    ? `Error: ${status.server_error}`
    : status.paired
      ? status.connected > 0
        ? "Connected"
        : "Paired, waiting for Home Assistant"
      : "Not paired yet";

  return (
    <>
      <PanelSection title="Home Assistant">
        <PanelSectionRow>
          <Field label="Connection" focusable>
            {connection}
          </Field>
        </PanelSectionRow>
        {status.pairing_code && (
          <PanelSectionRow>
            <Field label="Pairing code" description="Enter this code in Home Assistant" focusable>
              <span style={{ fontSize: "1.6em", fontWeight: 700, letterSpacing: "0.08em" }}>
                {status.pairing_code}
              </span>
            </Field>
          </PanelSectionRow>
        )}
        {status.pairing_code && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => cancelPairing()}>
              Cancel pairing
            </ButtonItem>
          </PanelSectionRow>
        )}
        {status.paired && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => unpairAll().then(setStatus)}>
              Remove pairing
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Details">
        <PanelSectionRow>
          <Field label="Game" focusable>
            {status.game ? status.game.title : "—"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="Address"
            description={status.mac ? `${status.hostname} · MAC ${status.mac}` : status.hostname}
            focusable
          >
            {status.ip ?? status.hostname}:{status.port}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="SteamOS" focusable>
            {status.os_version ?? "unknown"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="mDNS" focusable>
            {status.discovery === "none" ? "unavailable (add manually)" : status.discovery}
          </Field>
        </PanelSectionRow>
        {status.last_power && (
          <PanelSectionRow>
            <Field
              label="Last power action"
              description={status.last_power.detail}
              focusable
            >
              {status.last_power.action}
              {status.last_power.ok === false ? " — failed" : status.last_power.ok ? " — ok" : " — sent"}
            </Field>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field label="Plugin" focusable>
            v{status.version}
          </Field>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};

// ------------------------------------------------------------------ plugin setup

export default definePlugin(() => {
  // 1. Heartbeat: as long as this runs, the backend reports "gaming".
  heartbeat().catch(() => undefined);
  const heartbeatTimer = setInterval(() => heartbeat().catch(() => undefined), HEARTBEAT_MS);

  // 2. Running game: initial state + lifetime notifications.
  reportMainRunningApp();
  let lifetimeHook: { unregister: () => void } | null = null;
  try {
    lifetimeHook = SteamClient.GameSessions.RegisterForAppLifetimeNotifications(
      (data: { unAppID: number; nInstanceID: number; bRunning: boolean }) => {
        if (data.bRunning) {
          let overview: any = null;
          try {
            overview = appStore.GetAppOverviewByAppID(data.unAppID);
          } catch {
            /* ignore */
          }
          reportApp(data.unAppID, overview);
        } else {
          // Another instance may still be running (rare); re-read the router.
          setTimeout(reportMainRunningApp, 500);
        }
      },
    );
  } catch (err) {
    console.error("[steamos-ha] RegisterForAppLifetimeNotifications failed", err);
  }

  // 3. Backend → frontend events.
  const onNotify = (payload: NotifyPayload) => {
    toaster.toast({
      title: payload.title || "Home Assistant",
      body: payload.message,
      duration: Math.round((payload.duration || 6) * 1000),
      icon: ICONS[payload.icon] ?? ICONS.home,
    });
  };
  const onPairingCode = (code: string | null) => {
    if (code) {
      toaster.toast({
        title: "Pair with Home Assistant",
        body: `Pairing code: ${code}`,
        duration: 15000,
        icon: <FaHome />,
      });
    }
  };
  const onPower = (action: string) => {
    const attempts = POWER_CALLS[action];
    if (!attempts) {
      powerResult(action, false, `unknown action ${action}`).catch(() => undefined);
      return;
    }
    const tried: string[] = [];
    for (const [path, ...args] of attempts) {
      try {
        if (callSteamClient(path, args)) {
          powerResult(action, true, `SteamClient.${path}`).catch(() => undefined);
          return;
        }
        tried.push(`${path} (missing)`);
      } catch (err) {
        tried.push(`${path} (${err})`);
      }
    }
    powerResult(action, false, `no usable call: ${tried.join(", ")}`).catch(() => undefined);
  };
  addEventListener<[NotifyPayload]>("notify", onNotify);
  addEventListener<[string | null]>("pairing_code", onPairingCode);
  addEventListener<[string]>("power", onPower);

  return {
    name: "SteamOS HA",
    titleView: <div className={staticClasses.Title}>Home Assistant</div>,
    content: <Content />,
    icon: <FaHome />,
    onDismount() {
      clearInterval(heartbeatTimer);
      lifetimeHook?.unregister();
      removeEventListener("notify", onNotify);
      removeEventListener("pairing_code", onPairingCode);
      removeEventListener("power", onPower);
    },
  };
});
