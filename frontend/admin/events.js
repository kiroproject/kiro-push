// KIRO push: "События" tab: list, presets, enable toggle, duplicate / delete / test / run. Opens the editor.
import {
  esc, num, fmtDate, clone, KIND_RU, KIND_COLOR, RUNNABLE_KINDS, scheduleSummary, audienceSummary, giftSummary, PRESETS, blankDraft, fromEvent, toPayload,
} from "./logic.js";
import { api, badge, confirmBox, toast, fail, busy, kpuSwitch, skeleton, $, $$ } from "./ui.js";
import { mountEditor } from "./editor.js";
import { testEvent, runEvent } from "./actions.js";

export function mountEvents(host, ctx) {
  let editor = null;
  let disposed = false;

  const stop = () => { editor?.destroy(); editor = null; };

  async function list() {
    stop();
    ctx.setGuard(null);
    host.innerHTML = skeleton();
    try {
      const [{ events }, deliveries] = await Promise.all([api("/events"), api("/deliveries?per_page=200").catch(() => ({ items: [] }))]);
      if (disposed) return;
      const lastSent = {};
      for (const d of deliveries.items || []) if (d.sent_at && !lastSent[d.event_id]) lastSent[d.event_id] = d.sent_at;
      paintList(events, lastSent);
    } catch (err) {
      host.innerHTML = `<div class="kpu-msg">Не удалось загрузить события: ${esc(err.message)} <button class="kpu-btn kpu-sm" data-retry>Повторить</button></div>`;
      $(host, "[data-retry]").onclick = list;
    }
  }

  function paintList(events, lastSent) {
    const currency = ctx.currency;
    host.innerHTML = `<div class="kpu-stack">
      <div class="kpu-card"><h3>Создать событие <span class="kpu-muted">заготовка только заполняет редактор, ничего не отправляется</span></h3>
        <div class="kpu-radio" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">${PRESETS.map((p) => `<label style="cursor:pointer" data-preset="${p.id}" role="button" tabindex="0"><span><b>${esc(p.title)}</b></span><small>${esc(p.note)}</small></label>`).join("")}
          <label style="cursor:pointer" data-preset="blank" role="button" tabindex="0"><span><b>Пустое событие</b></span><small>Настроить всё самостоятельно</small></label></div></div>
      ${events.length ? events.map((ev) => card(ev, lastSent[ev.id], currency)).join("") : `<div class="kpu-card"><p class="kpu-muted">Событий пока нет. Выберите заготовку выше: «С днём рождения» — самый частый случай.</p></div>`}</div>`;
    $$(host, "[data-preset]").forEach((el) => {
      const go = () => {
        const p = PRESETS.find((x) => x.id === el.dataset.preset);
        openEditor(p ? p.build() : blankDraft("birthday"));
      };
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
    for (const ev of events) {
      const el = $(host, `[data-ev="${ev.id}"]`);
      $(el, "[data-edit]").onclick = () => openEditor(fromEvent(ev));
      $(el, "[data-dup]").onclick = () => { const d = fromEvent(ev); d.id = null; d.enabled = false; d.name = `${ev.name} (копия)`; openEditor(d); };
      $(el, "[data-test]").onclick = (e) => busy(e.currentTarget, () => testEvent(ev.id, {}));
      $(el, "[data-run]")?.addEventListener("click", (e) => busy(e.currentTarget, async () => { if (await runEvent(ev, currency)) list(); }));
      $(el, "[data-del]").onclick = async () => {
        if (!(await confirmBox(`Удалить событие «${esc(ev.name)}»? Оно перестанет отправляться. История доставок останется в журнале.`, "Удалить", { danger: true }))) return;
        try { await api(`/events/${ev.id}`, "DELETE"); toast("Событие удалено"); ctx.refreshOverview(); list(); } catch (err) { fail(err); }
      };
      $(el, "[data-toggle]").onchange = async (e) => {
        const box = e.target;
        const want = box.checked;
        box.disabled = true;
        try {
          const d = fromEvent(ev);
          d.enabled = want;
          await api(`/events/${ev.id}`, "PUT", toPayload(d));
          toast(want ? "Событие включено" : "Событие выключено");
          ctx.refreshOverview();
          list();
        } catch (err) {
          box.checked = !want;
          box.disabled = false;
          fail(err);
        }
      };
    }
  }

  function card(ev, last, currency) {
    const s = ev.stats || {};
    const g = s.gifts || {};
    const counters = [
      `отправлено <b>${num(s.sent)}</b>`,
      s.failed ? `<span style="color:#e5484d">ошибок ${num(s.failed)}</span>` : "",
      s.skipped ? `пропущено ${num(s.skipped)}` : "",
      s.blocked ? `заблокировали бота ${num(s.blocked)}` : "",
      s.pending ? `в очереди ${num(s.pending)}` : "",
      g.granted ? `подарков выдано ${num(g.granted)}` : "",
    ].filter(Boolean);
    const runnable = RUNNABLE_KINDS.includes(ev.kind);
    return `<div class="kpu-card" data-ev="${ev.id}">
      <div class="kpu-row"><b class="kpu-grow" style="font-size:15px;min-width:0;overflow-wrap:anywhere">${esc(ev.name)}</b>${badge(KIND_RU[ev.kind] || ev.kind_label, KIND_COLOR[ev.kind] || "#888")}
        ${kpuSwitch('data-toggle', ev.enabled, ev.enabled ? "Включено" : "Выключено")}</div>
      <dl class="kpu-kv" style="margin:10px 0">
        <dt>Расписание</dt><dd>${esc(scheduleSummary(ev))}</dd>
        <dt>Аудитория</dt><dd>${esc(audienceSummary(ev.audience))}</dd>
        <dt>Подарок</dt><dd>${esc(giftSummary(ev.gift, currency))}</dd>
        <dt>Итоги</dt><dd>${counters.length ? counters.join(" · ") : '<span class="kpu-muted">пока ничего не отправлялось</span>'}</dd>
        <dt>Последняя отправка</dt><dd>${esc(last ? fmtDate(last) : "—")}</dd></dl>
      <div class="kpu-row"><button class="kpu-btn kpu-sm kpu-primary" data-edit>Изменить</button><button class="kpu-btn kpu-sm" data-dup>Дублировать</button>
        <button class="kpu-btn kpu-sm" data-test>Тест себе</button>${runnable ? `<button class="kpu-btn kpu-sm" data-run>Запустить…</button>` : ""}
        <span class="kpu-grow"></span><button class="kpu-btn kpu-sm kpu-danger" data-del>Удалить</button></div></div>`;
  }

  function openEditor(draft) {
    stop();
    host.innerHTML = "";
    const box = document.createElement("div");
    box.className = "kpu-stack";
    host.appendChild(box);
    editor = mountEditor(box, ctx, { draft, onClose: async () => { if (editor.dirty() && !(await confirmBox("Есть несохранённые изменения. Выйти без сохранения?", "Выйти без сохранения", { danger: true }))) return; ctx.refreshOverview(); list(); } });
    ctx.setGuard(() => editor && editor.dirty());
    host.scrollIntoView?.({ block: "start" });
  }

  list();
  return { destroy() { disposed = true; stop(); ctx.setGuard(null); } };
}
