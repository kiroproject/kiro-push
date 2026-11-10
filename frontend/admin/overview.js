// KIRO push: "Обзор" tab: status, statistics, warnings and the plugin settings form.
import {
  esc, num, pct, CONFIG_FIELDS, ERRORS, DELIVERY_STATUS, sparkBars, plural, humanDay,
} from "./logic.js";
import { api, toast, fail, busy, kpuSwitch, skeleton, $, $$ } from "./ui.js";

export function mountOverview(host, ctx) {
  let disposed = false;

  async function load() {
    host.innerHTML = skeleton();
    try {
      const [ov, { events }] = await Promise.all([api("/overview"), api("/events")]);
      if (disposed) return;
      ctx.setOverview(ov);
      paint(ov, events);
    } catch (err) {
      host.innerHTML = `<div class="kpu-msg">Не удалось загрузить обзор: ${esc(err.message)} <button class="kpu-btn kpu-sm" data-retry>Повторить</button></div>`;
      $(host, "[data-retry]").onclick = load;
    }
  }

  function banners(ov, events) {
    const out = [];
    if (!ov.bot_available) out.push(["bad", "Бот сейчас недоступен", "Плагин не сможет ничего отправить и проверять даты рождения в Telegram, пока бот не запущен в этом процессе. Проверьте токен и запуск бота; сообщения не теряются, доставки подождут."]);
    const wheelGift = events.filter((e) => e.gift?.kind === "wheel_spins" && e.enabled);
    if (!ov.wheel_installed && wheelGift.length) out.push(["warn", "Не установлен плагин «Колесо удачи»", `Подарок «крутки колеса» выбран в событиях: ${wheelGift.map((e) => `«${esc(e.name)}»`).join(", ")}. Пока плагин не установлен, подарок не выдаётся: пользователи получат сообщение по правилу «если подарок выдать нельзя».`]);
    if (ov.config.enabled && !events.some((e) => e.enabled)) out.push(["warn", "Нет включённых событий", "Плагин включён, но ни одно событие не включено, поэтому ничего не отправляется. Создайте событие во вкладке «События»."]);
    if (!ov.config.enabled) out.push(["info", "Плагин выключен", "Задание ничего не планирует и не отправляет, а карточка «День рождения» в кабинете скрыта. Включите плагин переключателем ниже, когда события готовы."]);
    return out.map(([k, t, b]) => `<div class="kpu-banner ${k}" role="${k === "bad" ? "alert" : "status"}"><b>${esc(t)}</b><div>${b}</div></div>`).join("");
  }

  function statusCard(ov) {
    return `<div class="kpu-card"><div class="kpu-row"><b style="font-size:15px">Состояние плагина</b><span class="kpu-grow"></span>${kpuSwitch("data-enabled", ov.config.enabled, ov.config.enabled ? "Включён" : "Выключен")}</div>
      <div class="kpu-muted" style="margin-top:6px">Версия ${esc(ov.version)} · часовой пояс ${esc(ov.config.timezone)} · событий: ${num(ov.events_total)} · бот: ${ov.bot_available ? "доступен" : "недоступен"} · колесо удачи: ${ov.wheel_installed ? "установлено" : "не установлено"}</div></div>`;
  }

  function statsCards(ov) {
    const b = ov.birthdates, up = ov.upcoming, dl = ov.deliveries;
    const cov = b.coverage === null || b.coverage === undefined ? "—" : pct(b.coverage);
    const bars = sparkBars(up.days);
    const total = Object.values(dl.total).reduce((a, x) => a + x, 0);
    const bar = Object.entries(DELIVERY_STATUS).map(([k, [, color]]) => (dl.total[k] ? `<i style="width:${(dl.total[k] / total) * 100}%;background:${color}" title="${esc(DELIVERY_STATUS[k][0])}: ${dl.total[k]}"></i>` : "")).join("");
    const nextDays = up.days.filter((d) => d.count).slice(0, 3);
    return `<div class="kpu-card"><h3>Даты рождения</h3>
        <div class="kpu-grid">
          <div class="kpu-stat"><small>Собрано</small><b>${num(b.collected)}</b><small>из ${num(b.eligible_users)} пользователей</small></div>
          <div class="kpu-stat"><small>Покрытие</small><b>${esc(cov)}</b><small>с годом: ${num(b.with_year)}</small></div>
          <div class="kpu-stat"><small>Скрыты в Telegram</small><b>${num(b.hidden)}</b><small>пользователи не открыли дату</small></div>
          <div class="kpu-stat"><small>Ещё не проверены</small><b>${num(b.unknown)}</b><small>заблокировали бота: ${num(b.blocked)} · ошибок: ${num(b.errors)}</small></div>
        </div>
        <div class="kpu-muted" style="margin-top:8px">Источники: Telegram ${num(b.by_source.telegram)} · сам пользователь ${num(b.by_source.user)} · админ ${num(b.by_source.admin)}</div>
        ${b.collected === 0 && b.eligible_users > 0 ? `<div class="kpu-banner info" style="margin-top:8px">Дат пока нет. Откройте вкладку «Дни рождения» и сделайте пробный скан: он покажет, отдаёт ли Telegram даты вашему боту.</div>` : ""}
      </div>
      <div class="kpu-card"><h3>Ближайшие 30 дней <span class="kpu-muted">${num(up.total)} ${plural(up.total, "именинник", "именинника", "именинников")}</span></h3>
        <div class="kpu-spark" role="img" aria-label="Именинники по дням на ближайшие 30 дней">${bars.map((x) => `<i class="${x.count ? "" : "z"}" style="height:${x.height}%" title="${esc(x.label)}"></i>`).join("")}</div>
        <div class="kpu-muted" style="margin-top:6px">${nextDays.length ? `Ближайшие: ${nextDays.map((d) => { const dt = new Date(`${d.date}T00:00:00`); return `${humanDay(dt.getDate(), dt.getMonth() + 1)} (${d.count})`; }).join(", ")}` : "В ближайшие 30 дней именинников нет"}</div></div>
      <div class="kpu-card"><h3>Доставки</h3>
        <div class="kpu-grid">${Object.entries(DELIVERY_STATUS).map(([k, [label, color]]) => `<div class="kpu-stat"><small style="color:${color}">${esc(label)}</small><b>${num(dl.total[k])}</b></div>`).join("")}</div>
        <div class="kpu-bar" style="margin-top:10px" aria-hidden="true">${bar}</div>
        <div class="kpu-muted" style="margin-top:8px">Промокоды, выданные плагином: <b>${num(dl.promo.issued)}</b> · активировано: <b>${num(dl.promo.activated)}</b></div></div>`;
  }

  function fieldHtml(ov, [key, label, help, type, min, max]) {
    const v = ov.config[key];
    const id = `kpu-cfg-${key}`;
    if (type === "bool") return `<div style="grid-column:1/-1"><label class="chk" for="${id}"><input id="${id}" type="checkbox" data-cfg="${key}" ${v ? "checked" : ""}> ${esc(label)}</label><div class="kpu-muted" style="margin-left:26px">${esc(help)}</div><div data-cerr="${key}"></div></div>`;
    if (type === "textarea") return `<label class="f" for="${id}" style="grid-column:1/-1">${esc(label)}<textarea id="${id}" data-cfg="${key}" rows="3" maxlength="500" style="font-family:inherit">${esc(v)}</textarea><span class="kpu-muted">${esc(help)}</span><span data-cerr="${key}"></span></label>`;
    if (type === "lang") return `<label class="f" for="${id}">${esc(label)}<select id="${id}" data-cfg="${key}"><option value="ru" ${v === "ru" ? "selected" : ""}>Русский</option><option value="en" ${v === "en" ? "selected" : ""}>English</option></select><span class="kpu-muted">${esc(help)}</span><span data-cerr="${key}"></span></label>`;
    return `<label class="f" for="${id}">${esc(label)}<input id="${id}" data-cfg="${key}" ${type === "int" ? `type="number" min="${min}" max="${max}" step="1"` : 'type="text"'} value="${esc(v)}"><span class="kpu-muted">${esc(help)}</span><span data-cerr="${key}"></span></label>`;
  }

  function settingsCard(ov) {
    return `<div class="kpu-card"><h3>Настройки</h3><form data-form novalidate>${CONFIG_FIELDS.map((g) => `<fieldset style="border:0;padding:0;margin:0 0 14px"><legend style="font-weight:600;margin-bottom:8px;padding:0">${esc(g.group)}</legend><div class="kpu-form">${g.items.filter((it) => it[0] !== "enabled").map((it) => fieldHtml(ov, it)).join("")}</div></fieldset>`).join("")}
      <div class="kpu-row"><button type="submit" class="kpu-btn kpu-primary" data-save>Сохранить настройки</button><span class="kpu-muted" data-saved></span></div></form></div>`;
  }

  function collect() {
    const out = {};
    for (const g of CONFIG_FIELDS) for (const [key, , , type] of g.items) {
      const el = $(host, `[data-cfg="${key}"]`);
      if (!el) continue;
      out[key] = type === "bool" ? el.checked : type === "int" ? (el.value === "" ? null : Number(el.value)) : el.value;
    }
    return out;
  }

  async function saveConfig(patch, button) {
    $$(host, "[data-cerr]").forEach((e) => { e.innerHTML = ""; });
    try {
      const res = await busy(button, () => api("/config", "PUT", patch));
      ctx.patchConfig(res.config);
      return res.config;
    } catch (err) {
      const key = String(err.code || "").replace(/^invalid_/, "");
      const slot = $(host, `[data-cerr="${key}"]`);
      if (slot) { slot.innerHTML = `<span class="kpu-err">${esc(err.message)}</span>`; slot.scrollIntoView?.({ block: "center" }); }
      fail(err);
      return null;
    }
  }

  function paint(ov, events) {
    host.innerHTML = `<div class="kpu-stack">${banners(ov, events)}${statusCard(ov)}${statsCards(ov)}${settingsCard(ov)}</div>`;
    $(host, "[data-enabled]").addEventListener("change", async (e) => {
      const box = e.target;
      const cfg = await saveConfig({ enabled: box.checked }, null);
      if (!cfg) { box.checked = !box.checked; return; }
      toast(cfg.enabled ? "Плагин включён" : "Плагин выключен");
      load();
    });
    $(host, "[data-form]").addEventListener("submit", async (e) => {
      e.preventDefault();
      const cfg = await saveConfig(collect(), $(host, "[data-save]"));
      if (cfg) { toast("Настройки сохранены"); $(host, "[data-saved]").textContent = "Сохранено"; load(); }
    });
  }

  load();
  return { destroy() { disposed = true; } };
}

export { ERRORS };
