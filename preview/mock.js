// Stubbed admin API for the standalone preview (preview/index.html). Realistic data; writes mutate in-memory state only.
const PH = [
  ["name", "Имя: имя в Telegram, иначе @username, иначе «друг»", "Алексей"], ["first_name", "Имя из профиля (может быть пустым)", "Алексей"],
  ["age", "Возраст (только если известен год рождения)", "30"], ["years", "Сколько лет с регистрации (годовщина)", "3"],
  ["days_left", "Дней до конца подписки (0, если подписки нет)", "12"], ["tariff", "Название тарифа подписки", "Premium"],
  ["gift", "Подарок текстом, например «+7 дней»", "+7 дней"], ["promo_code", "Персональный промокод (подарок «скидка»)", "PUSH-AB12CD"],
  ["promo_discount", "Размер скидки по промокоду", "20%"], ["balance", "Баланс пользователя", "150 ₽"], ["spins", "Билеты колеса удачи (если плагин установлен)", "3"],
];
const caps = {
  event_kinds: [["birthday", "День рождения"], ["registration_anniversary", "Годовщина регистрации"], ["fixed_date", "Праздник (каждый год)"], ["one_time", "Разовое уведомление"], ["manual", "Ручной запуск"]].map(([id, label]) => ({ id, label })),
  gift_kinds: [], gift_fallbacks: [{ id: "send_without_gift", label: "Отправить поздравление без подарка" }, { id: "skip", label: "Не отправлять" }],
  feb29_rules: [{ id: "feb28", label: "28 февраля" }, { id: "mar1", label: "1 марта" }],
  segments: [{ id: "none", label: "Без подписки" }, { id: "trial", label: "Пробная" }, { id: "paid", label: "Платная" }], languages: ["ru", "en"],
  media_kinds: ["photo", "video", "animation", "document"],
  button_kinds: [{ id: "url", label: "Ссылка" }, { id: "web_app", label: "Mini App (https)" }, { id: "section", label: "Раздел Mini App магазина" }],
  button_styles: ["primary", "success", "danger"], sections: ["home", "plans", "install", "trial", "invite", "devices", "support", "settings"],
  placeholders: PH.map(([name, description, sample]) => ({ name, description, sample })),
  limits: { text: 4096, caption: 1024, media: 10, button_rows: 5, buttons_per_row: 3, button_label: 64, media_bytes: { photo: 10485760, video: 52428800, animation: 52428800, document: 52428800 }, media_chunk: 4194304 },
  allowed_html_tags: ["a", "b", "blockquote", "code", "i", "pre", "s", "tg-emoji", "tg-spoiler", "u"], optional_block_syntax: "[[ текст с {age} ]]",
  emoji_library: { library: "/api/admin/telegram-emoji/library", catalog: "/api/admin/telegram-emoji/catalog", previews: "/api/admin/telegram-emoji/previews", media: "/api/admin/telegram-emoji/media/{emoji_id}" },
};
caps.gift_kinds = [{ id: "none", label: "Без подарка", available: true }, { id: "traffic", label: "Трафик (ГБ)", available: true }, { id: "days", label: "Дни подписки", available: true }, { id: "balance", label: "Баланс", available: true }, { id: "promo_discount", label: "Персональный промокод со скидкой", available: true }, { id: "wheel_spins", label: "Билеты колеса удачи", available: false }];

const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const tpl = (text, text_no_gift = "", extra = {}) => ({ text, text_no_gift, media: [], buttons: [], disable_notification: false, protect_content: false, disable_preview: true, ...extra });
const BDAY_RU = "🎂 <b>С днём рождения, {name}!</b>\n\nМы рады, что вы с нами.[[ Вам исполнилось {age}.]][[ Наш подарок: {gift}.]][[\nПромокод: <code>{promo_code}</code>]]";
const aud = (segments = ["none", "trial", "paid"], language = "any") => ({ segments, language, tariff_keys: [] });
const zero = { pending: 0, sending: 0, sent: 0, skipped: 0, failed: 0, blocked: 0 };

const state = {
  nextId: 6,
  config: { enabled: true, timezone: "Europe/Moscow", default_language: "ru", send_rate_per_second: 15, scan_rate_per_second: 20, batch_size: 400, catchup_hours: 24, max_send_attempts: 3, retry_minutes: 10, auto_backfill: false, scan_new_users: true, rescan_missing_days: 30, rescan_max_attempts: 3, blocked_retry_days: 90, user_entry_enabled: true, consent_text: "Я согласен(на) на хранение даты рождения для поздравлений и подарков.", user_attempts_per_hour: 5, debug_log: false },
  events: [
    { id: 1, name: "С днём рождения", kind: "birthday", enabled: true, params: { offset_days: 0 }, audience: aud(), send_time: "10:00", feb29: "feb28", gift: { kind: "promo_discount", percent: 20, valid_days: 14, applies_to: "subscription" }, gift_fallback: "send_without_gift",
      templates: { ru: tpl(BDAY_RU, "🎂 <b>С днём рождения, {name}!</b>\n\nМы рады, что вы с нами.", { buttons: [[{ kind: "section", label: "Выбрать тариф", url: "", section: "plans", style: "primary", icon_custom_emoji_id: "", icon_emoji: "🎁" }, { kind: "url", label: "Канал", url: "https://t.me/kirovpn", section: "home", style: "", icon_custom_emoji_id: "", icon_emoji: "" }]] }), en: tpl("🎂 <b>Happy birthday, {name}!</b>") },
      stats: { ...zero, sent: 1284, failed: 12, skipped: 41, blocked: 9, pending: 3, gifts: { granted: 1240, withheld: 33 }, promo: { issued: 1240, activated: 318 } } },
    { id: 2, name: "Годовщина с нами", kind: "registration_anniversary", enabled: true, params: { offset_days: 0, min_years: 1 }, audience: aud(["trial", "paid"]), send_time: "12:30", feb29: "mar1", gift: { kind: "wheel_spins", spins: 3, ignore_cap: false }, gift_fallback: "skip",
      templates: { ru: tpl("🎉 <b>{name}, нам уже {years} лет вместе!</b>[[ Дарим {gift}.]]", "🎉 <b>{name}, мы вместе {years} лет!</b>") }, stats: { ...zero, sent: 211, skipped: 4, gifts: { granted: 0, withheld: 215 }, promo: { issued: 0, activated: 0 } } },
    { id: 3, name: "Новый год", kind: "fixed_date", enabled: false, params: { date: "12-31" }, audience: aud(), send_time: "18:00", feb29: "feb28", gift: { kind: "days", days: 3 }, gift_fallback: "send_without_gift",
      templates: { ru: tpl("🎄 <b>С Новым годом, {name}!</b>[[ Подарок: {gift}.]]") }, stats: null },
    { id: 4, name: "Чёрная пятница", kind: "one_time", enabled: true, params: { date: "2026-11-27" }, audience: aud(["paid"], "ru"), send_time: "09:00", feb29: "feb28", gift: { kind: "balance", amount_minor: 15000 }, gift_fallback: "send_without_gift",
      templates: { ru: tpl("🔥 <b>{name}, чёрная пятница!</b> Дарим {gift}.") }, stats: { ...zero, pending: 4120 } },
    { id: 5, name: "Новости сервиса", kind: "manual", enabled: false, params: {}, audience: aud(["trial", "paid"]), send_time: "10:00", feb29: "feb28", gift: { kind: "traffic", gb: 5 }, gift_fallback: "send_without_gift",
      templates: { ru: tpl("👋 <b>{name}, есть новости!</b> Дарим {gift}."), en: tpl("") }, stats: { ...zero, sent: 530, failed: 2, gifts: { granted: 528, withheld: 4 }, promo: { issued: 0, activated: 0 } } },
  ],
  bd: [
    { user_id: 1042, telegram_id: 555000111, username: "ivan_k", first_name: "Иван", day: 15, month: 3, year: 1990, date: "15.03.1990", source: "telegram", locked: true, status: "found", attempts: 1, last_attempt_at: ago(3000), fetched_at: ago(3000) },
    { user_id: 1043, telegram_id: 555000112, username: "maria", first_name: "Мария", day: 2, month: 11, year: null, date: "02.11", source: "user", locked: true, status: "found", attempts: 0, last_attempt_at: null, fetched_at: ago(900) },
    { user_id: 1051, telegram_id: 555000120, username: null, first_name: "Алексей", day: 29, month: 2, year: 1996, date: "29.02.1996", source: "admin", locked: true, status: "found", attempts: 2, last_attempt_at: ago(120), fetched_at: ago(120) },
    { user_id: 1060, telegram_id: 555000130, username: "olga_t", first_name: "Ольга", day: null, month: null, year: null, date: "", source: null, locked: false, status: "absent", attempts: 2, last_attempt_at: ago(1500), fetched_at: null },
    { user_id: 1061, telegram_id: 555000131, username: "dmitry77", first_name: "Дмитрий", day: null, month: null, year: null, date: "", source: null, locked: false, status: "blocked", attempts: 1, last_attempt_at: ago(500), fetched_at: null },
    { user_id: 1070, telegram_id: 555000140, username: "sergey", first_name: "Сергей", day: 8, month: 12, year: 1985, date: "08.12.1985", source: "telegram", locked: true, status: "found", attempts: 1, last_attempt_at: ago(7000), fetched_at: ago(7000) },
    { user_id: 1071, telegram_id: 555000141, username: "anna_s", first_name: "Анна", day: null, month: null, year: null, date: "", source: null, locked: false, status: "cleared", attempts: 1, last_attempt_at: ago(60), fetched_at: null },
  ],
  backfill: { status: "running", dry_run: true, rate: 20, total: 18400, scanned: 7024, found: 61, absent: 6770, blocked: 142, errors: 3, rate_limited: 2, progress: 0.3817, hit_rate: 0.0087, cursor: 8123, started_at: ago(22), finished_at: null, updated_at: ago(0.3), paused_until: null, last_error: null, can_resume: false },
  deliveries: [
    { id: 9021, event_id: 1, event_name: "С днём рождения", user_id: 1042, username: "ivan_k", first_name: "Иван", period_key: "2026", status: "sent", reason: null, gift_kind: "promo_discount", gift_status: "granted", gift_note: null, promo_code: "PUSH-K7M2QX", promo_code_id: 77, message_id: 4410, attempts: 1, language: "ru", scheduled_for: ago(300), sent_at: ago(299), created_at: ago(310), updated_at: ago(299) },
    { id: 9020, event_id: 1, event_name: "С днём рождения", user_id: 1060, username: "olga_t", first_name: "Ольга", period_key: "2026", status: "skipped", reason: "user_blocked", gift_kind: "promo_discount", gift_status: "pending", gift_note: null, promo_code: null, promo_code_id: null, message_id: null, attempts: 1, language: "ru", scheduled_for: ago(305), sent_at: null, created_at: ago(310), updated_at: ago(300) },
    { id: 9019, event_id: 2, event_name: "Годовщина с нами", user_id: 1043, username: "maria", first_name: "Мария", period_key: "2026", status: "skipped", reason: "gift_unavailable", gift_kind: "wheel_spins", gift_status: "unavailable", gift_note: "wheel_not_installed", promo_code: null, promo_code_id: null, message_id: null, attempts: 1, language: "ru", scheduled_for: ago(400), sent_at: null, created_at: ago(410), updated_at: ago(400) },
    { id: 9018, event_id: 5, event_name: "Новости сервиса", user_id: 1051, username: null, first_name: "Алексей", period_key: "m:3fa9c1d2e4b50a17", status: "failed", reason: "send_error:Bad Request: message is too long", gift_kind: "traffic", gift_status: "granted", gift_note: null, promo_code: null, promo_code_id: null, message_id: null, attempts: 3, language: "ru", scheduled_for: ago(900), sent_at: null, created_at: ago(910), updated_at: ago(880) },
    { id: 9017, event_id: 5, event_name: "Новости сервиса", user_id: 1061, username: "dmitry77", first_name: "Дмитрий", period_key: "m:3fa9c1d2e4b50a17", status: "blocked", reason: "Forbidden: bot was blocked by the user", gift_kind: "traffic", gift_status: "granted", gift_note: null, promo_code: null, promo_code_id: null, message_id: null, attempts: 1, language: "ru", scheduled_for: ago(900), sent_at: null, created_at: ago(910), updated_at: ago(890) },
    { id: 9016, event_id: 4, event_name: "Чёрная пятница", user_id: 1070, username: "sergey", first_name: "Сергей", period_key: "2026-11-27", status: "pending", reason: "rate_limited", gift_kind: "balance", gift_status: "pending", gift_note: null, promo_code: null, promo_code_id: null, message_id: null, attempts: 1, language: "ru", scheduled_for: ago(1), sent_at: null, created_at: ago(2), updated_at: ago(1) },
    { id: 9015, event_id: 1, event_name: "С днём рождения", user_id: 1071, username: "anna_s", first_name: "Анна", period_key: "2026", status: "failed", reason: "interrupted_during_send", gift_kind: "promo_discount", gift_status: "granted", gift_note: null, promo_code: "PUSH-ZZ91PL", promo_code_id: 78, message_id: null, attempts: 1, language: "ru", scheduled_for: ago(1500), sent_at: null, created_at: ago(1510), updated_at: ago(1490) },
  ],
  logs: [
    { id: 55, at: ago(2), level: "info", event: "backfill_started", user_id: null, path: "POST /api/admin/kiro-push/backfill/start", status: 200, detail: { dry_run: true, rate: 20, resume: false, total: 18400 } },
    { id: 54, at: ago(20), level: "warn", event: "request_failed", user_id: null, path: "PUT /api/admin/kiro-push/events/2", status: 409, detail: { error_code: "wheel_not_installed" } },
    { id: 53, at: ago(55), level: "error", event: "delivery_crashed", user_id: 1051, path: null, status: null, detail: { delivery: 9018, error: "TimeoutError" } },
    { id: 52, at: ago(190), level: "info", event: "admin_event_run", user_id: null, path: "POST /api/admin/kiro-push/events/5/run", status: 200, detail: { event_id: 5, batch_id: "3fa9c1d2e4b50a17", matched: 532, queued: 532 } },
    { id: 51, at: ago(600), level: "info", event: "birthdate_set_by_user", user_id: 1043, path: "POST /api/plugins/kiro-push/birthdate", status: 200, detail: { has_year: false } },
  ],
};

function totals() {
  const t = { ...zero };
  for (const e of state.events) if (e.stats) for (const k of Object.keys(zero)) t[k] += e.stats[k] || 0;
  return t;
}
function upcoming() {
  const days = [];
  const base = new Date();
  const counts = [0, 0, 2, 0, 5, 1, 0, 0, 3, 0, 0, 8, 1, 0, 0, 2, 4, 0, 0, 1, 0, 6, 0, 0, 2, 1, 0, 0, 3, 7];
  for (let i = 0; i < 30; i += 1) days.push({ date: new Date(base.getTime() + i * 86400000).toISOString().slice(0, 10), count: counts[i] });
  return { days, total: days.reduce((a, x) => a + x.count, 0) };
}
function overview() {
  return {
    version: "0.1.0", config: state.config, wheel_installed: false, bot_available: true, currency: "RUB",
    birthdates: { eligible_users: 18400, collected: 1480, by_source: { telegram: 61, user: 1380, admin: 39 }, hidden: 9210, blocked: 612, errors: 3, cleared: 5, unknown: 7090, coverage: 0.0804, with_year: 1120 },
    upcoming: upcoming(), deliveries: { total: totals(), events: {}, promo: { issued: 1240, activated: 318 } }, backfill: state.backfill,
    events_total: state.events.length, capabilities: caps,
  };
}
const renderText = (text, withGift) => {
  const vals = Object.fromEntries(PH.map(([n, , s]) => [n, s]));
  if (!withGift) { vals.gift = ""; vals.promo_code = ""; vals.promo_discount = ""; }
  return text
    .replace(/\[\[(.*?)\]\]/gs, (_, inner) => ([...inner.matchAll(/\{(\w+)\}/g)].some((m) => !vals[m[1]]) ? "" : inner))
    .replace(/\{(\w+)\}/g, (_, n) => vals[n] ?? "");
};
function preview(body) {
  const ev = body.event;
  const t = ev.templates[body.language];
  if (!t) return [404, { ok: false, error: "template_missing" }];
  const errors = [];
  const warnings = [];
  const unknown = [...String(t.text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((n) => !PH.some((p) => p[0] === n));
  if (unknown.length) errors.push(`[${body.language}] Неизвестные подстановки {${unknown[0]}}`);
  if (/\{age\}/.test(t.text.replace(/\[\[.*?\]\]/gs, ""))) warnings.push("{age} вне блока [[ ]]: у пользователей без года рождения подстановка будет пустой");
  if (errors.length) return [200, { ok: true, rendered: null, errors, warnings, gift_text: "" }];
  const withGift = body.with_gift !== false && ev.gift.kind !== "none";
  const text = renderText(withGift || !t.text_no_gift ? t.text : t.text_no_gift, withGift);
  const plain = text.replace(/<[^>]+>/g, "");
  return [200, { ok: true, errors, warnings, gift_text: withGift ? "промокод со скидкой 20%" : "", rendered: { text, media: t.media, buttons: t.buttons.map((r) => r.map((b) => ({ ...b }))), disable_notification: t.disable_notification, protect_content: t.protect_content, disable_preview: t.disable_preview, language: body.language, used_no_gift: !withGift && !!t.text_no_gift, visible_length: plain.length, warnings: [] } }];
}
const SETS = [{ name: "kiro_set", title: "Kiro emoji", count: 12 }, { name: "party", title: "Party", count: 8 }];
const EMOJI_FB = ["🎁", "🎂", "🎉", "⭐", "🔥", "💎", "🚀", "❤️", "🎈", "🏆", "💙", "✨"];

export async function mockFetch(input, init = {}) {
  const url = new URL(typeof input === "string" ? input : input.url, location.origin);
  const method = (init.method || "GET").toUpperCase();
  const path = url.pathname;
  const body = init.body && typeof init.body === "string" ? JSON.parse(init.body) : null;
  await new Promise((r) => setTimeout(r, 40));
  const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  if (path === "/api/admin/telegram-emoji/library") return json({ ok: true, library: { schema_version: 1, sets: ["kiro_set"], manual_ids: [] }, revision: "x".repeat(64), sets: SETS.map((s) => ({ ...s, state: "ready" })) });
  if (path === "/api/admin/telegram-emoji/catalog") return json({ ok: true, items: EMOJI_FB.map((fb, i) => ({ id: String(5368324170671202286n + BigInt(i)), fallback: fb, set_name: "kiro_set", thumbnail_url: null, format: "static" })), total: 12, offset: 0, limit: 60 });
  const P = "/api/admin/kiro-push";
  if (!path.startsWith(P)) return json({ ok: false, error: "not_found" }, 404);
  const r = path.slice(P.length);
  if (r === "/overview") return json({ ok: true, ...overview() });
  if (r === "/config" && method === "PUT") { Object.assign(state.config, body); return json({ ok: true, config: state.config }); }
  if (r === "/events" && method === "GET") return json({ ok: true, events: state.events.map((e) => ({ ...e, kind_label: e.kind })) });
  if (r === "/events" && method === "POST") { const ev = { ...body, id: state.nextId++, stats: null }; state.events.push(ev); return json({ ok: true, id: ev.id, warnings: [] }, 201); }
  let m = /^\/events\/(\d+)(\/\w+)?$/.exec(r);
  if (m) {
    const ev = state.events.find((e) => e.id === Number(m[1]));
    if (!ev) return json({ ok: false, error: "event_not_found" }, 404);
    if (!m[2] && method === "PUT") { Object.assign(ev, body); return json({ ok: true, id: ev.id, warnings: [] }); }
    if (!m[2] && method === "DELETE") { state.events = state.events.filter((e) => e !== ev); return json({ ok: true }); }
    if (m[2] === "/test") return json({ ok: true, message_id: 1 });
    if (m[2] === "/run") return body.dry_run ? json({ ok: true, dry_run: true, matched: 532, sample: ["@ivan_k", "@maria", "Алексей"] }) : json({ ok: true, dry_run: false, batch_id: body.batch_id, queued: 532, already: 0, matched: 532, sample: [] });
  }
  if (r === "/preview") { const [s, d] = preview(body); return json(d, s); }
  if (r === "/media" && method === "GET") return json({ ok: true, media: [{ id: "a".repeat(32), kind: "document", filename: "guide.pdf", content_type: "application/pdf", size: 240000, created_at: ago(900) }] });
  if (r === "/birthdates" && method === "GET") {
    const q = (url.searchParams.get("q") || "").toLowerCase();
    let items = state.bd.filter((x) => !q || (x.username || "").toLowerCase().includes(q) || (x.first_name || "").toLowerCase().includes(q));
    const status = url.searchParams.get("status") || (q ? "all" : "set");
    if (status === "set") items = items.filter((x) => x.day != null); else if (status !== "all") items = items.filter((x) => x.status === status);
    return json({ ok: true, total: items.length, page: 1, per_page: 50, items });
  }
  if (r === "/birthdates/import") return json({ ok: true, saved: 2, skipped: 1, errors: 1, results: [{ user: "@a", status: "saved" }, { user: "@b", status: "skipped", reason: "already_set" }, { user: "@zzz", status: "error", error: "user_not_found" }] });
  m = /^\/birthdates\/(.+)$/.exec(r);
  if (m && method === "PUT") return json({ ok: true, user_id: 1, ...body, source: "admin", locked: true });
  if (m && method === "DELETE") return json({ ok: true });
  if (r === "/backfill/status") return json({ ok: true, backfill: state.backfill });
  if (r === "/backfill/start") { state.backfill = { ...state.backfill, status: "running", dry_run: !!body.dry_run }; return json({ ok: true, backfill: state.backfill }); }
  if (r === "/backfill/stop") { state.backfill = { ...state.backfill, status: "stopped", can_resume: true }; return json({ ok: true, backfill: state.backfill }); }
  if (r === "/deliveries") { const st = url.searchParams.get("status"); const items = state.deliveries.filter((d) => !st || d.status === st); return json({ ok: true, total: items.length, page: 1, per_page: 50, items }); }
  if (r === "/stats") { const o = overview(); return json({ ok: true, birthdates: o.birthdates, upcoming: o.upcoming, deliveries: o.deliveries }); }
  if (r === "/logs") return json({ ok: true, debug: false, counts: { info: 3, warn: 1, error: 1 }, max_rows: 5000, rows: state.logs });
  if (r === "/logs/clear") return json({ ok: true, removed: state.logs.length });
  return json({ ok: false, error: "not_found" }, 404);
}
