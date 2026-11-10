// KIRO push: customer side (frontend host API v1).
// A compact "Birthday" card in the Mini App settings: the date can be entered ONCE, then it is locked and only
// support can change it. The card is hidden when the plugin or the self-entry is switched off.
const MONTHS = {
  ru: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
};

const TEXTS = {
  ru: {
    title: "День рождения",
    intro: "Укажите дату, чтобы получать поздравления и подарки",
    warning: "Дату можно указать один раз. Изменить её потом можно только через поддержку.",
    day: "День",
    month: "Месяц",
    year: "Год (необязательно)",
    noYear: "Не указывать",
    save: "Сохранить",
    confirmTitle: "Проверьте дату",
    confirmText: "Сохранить дату? Изменить её потом можно только через поддержку.",
    confirmYes: "Да, сохранить",
    back: "Назад",
    saved: "Дата рождения сохранена",
    set: "Дата рождения",
    locked: "Изменить дату можно через поддержку",
    support: "Написать в поддержку",
    errors: {
      invalid_date: "Такой даты не существует",
      invalid_year: "Проверьте год рождения",
      consent_required: "Нужно согласие на обработку даты рождения",
      rate_limited: "Слишком много попыток, попробуйте через час",
      already_set: "Дата уже указана",
      disabled: "Сейчас дату указать нельзя",
      generic: "Не удалось сохранить, попробуйте ещё раз",
    },
  },
  en: {
    title: "Birthday",
    intro: "Add your date to get greetings and gifts",
    warning: "You can set the date only once. Afterwards it can be changed only through support.",
    day: "Day",
    month: "Month",
    year: "Year (optional)",
    noYear: "Do not specify",
    save: "Save",
    confirmTitle: "Check the date",
    confirmText: "Save this date? Afterwards it can be changed only through support.",
    confirmYes: "Yes, save",
    back: "Back",
    saved: "Birthday saved",
    set: "Birthday",
    locked: "You can change the date through support",
    support: "Contact support",
    errors: {
      invalid_date: "This date does not exist",
      invalid_year: "Check the year of birth",
      consent_required: "Consent to processing the birth date is required",
      rate_limited: "Too many attempts, try again in an hour",
      already_set: "The date is already set",
      disabled: "The date cannot be set right now",
      generic: "Could not save, please try again",
    },
  },
};

const STYLE = `
.kp-card{box-sizing:border-box;width:100%;padding:16px;border:1px solid var(--border,rgba(128,128,128,.28));border-radius:var(--radius,14px);
  background:var(--bg-elevated,var(--card,transparent));color:var(--text,inherit);font:inherit;display:grid;gap:12px}
.kp-card h3{margin:0;font-size:1.05rem;line-height:1.3}
.kp-card p{margin:0;color:var(--muted,inherit);font-size:.92rem;line-height:1.4}
.kp-card .kp-warn{padding:10px 12px;border-radius:10px;background:rgba(245,190,70,.14);border:1px solid rgba(245,190,70,.45);color:var(--text,inherit)}
.kp-card .kp-row{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.6fr) minmax(0,1.2fr);gap:8px}
.kp-card label.kp-field{display:grid;gap:4px;font-size:.8rem;color:var(--muted,inherit)}
.kp-card select{box-sizing:border-box;width:100%;min-height:44px;padding:0 8px;border-radius:10px;border:1px solid var(--border,rgba(128,128,128,.4));
  background:var(--bg,transparent);color:var(--text,inherit);font:inherit;font-size:1rem}
.kp-card .kp-consent{display:flex;gap:10px;align-items:flex-start;font-size:.9rem;line-height:1.35;cursor:pointer}
.kp-card .kp-consent input{width:22px;height:22px;flex:none;margin:0;accent-color:var(--accent,#2f80ed)}
.kp-card button{min-height:44px;padding:0 16px;border-radius:12px;border:1px solid var(--border,rgba(128,128,128,.4));background:transparent;
  color:inherit;font:inherit;font-weight:600;cursor:pointer}
.kp-card button.kp-primary{background:var(--accent,#2f80ed);border-color:transparent;color:#fff}
.kp-card button:disabled{opacity:.5;cursor:not-allowed}
.kp-card button:focus-visible,.kp-card select:focus-visible,.kp-card input:focus-visible{outline:2px solid var(--accent,#2f80ed);outline-offset:2px}
.kp-card .kp-actions{display:flex;gap:8px;flex-wrap:wrap}
.kp-card .kp-error{color:#e5484d;font-size:.9rem}
.kp-card .kp-date{font-size:1.15rem;font-weight:600}
@media (max-width:360px){.kp-card .kp-row{grid-template-columns:1fr 1fr}}
`;

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function pick(props) {
  const code = String(props?.language || props?.host?.language || "ru").toLowerCase().slice(0, 2);
  return { lang: code === "en" ? "en" : "ru", t: TEXTS[code === "en" ? "en" : "ru"] };
}

function daysIn(month, year) {
  // 29 February is a real birthday even when the year is unknown
  return new Date(year || 2000, month, 0).getDate();
}

export function humanDate(lang, day, month, year) {
  const m = MONTHS[lang][month - 1];
  const base = lang === "en" ? `${m} ${day}` : `${day} ${m}`;
  return year ? `${base}, ${year}` : base;
}

function collapse(target) {
  const box = target.closest && target.closest(".extension-content");
  if (box) box.style.display = "none";
}

function optionList(from, to, selected, placeholder) {
  const items = placeholder ? [`<option value="">${esc(placeholder)}</option>`] : [];
  for (let n = from; n <= to; n += 1) items.push(`<option value="${n}"${n === selected ? " selected" : ""}>${n}</option>`);
  return items.join("");
}

function renderSet(st, data) {
  const { lang, t } = st.l10n;
  const canSupport = typeof st.host.navigateSection === "function";
  st.root.innerHTML = `<style>${STYLE}</style>
    <section class="kp-card" aria-labelledby="kp-title">
      <h3 id="kp-title">${esc(t.title)}</h3>
      <div class="kp-date">${esc(t.set)}: ${esc(humanDate(lang, data.day, data.month, data.year))}</div>
      <p>${esc(t.locked)}</p>
      ${canSupport ? `<div class="kp-actions"><button type="button" data-support>${esc(t.support)}</button></div>` : ""}
    </section>`;
  const button = st.root.querySelector("[data-support]");
  if (button) button.addEventListener("click", () => st.host.navigateSection("support"));
}

function renderForm(st, data, draft = {}) {
  const { lang, t } = st.l10n;
  const thisYear = new Date().getFullYear();
  const month = draft.month || 0;
  const years = ['<option value="">' + esc(t.noYear) + "</option>"];
  for (let y = thisYear - 5; y >= thisYear - 110; y -= 1) years.push(`<option value="${y}"${y === draft.year ? " selected" : ""}>${y}</option>`);
  st.root.innerHTML = `<style>${STYLE}</style>
    <section class="kp-card" aria-labelledby="kp-title">
      <h3 id="kp-title">${esc(t.title)}</h3>
      <p>${esc(t.intro)}</p>
      <div class="kp-row">
        <label class="kp-field">${esc(t.day)}<select data-day aria-label="${esc(t.day)}">${optionList(1, month ? daysIn(month, draft.year) : 31, draft.day, "—")}</select></label>
        <label class="kp-field">${esc(t.month)}<select data-month aria-label="${esc(t.month)}"><option value="">—</option>${MONTHS[lang]
          .map((name, i) => `<option value="${i + 1}"${i + 1 === month ? " selected" : ""}>${esc(name[0].toUpperCase() + name.slice(1))}</option>`)
          .join("")}</select></label>
        <label class="kp-field">${esc(t.year)}<select data-year aria-label="${esc(t.year)}">${years.join("")}</select></label>
      </div>
      <p class="kp-warn" role="note">${esc(t.warning)}</p>
      <label class="kp-consent"><input type="checkbox" data-consent${draft.consent ? " checked" : ""}><span>${esc(data.consent_text)}</span></label>
      <div class="kp-error" role="alert" data-error>${esc(draft.error || "")}</div>
      <div class="kp-actions"><button type="button" class="kp-primary" data-save>${esc(t.save)}</button></div>
    </section>`;
  const q = (sel) => st.root.querySelector(sel);
  const read = () => ({
    day: Number(q("[data-day]").value) || 0,
    month: Number(q("[data-month]").value) || 0,
    year: Number(q("[data-year]").value) || 0,
    consent: q("[data-consent]").checked,
  });
  const refresh = () => {
    const v = read();
    q("[data-save]").disabled = !(v.day && v.month && v.consent);
  };
  const rebuildDays = () => {
    const v = read();
    if (v.month && v.day > daysIn(v.month, v.year)) renderForm(st, data, { ...v, day: 0 });
    else refresh();
  };
  q("[data-month]").addEventListener("change", rebuildDays);
  q("[data-year]").addEventListener("change", rebuildDays);
  q("[data-day]").addEventListener("change", refresh);
  q("[data-consent]").addEventListener("change", refresh);
  q("[data-save]").addEventListener("click", () => {
    const v = read();
    if (v.day > daysIn(v.month, v.year)) {
      q("[data-error]").textContent = t.errors.invalid_date;
      return;
    }
    renderConfirm(st, data, v);
  });
  refresh();
}

function renderConfirm(st, data, v) {
  const { lang, t } = st.l10n;
  st.root.innerHTML = `<style>${STYLE}</style>
    <section class="kp-card" aria-labelledby="kp-title">
      <h3 id="kp-title">${esc(t.confirmTitle)}</h3>
      <div class="kp-date">${esc(humanDate(lang, v.day, v.month, v.year || 0))}</div>
      <p class="kp-warn" role="note">${esc(t.confirmText)}</p>
      <div class="kp-error" role="alert" data-error></div>
      <div class="kp-actions"><button type="button" class="kp-primary" data-yes>${esc(t.confirmYes)}</button><button type="button" data-back>${esc(t.back)}</button></div>
    </section>`;
  st.root.querySelector("[data-back]").addEventListener("click", () => renderForm(st, data, v));
  const yes = st.root.querySelector("[data-yes]");
  yes.addEventListener("click", async () => {
    yes.disabled = true;
    try {
      const body = { day: v.day, month: v.month, consent: true };
      if (v.year) body.year = v.year;
      const out = await st.host.request("/birthdate", { method: "POST", body: JSON.stringify(body) });
      if (st.disposed) return;
      if (typeof st.host.notify === "function") st.host.notify(t.saved);
      renderSet(st, out);
    } catch (err) {
      if (st.disposed) return;
      const code = err?.error || err?.data?.error || "";
      if (code === "already_set") {
        load(st);
        return;
      }
      renderForm(st, data, { ...v, error: t.errors[code] || t.errors.generic });
    }
  });
}

async function load(st) {
  try {
    const data = await st.host.request("/birthdate");
    if (st.disposed) return;
    if (!data.enabled) {
      collapse(st.target);
      return;
    }
    if (data.state === "set") renderSet(st, data);
    else renderForm(st, data);
  } catch {
    if (!st.disposed) collapse(st.target);
  }
}

export function mountView(view, element, props) {
  const root = document.createElement("div");
  root.className = "plugin-host";
  root.style.cssText = "display:grid;width:100%;min-width:0";
  element.replaceChildren(root);
  const st = { root, target: element, host: props.host, l10n: pick(props), disposed: false };
  load(st);
  return st;
}

export function updateView(instance, props) {
  if (instance && props) instance.l10n = pick(props);
}

export function unmountView(instance) {
  if (!instance) return;
  instance.disposed = true;
  instance.root.remove();
}
