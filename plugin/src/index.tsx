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
import { FaHome } from "react-icons/fa";

import {
  cancelPairing,
  getStatus,
  heartbeat,
  NotifyPayload,
  PluginStatus,
  setRunningApp,
  unpairAll,
} from "./api";

// Steam client globals available inside the Gaming Mode UI.
declare const SteamClient: any;
declare const appStore: any;

const HEARTBEAT_MS = 5000;
const SHORTCUT_APP_TYPE = 1073741824;

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
          <Field label="Status">Laden…</Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const connection = status.server_error
    ? `Fout: ${status.server_error}`
    : status.paired
      ? status.connected > 0
        ? "Verbonden"
        : "Gekoppeld, wacht op Home Assistant"
      : "Nog niet gekoppeld";

  return (
    <>
      <PanelSection title="Home Assistant">
        <PanelSectionRow>
          <Field label="Verbinding" focusable>
            {connection}
          </Field>
        </PanelSectionRow>
        {status.pairing_code && (
          <PanelSectionRow>
            <Field label="Koppelcode" description="Vul deze code in Home Assistant in" focusable>
              <span style={{ fontSize: "1.6em", fontWeight: 700, letterSpacing: "0.08em" }}>
                {status.pairing_code}
              </span>
            </Field>
          </PanelSectionRow>
        )}
        {status.pairing_code && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => cancelPairing()}>
              Koppelen annuleren
            </ButtonItem>
          </PanelSectionRow>
        )}
        {status.paired && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => unpairAll().then(setStatus)}>
              Koppeling verwijderen
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
            label="FPS"
            description={
              status.mangohud.last_error
                ? `MangoHud: ${status.mangohud.last_error}`
                : status.mangohud.active
                  ? "via MangoHud-log"
                  : status.mangohud.enabled
                    ? "start bij volgende game"
                    : "uitgeschakeld"
            }
            focusable
          >
            {status.perf?.fps != null ? Math.round(status.perf.fps) : "—"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Adres" focusable>
            {status.hostname}:{status.port}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="mDNS" focusable>
            {status.discovery === "none" ? "niet beschikbaar (handmatig toevoegen)" : status.discovery}
          </Field>
        </PanelSectionRow>
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
      icon: <FaHome />,
    });
  };
  const onPairingCode = (code: string | null) => {
    if (code) {
      toaster.toast({
        title: "Home Assistant koppelen",
        body: `Koppelcode: ${code}`,
        duration: 15000,
        icon: <FaHome />,
      });
    }
  };
  addEventListener<[NotifyPayload]>("notify", onNotify);
  addEventListener<[string | null]>("pairing_code", onPairingCode);

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
    },
  };
});
