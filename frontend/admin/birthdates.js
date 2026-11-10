// KIRO push: "Дни рождения" tab: coverage, Telegram scan panel, birthdate table (support flow), import.
import {
  esc, num, pct, fmtDate, BD_STATUS, SOURCE_RU, BACKFILL_STATUS, MONTHS_NOM_RU, validateBirthdate, parseImport, errorText, reasonText, debounce,
} from "./logic.js";
import { api, badge, modal, confirmBox, toast, fail, busy, skeleton, $, $$ } from "./ui.js";

const POLL_MS = 3000;

export function mountBirthdates(host, ctx) {
  const st = { q: "", status: "set", source: "", month: "", page: 1, per: 50, poll: 0, disposed: false, bf: null, rows: null };

  host.innerHTML = `<div class="kpu-stack">
    <div class="kpu-banner warn"><b>Раздел поддержки.</b> Здесь можно исправить или удалить дату рождения конкретного пользователя. Каждое изменение записывается в аудит. Дата, заданная администратором, заблокирована: Telegram и сам пользователь её не перезапишут. После удаления пользователь сможет ввести дату заново в кабинете.</div>
    <div data-cover></div><div data-scan></div>
    <div class="kpu-card"><h3>Даты рождения пользователей</h3><div data-filters></div><div data-table class="kpu-scroll">${skeleton()}</div><div data-pager></div></div>
    <div class="kpu-card" data-import></div></div>`;

  // ------------------------------------------------------------------------- coverage
  async function loadCover() {
    try {
      const { birthdates: b } = await api("/stats");
      $(host, "[data-cover]").innerHTML = `<div class="kpu-grid">
        <div class="kpu-stat"><small>Собрано дат</small><b>${num(b.collected)}</b><small>покрытие ${b.coverage == null ? "—" : pct(b.coverage)}</small></div>
        <div class="kpu-stat"><small>Из Telegram</small><b>${num(b.by_source.telegram)}</b></div>
        <div class="kpu-stat"><small>Ввели сами</small><b>${num(b.by_source.user)}</b></div>
        <div class="kpu-stat"><small>Админом</small><b>${num(b.by_source.admin)}</b></div>
        <div class="kpu-stat"><small>Скрыты</small><b>${num(b.hidden)}</b></div>
        <div class="kpu-stat"><small>Не проверялись</small><b>${num(b.unknown)}</b></div></div>`;
    } catch (err) { $(host, "[data-cover]").innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`; }
  }

  // ------------------------------------------------------------------------- scan panel
  function verdict(bf) {
    if (bf.status === "idle" || !bf.scanned) return "";
    const hit = bf.hit_rate ?? 0;
    const what = bf.dry_run ? "Пробный скан ничего не сохранил." : "Найденные даты сохранены.";
    if (hit < 0.02) return `<div class="kpu-banner warn"><b>Telegram почти не отдаёт даты вашему боту (${pct(hit)}).</b> ${what} Скорее всего, пользователи не открыли дату рождения для бота, либо Telegram не показывает её боту. Тогда единственный способ собрать даты — чтобы пользователи ввели их сами в кабинете (карточка «День рождения» в настройках Mini App). Включите «Разрешить вводить дату в кабинете» на вкладке «Обзор».</div>`;
    if (hit < 0.25) return `<div class="kpu-banner info"><b>Найдено ${pct(hit)} дат.</b> ${what} Остальные пользователи скрыли дату: их можно проверить позже (плагин повторяет проверку раз в несколько дней) или попросить ввести её самим.</div>`;
    return `<div class="kpu-banner ok"><b>Хороший результат: найдено ${pct(hit)} дат.</b> ${what}</div>`;
  }

  function paintScan() {
    const bf = st.bf;
    const el = $(host, "[data-scan]");
    if (!bf) { el.innerHTML = `<div class="kpu-card">${skeleton()}</div>`; return; }
    const running = bf.status === "running";
    const progress = Math.round((bf.progress || 0) * 100);
    el.innerHTML = `<div class="kpu-card"><h3>Сканирование Telegram <span class="kpu-badge" style="color:${running ? "#22c55e" : "#8b93a3"}">${esc(BACKFILL_STATUS[bf.status] || bf.status)}${bf.dry_run && bf.status !== "idle" ? ", пробное" : ""}</span></h3>
      <p class="kpu-muted" style="margin-bottom:10px">Плагин спрашивает у Telegram дату рождения каждого пользователя (метод getChat). Telegram отдаёт дату только если человек открыл её для бота. «Пробный скан» только считает долю найденных дат и <b>ничего не сохраняет</b>. Скан идёт в фоне порциями раз в минуту, поэтому прогресс обновляется рывками.</p>
      ${bf.status !== "idle" ? `<div class="kpu-prog big" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><i style="width:${progress}%"></i></div>
        <div class="kpu-grid" style="margin-top:10px">
          <div class="kpu-stat"><small>Просмотрено</small><b>${num(bf.scanned)}</b><small>из ${num(bf.total)} (${progress}%)</small></div>
          <div class="kpu-stat"><small>Найдено дат</small><b>${num(bf.found)}</b><small>доля: ${bf.hit_rate == null ? "—" : pct(bf.hit_rate)}</small></div>
          <div class="kpu-stat"><small>Скрыто</small><b>${num(bf.absent)}</b></div>
          <div class="kpu-stat"><small>Заблокировали бота</small><b>${num(bf.blocked)}</b></div>
          <div class="kpu-stat"><small>Ошибки</small><b>${num(bf.errors)}</b><small>лимит Telegram: ${num(bf.rate_limited)}</small></div></div>
        <div class="kpu-muted" style="margin-top:6px">${bf.started_at ? `Запущено ${esc(fmtDate(bf.started_at))}` : ""}${bf.finished_at ? ` · завершено ${esc(fmtDate(bf.finished_at))}` : ""} · скорость ${num(bf.rate)} запросов/с${bf.paused_until ? ` · пауза Telegram до ${esc(fmtDate(bf.paused_until))}` : ""}</div>
        ${bf.last_error ? `<div class="kpu-err">Последняя ошибка: ${esc(bf.last_error)}</div>` : ""}` : ""}
      <div style="margin-top:10px">${verdict(bf)}</div>
      <div class="kpu-row" style="margin-top:12px">
        <label class="f" style="width:150px">Скорость, запросов/с<input type="number" min="1" max="28" step="1" data-rate value="${esc(bf.rate || ctx.config?.scan_rate_per_second || 20)}" ${running ? "disabled" : ""}></label>
        ${running ? `<button class="kpu-btn kpu-danger" data-stop style="align-self:flex-end">Остановить</button>` : `
          <button class="kpu-btn" data-start-dry style="align-self:flex-end">Пробный скан (ничего не сохраняет)</button>
          <button class="kpu-btn kpu-primary" data-start style="align-self:flex-end">Запустить скан</button>
          ${bf.can_resume ? `<button class="kpu-btn" data-resume style="align-self:flex-end">Продолжить с места остановки</button>` : ""}`}
      </div><div data-scan-err></div></div>`;
    const rate = () => Number($(el, "[data-rate]").value) || 20;
    const start = (extra, button) => busy(button, async () => {
      try {
        const r = await api("/backfill/start", "POST", { rate: rate(), ...extra });
        st.bf = r.backfill; paintScan(); schedulePoll(); toast(extra.dry_run ? "Пробный скан запущен" : extra.resume ? "Сканирование продолжено" : "Сканирование запущено");
      } catch (err) { fail(err); if (err.code === "already_running") refreshScan(); }
    });
    $(el, "[data-start-dry]")?.addEventListener("click", (e) => start({ dry_run: true }, e.currentTarget));
    $(el, "[data-start]")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!(await confirmBox(`Запустить настоящее сканирование? Найденные даты будут сохранены. Скан идёт в фоне порциями при скорости ${rate()} запросов в секунду, остановить его можно в любой момент.`, "Запустить"))) return;
      start({ dry_run: false }, btn);
    });
    $(el, "[data-resume]")?.addEventListener("click", (e) => start({ resume: true, dry_run: !!bf.dry_run }, e.currentTarget));
    $(el, "[data-stop]")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
      try { const r = await api("/backfill/stop", "POST", {}); st.bf = r.backfill; paintScan(); loadRows(); toast("Сканирование остановлено"); } catch (err) { fail(err); }
    }));
  }

  async function refreshScan() {
    try {
      const { backfill } = await api("/backfill/status");
      const was = st.bf?.status;
      st.bf = backfill;
      if (!$(host, "[data-scan]")) return;
      paintScan();
      if (was === "running" && backfill.status !== "running") { loadRows(); loadCover(); }
    } catch { /* keep the last picture; the next tick retries */ }
  }

  function schedulePoll() {
    clearInterval(st.poll);
    st.poll = 0;
    if (st.disposed || st.bf?.status !== "running") return;
    st.poll = setInterval(async () => {
      await refreshScan();
      if (st.bf?.status !== "running") { clearInterval(st.poll); st.poll = 0; }
    }, POLL_MS);
  }

  // ------------------------------------------------------------------------- table
  function paintFilters() {
    $(host, "[data-filters]").innerHTML = `<div class="kpu-form" style="margin-bottom:10px">
      <label class="f">Поиск<input type="search" data-q placeholder="ID, @username, Telegram ID или имя" value="${esc(st.q)}"></label>
      <label class="f">Статус<select data-status>${[["set", "С датой"], ["all", "Все"], ["unknown", "Не проверялись"], ["absent", "Скрыта"], ["blocked", "Бот заблокирован"], ["error", "Ошибка проверки"], ["cleared", "Удалена админом"]].map(([v, l]) => `<option value="${v}" ${st.status === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="f">Источник<select data-source><option value="">Любой</option>${Object.entries(SOURCE_RU).map(([v, [l]]) => `<option value="${v}" ${st.source === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="f">Месяц<select data-month><option value="">Любой</option>${MONTHS_NOM_RU.map((m, i) => `<option value="${i + 1}" ${String(st.month) === String(i + 1) ? "selected" : ""}>${m}</option>`).join("")}</select></label></div>`;
    const reload = () => { st.page = 1; loadRows(); };
    const dq = debounce(reload, 400);
    $(host, "[data-q]").addEventListener("input", (e) => { st.q = e.target.value.trim(); dq(); });
    $(host, "[data-status]").onchange = (e) => { st.status = e.target.value; reload(); };
    $(host, "[data-source]").onchange = (e) => { st.source = e.target.value; reload(); };
    $(host, "[data-month]").onchange = (e) => { st.month = e.target.value; reload(); };
  }

  async function loadRows() {
    const box = $(host, "[data-table]");
    if (!box) return;
    box.classList.add("kpu-loading");
    const params = new URLSearchParams({ page: String(st.page), per_page: String(st.per) });
    if (st.q) params.set("q", st.q);
    if (st.status && !st.q) params.set("status", st.status);
    if (st.q && st.status !== "set") params.set("status", st.status);
    if (st.source) params.set("source", st.source);
    if (st.month) params.set("month", st.month);
    try {
      const data = await api(`/birthdates?${params}`);
      if (st.disposed) return;
      box.classList.remove("kpu-loading");
      st.rows = data;
      paintRows(data);
    } catch (err) {
      box.classList.remove("kpu-loading");
      box.innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`;
    }
  }

  function userCell(r) {
    return `<b>${esc(r.first_name || (r.username ? `@${r.username}` : `ID ${r.user_id}`))}</b><div class="kpu-muted">${r.username ? `@${esc(r.username)} · ` : ""}ID ${esc(r.user_id)} · TG ${esc(r.telegram_id)}</div>`;
  }

  function paintRows(data) {
    const box = $(host, "[data-table]");
    const pager = $(host, "[data-pager]");
    if (!data.items.length) {
      box.innerHTML = `<p class="kpu-muted">Никого не найдено. ${st.status === "set" && !st.q ? "Дат пока нет: запустите сканирование выше или попросите пользователей ввести дату в кабинете." : "Измените фильтры."}</p>`;
      pager.innerHTML = "";
      return;
    }
    box.innerHTML = `<table class="kpu-table"><tr><th>Пользователь</th><th>Дата</th><th>Источник</th><th>Статус</th><th>Проверка</th><th></th></tr>${data.items.map((r) => {
      const [sl, sc] = SOURCE_RU[r.source] || ["—", "#888"];
      const [bl, bc] = BD_STATUS[r.status] || [r.status, "#888"];
      const hasDate = r.day != null;
      return `<tr data-u="${esc(r.user_id)}"><td>${userCell(r)}</td>
        <td>${hasDate ? `<b>${esc(r.date)}</b>${r.locked ? ' <span class="kpu-lock" title="Заблокирована: Telegram и пользователь её не изменят" aria-label="Заблокирована">🔒</span>' : ""}${r.year ? "" : ' <span class="kpu-muted">без года</span>'}` : '<span class="kpu-muted">—</span>'}</td>
        <td>${r.source ? badge(sl, sc) : '<span class="kpu-muted">—</span>'}</td><td>${badge(bl, bc)}</td>
        <td class="kpu-muted">${r.attempts ? `попыток: ${num(r.attempts)}` : ""}${r.last_attempt_at ? `<br>${esc(fmtDate(r.last_attempt_at))}` : ""}</td>
        <td style="white-space:nowrap"><button class="kpu-btn kpu-sm" data-edit>${hasDate ? "Изменить" : "Задать"}</button> ${hasDate ? '<button class="kpu-btn kpu-sm kpu-danger" data-del>Удалить</button>' : ""}</td></tr>`;
    }).join("")}</table>`;
    const pages = Math.max(1, Math.ceil(data.total / data.per_page));
    pager.innerHTML = `<div class="kpu-pager"><span class="kpu-muted">Всего ${num(data.total)} · страница ${data.page} из ${pages}</span><button class="kpu-btn kpu-sm" data-prev ${data.page <= 1 ? "disabled" : ""}>← Назад</button><button class="kpu-btn kpu-sm" data-next ${data.page >= pages ? "disabled" : ""}>Вперёд →</button></div>`;
    $(pager, "[data-prev]").onclick = () => { st.page -= 1; loadRows(); };
    $(pager, "[data-next]").onclick = () => { st.page += 1; loadRows(); };
    $$(box, "tr[data-u]").forEach((tr) => {
      const r = data.items.find((x) => String(x.user_id) === tr.dataset.u);
      $(tr, "[data-edit]").onclick = () => editDialog(r);
      $(tr, "[data-del]")?.addEventListener("click", () => deleteDialog(r));
    });
  }

  function editDialog(r) {
    modal(`<h3>Дата рождения: ${esc(r.first_name || r.username || r.user_id)}</h3>
      <p class="kpu-muted" style="margin-bottom:10px">Сохранённая админом дата блокируется: Telegram и сам пользователь её больше не изменят. Год можно не указывать, тогда в сообщениях не будет возраста.</p>
      <div class="kpu-row"><label class="f" style="width:80px">День<input type="number" min="1" max="31" data-d value="${esc(r.day ?? "")}"></label>
        <label class="f">Месяц<select data-m><option value="">—</option>${MONTHS_NOM_RU.map((m, i) => `<option value="${i + 1}" ${r.month === i + 1 ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label class="f" style="width:100px">Год<input type="number" data-y min="1900" max="2100" placeholder="необяз." value="${esc(r.year ?? "")}"></label></div>
      <div data-e class="kpu-err"></div>
      <div class="kpu-row" style="justify-content:flex-end;margin-top:12px"><button class="kpu-btn" data-no>Отмена</button><button class="kpu-btn kpu-primary" data-ok>Сохранить</button></div>`, (m, close) => {
      $(m, "[data-no]").onclick = close;
      $(m, "[data-ok]").onclick = (e) => busy(e.currentTarget, async () => {
        const day = $(m, "[data-d]").value, month = $(m, "[data-m]").value, year = $(m, "[data-y]").value;
        const bad = validateBirthdate(day, month, year);
        if (bad) { $(m, "[data-e]").textContent = bad; return; }
        try {
          await api(`/birthdates/${encodeURIComponent(r.user_id)}`, "PUT", { day: Number(day), month: Number(month), ...(year ? { year: Number(year) } : {}) });
          close(); toast("Дата сохранена"); loadRows(); loadCover();
        } catch (err) { $(m, "[data-e]").textContent = err.message; }
      });
    });
  }

  async function deleteDialog(r) {
    const ok = await confirmBox(`Удалить дату рождения пользователя <b>${esc(r.first_name || r.username || r.user_id)}</b> (${esc(r.date)})?<br><span class="kpu-muted">Пользователь перестанет получать поздравления, пока не введёт дату заново в кабинете. Telegram удалённую запись автоматически не заполнит. Действие записывается в аудит.</span>`, "Удалить дату", { danger: true });
    if (!ok) return;
    try { await api(`/birthdates/${encodeURIComponent(r.user_id)}`, "DELETE"); toast("Дата удалена"); loadRows(); loadCover(); } catch (err) { fail(err); }
  }

  // ------------------------------------------------------------------------- import
  function paintImport() {
    const el = $(host, "[data-import]");
    el.innerHTML = `<h3>Импорт дат <span class="kpu-muted">для поддержки, до 2000 строк</span></h3>
      <details><summary>Как заполнить</summary><p class="kpu-muted" style="margin-top:6px">Одна строка — один пользователь: <code>пользователь;день;месяц;год</code>. Пользователь — внутренний ID, Telegram ID или @username. Год можно не указывать. Подойдёт и <code>@name;15.03.1990</code>. Также принимается JSON-массив <code>[{"user":"@name","day":15,"month":3,"year":1990}]</code>. Строки, начинающиеся с #, пропускаются.</p></details>
      <textarea data-text rows="5" style="margin-top:8px" placeholder="@ivan;15;3;1990&#10;123456789;1;12" aria-label="Данные для импорта"></textarea>
      <div class="kpu-row" style="margin-top:8px"><label class="chk"><input type="checkbox" data-over> Заменять уже сохранённые даты</label><span class="kpu-grow"></span><span class="kpu-muted" data-parsed></span><button class="kpu-btn kpu-primary" data-go disabled>Импортировать</button></div>
      <div data-res></div>`;
    const ta = $(el, "[data-text]");
    const parse = () => {
      const p = parseImport(ta.value);
      $(el, "[data-parsed]").innerHTML = ta.value.trim() ? `Строк: <b>${p.items.length}</b>${p.errors.length ? ` · <span style="color:#e5484d">с ошибкой: ${p.errors.length}</span>` : ""}` : "";
      $(el, "[data-go]").disabled = !p.items.length;
      return p;
    };
    ta.addEventListener("input", parse);
    $(el, "[data-go]").onclick = (e) => busy(e.currentTarget, async () => {
      const p = parse();
      if (p.errors.length) {
        $(el, "[data-res]").innerHTML = `<div class="kpu-banner bad" style="margin-top:8px"><b>Исправьте строки:</b><ul>${p.errors.slice(0, 10).map((x) => `<li>строка ${x.line}: ${esc(x.msg)}</li>`).join("")}</ul></div>`;
        return;
      }
      if (!(await confirmBox(`Импортировать ${p.items.length} дат${$(el, "[data-over]").checked ? ", заменяя уже сохранённые" : ""}? Импортированные даты блокируются от изменений.`, "Импортировать"))) return;
      try {
        const r = await api("/birthdates/import", "POST", { items: p.items, overwrite: $(el, "[data-over]").checked });
        const bad = (r.results || []).filter((x) => x.status !== "saved");
        $(el, "[data-res]").innerHTML = `<div class="kpu-banner ${r.errors ? "warn" : "ok"}" style="margin-top:8px"><b>Сохранено: ${num(r.saved)} · пропущено: ${num(r.skipped)} · ошибок: ${num(r.errors)}</b>${bad.length ? `<ul>${bad.slice(0, 15).map((x) => `<li>${esc(x.user)}: ${esc(x.status === "skipped" ? "уже есть дата (включите замену)" : errorText(x.error))}</li>`).join("")}</ul>` : ""}</div>`;
        loadRows(); loadCover();
      } catch (err) { fail(err); }
    });
  }

  // ------------------------------------------------------------------------- start
  paintFilters();
  paintImport();
  paintScan();
  loadCover();
  loadRows();
  api("/backfill/status").then(({ backfill }) => { st.bf = backfill; paintScan(); schedulePoll(); }).catch((err) => { $(host, "[data-scan]").innerHTML = `<div class="kpu-msg">${esc(err.message)}</div>`; });
  return { destroy() { st.disposed = true; clearInterval(st.poll); } };
}

export { reasonText };
