// KIRO push: "Журнал" tab: deliveries (filters, gift status, plain-language reasons, JSON download) and the plugin journal.
import {
  esc, num, fmtDate, DELIVERY_STATUS, GIFT_STATUS, GIFT_KIND_RU, LEVEL_RU, LOG_EVENTS, reasonText,
} from "./logic.js";
import { api, badge, confirmBox, toast, fail, busy, downloadFile, skeleton, $, $$ } from "./ui.js";
import { debounce } from "./logic.js";

const maskCode = (c) => (c ? `${String(c).slice(0, 4)}${"•".repeat(Math.max(2, String(c).length - 4))}` : "");

export function mountJournal(host, ctx) {
  const st = { tab: "deliveries", page: 1, per: 50, event_id: "", status: "", period: "", user: "", level: "", limit: 200, events: [], disposed: false };

  host.innerHTML = `<div class="kpu-stack"><div class="kpu-tabs" role="tablist"><button class="kpu-tab on" role="tab" data-jt="deliveries">Доставки</button><button class="kpu-tab" role="tab" data-jt="log">События плагина</button></div><div data-body></div></div>`;
  $$(host, "[data-jt]").forEach((b) => b.addEventListener("click", () => {
    st.tab = b.dataset.jt;
    $$(host, "[data-jt]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", String(x === b)); });
    show();
  }));
  const body = () => $(host, "[data-body]");

  function show() {
    if (st.tab === "deliveries") showDeliveries(); else showLog();
  }

  // ------------------------------------------------------------------------- deliveries
  function query(extra = {}) {
    const p = new URLSearchParams(extra);
    for (const k of ["event_id", "status", "period", "user"]) if (st[k]) p.set(k, st[k]);
    return p;
  }

  async function showDeliveries() {
    if (!st.events.length) { try { st.events = (await api("/events")).events; } catch { st.events = []; } }
    body().innerHTML = `<div class="kpu-card"><h3>Доставки <span class="kpu-row"><label class="chk kpu-muted"><input type="checkbox" data-unmask> без маскирования в файле</label><button class="kpu-btn kpu-sm" data-dl>Скачать JSON</button></span></h3>
      <div class="kpu-form" style="margin-bottom:10px">
        <label class="f">Событие<select data-f="event_id"><option value="">Все</option>${st.events.map((e) => `<option value="${e.id}" ${String(st.event_id) === String(e.id) ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></label>
        <label class="f">Статус<select data-f="status"><option value="">Любой</option>${Object.entries(DELIVERY_STATUS).map(([v, [l]]) => `<option value="${v}" ${st.status === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="f">Период<input type="text" data-f="period" placeholder="2026, 2026-11-25 или m:…" value="${esc(st.period)}"><span class="kpu-muted">Год для ежегодных событий, дата для разовых</span></label>
        <label class="f">Пользователь<input type="search" data-f="user" placeholder="ID, Telegram ID или @username" value="${esc(st.user)}"></label></div>
      <div data-table class="kpu-scroll">${skeleton()}</div><div data-pager></div></div>`;
    const reload = () => { st.page = 1; loadDeliveries(); };
    const dr = debounce(reload, 400);
    $$(body(), "[data-f]").forEach((el) => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => { st[el.dataset.f] = el.value.trim(); (el.tagName === "SELECT" ? reload : dr)(); }));
    $(body(), "[data-dl]").onclick = async (e) => {
      const unmask = $(body(), "[data-unmask]").checked;
      if (unmask && !(await confirmBox("В файле будут настоящие ID пользователей, имена и промокоды. Храните его осторожно. Продолжить?", "Скачать без маскирования"))) return;
      busy(e.currentTarget, async () => {
        try { await downloadFile(`/deliveries/download?${query({ limit: "2000", mask: unmask ? "0" : "1" })}`, "kiro-push-deliveries.json"); toast("Файл сохранён"); } catch (err) { fail(err); }
      });
    };
    loadDeliveries();
  }

  async function loadDeliveries() {
    const box = $(body(), "[data-table]");
    if (!box) return;
    box.classList.add("kpu-loading");
    try {
      const data = await api(`/deliveries?${query({ page: String(st.page), per_page: String(st.per) })}`);
      if (st.disposed || st.tab !== "deliveries") return;
      box.classList.remove("kpu-loading");
      if (!data.items.length) {
        box.innerHTML = `<p class="kpu-muted">Доставок пока нет. Они появятся, когда сработает событие или вы запустите ручную рассылку.</p>`;
        $(body(), "[data-pager]").innerHTML = "";
        return;
      }
      box.innerHTML = `<table class="kpu-table"><tr><th>#</th><th>Событие</th><th>Пользователь</th><th>Период</th><th>Статус</th><th>Подарок</th><th>Когда</th></tr>${data.items.map(deliveryRow).join("")}</table>`;
      $$(box, "[data-reveal]").forEach((b) => b.addEventListener("click", () => { b.previousElementSibling.textContent = b.dataset.reveal; b.remove(); }));
      const pages = Math.max(1, Math.ceil(data.total / data.per_page));
      $(body(), "[data-pager]").innerHTML = pager(data, pages);
      $(body(), "[data-prev]").onclick = () => { st.page -= 1; loadDeliveries(); };
      $(body(), "[data-next]").onclick = () => { st.page += 1; loadDeliveries(); };
    } catch (err) {
      box.classList.remove("kpu-loading");
      box.innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`;
    }
  }

  const pager = (data, pages) => `<div class="kpu-pager"><span class="kpu-muted">Всего ${num(data.total)} · страница ${data.page} из ${pages}</span><button class="kpu-btn kpu-sm" data-prev ${data.page <= 1 ? "disabled" : ""}>← Назад</button><button class="kpu-btn kpu-sm" data-next ${data.page >= pages ? "disabled" : ""}>Вперёд →</button></div>`;

  function deliveryRow(d) {
    const [sl, sc] = DELIVERY_STATUS[d.status] || [d.status, "#888"];
    const reason = reasonText(d.reason);
    const gs = d.gift_status ? GIFT_STATUS[d.gift_status] : null;
    const gnote = reasonText(d.gift_note);
    const gift = d.gift_kind && d.gift_kind !== "none"
      ? `<div>${esc(GIFT_KIND_RU[d.gift_kind] || d.gift_kind)} ${gs ? badge(gs[0], gs[1]) : ""}</div>${gnote ? `<div class="kpu-muted">${esc(gnote)}</div>` : ""}${d.promo_code ? `<div class="kpu-code">${esc(maskCode(d.promo_code))}<button type="button" class="kpu-btn kpu-sm" style="margin-left:6px" data-reveal="${esc(d.promo_code)}">показать</button></div>` : ""}`
      : '<span class="kpu-muted">—</span>';
    return `<tr><td class="kpu-muted">${esc(d.id)}</td><td>${esc(d.event_name || `#${d.event_id}`)}</td>
      <td><b>${esc(d.first_name || (d.username ? `@${d.username}` : `ID ${d.user_id}`))}</b><div class="kpu-muted">${d.username ? `@${esc(d.username)} · ` : ""}ID ${esc(d.user_id)}${d.language ? ` · ${esc(d.language)}` : ""}</div></td>
      <td class="kpu-muted">${esc(String(d.period_key).replace(/^m:/, "запуск "))}</td>
      <td>${badge(sl, sc)}${reason ? `<div class="kpu-muted" style="max-width:260px">${esc(reason)}</div>` : ""}${d.attempts > 1 ? `<div class="kpu-muted">попыток: ${d.attempts}</div>` : ""}</td>
      <td>${gift}</td><td class="kpu-muted">${esc(fmtDate(d.sent_at || d.scheduled_for || d.created_at))}</td></tr>`;
  }

  // ------------------------------------------------------------------------- plugin journal
  async function showLog() {
    body().innerHTML = `<div class="kpu-card"><h3>События плагина <span class="kpu-row"><button class="kpu-btn kpu-sm" data-dl>Скачать</button><button class="kpu-btn kpu-sm kpu-danger" data-clear>Очистить</button></span></h3>
      <div class="kpu-row" style="margin-bottom:10px"><label class="f" style="width:170px">Уровень<select data-level><option value="">Все</option><option value="info" ${st.level === "info" ? "selected" : ""}>info и выше</option><option value="warn" ${st.level === "warn" ? "selected" : ""}>warn и выше</option><option value="error" ${st.level === "error" ? "selected" : ""}>Только error</option></select></label>
        <label class="f" style="width:130px">Записей<select data-limit>${[100, 200, 500, 1000].map((n) => `<option ${st.limit === n ? "selected" : ""}>${n}</option>`).join("")}</select></label><span class="kpu-muted" data-meta></span></div>
      <div data-table class="kpu-scroll">${skeleton()}</div></div>`;
    $(body(), "[data-level]").onchange = (e) => { st.level = e.target.value; loadLog(); };
    $(body(), "[data-limit]").onchange = (e) => { st.limit = Number(e.target.value); loadLog(); };
    $(body(), "[data-dl]").onclick = (e) => busy(e.currentTarget, async () => {
      try { await downloadFile("/logs/download?mask=1", "kiro-push-log.txt"); toast("Файл сохранён. ID и промокоды в нём замаскированы"); } catch (err) { fail(err); }
    });
    $(body(), "[data-clear]").onclick = async () => {
      if (!(await confirmBox("Очистить журнал событий плагина? Записи удалятся безвозвратно. История доставок не затрагивается.", "Очистить", { danger: true }))) return;
      try { const r = await api("/logs/clear", "POST", {}); toast(`Удалено записей: ${r.removed}`); loadLog(); } catch (err) { fail(err); }
    };
    loadLog();
  }

  async function loadLog() {
    const box = $(body(), "[data-table]");
    if (!box) return;
    box.classList.add("kpu-loading");
    try {
      const p = new URLSearchParams({ limit: String(st.limit) });
      if (st.level) p.set("level", st.level);
      const data = await api(`/logs?${p}`);
      if (st.disposed || st.tab !== "log") return;
      box.classList.remove("kpu-loading");
      const c = data.counts || {};
      $(body(), "[data-meta]").textContent = `В журнале: ${Object.entries(c).map(([k, v]) => `${k} ${v}`).join(", ") || "пусто"} · подробный журнал ${data.debug ? "включён" : "выключен"}`;
      if (!data.rows.length) { box.innerHTML = `<p class="kpu-muted">Записей нет.</p>`; return; }
      box.innerHTML = `<table class="kpu-table"><tr><th>Время</th><th>Уровень</th><th>Событие</th><th>Пользователь</th><th>Подробности</th></tr>${data.rows.map((r) => {
        const [ll, lc] = LEVEL_RU[r.level] || [r.level, "#888"];
        const detail = r.detail && Object.keys(r.detail).length ? JSON.stringify(r.detail, null, 0) : "";
        return `<tr><td class="kpu-muted" style="white-space:nowrap">${esc(fmtDate(r.at))}</td><td>${badge(ll, lc)}</td>
          <td><b>${esc(LOG_EVENTS[r.event] || r.event)}</b><div class="kpu-muted kpu-code">${esc(r.event)}${r.status ? ` · ${esc(r.status)}` : ""}${r.path ? ` · ${esc(r.path)}` : ""}</div></td>
          <td class="kpu-muted">${r.user_id ? esc(r.user_id) : "—"}</td>
          <td class="kpu-code" style="max-width:360px;overflow-wrap:anywhere">${detail ? esc(detail.length > 220 ? `${detail.slice(0, 220)}…` : detail) : ""}</td></tr>`;
      }).join("")}</table>`;
    } catch (err) {
      box.classList.remove("kpu-loading");
      box.innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`;
    }
  }

  show();
  return { destroy() { st.disposed = true; } };
}
