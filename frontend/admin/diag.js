// KIRO push: client-side diagnostics. Records WHY the page was reloaded while an event was being edited (a reload loses
// module memory, the editor and unsaved input). Sends a few allowlisted scalars to POST /client-log; never draft content.
// Everything here is fire-and-forget: failures are swallowed and never reach the user.
import {
  EDITOR_OPEN_KEY, PAGEHIDE_KEY, MOUNTED_AT_KEY, ERRORS_SENT_KEY, cleanClientDetail, CLIENT_EVENTS,
  getFlag, storageGet, storageSet, storageRemove,
} from "./logic.js";
import { api, sessionStore } from "./ui.js";

const MAX_ERRORS_PER_SESSION = 3;
let mounts = 0; // module memory: survives a Core re-mount of the view, not a real page reload
let sentErrors = 0;

/** Facts about the page itself (no personal data). `tab` is the plugin tab id. */
export function pageFacts(tab) {
  let nav = "";
  try { nav = performance.getEntriesByType("navigation")[0]?.type || ""; } catch { /* old WebView */ }
  let tg = false;
  try { tg = !!window.Telegram?.WebApp; } catch { /* ignore */ }
  return {
    nav_type: nav || "unknown",
    visibility: document.visibilityState || "unknown",
    in_telegram: tg,
    ua: (navigator.userAgent || "").slice(0, 160),
    tab: tab || "",
  };
}

export function clientLog(event, detail) {
  if (!CLIENT_EVENTS.includes(event)) return;
  try {
    api("/client-log", "POST", { event, detail: cleanClientDetail(detail) }).catch(() => {});
  } catch { /* never surface */ }
}

/**
 * Called once per plugin mount. Detects "the page died while the editor was open" from the sessionStorage flag that
 * the events tab sets on editor open and clears on a normal close. Returns {stop}.
 */
export function startDiagnostics({ getTab }) {
  const ss = sessionStore();
  const now = Date.now();
  const facts = pageFacts(getTab());
  const remount = mounts > 0;
  mounts += 1;
  const wasOpen = getFlag(ss, EDITOR_OPEN_KEY);
  const prevMounted = Number(storageGet(ss, MOUNTED_AT_KEY)) || 0;
  const pagehide = storageGet(ss, PAGEHIDE_KEY);
  storageRemove(ss, PAGEHIDE_KEY);
  storageSet(ss, MOUNTED_AT_KEY, now);
  if (wasOpen) {
    // ms_since_mount here = how long the previous mount lived before it was lost; last_pagehide "none" = no unload event (renderer killed / remount).
    clientLog("page_reload_detected", {
      ...facts, nav_type: remount ? "remount" : facts.nav_type, editor_open: true,
      ms_since_mount: prevMounted ? now - prevMounted : 0, last_pagehide: pagehide || "none",
    });
  }

  const onPageHide = (e) => storageSet(sessionStore(), PAGEHIDE_KEY, e && e.persisted ? "persisted" : "not");
  const report = (event, message) => {
    if (!getFlag(sessionStore(), EDITOR_OPEN_KEY)) return;
    const done = Number(storageGet(sessionStore(), ERRORS_SENT_KEY)) || sentErrors;
    if (done >= MAX_ERRORS_PER_SESSION) return;
    sentErrors = done + 1;
    storageSet(sessionStore(), ERRORS_SENT_KEY, sentErrors);
    clientLog(event, { ...pageFacts(getTab()), editor_open: true, message: String(message || "").slice(0, 200), ms_since_mount: Date.now() - now });
  };
  const onError = (e) => {
    const msg = e && e.message;
    if (!msg || /ResizeObserver loop/i.test(msg)) return;
    report("js_error", msg);
  };
  const onRejection = (e) => {
    const r = e && e.reason;
    report("unhandled_rejection", (r && r.message) || (typeof r === "string" ? r : "rejected"));
  };
  window.addEventListener("pagehide", onPageHide);
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return {
    stop() {
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    },
  };
}
