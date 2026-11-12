// Pure-logic checks for frontend/admin/logic.js. Prints {passed, failed:[...], errors:[...], reasons:[...], configKeys:[...]} as JSON.
import { pathToFileURL } from "node:url";
import path from "node:path";
const root = path.resolve(process.argv[2]);
const L = await import(pathToFileURL(path.join(root, "frontend/admin/logic.js")).href);
let passed = 0;
const failed = [];
const eq = (a, b, name) => { if (JSON.stringify(a) === JSON.stringify(b)) passed += 1; else failed.push(`${name}: got ${JSON.stringify(a)} expected ${JSON.stringify(b)}`); };

// money
eq(L.majorToMinor("150"), 15000, "major 150");
eq(L.majorToMinor("99,5"), 9950, "major 99,5");
eq(L.majorToMinor("0.07"), 7, "major 0.07");
eq(L.majorToMinor("1.234"), null, "major 3 decimals");
eq(L.majorToMinor("abc"), null, "major text");
eq(L.majorToMinor("19.99"), 1999, "major float trap");
eq(L.minorToMajor(15050), "150.5", "minor 15050");
eq(L.minorToMajor(15000), "150", "minor 15000");
eq(L.minorToMajor(7), "0.07", "minor 7");
eq(L.currencySymbol("RUB"), "₽", "symbol RUB");
eq(L.currencySymbol(""), "₽", "symbol default");
eq(L.currencySymbol("₸"), "₸", "symbol passthrough");

// summaries
eq(L.scheduleSummary({ kind: "birthday", send_time: "10:00", params: { offset_days: 0 } }), "ежегодно в 10:00 · день рождения", "schedule birthday");
eq(L.scheduleSummary({ kind: "birthday", send_time: "09:30", params: { offset_days: -1 } }), "ежегодно в 09:30 · день рождения, за 1 день до", "schedule birthday -1");
eq(L.scheduleSummary({ kind: "birthday", send_time: "09:30", params: { offset_days: 3 } }), "ежегодно в 09:30 · день рождения, через 3 дня после", "schedule birthday +3");
eq(L.scheduleSummary({ kind: "fixed_date", send_time: "18:00", params: { date: "12-31" } }), "каждый год 31 декабря в 18:00 · праздник", "schedule fixed");
eq(L.scheduleSummary({ kind: "one_time", send_time: "09:00", params: { date: "2026-11-27" } }), "один раз 27 ноября 2026 в 09:00", "schedule one_time");
eq(L.scheduleSummary({ kind: "manual", params: {} }).startsWith("только по кнопке"), true, "schedule manual");
eq(L.audienceSummary({ segments: ["none", "trial", "paid"], language: "any", tariff_keys: [] }), "все пользователи", "audience all");
eq(L.audienceSummary({ segments: ["paid"], language: "ru", tariff_keys: ["m1"] }), "Платная, язык: русский, тарифы: m1", "audience paid");
eq(L.audienceSummary({ segments: ["none", "trial", "paid"], language: "any", tariff_keys: [], skip_blocked: true }), "все пользователи", "audience: skip_blocked on adds nothing");
eq(L.audienceSummary({ segments: ["paid"], language: "any", tariff_keys: [], skip_blocked: false }), "Платная, включая заблокировавших бота", "audience: skip_blocked off is mentioned");
eq(L.giftSummary({ kind: "balance", amount_minor: 15000 }, "RUB"), "+150 ₽ на баланс", "gift balance");
eq(L.giftSummary({ kind: "days", days: 1 }), "+1 день", "gift days 1");
eq(L.giftSummary({ kind: "promo_discount", percent: 20, valid_days: 14 }), "промокод −20% на 14 дн.", "gift promo");
eq(L.giftSummary({ kind: "none" }), "без подарка", "gift none");

// Telegram HTML parser: whitelist, no raw html survives
const tree = L.parseTelegramHtml('<b>hi</b> <script>alert(1)</script><a href="javascript:alert(1)" onclick="x()">bad</a> <a href="https://x.io">ok</a> <tg-emoji emoji-id="123" onclick="x">🎁</tg-emoji> <img src=x onerror=y> &lt;b&gt;');
const dumpNode = (n) => (n.t === "text" ? n.v : `<${n.tag}${Object.entries(n.attrs).map(([k, v]) => ` ${k}="${v}"`).join("")}>${n.children.map(dumpNode).join("")}</${n.tag}>`);
const dump = (n) => n.children.map(dumpNode).join("");
const flat = dump(tree);
eq(flat.includes("onclick") || flat.includes("onerror") || flat.includes("javascript:"), false, "parser strips handlers and js urls");
eq(flat.includes('<a href="https://x.io">ok</a>'), true, "parser keeps https link");
eq(flat.includes('<tg-emoji emoji-id="123">🎁</tg-emoji>'), true, "parser keeps emoji id only");
eq(flat.includes("<script>"), false, "parser drops script tag");
eq(flat.includes("&lt;b&gt;"), false, "entities are decoded to text (rendered via textContent)");
eq(L.visibleLength("<b>ab</b> &amp; c"), 6, "visibleLength");
eq(dump(L.parseTelegramHtml("<b>unclosed <i>x")), "<b>unclosed <i>x</i></b>", "parser closes tags");
eq(dump(L.parseTelegramHtml('<span class="tg-spoiler">s</span>')), "<tg-spoiler>s</tg-spoiler>", "span spoiler");
eq(dump(L.parseTelegramHtml('<span class="x">s</span>')), "s", "other span dropped");

// toolbar ops
eq(L.wrapSelection("hello world", 6, 11, "<b>", "</b>"), { value: "hello <b>world</b>", start: 9, end: 14 }, "wrap selection");
eq(L.wrapSelection("ab", 1, 1, "<i>", "</i>").value, "a<i>текст</i>b", "wrap empty uses fallback");
eq(L.insertText("ab", 1, 1, "{name}"), { value: "a{name}b", start: 7, end: 7 }, "insert text");
eq(L.emojiTag("5368324170671202286", "🎁"), '<tg-emoji emoji-id="5368324170671202286">🎁</tg-emoji>', "emoji tag");

// import parsing / birthdate validation
eq(L.parseImport("@a;15;3;1990\n123;1;12").items, [{ user: "@a", day: 15, month: 3, year: 1990 }, { user: "123", day: 1, month: 12 }], "import lines");
eq(L.parseImport("@a;15.03.1990").items, [{ user: "@a", day: 15, month: 3, year: 1990 }], "import dotted");
eq(L.parseImport("@a;31;2;1990").errors.length, 1, "import bad day");
eq(L.parseImport('[{"user":"@x","day":2,"month":5}]').items, [{ user: "@x", day: 2, month: 5 }], "import json");
eq(L.validateBirthdate(29, 2, ""), "", "29 feb ok");
eq(L.validateBirthdate(30, 2, "") !== "", true, "30 feb bad");
eq(L.validateBirthdate(1, 1, 2100) !== "", true, "future year bad");

// draft <-> payload
const draft = L.PRESETS[0].build();
const payload = L.toPayload(draft);
eq(payload.kind, "birthday", "preset kind");
eq(payload.gift, { kind: "promo_discount", percent: 20, valid_days: 14, applies_to: "subscription" }, "preset gift payload");
eq(Object.keys(payload.templates), ["ru", "en"], "templates both langs");
eq(L.toPayload({ ...draft, gift: { ...draft.gift, kind: "balance", amount: "150,50" } }).gift, { kind: "balance", amount_minor: 15050 }, "balance conversion");
const back = L.fromEvent({ id: 7, name: "x", kind: "birthday", enabled: true, params: { offset_days: -1 }, audience: { segments: ["paid"], language: "ru", tariff_keys: ["a", "b"] }, send_time: "11:00", feb29: "mar1", gift: { kind: "balance", amount_minor: 9950 }, gift_fallback: "skip", templates: { ru: { text: "t" } } });
eq([back.gift.amount, back.audience.tariff_keys, back.templates.ru.text, back.templates.en.text], ["99.5", "a, b", "t", ""], "fromEvent");
eq(L.toPayload(back).audience.tariff_keys, ["a", "b"], "tariff keys round trip");
eq(payload.audience.skip_blocked, true, "preset payload: skip_blocked defaults to true");
eq(L.blankDraft("manual").audience.skip_blocked, true, "blank draft: skip_blocked on");
eq(L.fromEvent({ id: 8, name: "old", kind: "manual", audience: { segments: ["paid"], language: "any", tariff_keys: [] }, templates: {} }).audience.skip_blocked, true, "fromEvent: missing key = on");
const noSkip = L.fromEvent({ id: 9, name: "x", kind: "manual", audience: { segments: ["paid"], language: "any", tariff_keys: [], skip_blocked: false }, templates: {} });
eq([noSkip.audience.skip_blocked, L.toPayload(noSkip).audience.skip_blocked], [false, false], "skip_blocked=false round trip");
eq(L.toPayload({ ...noSkip, audience: { ...noSkip.audience, skip_blocked: true } }).audience.skip_blocked, true, "skip_blocked=true to payload");
eq(L.PRESETS.every((p) => L.toPayload(p.build()).audience.skip_blocked === true), true, "all presets send skip_blocked=true");
eq(L.toPayload(draft, { forPreview: true }).enabled, false, "preview payload disabled");
eq(L.draftProblems({ ...L.blankDraft("fixed_date"), name: "x" }).map((p) => p[0]), ["params"], "problems: fixed date missing");
eq(L.splitIssues(["[ru] a", "[en] b", "c"]), { ru: ["a"], en: ["b"], general: ["c"] }, "split issues");
eq(L.newBatchId().length, 16, "batch id");
eq(L.PRESETS.map((p) => L.toPayload(p.build()).kind), ["birthday", "registration_anniversary", "fixed_date", "one_time", "manual"], "presets cover kinds");

// draft autosave + recovery helpers (storage is a stub; a blocked storage must never throw)
const memStore = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); }, _m: m }; };
const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
const T0 = 1_800_000_000_000;
const edited = { ...L.PRESETS[0].build(), name: "Анонс", id: 5 };
const store = memStore();
eq(L.saveDraft(store, { eventId: 5, name: edited.name, draft: edited }, T0), "saved", "draft: saved");
const rec = L.loadDraft(store, T0 + 1000);
eq([rec.eventId, rec.name, rec.savedAt, rec.draft.kind, rec.draft.gift.kind, rec.draft.templates.ru.text === edited.templates.ru.text], [5, "Анонс", T0, "birthday", "promo_discount", true], "draft: round trip");
eq(JSON.parse(store.getItem(L.DRAFT_KEY)).v, 1, "draft: versioned record");
eq(L.loadDraft(store, T0 + L.DRAFT_TTL_MS + 1), null, "draft: expires after 7 days");
eq(store.getItem(L.DRAFT_KEY), null, "draft: expired record removed");
L.saveDraft(store, { eventId: null, name: "N", draft: edited }, T0);
eq(L.loadDraft(store, T0 + L.DRAFT_TTL_MS - 5)?.eventId, null, "draft: new event has eventId null");
eq(L.loadDraft(store, T0 - 7_200_000), null, "draft: savedAt in the far future is rejected");
store.setItem(L.DRAFT_KEY, "{not json");
eq(L.loadDraft(store, T0), null, "draft: corrupt JSON ignored");
eq(store.getItem(L.DRAFT_KEY), null, "draft: corrupt record removed");
store.setItem(L.DRAFT_KEY, JSON.stringify({ v: 2, scope: "event-editor", savedAt: T0, draft: edited }));
eq(L.loadDraft(store, T0), null, "draft: unknown version ignored");
store.setItem(L.DRAFT_KEY, JSON.stringify({ v: 1, scope: "event-editor", savedAt: T0, draft: { kind: "nope", templates: {} } }));
eq(L.loadDraft(store, T0), null, "draft: unknown kind ignored");
const huge = { ...edited, templates: { ...edited.templates, ru: { ...edited.templates.ru, text: "я".repeat(L.DRAFT_MAX_BYTES) } } };
eq(L.saveDraft(store, { eventId: 1, name: "big", draft: huge }, T0), "too_large", "draft: >300 KB is not stored");
eq(L.serializeDraft({ draft: huge }), null, "draft: serialize size guard");
const partial = L.normalizeDraft({ kind: "manual", name: "p", templates: { ru: { text: "x" } }, audience: { language: "ru" } });
eq([partial.audience.segments, partial.audience.language, partial.templates.en.media, partial.templates.ru.disable_preview, partial.gift.kind], [["none", "trial", "paid"], "ru", [], true, "none"], "draft: normalize fills defaults");
eq(L.saveDraft(blocked, { draft: edited }, T0), "error", "draft: blocked storage -> error, no throw");
eq(L.loadDraft(blocked, T0), null, "draft: blocked storage -> null");
eq(L.loadDraft(null, T0), null, "draft: no storage -> null");
L.clearDraft(blocked); L.clearDraft(null);
eq(L.loadDraft(memStore(), T0), null, "draft: empty storage");
L.saveDraft(store, { draft: edited }, T0); L.clearDraft(store);
eq(store.getItem(L.DRAFT_KEY), null, "draft: cleared");
// tab + editor-open flag
const ss = memStore();
const tabs = ["overview", "events", "birthdates", "journal"];
eq(L.loadTab(ss, tabs, "overview"), "overview", "tab: default");
L.saveTab(ss, "events");
eq(L.loadTab(ss, tabs, "overview"), "events", "tab: remembered");
ss.setItem(L.TAB_KEY, "evil<script>");
eq(L.loadTab(ss, tabs, "journal"), "journal", "tab: unknown value -> fallback");
eq(L.loadTab(blocked, tabs, "journal"), "journal", "tab: blocked storage -> fallback");
eq(L.saveTab(blocked, "events"), false, "tab: blocked storage save is a no-op");
eq([L.getFlag(ss, L.EDITOR_OPEN_KEY), L.setFlag(ss, L.EDITOR_OPEN_KEY, true), L.getFlag(ss, L.EDITOR_OPEN_KEY)], [false, true, true], "flag: set");
L.setFlag(ss, L.EDITOR_OPEN_KEY, false);
eq(L.getFlag(ss, L.EDITOR_OPEN_KEY), false, "flag: cleared");
eq(L.getFlag(blocked, L.EDITOR_OPEN_KEY), false, "flag: blocked storage");
// client diagnostics payload
eq(L.cleanClientDetail({ nav_type: "reload", ua: "u".repeat(300), message: "m ".repeat(200), in_telegram: true, editor_open: false, tab: "events", ms_since_mount: 1234.7, visibility: "visible", last_pagehide: "not" }),
  { nav_type: "reload", ua: "u".repeat(160), message: "m ".repeat(200).trim().slice(0, 200), in_telegram: true, editor_open: false, tab: "events", ms_since_mount: 1235, visibility: "visible", last_pagehide: "not" }, "client detail: clipped allowlist");
eq(L.cleanClientDetail({ draft: "secret", name: "x", message: { a: 1 }, in_telegram: "yes", ms_since_mount: NaN }), {}, "client detail: forbidden keys / wrong types dropped");
eq(L.CLIENT_EVENTS, ["page_reload_detected", "editor_restored", "js_error", "unhandled_rejection"], "client events");
eq(Object.keys(L.LOG_EVENTS).filter((k) => k.startsWith("client_")).sort(), L.CLIENT_EVENTS.map((e) => `client_${e}`).sort(), "journal titles for every client event");

// reasons
eq(L.reasonText("send_error:Bad Request"), "Ошибка отправки: Bad Request", "reason prefix");
eq(L.reasonText("user_banned"), "Пользователь забанен", "reason known");
eq(L.reasonText(""), "", "reason empty");

console.log(JSON.stringify({ passed, failed, errors: Object.keys(L.ERRORS), reasons: Object.keys(L.REASONS), configKeys: L.CONFIG_KEYS }));
process.exit(failed.length ? 1 : 0);
