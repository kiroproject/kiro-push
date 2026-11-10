// KIRO push: admin application (frontend host API v1).
// Tabs: Обзор (status, stats, settings), События (list + editor), Дни рождения (scan, table, import), Журнал (deliveries, plugin journal).
// Modules: logic.js (pure), ui.js (styles, api, dialogs), pickers.js (emoji, media), editor.js, events.js, overview.js, birthdates.js, journal.js.
import { STYLE, api, confirmBox, fail, h, $, $$ } from "./ui.js";
import { esc } from "./logic.js";
import { mountOverview } from "./overview.js";
import { mountEvents } from "./events.js";
import { mountBirthdates } from "./birthdates.js";
import { mountJournal } from "./journal.js";

const TABS = [["overview", "Обзор"], ["events", "События"], ["birthdates", "Дни рождения"], ["journal", "Журнал"]];
const MOUNTS = { overview: mountOverview, events: mountEvents, birthdates: mountBirthdates, journal: mountJournal };
const remembered = { tab: "overview" };

function mountApp(target) {
  const root = document.createElement("div");
  root.className = "kpu";
  root.innerHTML = `<style>${STYLE}</style>
    <div class="kpu-row"><h2 class="kpu-grow">Push-поздравления</h2><div class="kpu-tabs" role="tablist" aria-label="Разделы плагина">${TABS.map(([id, t]) => `<button type="button" class="kpu-tab" role="tab" data-tab="${id}">${t}</button>`).join("")}</div></div>
    <div data-view></div>`;
  target.replaceChildren(root);

  const app = { root, disposed: false, tab: remembered.tab, view: null, guard: null, overview: null, ctx: null };

  // Shared context for tabs. `caps`, `currency`, ... come from GET /overview (loaded once, refreshed by the Overview tab).
  const ctx = {
    get caps() { return app.overview?.capabilities; },
    get currency() { return app.overview?.currency || "RUB"; },
    get wheelInstalled() { return !!app.overview?.wheel_installed; },
    get timezone() { return app.overview?.config?.timezone; },
    get defaultLanguage() { return app.overview?.config?.default_language; },
    get config() { return app.overview?.config; },
    setOverview(ov) { app.overview = ov; app.stale = false; },
    patchConfig(cfg) { if (app.overview) app.overview.config = cfg; },
    refreshOverview() { app.stale = true; },
    setGuard(fn) { app.guard = fn; },
  };
  app.ctx = ctx;

  async function ensureOverview() {
    if (!app.overview || app.stale) { app.overview = await api("/overview"); app.stale = false; }
    return app.overview;
  }

  async function show(tab) {
    app.view?.destroy?.();
    app.view = null;
    app.guard = null;
    app.tab = tab;
    remembered.tab = tab;
    $$(root, "[data-tab]").forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
    const view = $(root, "[data-view]");
    view.innerHTML = `<div class="kpu-muted" role="status">Загрузка…</div>`;
    try {
      if (tab !== "overview") await ensureOverview();
      if (app.disposed || app.tab !== tab) return;
      view.replaceChildren();
      const box = h(`<div></div>`);
      view.appendChild(box);
      app.view = MOUNTS[tab](box, ctx);
    } catch (err) {
      view.innerHTML = `<div class="kpu-msg">Не удалось открыть раздел: ${esc(err.message)} <button type="button" class="kpu-btn kpu-sm" data-retry>Повторить</button></div>`;
      $(view, "[data-retry]").onclick = () => show(tab);
    }
  }

  $$(root, "[data-tab]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.tab === app.tab && !app.guard) return;
    if (app.guard && app.guard()) {
      const ok = await confirmBox("В редакторе есть несохранённые изменения. Перейти в другой раздел без сохранения?", "Перейти без сохранения", { danger: true });
      if (!ok) return;
    }
    show(b.dataset.tab);
  }));

  show(app.tab).catch(fail);
  return app;
}

export function mountView(view, target) {
  return mountApp(target);
}

export function updateView() {
  // Admin shell props must not re-mount the editor and wipe unsaved input.
}

export function unmountView(instance) {
  if (!instance) return;
  instance.disposed = true;
  instance.view?.destroy?.();
  instance.root.remove();
}
