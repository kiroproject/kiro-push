// KIRO push: event editor (form on the left, live Telegram preview on the right).
import {
  esc, num, clone, debounce, plural, KIND_RU, SEGMENT_RU, SECTIONS_RU, MEDIA_RU, STYLE_RU, BUTTON_KIND_RU, MONTHS_RU, GIFT_KIND_RU,
  RUNNABLE_KINDS, TOOLBAR, scheduleSummary, offsetText, giftSummary, currencySymbol, majorToMinor, wrapSelection,
  insertText, emojiTag, linkOpen, visibleLength, parseTelegramHtml, renderTree, emptyTemplate, templateIsEmpty, toPayload, fromEvent, blankDraft,
  draftProblems, splitIssues, FIELD_OF_ERROR, fmtSize,
} from "./logic.js";
import { api, modal, toast, fail, confirmBox, busy, h, $, $$ } from "./ui.js";
import { testEvent, checkAudience, runEvent } from "./actions.js";
import { pickEmoji, emojiMediaUrl, uploadFile, pickStoredMedia, mediaUrl } from "./pickers.js";

const FIELD_ERR_ATTR = "data-err";
const GIFT_HELP = {
  none: "Сообщение уйдёт без подарка.",
  traffic: "Добавляется к лимиту трафика. Нужна активная подписка (пробная или платная).",
  days: "Продлевает текущую подписку. Нужна активная подписка (пробная или платная).",
  balance: "Зачисляется на баланс пользователя в магазине. Не требует подписки.",
  promo_discount: "Создаётся личный одноразовый промокод; его код попадёт в {promo_code}, размер скидки в {promo_discount}.",
  wheel_spins: "Билеты плагина «Колесо удачи». Не требуют подписки.",
};

export function mountEditor(host, ctx, { draft, onClose }) {
  const caps = ctx.caps;
  const st = {
    d: draft,
    lang: !templateIsEmpty(draft.templates.ru) || templateIsEmpty(draft.templates.en) ? "ru" : "en",
    snapshot: JSON.stringify(draft),
    saving: false,
    seq: 0,
    sample: "",
    noYear: false,
    withGift: true,
    activeTa: "text",
    issues: { ru: [], en: [], general: [] },
    warnings: [],
    fieldErrors: {},
    preview: null,
    previewState: "idle",
    batchId: null,
    dead: false,
  };
  const isDirty = () => JSON.stringify(st.d) !== st.snapshot;
  const tpl = () => st.d.templates[st.lang];
  const giftOn = () => st.d.gift.kind !== "none";
  const sym = currencySymbol(ctx.currency);

  host.innerHTML = `
    <div class="kpu-card"><div class="kpu-row">
      <button class="kpu-btn" data-back>← К событиям</button>
      <input class="kpu-title-input" type="text" data-name maxlength="120" placeholder="Название события" aria-label="Название события">
      <span data-sw></span>
      <span class="kpu-grow"></span><span class="kpu-muted" data-state></span>
    </div><div data-err="name"></div></div>
    <div class="kpu-row" data-actions></div>
    <div data-top-issues></div>
    <div class="kpu-compose">
      <div class="kpu-stack">
        <div class="kpu-card" data-sec="when"></div>
        <div class="kpu-card" data-sec="aud"></div>
        <div class="kpu-card" data-sec="gift"></div>
        <div class="kpu-card" data-sec="msg"></div>
      </div>
      <div class="kpu-side">
        <div class="kpu-card" data-sec="preview"></div>
        <div data-sec="summary"></div>
      </div>
    </div>`;

  const sec = (n) => $(host, `[data-sec="${n}"]`);
  const setState = (t) => { $(host, "[data-state]").textContent = t; };

  // ------------------------------------------------------------------------- change tracking
  const refreshPreview = debounce(() => runPreview(), 550);
  function changed({ preview = true, chrome = true } = {}) {
    if (chrome) paintChrome();
    if (preview) refreshPreview();
  }

  function paintChrome() {
    setState(isDirty() ? "Есть несохранённые изменения" : st.d.id ? "Сохранено" : "Новое событие");
    const sum = $(host, '[data-sec="summary"]');
    const d = st.d;
    const aud = d.audience.segments.length === 3 ? "все пользователи" : d.audience.segments.map((s) => SEGMENT_RU[s]).join(" + ") || "никто";
    let gift = giftSummary(toPayloadSafe().gift, ctx.currency);
    sum.innerHTML = `<div class="kpu-card"><h3>Итог</h3><dl class="kpu-kv"><dt>Когда</dt><dd>${esc(scheduleSummary({ kind: d.kind, send_time: d.send_time, params: toPayloadSafe().params }))}</dd><dt>Кому</dt><dd>${esc(aud)}${d.audience.language !== "any" ? `, язык ${esc(d.audience.language)}` : ""}</dd><dt>Подарок</dt><dd>${esc(gift)}</dd></dl></div>`;
    for (const b of $$(host, "[data-needs-saved]")) b.title = st.d.id ? "" : "Сначала сохраните событие";
  }

  function toPayloadSafe() {
    const p = toPayload(st.d);
    if (p.gift.kind === "balance" && p.gift.amount_minor === null) p.gift.amount_minor = 0;
    return p;
  }

  function showFieldErrors() {
    $$(host, `[${FIELD_ERR_ATTR}]`).forEach((el) => { el.innerHTML = ""; });
    for (const [field, msgs] of Object.entries(st.fieldErrors)) {
      const el = $(host, `[${FIELD_ERR_ATTR}="${field}"]`);
      if (el) el.innerHTML = msgs.map((m) => `<div class="kpu-err">${esc(m)}</div>`).join("");
    }
    const top = $(host, "[data-top-issues]");
    const general = st.issues.general;
    top.innerHTML = general.length ? `<div class="kpu-banner bad" role="alert"><b>Исправьте перед сохранением:</b><ul>${general.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>` : "";
  }

  function addFieldError(field, msg) {
    (st.fieldErrors[field] ||= []).push(msg);
  }

  // ------------------------------------------------------------------------- header + actions
  function paintHeader() {
    $(host, "[data-name]").value = st.d.name;
    $(host, "[data-sw]").innerHTML = `<label class="kpu-sw"><input type="checkbox" data-enabled ${st.d.enabled ? "checked" : ""}><span class="t"></span><span>${st.d.enabled ? "Включено" : "Выключено"}</span></label>`;
    $(host, "[data-enabled]").addEventListener("change", (e) => { st.d.enabled = e.target.checked; $(host, "[data-sw] span:last-child").textContent = st.d.enabled ? "Включено" : "Выключено"; changed({ preview: false }); });
    const runnable = RUNNABLE_KINDS.includes(st.d.kind);
    $(host, "[data-actions]").innerHTML = `<button class="kpu-btn kpu-primary" data-save>Сохранить</button>
      <button class="kpu-btn" data-test data-needs-saved>Отправить тест себе</button>
      ${runnable ? `<button class="kpu-btn" data-check data-needs-saved>Проверить аудиторию</button><button class="kpu-btn" data-run data-needs-saved>Запустить…</button>` : `<span class="kpu-muted">Событие запускается само по расписанию; получатели определяются в момент отправки.</span>`}`;
    $(host, "[data-save]").onclick = (e) => busy(e.currentTarget, () => save());
    $(host, "[data-test]").onclick = (e) => busy(e.currentTarget, () => sendTest());
    $(host, "[data-check]")?.addEventListener("click", (e) => busy(e.currentTarget, () => checkAudienceNow()));
    $(host, "[data-run]")?.addEventListener("click", (e) => busy(e.currentTarget, () => runNow()));
  }
  $(host, "[data-name]").addEventListener("input", (e) => { st.d.name = e.target.value; changed({ preview: false }); });
  $(host, "[data-back]").onclick = () => onClose();

  // ------------------------------------------------------------------------- section: when
  function paintWhen() {
    const d = st.d, p = d.params;
    const kinds = caps.event_kinds;
    const [mm, dd] = (p.date || "").split("-");
    const body = {
      birthday: `<label class="f">Смещение относительно дня рождения, дней<input type="number" min="-30" max="30" step="1" data-p="offset_days" value="${esc(p.offset_days)}"><span class="kpu-muted" data-off>${esc(offsetText(p.offset_days))}. Отрицательное число: раньше, положительное: позже</span></label>`,
      registration_anniversary: `<label class="f">Смещение относительно годовщины, дней<input type="number" min="-30" max="30" step="1" data-p="offset_days" value="${esc(p.offset_days)}"><span class="kpu-muted" data-off>${esc(offsetText(p.offset_days, "годовщины"))}</span></label>
        <label class="f">Не раньше чем через, лет<input type="number" min="1" max="50" step="1" data-p="min_years" value="${esc(p.min_years)}"><span class="kpu-muted">С какой годовщины начинать (1 = через год после регистрации)</span></label>`,
      fixed_date: `<div class="kpu-row" style="align-items:flex-end"><label class="f">День<input type="number" min="1" max="31" data-md="d" value="${esc(dd ? Number(dd) : "")}" style="width:80px"></label>
        <label class="f">Месяц<select data-md="m"><option value="">—</option>${MONTHS_RU.map((m, i) => `<option value="${i + 1}" ${Number(mm) === i + 1 ? "selected" : ""}>${m}</option>`).join("")}</select></label></div><span class="kpu-muted">Отправляется каждый год в этот день</span>`,
      one_time: `<label class="f">Дата отправки<input type="date" data-p="date" value="${esc(p.date)}"></label><span class="kpu-muted">Отправится один раз в выбранный день в указанное время</span>`,
      manual: `<div class="kpu-banner info">Ручное событие не запускается по расписанию: нажмите «Запустить» после сохранения. Подарок выдаётся каждому получателю.</div>`,
    }[d.kind];
    const feb = ["birthday", "registration_anniversary", "fixed_date"].includes(d.kind);
    sec("when").innerHTML = `<h3>1. Когда отправлять</h3>
      <div class="kpu-form">
        <label class="f">Вид события<select data-kind>${kinds.map((k) => `<option value="${k.id}" ${d.kind === k.id ? "selected" : ""}>${esc(KIND_RU[k.id] || k.label)}</option>`).join("")}</select></label>
        ${d.kind !== "manual" ? `<label class="f">Время отправки<input type="time" data-time value="${esc(d.send_time)}"><span class="kpu-muted">Часовой пояс плагина: ${esc(ctx.timezone || "Europe/Moscow")}</span></label>` : ""}
      </div>
      <div class="kpu-form" style="margin-top:10px">${body}</div>
      ${feb ? `<div class="kpu-sub"><div class="kpu-muted" style="margin-bottom:6px">Если дата — 29 февраля, а год не високосный, отправить:</div><div class="kpu-chips">${caps.feb29_rules.map((r) => `<button type="button" class="kpu-chip ${d.feb29 === r.id ? "on" : ""}" data-feb="${r.id}" aria-pressed="${d.feb29 === r.id}">${esc(r.label)}</button>`).join("")}</div></div>` : ""}
      <div data-err="params"></div><div data-err="send_time"></div><div data-err="kind"></div>`;
    $(sec("when"), "[data-kind]").onchange = (e) => {
      const keep = st.d;
      st.d = { ...keep, kind: e.target.value, params: { offset_days: 0, min_years: 1, date: "" } };
      if (st.d.kind === "manual") st.d.enabled = keep.enabled;
      paintAll();
      changed();
    };
    $(sec("when"), "[data-time]")?.addEventListener("input", (e) => { st.d.send_time = e.target.value; changed(); });
    $$(sec("when"), "[data-p]").forEach((el) => el.addEventListener("input", () => {
      p[el.dataset.p] = el.type === "number" ? el.value : el.value;
      const off = $(sec("when"), "[data-off]");
      if (off && el.dataset.p === "offset_days") off.textContent = `${offsetText(p.offset_days, d.kind === "birthday" ? "дня рождения" : "годовщины")}. Отрицательное число: раньше, положительное: позже`;
      changed();
    }));
    $$(sec("when"), "[data-md]").forEach((el) => el.addEventListener("input", () => {
      const day = $(sec("when"), '[data-md="d"]').value, mon = $(sec("when"), '[data-md="m"]').value;
      p.date = day && mon ? `${String(mon).padStart(2, "0")}-${String(day).padStart(2, "0")}` : "";
      changed();
    }));
    $$(sec("when"), "[data-feb]").forEach((b) => b.addEventListener("click", () => { st.d.feb29 = b.dataset.feb; paintWhen(); showFieldErrors(); changed(); }));
  }

  // ------------------------------------------------------------------------- section: audience
  function paintAudience() {
    const a = st.d.audience;
    sec("aud").innerHTML = `<h3>2. Кому</h3>
      <div class="kpu-muted" style="margin-bottom:6px">Категории объединяются. Забанённые и заблокировавшие бота пользователи не получают сообщения никогда.</div>
      <div class="kpu-chips">${caps.segments.map((s) => `<button type="button" class="kpu-chip ${a.segments.includes(s.id) ? "on" : ""}" data-seg="${s.id}" aria-pressed="${a.segments.includes(s.id)}">${esc(SEGMENT_RU[s.id] || s.label)}</button>`).join("")}</div>
      <div class="kpu-form" style="margin-top:10px">
        <label class="f">Язык пользователя<select data-alang><option value="any">Любой</option><option value="ru">Русский</option><option value="en">Английский</option></select></label>
        <label class="f">Тарифы (ключи через запятую)<input type="text" data-tariffs value="${esc(a.tariff_keys)}" placeholder="Пусто = любые"><span class="kpu-muted">Ограничивает только платных пользователей</span></label>
      </div><div data-err="audience"></div>`;
    $(sec("aud"), "[data-alang]").value = a.language;
    $$(sec("aud"), "[data-seg]").forEach((b) => b.addEventListener("click", () => {
      const i = a.segments.indexOf(b.dataset.seg);
      if (i >= 0) a.segments.splice(i, 1); else a.segments.push(b.dataset.seg);
      a.segments = ["none", "trial", "paid"].filter((s) => a.segments.includes(s));
      paintAudience(); showFieldErrors(); changed();
    }));
    $(sec("aud"), "[data-alang]").onchange = (e) => { a.language = e.target.value; changed(); };
    $(sec("aud"), "[data-tariffs]").oninput = (e) => { a.tariff_keys = e.target.value; changed(); };
  }

  // ------------------------------------------------------------------------- section: gift
  const GIFT_ORDER = ["none", "traffic", "days", "balance", "promo_discount", "wheel_spins"];
  function giftFields() {
    const g = st.d.gift;
    switch (g.kind) {
      case "traffic": return `<label class="f">Сколько ГБ<input type="number" min="0.1" max="10000" step="any" data-g="gb" value="${esc(g.gb)}"></label>`;
      case "days": return `<label class="f">Сколько дней<input type="number" min="1" max="3650" step="1" data-g="days" value="${esc(g.days)}"></label>`;
      case "balance": return `<label class="f">Сумма, ${esc(sym)}<input type="text" inputmode="decimal" data-g="amount" value="${esc(g.amount)}"><span class="kpu-muted" data-minor></span></label>`;
      case "promo_discount": return `<label class="f">Скидка, %<input type="number" min="1" max="100" step="any" data-g="percent" value="${esc(g.percent)}"></label>
        <label class="f">Срок действия, дней<input type="number" min="1" max="365" step="1" data-g="valid_days" value="${esc(g.valid_days)}"></label>
        <label class="f">Действует на<select data-g="applies_to"><option value="subscription" ${g.applies_to === "subscription" ? "selected" : ""}>Покупку подписки</option><option value="all" ${g.applies_to === "all" ? "selected" : ""}>Любые покупки</option></select></label>`;
      case "wheel_spins": return `<label class="f">Сколько круток<input type="number" min="1" max="100" step="1" data-g="spins" value="${esc(g.spins)}"></label>
        <label class="chk" style="align-self:end"><input type="checkbox" data-g="ignore_cap" ${g.ignore_cap ? "checked" : ""}> Игнорировать лимит накопления круток</label>`;
      default: return "";
    }
  }
  function paintGift() {
    const g = st.d.gift;
    const wheelOk = ctx.wheelInstalled;
    sec("gift").innerHTML = `<h3>3. Подарок</h3>
      <div class="kpu-radio" role="radiogroup" aria-label="Вид подарка">${GIFT_ORDER.map((k) => {
        const dis = k === "wheel_spins" && !wheelOk;
        return `<label class="${dis ? "dis" : ""}" title="${dis ? "Плагин «Колесо удачи» (kiro-wheel) не установлен" : ""}"><span><input type="radio" name="gk" value="${k}" ${g.kind === k ? "checked" : ""} ${dis ? "disabled" : ""}> ${esc(GIFT_KIND_RU[k])}</span></label>`;
      }).join("")}</div>
      ${wheelOk ? "" : `<div class="kpu-muted" style="margin-top:6px">«Крутки колеса» недоступны: плагин «Колесо удачи» не установлен.</div>`}
      <div class="kpu-muted" style="margin-top:8px">${esc(GIFT_HELP[g.kind])}</div>
      ${g.kind !== "none" ? `<div class="kpu-form" style="margin-top:10px">${giftFields()}</div>` : ""}
      ${g.kind !== "none" ? `<div class="kpu-sub"><div class="kpu-muted" style="margin-bottom:6px">Если подарок выдать нельзя (например, дни или трафик, а подписки нет):</div>
        <div class="kpu-chips">${caps.gift_fallbacks.map((f) => `<button type="button" class="kpu-chip ${st.d.gift_fallback === f.id ? "on" : ""}" data-fb="${f.id}" aria-pressed="${st.d.gift_fallback === f.id}">${esc(f.label)}</button>`).join("")}</div>
        <div class="kpu-muted" style="margin-top:6px">${st.d.gift_fallback === "skip" ? "Такой пользователь не получит ничего: доставка будет пропущена." : "Пользователь получит поздравление без подарка. Используется «Текст без подарка» из шаблона, если он задан."}</div></div>` : ""}
      <div data-err="gift"></div>`;
    $$(sec("gift"), 'input[name="gk"]').forEach((r) => r.addEventListener("change", () => { g.kind = r.value; paintGift(); paintMessage(); showFieldErrors(); changed(); }));
    $$(sec("gift"), "[data-g]").forEach((el) => el.addEventListener("input", () => { g[el.dataset.g] = el.type === "checkbox" ? el.checked : el.value; minorHint(); changed(); }));
    $$(sec("gift"), "[data-fb]").forEach((b) => b.addEventListener("click", () => { st.d.gift_fallback = b.dataset.fb; paintGift(); showFieldErrors(); changed(); }));
    minorHint();
  }
  function minorHint() {
    const el = $(sec("gift"), "[data-minor]");
    if (!el) return;
    const minor = majorToMinor(st.d.gift.amount);
    el.textContent = minor === null ? "Введите число, например 150 или 99,50" : `В минимальных единицах валюты: ${num(minor)}`;
    el.style.color = minor === null ? "#e5484d" : "";
  }

  // ------------------------------------------------------------------------- section: message
  function activeTa() { return $(sec("msg"), st.activeTa === "nogift" && $(sec("msg"), "[data-nogift]") ? "[data-nogift]" : "[data-text]"); }

  function setTaValue(ta, result) {
    ta.value = result.value;
    ta.focus();
    ta.setSelectionRange(result.start, result.end);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function limitInfo() {
    const t = tpl();
    const limit = t.media.length ? caps.limits.caption : caps.limits.text;
    return { limit, label: t.media.length ? "подпись к медиа" : "сообщение" };
  }

  function paintCounter() {
    const t = tpl();
    const { limit, label } = limitInfo();
    const len = visibleLength(t.text);
    const el = $(sec("msg"), "[data-counter]");
    if (!el) return;
    el.textContent = `${num(len)} / ${num(limit)} (${label})`;
    el.style.color = len > limit ? "#e5484d" : "";
    const ng = $(sec("msg"), "[data-counter-ng]");
    if (ng) { const l2 = visibleLength(t.text_no_gift); ng.textContent = `${num(l2)} / ${num(limit)}`; ng.style.color = l2 > limit ? "#e5484d" : ""; }
  }

  function paintMessage() {
    const t = tpl();
    const other = st.lang === "ru" ? "en" : "ru";
    sec("msg").innerHTML = `<h3>4. Сообщение
        <span class="kpu-lang" role="tablist" aria-label="Язык шаблона">${["ru", "en"].map((l) => `<button type="button" role="tab" data-lang="${l}" class="${st.lang === l ? "on" : ""}" aria-selected="${st.lang === l}">${l === "ru" ? "Русский" : "English"}${templateIsEmpty(st.d.templates[l]) ? "" : '<span class="d" title="Шаблон заполнен"></span>'}</button>`).join("")}</span></h3>
      <div class="kpu-muted" style="margin-bottom:8px">Пользователь получит шаблон на своём языке; если его нет, используется язык по умолчанию (${esc(ctx.defaultLanguage || "ru")}).
        ${templateIsEmpty(st.d.templates[other]) ? "" : `<button type="button" class="kpu-btn kpu-sm" data-copy>Скопировать из ${other === "ru" ? "русского" : "английского"}</button>`}</div>
      <div class="kpu-toolbar" role="toolbar" aria-label="Форматирование">${TOOLBAR.map((b) => `<button type="button" class="kpu-btn" data-tag="${b.id}" title="${esc(b.title)}" aria-label="${esc(b.title)}">${b.id === "b" ? "<b>Ж</b>" : b.id === "i" ? "<i>К</i>" : b.id === "u" ? "<u>П</u>" : b.id === "s" ? "<s>З</s>" : esc(b.label)}</button>`).join("")}
        <button type="button" class="kpu-btn" data-emoji title="Эмодзи, в том числе custom emoji из библиотеки" aria-label="Эмодзи">😀</button><span class="kpu-grow"></span><span class="kpu-muted" data-counter></span></div>
      <textarea data-text rows="8" aria-label="Текст сообщения" placeholder="Текст сообщения. Разметка Telegram: &lt;b&gt;, &lt;i&gt;, &lt;a href=...&gt;, подстановки {name}">${esc(t.text)}</textarea>
      <div class="kpu-ph" aria-label="Подстановки">${caps.placeholders.map((p) => `<button type="button" data-ph="${esc(p.name)}" title="${esc(p.description)}. Пример: ${esc(p.sample)}">{${esc(p.name)}}</button>`).join("")}<button type="button" data-ph-block title="Необязательный блок: весь фрагмент в [[ ]] исчезает, если внутри есть пустая подстановка (например, {age} без года рождения или {promo_code} без подарка)">[[ блок ]]</button></div>
      <div class="kpu-muted" style="margin-top:6px">Фрагмент в <code>[[ … ]]</code> показывается, только если все подстановки внутри заполнены. Выделите текст и нажмите «[[ блок ]]».</div>
      <div data-err="templates"></div>${st.issues[st.lang].map((i) => `<div class="kpu-err">${esc(i)}</div>`).join("")}
      ${giftOn() ? `<details class="kpu-sub" ${t.text_no_gift ? "open" : ""}><summary>Текст без подарка <span class="kpu-muted">(если подарок выдать нельзя и выбрано «отправить без подарка»)</span> <span class="kpu-muted" data-counter-ng style="float:right"></span></summary>
        <textarea data-nogift rows="4" style="margin-top:8px" aria-label="Текст без подарка" placeholder="Например: С днём рождения, {name}!">${esc(t.text_no_gift)}</textarea></details>` : ""}
      <div class="kpu-sub"><div class="kpu-row"><b>Медиа</b><span class="kpu-muted">до ${caps.limits.media} файлов; фото, видео, GIF, документы</span><span class="kpu-grow"></span>
        <label class="kpu-btn kpu-sm" style="cursor:pointer">Загрузить<input type="file" multiple hidden data-file accept="image/*,video/*,.pdf,.zip,.txt"></label>
        <button type="button" class="kpu-btn kpu-sm" data-stored>Из загруженных</button><button type="button" class="kpu-btn kpu-sm" data-url>По ссылке</button></div>
        <div data-media></div><div data-err="media"></div></div>
      <div class="kpu-sub"><div class="kpu-row"><b>Кнопки</b><span class="kpu-muted">до ${caps.limits.button_rows} рядов по ${caps.limits.buttons_per_row}; подпись и ссылка поддерживают подстановки</span></div>
        <div data-buttons></div><div data-err="buttons"></div></div>
      <div class="kpu-sub"><div class="kpu-chips" style="gap:14px">
        <label class="chk"><input type="checkbox" data-flag="disable_notification" ${t.disable_notification ? "checked" : ""}> Без звука</label>
        <label class="chk"><input type="checkbox" data-flag="protect_content" ${t.protect_content ? "checked" : ""}> Запретить пересылку и сохранение</label>
        <label class="chk"><input type="checkbox" data-flag="preview" ${t.disable_preview ? "" : "checked"}> Показывать превью ссылок</label></div></div>`;
    const root = sec("msg");
    $$(root, "[data-lang]").forEach((b) => b.addEventListener("click", () => { st.lang = b.dataset.lang; paintMessage(); showFieldErrors(); refreshPreview(); }));
    $(root, "[data-copy]")?.addEventListener("click", () => { st.d.templates[st.lang] = { ...clone(st.d.templates[other]) }; paintMessage(); changed(); });
    const ta = $(root, "[data-text]");
    ta.addEventListener("focus", () => { st.activeTa = "text"; });
    ta.addEventListener("input", () => { t.text = ta.value; paintCounter(); changed(); });
    const ng = $(root, "[data-nogift]");
    ng?.addEventListener("focus", () => { st.activeTa = "nogift"; });
    ng?.addEventListener("input", () => { t.text_no_gift = ng.value; paintCounter(); changed(); });
    $$(root, "[data-tag]").forEach((b) => b.addEventListener("click", async () => {
      const tag = TOOLBAR.find((x) => x.id === b.dataset.tag);
      const el = activeTa();
      const start = el.selectionStart, end = el.selectionEnd;
      let open = tag.open;
      if (tag.id === "a") {
        const url = await askText("Ссылка", "https://… или tg://…");
        if (!url) return;
        open = linkOpen(url);
      }
      setTaValue(el, wrapSelection(el.value, start, end, open, tag.close));
    }));
    $$(root, "[data-ph]").forEach((b) => b.addEventListener("click", () => {
      const el = activeTa();
      const r = insertText(el.value, el.selectionStart, el.selectionEnd, `{${b.dataset.ph}}`);
      setTaValue(el, r);
    }));
    $(root, "[data-ph-block]").onclick = () => {
      const el = activeTa();
      setTaValue(el, wrapSelection(el.value, el.selectionStart, el.selectionEnd, "[[", "]]", " текст с {age} "));
    };
    $(root, "[data-emoji]").onclick = async () => {
      const el = activeTa();
      const start = el.selectionStart, end = el.selectionEnd;
      const picked = await pickEmoji(caps);
      if (!picked) return;
      setTaValue(el, insertText(el.value, start, end, picked.id ? emojiTag(picked.id, picked.fallback) : picked.fallback));
    };
    $$(root, "[data-flag]").forEach((c) => c.addEventListener("change", () => {
      if (c.dataset.flag === "preview") t.disable_preview = !c.checked; else t[c.dataset.flag] = c.checked;
      changed();
    }));
    paintMedia();
    paintButtons();
    paintCounter();
    // uploads
    $(root, "[data-file]").addEventListener("change", async (e) => {
      const files = [...e.target.files];
      e.target.value = "";
      for (const file of files) {
        if (t.media.length >= caps.limits.media) { toast(`Не больше ${caps.limits.media} файлов`, "bad"); break; }
        const row = h(`<div class="kpu-item"><div class="ph">⏳</div><div class="kpu-grow">${esc(file.name)}<div class="kpu-prog"><i style="width:0%"></i></div></div></div>`);
        $(root, "[data-media]").appendChild(row);
        try {
          const res = await uploadFile(file, caps.limits, (p) => { row.querySelector("i").style.width = `${Math.round(p * 100)}%`; });
          t.media.push({ kind: res.kind, id: res.id, name: res.name, size: res.size });
          paintMedia(); paintCounter(); changed();
        } catch (err) { row.remove(); fail(err); }
      }
    });
    $(root, "[data-stored]").onclick = async () => {
      const m = await pickStoredMedia();
      if (!m) return;
      if (t.media.length >= caps.limits.media) return toast(`Не больше ${caps.limits.media} файлов`, "bad");
      t.media.push({ kind: m.kind, id: m.id, name: m.filename, size: m.size });
      paintMedia(); paintCounter(); changed();
    };
    $(root, "[data-url]").onclick = async () => {
      const url = await askText("Адрес файла (https://…)", "https://example.com/banner.jpg", true);
      if (!url) return;
      if (!/^https:\/\//i.test(url.value || url)) return toast("Ссылка на медиа должна начинаться с https://", "bad");
      t.media.push({ kind: url.kind, url: url.value });
      paintMedia(); paintCounter(); changed();
    };
  }

  function paintMedia() {
    const t = tpl();
    const box = $(sec("msg"), "[data-media]");
    box.innerHTML = t.media.map((m, i) => `<div class="kpu-item" data-i="${i}">${m.kind === "photo" && m.id ? `<img src="${esc(mediaUrl(m.id))}" alt="">` : `<div class="ph">${m.kind === "video" ? "🎬" : m.kind === "animation" ? "🎞" : m.kind === "photo" ? "🖼" : "📄"}</div>`}
      <div class="kpu-grow"><b>${esc(MEDIA_RU[m.kind])}</b> <span class="kpu-muted">${esc(m.name || (m.url ? m.url.slice(0, 50) : m.id ? "загруженный файл" : "file_id"))} ${m.size ? esc(fmtSize(m.size)) : ""}</span>
        ${m.id && (m.kind === "video" || m.kind === "animation") ? `<label class="chk" style="margin-top:4px"><input type="checkbox" data-gif ${m.kind === "animation" ? "checked" : ""}> Отправить как GIF (без звука, зациклено)</label>` : ""}</div>
      <button type="button" class="kpu-btn kpu-sm" data-up ${i === 0 ? "disabled" : ""} aria-label="Выше">↑</button><button type="button" class="kpu-btn kpu-sm" data-dn ${i === t.media.length - 1 ? "disabled" : ""} aria-label="Ниже">↓</button><button type="button" class="kpu-btn kpu-sm kpu-danger" data-rm aria-label="Убрать">✕</button></div>`).join("");
    $$(box, "[data-i]").forEach((row) => {
      const i = Number(row.dataset.i);
      $(row, "[data-rm]").onclick = () => { t.media.splice(i, 1); paintMedia(); paintCounter(); changed(); };
      $(row, "[data-up]").onclick = () => { [t.media[i - 1], t.media[i]] = [t.media[i], t.media[i - 1]]; paintMedia(); changed(); };
      $(row, "[data-dn]").onclick = () => { [t.media[i + 1], t.media[i]] = [t.media[i], t.media[i + 1]]; paintMedia(); changed(); };
      $(row, "[data-gif]")?.addEventListener("change", (e) => { t.media[i].kind = e.target.checked ? "animation" : "video"; paintMedia(); changed(); });
    });
    if (t.media.length > 1) box.insertAdjacentHTML("beforeend", `<div class="kpu-muted" style="margin-top:6px">Несколько файлов уйдут альбомом; к альбому нельзя прикрепить кнопки, а GIF в альбом не входит.</div>`);
  }

  function newButton() { return { kind: "url", label: "", url: "", section: "home", style: "", icon_custom_emoji_id: "", icon_emoji: "" }; }

  function paintButtons() {
    const t = tpl();
    const box = $(sec("msg"), "[data-buttons]");
    const lim = caps.limits;
    box.innerHTML = t.buttons.map((row, r) => `<div class="kpu-sub" data-r="${r}"><div class="kpu-row" style="margin-bottom:4px"><span class="kpu-muted">Ряд ${r + 1}</span><span class="kpu-grow"></span>
        <button type="button" class="kpu-btn kpu-sm" data-rup ${r === 0 ? "disabled" : ""} aria-label="Ряд выше">↑</button><button type="button" class="kpu-btn kpu-sm" data-rdn ${r === t.buttons.length - 1 ? "disabled" : ""} aria-label="Ряд ниже">↓</button><button type="button" class="kpu-btn kpu-sm kpu-danger" data-rrm aria-label="Удалить ряд">✕</button></div>
        ${row.map((b, i) => `<div class="kpu-item" data-b="${i}" style="margin-top:6px">
          <select data-k aria-label="Тип кнопки">${caps.button_kinds.map((k) => `<option value="${k.id}" ${b.kind === k.id ? "selected" : ""}>${esc(BUTTON_KIND_RU[k.id] || k.label)}</option>`).join("")}</select>
          <button type="button" class="kpu-btn kpu-sm" data-icon title="Иконка кнопки" aria-label="Иконка кнопки">${b.icon_emoji ? esc(b.icon_emoji) : b.icon_custom_emoji_id ? "★" : "☺"}${b.icon_custom_emoji_id ? '<span class="kpu-muted"> custom</span>' : ""}</button>
          <input type="text" data-label maxlength="${lim.button_label}" placeholder="Подпись" aria-label="Подпись кнопки" value="${esc(b.label)}" style="flex:1;min-width:110px">
          ${b.kind === "section" ? `<select data-sec aria-label="Раздел">${caps.sections.map((s) => `<option value="${s}" ${b.section === s ? "selected" : ""}>${esc(SECTIONS_RU[s] || s)}</option>`).join("")}</select>` : `<input type="text" data-url placeholder="https://…" aria-label="Адрес" value="${esc(b.url)}" style="flex:2;min-width:140px">`}
          <select data-style aria-label="Цвет">${["", ...caps.button_styles].map((s) => `<option value="${s}" ${b.style === s ? "selected" : ""}>${esc(STYLE_RU[s] || s)}</option>`).join("")}</select>
          <button type="button" class="kpu-btn kpu-sm kpu-danger" data-rm aria-label="Удалить кнопку">✕</button></div>`).join("")}
        ${row.length < lim.buttons_per_row ? `<button type="button" class="kpu-btn kpu-sm" data-addb style="margin-top:6px">+ Кнопка в ряд</button>` : ""}</div>`).join("")
      + (t.buttons.length < lim.button_rows ? `<button type="button" class="kpu-btn kpu-sm" data-addr style="margin-top:8px">+ Новый ряд</button>` : "");
    $$(box, "[data-r]").forEach((rowEl) => {
      const r = Number(rowEl.dataset.r), row = t.buttons[r];
      $(rowEl, "[data-rrm]").onclick = () => { t.buttons.splice(r, 1); paintButtons(); changed(); };
      $(rowEl, "[data-rup]").onclick = () => { [t.buttons[r - 1], t.buttons[r]] = [t.buttons[r], t.buttons[r - 1]]; paintButtons(); changed(); };
      $(rowEl, "[data-rdn]").onclick = () => { [t.buttons[r + 1], t.buttons[r]] = [t.buttons[r], t.buttons[r + 1]]; paintButtons(); changed(); };
      $(rowEl, "[data-addb]")?.addEventListener("click", () => { row.push(newButton()); paintButtons(); changed(); });
      $$(rowEl, "[data-b]").forEach((el) => {
        const b = row[Number(el.dataset.b)];
        $(el, "[data-k]").onchange = (e) => { b.kind = e.target.value; paintButtons(); changed(); };
        $(el, "[data-label]").oninput = (e) => { b.label = e.target.value; changed(); };
        $(el, "[data-url]")?.addEventListener("input", (e) => { b.url = e.target.value; changed(); });
        $(el, "[data-sec]")?.addEventListener("change", (e) => { b.section = e.target.value; changed(); });
        $(el, "[data-style]").onchange = (e) => { b.style = e.target.value; changed(); };
        $(el, "[data-rm]").onclick = () => { row.splice(Number(el.dataset.b), 1); if (!row.length) t.buttons.splice(r, 1); paintButtons(); changed(); };
        $(el, "[data-icon]").onclick = async () => {
          const picked = await pickEmoji(caps);
          if (!picked) return;
          // both fields are kept on purpose: the plain emoji is the fallback when Telegram rejects the custom one
          b.icon_custom_emoji_id = picked.id || "";
          b.icon_emoji = picked.fallback;
          paintButtons(); changed();
        };
      });
    });
    $(box, "[data-addr]")?.addEventListener("click", () => { t.buttons.push([newButton()]); paintButtons(); changed(); });
  }

  function askText(title, placeholder, withKind = false) {
    return new Promise((resolve) => {
      modal(`<h3>${esc(title)}</h3><input type="text" data-v placeholder="${esc(placeholder)}" style="width:100%" autofocus>
        ${withKind ? `<label class="f" style="margin-top:8px">Тип файла<select data-kind>${caps.media_kinds.map((k) => `<option value="${k}">${esc(MEDIA_RU[k] || k)}</option>`).join("")}</select></label>` : ""}
        <div class="kpu-row" style="justify-content:flex-end;margin-top:12px"><button class="kpu-btn" data-no>Отмена</button><button class="kpu-btn kpu-primary" data-ok>OK</button></div>`, (m, close) => {
        const ok = () => { const v = $(m, "[data-v]").value.trim(); close(); resolve(v ? (withKind ? { value: v, kind: $(m, "[data-kind]").value } : v) : null); };
        $(m, "[data-no]").onclick = () => { close(); resolve(null); };
        $(m, "[data-ok]").onclick = ok;
        $(m, "[data-v]").addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); });
      });
    });
  }

  // ------------------------------------------------------------------------- preview
  function paintPreviewShell() {
    sec("preview").innerHTML = `<h3>Предпросмотр <span class="kpu-muted" data-pv-lang></span></h3>
      <div class="tg" data-bubble aria-live="polite"><div class="tg-meta">Загрузка…</div></div>
      <div data-pv-msgs></div>
      <div class="kpu-sub"><label class="f">Пользователь для примера<input type="text" data-sample placeholder="@username, ID или Telegram ID (пусто = условные данные)" value="${esc(st.sample)}"></label>
        <label class="chk" style="margin-top:6px"><input type="checkbox" data-gift-on ${st.withGift ? "checked" : ""} ${giftOn() ? "" : "disabled"}> Показать «с подарком»</label>
        <label class="chk" style="margin-top:6px"><input type="checkbox" data-noyear ${st.noYear ? "checked" : ""}> Год рождения неизвестен (пустой {age})</label></div>`;
    $(sec("preview"), "[data-sample]").addEventListener("input", (e) => { st.sample = e.target.value.trim(); refreshPreview(); });
    $(sec("preview"), "[data-gift-on]").addEventListener("change", (e) => { st.withGift = e.target.checked; refreshPreview(); });
    $(sec("preview"), "[data-noyear]").addEventListener("change", (e) => { st.noYear = e.target.checked; refreshPreview(); });
  }

  async function runPreview() {
    if (st.dead) return;
    const seq = ++st.seq;
    const bubble = $(sec("preview"), "[data-bubble]");
    const msgs = $(sec("preview"), "[data-pv-msgs]");
    if (!bubble) return;
    $(sec("preview"), "[data-pv-lang]").textContent = st.lang === "ru" ? "· русский" : "· English";
    const gi = $(sec("preview"), "[data-gift-on]");
    if (gi) { gi.disabled = !giftOn(); }
    if (templateIsEmpty(tpl())) {
      bubble.innerHTML = `<div class="tg-meta">Для этого языка шаблон ещё не заполнен.</div>`;
      msgs.innerHTML = "";
      return;
    }
    const body = { event: toPayload(st.d, { forPreview: true }), language: st.lang, with_gift: st.withGift && giftOn() };
    if (st.sample) body.sample_user = st.sample; else if (st.noYear) body.sample_user = { age: "" };
    if (body.event.gift.kind === "balance" && body.event.gift.amount_minor === null) { msgs.innerHTML = `<div class="kpu-err">Укажите корректную сумму подарка</div>`; return; }
    bubble.style.opacity = ".6";
    try {
      const res = await api("/preview", "POST", body);
      if (seq !== st.seq || st.dead) return;
      bubble.style.opacity = "";
      st.preview = res;
      drawBubble(bubble, res.rendered);
      const warns = (res.warnings || []).filter((w) => !/задан и custom emoji, и обычный/.test(w));
      msgs.innerHTML = (res.errors || []).map((e) => `<div class="kpu-err">${esc(e)}</div>`).join("") + warns.map((w) => `<div class="kpu-warn">${esc(w)}</div>`).join("") +
        (res.rendered && res.gift_text ? `<div class="kpu-muted" style="margin-top:6px">Подарок: ${esc(res.gift_text)}</div>` : "");
    } catch (err) {
      if (seq !== st.seq || st.dead) return;
      bubble.style.opacity = "";
      bubble.innerHTML = `<div class="tg-meta">${esc(err.code === "template_missing" ? "Для этого языка шаблон ещё не заполнен." : "Предпросмотр недоступен")}</div>`;
      msgs.innerHTML = err.code === "template_missing" ? "" : `<div class="kpu-err">${esc(err.message)}${(err.data?.issues || []).map((i) => `<br>${esc(i)}`).join("")}</div>`;
    }
  }

  function drawBubble(bubble, r) {
    bubble.textContent = "";
    if (!r) { bubble.innerHTML = `<div class="tg-meta">Исправьте ошибки в шаблоне, чтобы увидеть сообщение.</div>`; return; }
    const emojiUrl = (id) => emojiMediaUrl(caps, id);
    const b = h(`<div class="tg-bubble"></div>`);
    for (const m of r.media || []) {
      const box = h(`<div class="tg-media"></div>`);
      if (m.kind === "photo" && m.id) { const img = document.createElement("img"); img.src = mediaUrl(m.id); img.alt = ""; box.appendChild(img); }
      else box.appendChild(h(`<div class="ph">${m.kind === "photo" ? "🖼 Фото по ссылке" : m.kind === "video" ? "🎬 Видео" : m.kind === "animation" ? "🎞 GIF" : "📄 Файл"}</div>`));
      b.appendChild(box);
    }
    const textBox = document.createElement("div");
    textBox.appendChild(renderTree(parseTelegramHtml(r.text), document, { emojiUrl }));
    b.appendChild(textBox);
    bubble.appendChild(b);
    if ((r.buttons || []).length) {
      const kb = h(`<div class="tg-kb"></div>`);
      for (const row of r.buttons) {
        const rowEl = document.createElement("div");
        for (const btn of row) {
          const s = document.createElement("span");
          if (btn.style) s.className = btn.style;
          s.textContent = `${btn.icon_emoji ? `${btn.icon_emoji} ` : ""}${btn.label}${btn.kind === "url" || btn.kind === "web_app" ? " ↗" : ""}`;
          rowEl.appendChild(s);
        }
        kb.appendChild(rowEl);
      }
      bubble.appendChild(kb);
    }
    const meta = h(`<div class="tg-meta"></div>`);
    meta.textContent = `${num(r.visible_length)} знаков${r.used_no_gift ? " · текст без подарка" : ""}${r.disable_notification ? " · без звука" : ""}${r.protect_content ? " · защищено" : ""}`;
    bubble.appendChild(meta);
  }

  // ------------------------------------------------------------------------- save / test / run
  function applyApiError(err) {
    st.fieldErrors = {};
    st.issues = { ru: [], en: [], general: [] };
    const field = FIELD_OF_ERROR[err.code];
    if (err.code === "invalid_event") {
      st.issues = splitIssues(err.data?.issues);
      if (!st.issues.general.length && !st.issues.ru.length && !st.issues.en.length) st.issues.general.push(err.message);
    } else if (field) addFieldError(field, err.message);
    else toast(err.message, "bad");
    paintMessage();
    showFieldErrors();
    if (err.code === "invalid_event") toast("В событии есть ошибки: они показаны на странице", "bad");
  }

  async function save() {
    st.fieldErrors = {};
    st.issues = { ru: [], en: [], general: [] };
    const local = draftProblems(st.d, { wheel_installed: ctx.wheelInstalled });
    if (local.length) {
      for (const [f, m] of local) addFieldError(f, m);
      paintMessage();
      showFieldErrors();
      toast(local[0][1], "bad");
      return false;
    }
    try {
      const payload = toPayload(st.d);
      const res = st.d.id ? await api(`/events/${st.d.id}`, "PUT", payload) : await api("/events", "POST", payload);
      if (!st.d.id) st.d.id = res.id;
      st.snapshot = JSON.stringify(st.d);
      st.warnings = res.warnings || [];
      paintMessage();
      showFieldErrors();
      paintChrome();
      toast(st.warnings.length ? "Событие сохранено, есть предупреждения" : "Событие сохранено", st.warnings.length ? "warn" : "ok");
      if (st.warnings.length) $(host, "[data-top-issues]").innerHTML += `<div class="kpu-banner warn"><b>Предупреждения:</b><ul>${st.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
      ctx.changed?.();
      return true;
    } catch (err) {
      applyApiError(err);
      return false;
    }
  }

  async function ensureSaved(what) {
    if (!st.d.id || isDirty()) {
      const ok = await confirmBox(st.d.id ? `${esc(what)} берёт сохранённый шаблон. Сохранить текущие изменения и продолжить?` : `${esc(what)} возможно только для сохранённого события. Сохранить его сейчас?`, "Сохранить и продолжить");
      if (!ok) return false;
      return save();
    }
    return true;
  }

  async function sendTest() {
    if (!(await ensureSaved("Тестовая отправка"))) return;
    await testEvent(st.d.id, { language: st.lang, with_gift: st.withGift && giftOn(), ...(st.sample ? { sample_user: st.sample } : {}) });
  }

  async function checkAudienceNow() {
    if (!(await ensureSaved("Проверка аудитории"))) return;
    await checkAudience(st.d.id, toPayload(st.d).audience);
  }

  async function runNow() {
    if (!(await ensureSaved("Запуск"))) return;
    const r = await runEvent({ id: st.d.id, name: st.d.name, gift: toPayloadSafe().gift }, ctx.currency);
    if (r) ctx.changed?.();
  }

  // ------------------------------------------------------------------------- lifecycle
  function paintAll() {
    paintHeader();
    paintWhen();
    paintAudience();
    paintGift();
    paintMessage();
    paintPreviewShell();
    showFieldErrors();
    paintChrome();
  }
  paintAll();
  runPreview();

  const onBeforeUnload = (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", onBeforeUnload);

  return {
    dirty: isDirty,
    destroy() {
      st.dead = true;
      refreshPreview.cancel();
      window.removeEventListener("beforeunload", onBeforeUnload);
    },
  };
}

export { blankDraft, fromEvent };
