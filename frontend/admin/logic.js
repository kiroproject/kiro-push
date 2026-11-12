// KIRO push: pure admin logic (no DOM, no network). Imported by the UI modules and by node-based tests.
// Money conversion, schedule / audience / gift summaries, error and reason dictionaries, Telegram-HTML parser
// (the preview renders only a whitelist), toolbar text operations, import parsing, draft <-> API payload.

// ----------------------------------------------------------------------------------------------- basics
export const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const num = (v) => Number(v || 0).toLocaleString("ru-RU");
export const pct = (v, digits = 1) => `${(Number(v || 0) * 100).toLocaleString("ru-RU", { maximumFractionDigits: digits })}%`;
export const clone = (v) => JSON.parse(JSON.stringify(v));

export function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export const fmtSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} МБ` : `${Math.max(1, Math.round((b || 0) / 1024))} КБ`);

export function debounce(fn, ms) {
  let t = 0;
  const wrapped = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
}

// ----------------------------------------------------------------------------------------------- money
const CURRENCY_SYMBOLS = { RUB: "₽", USD: "$", EUR: "€", UAH: "₴", KZT: "₸", BYN: "Br", GBP: "£", TRY: "₺" };

export function currencySymbol(code) {
  const c = String(code || "").trim();
  if (!c) return "₽";
  return CURRENCY_SYMBOLS[c.toUpperCase()] || c;
}

/** "150", "150,5", "150.50" -> minor units (15000, 15050, 15050). null if not a valid amount with up to 2 decimals. */
export function majorToMinor(value) {
  const s = String(value ?? "").trim().replace(/\s/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const [whole, frac = ""] = s.split(".");
  return parseInt(whole, 10) * 100 + parseInt((frac + "00").slice(0, 2), 10);
}

/** 15050 -> "150.5", 15000 -> "150" (string for an input field). */
export function minorToMajor(minor) {
  const n = Math.round(Number(minor) || 0);
  const whole = Math.floor(n / 100);
  const frac = String(n % 100).padStart(2, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : String(whole);
}

export function moneyText(minor, currency) {
  const major = minorToMajor(minor).replace(".", ",");
  return `${major} ${currencySymbol(currency)}`;
}

// ----------------------------------------------------------------------------------------------- dictionaries
export const KIND_RU = {
  birthday: "День рождения",
  registration_anniversary: "Годовщина регистрации",
  fixed_date: "Праздник (каждый год)",
  one_time: "Разовое уведомление",
  manual: "Ручной запуск",
};
export const KIND_COLOR = { birthday: "#ec4899", registration_anniversary: "#8b5cf6", fixed_date: "#f59e0b", one_time: "#3b82f6", manual: "#6b7280" };
export const RUNNABLE_KINDS = ["manual", "one_time", "fixed_date"];

export const SEGMENT_RU = { none: "Без подписки", trial: "Пробная", paid: "Платная" };
export const SECTIONS_RU = { home: "Главная", plans: "Тарифы", install: "Подключение", trial: "Пробный период", invite: "Бонусы", devices: "Устройства", support: "Поддержка", settings: "Настройки" };
export const MEDIA_RU = { photo: "Фото", video: "Видео", animation: "GIF", document: "Файл" };
export const STYLE_RU = { "": "Обычная", primary: "Синяя", success: "Зелёная", danger: "Красная" };
export const BUTTON_KIND_RU = { url: "Ссылка", web_app: "Mini App (https)", section: "Раздел магазина" };
export const MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
export const MONTHS_NOM_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

export const DELIVERY_STATUS = {
  pending: ["Ожидает", "#8b93a3"],
  sending: ["Отправляется", "#3b82f6"],
  sent: ["Отправлено", "#22c55e"],
  skipped: ["Пропущено", "#f59e0b"],
  failed: ["Ошибка", "#e5484d"],
  blocked: ["Заблокировал бота", "#a855f7"],
};
export const GIFT_STATUS = {
  pending: ["ожидает выдачи", "#8b93a3"],
  granted: ["выдан", "#22c55e"],
  not_eligible: ["не положен", "#f59e0b"],
  unavailable: ["недоступен", "#f59e0b"],
  failed: ["не выдан (ошибка)", "#e5484d"],
};
export const GIFT_KIND_RU = {
  none: "Без подарка",
  traffic: "Дополнительный трафик",
  days: "Дополнительные дни",
  balance: "Деньги на баланс",
  promo_discount: "Промокод на скидку",
  wheel_spins: "Крутки колеса",
};
export const BD_STATUS = {
  found: ["Дата есть", "#22c55e"],
  absent: ["Скрыта", "#8b93a3"],
  blocked: ["Бот заблокирован", "#a855f7"],
  error: ["Ошибка", "#e5484d"],
  cleared: ["Удалена", "#f59e0b"],
  unknown: ["Не проверялся", "#6b7280"],
};
export const SOURCE_RU = { telegram: ["Telegram", "#3b82f6"], user: ["Сам пользователь", "#22c55e"], admin: ["Админ", "#f59e0b"] };
export const BACKFILL_STATUS = { idle: "Не запускалось", running: "Идёт", stopped: "Остановлено", done: "Завершено" };
export const LEVEL_RU = { debug: ["debug", "#8b93a3"], info: ["info", "#3b82f6"], warn: ["warn", "#f59e0b"], error: ["error", "#e5484d"] };

// Settings form: key -> [label, help, type, min, max]. Bounds repeat the backend's CONFIG_BOUNDS.
export const CONFIG_FIELDS = [
  { group: "Основное", items: [
    ["enabled", "Плагин включён", "Пока выключен, задание ничего не отправляет и не сканирует, карточка в кабинете скрыта.", "bool"],
    ["timezone", "Часовой пояс (IANA)", "Время отправки событий задаётся по этим часам, например Europe/Moscow.", "text"],
    ["default_language", "Язык по умолчанию", "Шаблон этого языка используется, если нет шаблона на языке пользователя.", "lang"],
  ] },
  { group: "Скорость и повторы", items: [
    ["send_rate_per_second", "Скорость отправки, сообщений в секунду", "Telegram допускает около 30 в секунду на весь бот; оставьте запас.", "int", 1, 28],
    ["batch_size", "Доставок за один запуск задания", "Задание запускается раз в минуту и берёт не больше такого числа доставок.", "int", 10, 5000],
    ["catchup_hours", "Догонять пропущенное, часов", "Если сервер простоял, пропущенное поздравление отправится в пределах этого окна.", "int", 1, 168],
    ["max_send_attempts", "Попыток отправки", "После этого доставка получает статус «ошибка».", "int", 1, 10],
    ["retry_minutes", "Пауза между попытками, минут", "Через сколько повторять неудачную отправку.", "int", 1, 1440],
  ] },
  { group: "Даты рождения и сканирование", items: [
    ["scan_rate_per_second", "Скорость сканирования Telegram, запросов в секунду", "Делится с отправкой общей квотой бота.", "int", 1, 28],
    ["auto_backfill", "Запустить сканирование один раз автоматически", "Запустится при первом включении плагина; повторно не запускается.", "bool"],
    ["scan_new_users", "Проверять новых пользователей", "Дата рождения запрашивается у Telegram после регистрации.", "bool"],
    ["rescan_missing_days", "Повторная проверка скрытых дат, дней", "Через сколько дней снова спрашивать Telegram у тех, у кого дата скрыта.", "int", 1, 365],
    ["rescan_max_attempts", "Максимум повторных проверок", "Сколько раз всего спрашивать Telegram у одного пользователя.", "int", 1, 20],
    ["blocked_retry_days", "Заблокировал бота (по неудачной отправке), дней до повтора", "Если отправка не удалась из-за блокировки бота, пользователь пропускается столько дней, потом мы пробуем снова. Дополнительно всегда учитывается статус блокировки из самого Minishop. Работает, пока у события включено «Не отправлять заблокировавшим».", "int", 1, 3650],
  ] },
  { group: "Ввод даты пользователем", items: [
    ["user_entry_enabled", "Разрешить вводить дату в кабинете", "Если Telegram дату не отдаёт, пользователь может указать её сам один раз.", "bool"],
    ["consent_text", "Текст согласия", "Показывается пользователю рядом с галочкой согласия на обработку даты.", "textarea"],
    ["user_attempts_per_hour", "Попыток ввода в час", "Ограничение на одного пользователя.", "int", 1, 50],
  ] },
  { group: "Диагностика", items: [
    ["debug_log", "Подробный журнал", "Пишет в журнал плагина отладочные записи. Включайте на время расследования.", "bool"],
  ] },
];
export const CONFIG_KEYS = CONFIG_FIELDS.flatMap((g) => g.items.map((i) => i[0]));

function configErrors() {
  const out = {};
  for (const g of CONFIG_FIELDS) for (const [key, label, , type, min, max] of g.items) {
    out[`invalid_${key}`] = type === "int" ? `${label}: целое число от ${min} до ${max}` : `Некорректное значение: ${label}`;
  }
  return out;
}

export const ERRORS = {
  ...configErrors(),
  // generic / session
  forbidden: "Нет прав администратора",
  csrf_failed: "Сессия устарела, обновите страницу",
  unauthorized: "Нужно войти заново",
  not_found: "Не найдено",
  not_ready: "Плагин ещё запускается, повторите через минуту",
  internal_error: "Внутренняя ошибка плагина, подробности в журнале «События плагина»",
  disabled: "Функция выключена в настройках",
  rate_limited: "Слишком много попыток, подождите",
  invalid_json: "Некорректный запрос",
  invalid_page: "Некорректный номер страницы",
  invalid_filter: "Некорректный фильтр",
  invalid_status: "Некорректный статус в фильтре",
  invalid_month: "Некорректный месяц в фильтре",
  invalid_item: "Строка импорта не разобрана",
  // settings
  invalid_timezone: "Неизвестный часовой пояс. Нужно имя из базы IANA, например Europe/Moscow",
  invalid_default_language: "Язык по умолчанию: ru или en",
  invalid_consent_text: "Текст согласия: от 1 до 500 знаков",
  // events
  event_not_found: "Событие не найдено (возможно, удалено)",
  event_required: "Нет данных события",
  invalid_event: "В событии есть ошибки, они перечислены ниже",
  name_required: "Укажите название события",
  invalid_kind: "Неизвестный вид события",
  invalid_send_time: "Время отправки: ЧЧ:ММ, например 10:00",
  invalid_offset_days: "Смещение в днях: от −30 до 30",
  invalid_min_years: "Минимум лет с регистрации: от 1 до 50",
  invalid_date: "Некорректная дата",
  invalid_feb29: "Выберите правило для 29 февраля",
  invalid_audience: "Некорректная аудитория",
  audience_empty: "Выберите хотя бы одну категорию получателей",
  invalid_audience_language: "Язык аудитории: любой, русский или английский",
  invalid_gift_kind: "Неизвестный вид подарка",
  invalid_gift_fallback: "Выберите, что делать, если подарок выдать нельзя",
  invalid_gift_gb: "Трафик: от 0,1 до 10 000 ГБ",
  invalid_gift_days: "Дни: целое число от 1 до 3650",
  invalid_gift_amount: "Сумма: от 0,01 до 1 000 000",
  invalid_gift_percent: "Скидка: от 1 до 100 %",
  invalid_gift_valid_days: "Срок действия промокода: от 1 до 365 дней",
  invalid_gift_applies_to: "Выберите, на что действует скидка",
  invalid_gift_spins: "Крутки колеса: целое число от 1 до 100",
  wheel_not_installed: "Плагин «Колесо удачи» не установлен, подарок «крутки колеса» недоступен",
  invalid_language: "Язык шаблона: ru или en",
  template_missing: "Для выбранного языка нет шаблона. Заполните текст или медиа",
  templates_required: "У события нет шаблонов. Заполните хотя бы один язык и сохраните",
  invalid_media: "Некорректное медиа",
  invalid_media_url: "Ссылка на медиа должна начинаться с https://",
  invalid_buttons: "Некорректные кнопки",
  invalid_button_kind: "Неизвестный тип кнопки",
  invalid_button_style: "Неизвестный цвет кнопки",
  invalid_button_emoji: "ID custom emoji кнопки: число",
  // testing / running
  admin_telegram_unavailable: "Не найден ваш Telegram-аккаунт: тест можно отправить только себе в Telegram",
  bot_unavailable: "Бот сейчас недоступен, проверьте настройки бота",
  test_failed: "Telegram не принял тестовое сообщение",
  run_not_supported: "Для этого вида события ручной запуск не предусмотрен: оно запускается само по расписанию",
  invalid_batch_id: "Некорректный идентификатор запуска",
  no_recipients: "Под условия аудитории никто не подошёл",
  too_many_recipients: "Получателей слишком много (больше 20 000), сузьте аудиторию",
  // media
  media_too_large: "Файл больше допустимого размера",
  unsupported_file: "Файл не подходит по формату",
  invalid_chunk: "Сбой загрузки файла, попробуйте ещё раз",
  chunk_out_of_order: "Сбой загрузки файла, попробуйте ещё раз",
  incomplete_upload: "Файл загружен не полностью, попробуйте ещё раз",
  media_in_use: "Файл используется в шаблоне события",
  // birthdates
  user_not_found: "Пользователь не найден",
  birthdate_not_found: "У пользователя нет сохранённой даты",
  invalid_year: "Год рождения: возраст от 5 до 110 лет, не в будущем",
  already_set: "Дата уже сохранена",
  consent_required: "Нужно согласие пользователя",
  no_items: "Нет строк для импорта",
  too_many_items: "Слишком много строк (максимум 2000 за раз)",
  // backfill
  already_running: "Сканирование уже идёт",
  nothing_to_resume: "Нечего возобновлять: начните новое сканирование",
  invalid_rate: "Скорость сканирования: от 1 до 28 запросов в секунду",
  // client diagnostics
  invalid_client_event: "Неизвестное клиентское событие",
  invalid_client_detail: "Некорректные данные клиентского события",
};

export function errorText(code, data) {
  if (code && ERRORS[code]) return ERRORS[code];
  if (data && data.detail) return String(data.detail);
  return code || "Неизвестная ошибка";
}

// Delivery / skip reasons written by the scheduler (deliveries.reason, gift_note). Prefix forms are handled in reasonText.
export const REASONS = {
  event_deleted: "Событие удалено",
  event_disabled: "Событие выключено",
  user_missing: "Пользователь не найден",
  user_banned: "Пользователь забанен",
  no_telegram: "У пользователя нет Telegram",
  user_blocked: "Пользователь заблокировал бота",
  no_template: "Нет шаблона для языка пользователя",
  gift_not_eligible: "Подарок не положен (например, нет активной подписки), событие настроено «не отправлять»",
  gift_unavailable: "Подарок недоступен, событие настроено «не отправлять»",
  gift_failed: "Подарок не удалось выдать, событие настроено «не отправлять»",
  gift_pending: "Подарок ещё не выдан",
  message_too_long: "Сообщение длиннее 4096 знаков",
  empty_message: "Пустое сообщение",
  rate_limited: "Telegram ограничил скорость, отправка повторится",
  interrupted_during_send: "Отправка была прервана (перезапуск), повторно не отправляется, чтобы не прислать дубль",
  // gift notes
  subscription_required: "Нужна активная подписка",
  unknown_gift: "Неизвестный вид подарка",
  wheel_not_installed: "Плагин «Колесо удачи» не установлен",
  delivery_missing: "Доставка не найдена",
  already: "Билеты уже были выданы ранее",
};
export const REASON_PREFIXES = [
  ["gift_error:", "Ошибка при выдаче подарка"],
  ["send_error:", "Ошибка отправки"],
  ["crash:", "Внутренний сбой при отправке"],
  ["retry_after:", "Telegram просит подождать"],
];

export function reasonText(reason) {
  const r = String(reason || "");
  if (!r) return "";
  if (REASONS[r]) return REASONS[r];
  for (const [prefix, text] of REASON_PREFIXES) {
    if (r.startsWith(prefix)) {
      const rest = r.slice(prefix.length).trim();
      return rest ? `${text}: ${rest}` : text;
    }
  }
  if (/blocked|deactivated|chat not found|forbidden/i.test(r)) return `Пользователь недоступен для бота (${r})`;
  return r;
}

export const LOG_EVENTS = {
  request_failed: "Запрос завершился ошибкой",
  admin_config_saved: "Настройки сохранены",
  admin_event_created: "Событие создано",
  admin_event_updated: "Событие изменено",
  admin_event_deleted: "Событие удалено",
  admin_event_test: "Тестовая отправка администратору",
  admin_event_run: "Ручной запуск события",
  admin_birthdate_set: "Админ сохранил дату рождения",
  admin_birthdate_deleted: "Админ удалил дату рождения",
  admin_birthdates_imported: "Импорт дат рождения",
  admin_deliveries_exported: "Выгрузка доставок",
  admin_log_exported: "Выгрузка журнала",
  admin_log_cleared: "Журнал очищен",
  backfill_started: "Сканирование запущено",
  backfill_stopped: "Сканирование остановлено",
  birthdate_scanned_on_event: "Проверка Telegram для нового пользователя",
  birthdate_set_by_user: "Пользователь указал дату рождения сам",
  delivery_crashed: "Сбой при обработке доставки",
  gift_not_given: "Подарок не выдан",
  internal_error: "Внутренняя ошибка",
  client_page_reload_detected: "Страница перезагрузилась во время редактирования",
  client_editor_restored: "Черновик восстановлен после перезагрузки страницы",
  client_js_error: "Ошибка JavaScript на странице",
  client_unhandled_rejection: "Необработанная ошибка в браузере",
};

// ----------------------------------------------------------------------------------------------- summaries
const pad2 = (n) => String(n).padStart(2, "0");

export function humanDay(day, month) {
  return `${Number(day)} ${MONTHS_RU[Number(month) - 1] || "?"}`;
}

export function humanMmdd(mmdd) {
  const m = /^(\d{2})-(\d{2})$/.exec(String(mmdd || ""));
  return m ? humanDay(Number(m[2]), Number(m[1])) : "—";
}

export function humanIsoDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  return m ? `${Number(m[3])} ${MONTHS_RU[Number(m[2]) - 1]} ${m[1]}` : "—";
}

export function offsetText(offset, noun = "дня рождения") {
  const n = Number(offset) || 0;
  if (n === 0) return `в день ${noun === "дня рождения" ? "рождения" : "годовщины"}`;
  const a = Math.abs(n);
  const unit = plural(a, "день", "дня", "дней");
  return n < 0 ? `за ${a} ${unit} до` : `через ${a} ${unit} после`;
}

/** "ежегодно в 10:00 · день рождения" and its siblings. */
export function scheduleSummary(ev) {
  const time = ev.send_time || "10:00";
  const p = ev.params || {};
  switch (ev.kind) {
    case "birthday": {
      const off = Number(p.offset_days) || 0;
      return `ежегодно в ${time} · день рождения${off ? `, ${offsetText(off)}` : ""}`;
    }
    case "registration_anniversary": {
      const off = Number(p.offset_days) || 0;
      const years = Number(p.min_years) || 1;
      return `ежегодно в ${time} · годовщина регистрации (от ${years} ${plural(years, "года", "лет", "лет")})${off ? `, ${offsetText(off, "годовщины")}` : ""}`;
    }
    case "fixed_date":
      return `каждый год ${humanMmdd(p.date)} в ${time} · праздник`;
    case "one_time":
      return `один раз ${humanIsoDate(p.date)} в ${time}`;
    case "manual":
      return "только по кнопке «Запустить»";
    default:
      return "";
  }
}

export function audienceSummary(aud) {
  const a = aud || {};
  const segs = Array.isArray(a.segments) ? a.segments : ["none", "trial", "paid"];
  const all = ["none", "trial", "paid"].every((s) => segs.includes(s));
  const parts = [all ? "все пользователи" : segs.map((s) => SEGMENT_RU[s] || s).join(" + ")];
  if (a.language && a.language !== "any") parts.push(a.language === "ru" ? "язык: русский" : "язык: английский");
  if ((a.tariff_keys || []).length) parts.push(`тарифы: ${a.tariff_keys.join(", ")}`);
  if (a.skip_blocked === false) parts.push("включая заблокировавших бота");
  return parts.join(", ");
}

export function giftSummary(gift, currency) {
  const g = gift || { kind: "none" };
  switch (g.kind) {
    case "traffic": return `+${String(g.gb).replace(".", ",")} ГБ трафика`;
    case "days": return `+${g.days} ${plural(Number(g.days), "день", "дня", "дней")}`;
    case "balance": return `+${moneyText(g.amount_minor, currency)} на баланс`;
    case "promo_discount": return `промокод −${g.percent}% на ${g.valid_days || 14} дн.`;
    case "wheel_spins": return `+${g.spins} ${plural(Number(g.spins), "крутка", "крутки", "круток")} колеса`;
    default: return "без подарка";
  }
}

// ----------------------------------------------------------------------------------------------- Telegram HTML subset
const TAG_ALIAS = { strong: "b", b: "b", em: "i", i: "i", ins: "u", u: "u", strike: "s", del: "s", s: "s", code: "code", pre: "pre", a: "a", blockquote: "blockquote", "tg-spoiler": "tg-spoiler", "tg-emoji": "tg-emoji", span: "span" };
const SAFE_HREF = /^(https?:\/\/|tg:\/\/)/i;
const ENTITIES = { "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " " };

export function decodeEntities(s) {
  return String(s).replace(/&(?:lt|gt|amp|quot|apos|nbsp|#39);/g, (m) => ENTITIES[m] ?? m);
}

const TAG_RE = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)\s*(\/?)>/g;
const ATTR_RE = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;

function parseAttrs(src) {
  const out = {};
  for (const m of String(src).matchAll(ATTR_RE)) out[m[1].toLowerCase()] = decodeEntities(m[2] ?? m[3] ?? m[4] ?? "");
  return out;
}

/**
 * Parse Telegram HTML into a safe tree: {t:"text", v} | {t:"el", tag, attrs, children}.
 * Only whitelisted tags survive (b i u s tg-spoiler code pre a blockquote tg-emoji); attributes are rebuilt from scratch:
 * a keeps an http(s)/tg href, tg-emoji keeps a numeric emoji-id. Anything else is dropped but its text stays.
 */
export function parseTelegramHtml(src) {
  const root = { t: "el", tag: "root", attrs: {}, children: [] };
  const stack = [root];
  const top = () => stack[stack.length - 1];
  const pushText = (raw) => { if (raw) top().children.push({ t: "text", v: decodeEntities(raw) }); };
  const text = String(src ?? "");
  let last = 0;
  TAG_RE.lastIndex = 0;
  for (let m = TAG_RE.exec(text); m; m = TAG_RE.exec(text)) {
    pushText(text.slice(last, m.index));
    last = m.index + m[0].length;
    const closing = m[1] === "/";
    const name = m[2].toLowerCase();
    let tag = TAG_ALIAS[name];
    const attrs = parseAttrs(m[3]);
    if (tag === "span") tag = attrs.class === "tg-spoiler" ? "tg-spoiler" : undefined;
    if (!tag) continue; // unknown tag: its text children stay in the parent
    if (closing) {
      for (let i = stack.length - 1; i > 0; i -= 1) {
        if (stack[i].tag === tag) { stack.length = i; break; }
      }
      continue;
    }
    const el = { t: "el", tag, attrs: {}, children: [] };
    if (tag === "a") {
      const href = (attrs.href || "").trim();
      if (SAFE_HREF.test(href)) el.attrs.href = href;
    } else if (tag === "tg-emoji") {
      if (/^[1-9]\d{0,19}$/.test(attrs["emoji-id"] || "")) el.attrs["emoji-id"] = attrs["emoji-id"];
    } else if (tag === "pre" || tag === "code") {
      const cls = /^language-[\w+#-]{1,20}$/.test(attrs.class || "") ? attrs.class : "";
      if (cls) el.attrs.class = cls;
    }
    top().children.push(el);
    if (m[4] !== "/") stack.push(el);
  }
  pushText(text.slice(last));
  return root;
}

/** Characters Telegram counts: text without tags, entities decoded. */
export function visibleLength(src) {
  const walk = (node) => (node.t === "text" ? node.v.length : node.children.reduce((n, c) => n + walk(c), 0));
  return walk(parseTelegramHtml(src));
}

/**
 * Build DOM for a parsed tree using only createElement/createTextNode (never innerHTML).
 * opts.emojiUrl(id) -> url|null optionally shows the sticker for tg-emoji; the fallback character stays as alt content.
 */
export function renderTree(node, doc, opts = {}) {
  const frag = doc.createDocumentFragment();
  const add = (parent, n) => {
    if (n.t === "text") {
      const parts = n.v.split("\n");
      parts.forEach((part, i) => {
        if (i) parent.appendChild(doc.createElement("br"));
        if (part) parent.appendChild(doc.createTextNode(part));
      });
      return;
    }
    let el;
    switch (n.tag) {
      case "a":
        el = doc.createElement("a");
        if (n.attrs.href) { el.setAttribute("href", n.attrs.href); el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener noreferrer"); }
        break;
      case "tg-spoiler":
        el = doc.createElement("span");
        el.className = "kpu-spoil";
        el.setAttribute("title", "Спойлер: нажмите, чтобы показать");
        el.addEventListener?.("click", () => el.classList.toggle("open"));
        break;
      case "tg-emoji": {
        el = doc.createElement("span");
        el.className = "kpu-emoji";
        const url = n.attrs["emoji-id"] && opts.emojiUrl ? opts.emojiUrl(n.attrs["emoji-id"]) : null;
        if (url) {
          const img = doc.createElement("img");
          img.setAttribute("src", url);
          img.setAttribute("alt", "");
          img.addEventListener?.("error", () => img.remove());
          el.appendChild(img);
        }
        break;
      }
      default:
        el = doc.createElement(n.tag);
        if (n.attrs.class) el.className = n.attrs.class;
    }
    n.children.forEach((c) => add(el, c));
    parent.appendChild(el);
  };
  node.children.forEach((c) => add(frag, c));
  return frag;
}

// ----------------------------------------------------------------------------------------------- toolbar text operations
export const TOOLBAR = [
  { id: "b", label: "Ж", title: "Жирный", open: "<b>", close: "</b>" },
  { id: "i", label: "К", title: "Курсив", open: "<i>", close: "</i>" },
  { id: "u", label: "П", title: "Подчёркнутый", open: "<u>", close: "</u>" },
  { id: "s", label: "З", title: "Зачёркнутый", open: "<s>", close: "</s>" },
  { id: "spoiler", label: "▒", title: "Спойлер", open: "<tg-spoiler>", close: "</tg-spoiler>" },
  { id: "code", label: "{ }", title: "Моноширинный", open: "<code>", close: "</code>" },
  { id: "pre", label: "‹/›", title: "Блок кода", open: "<pre>", close: "</pre>" },
  { id: "quote", label: "❝", title: "Цитата", open: "<blockquote>", close: "</blockquote>" },
  { id: "a", label: "🔗", title: "Ссылка", open: null, close: "</a>" },
];

/** Wrap value[start:end] (or `fallback` when empty) into open/close. Returns the new value and the selection to restore. */
export function wrapSelection(value, start, end, open, close, fallback = "текст") {
  const s = Math.max(0, Math.min(start, end)), e = Math.max(start, end);
  const sel = value.slice(s, e) || fallback;
  const next = value.slice(0, s) + open + sel + close + value.slice(e);
  return { value: next, start: s + open.length, end: s + open.length + sel.length };
}

/** Insert text at the cursor (replacing a selection). Returns the value and the new caret. */
export function insertText(value, start, end, insert) {
  const s = Math.max(0, Math.min(start, end)), e = Math.max(start, end);
  return { value: value.slice(0, s) + insert + value.slice(e), start: s + insert.length, end: s + insert.length };
}

export function emojiTag(id, fallback) {
  return `<tg-emoji emoji-id="${String(id).replace(/\D/g, "")}">${esc(fallback || "⭐")}</tg-emoji>`;
}

export function linkOpen(url) {
  return `<a href="${esc(String(url).trim())}">`;
}

// ----------------------------------------------------------------------------------------------- birthdates
export function validateBirthdate(day, month, year, now = new Date()) {
  const d = Number(day), m = Number(month);
  if (!Number.isInteger(m) || m < 1 || m > 12) return "Месяц: от 1 до 12";
  const maxDay = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
  if (!Number.isInteger(d) || d < 1 || d > maxDay) return `День: от 1 до ${maxDay} для выбранного месяца`;
  if (year !== "" && year !== null && year !== undefined) {
    const y = Number(year);
    const thisYear = now.getFullYear();
    if (!Number.isInteger(y) || y > thisYear - 5 || y < thisYear - 110) return "Год: возраст от 5 до 110 лет";
  }
  return "";
}

/**
 * Import text -> API items. Accepts a JSON array ([{"user":"@a","day":1,"month":2,"year":1990}]) or lines
 * `user;day;month;year` (separators ; , tab or spaces; year optional) or `user;DD.MM.YYYY` / `user;DD.MM`.
 */
export function parseImport(text) {
  const raw = String(text || "").trim();
  const items = [], errors = [];
  if (!raw) return { items, errors };
  if (raw.startsWith("[")) {
    try {
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) throw new Error("not array");
      arr.forEach((x, i) => {
        if (x && typeof x === "object" && x.user !== undefined) items.push({ user: String(x.user), day: Number(x.day), month: Number(x.month), ...(x.year ? { year: Number(x.year) } : {}) });
        else errors.push({ line: i + 1, msg: "Нужен объект с полями user, day, month" });
      });
    } catch {
      errors.push({ line: 1, msg: "Некорректный JSON" });
    }
    return { items, errors };
  }
  raw.split(/\r?\n/).forEach((line, idx) => {
    const t = line.trim();
    if (!t || t.startsWith("#")) return;
    const parts = t.split(/[;,\t]+|\s+/).filter(Boolean);
    const user = parts[0];
    let day, month, year;
    const dotted = /^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?$/.exec(parts[1] || "");
    if (dotted && parts.length === 2) [, day, month, year] = dotted;
    else [, day, month, year] = parts;
    const bad = validateBirthdate(day, month, year ?? "");
    if (!user || day === undefined || month === undefined) errors.push({ line: idx + 1, msg: "Формат: пользователь;день;месяц;год" });
    else if (bad) errors.push({ line: idx + 1, msg: bad });
    else items.push({ user, day: Number(day), month: Number(month), ...(year ? { year: Number(year) } : {}) });
  });
  return { items, errors };
}

// ----------------------------------------------------------------------------------------------- event drafts
export const emptyTemplate = () => ({ text: "", text_no_gift: "", media: [], buttons: [], disable_notification: false, protect_content: false, disable_preview: true });

export function blankDraft(kind = "manual") {
  return {
    id: null,
    name: "",
    kind,
    enabled: false,
    params: { offset_days: 0, min_years: 1, date: "" },
    audience: { segments: ["none", "trial", "paid"], language: "any", tariff_keys: "", skip_blocked: true },
    send_time: "10:00",
    feb29: "feb28",
    gift: { kind: "none", gb: "1", days: "7", amount: "100", percent: "20", valid_days: "14", applies_to: "subscription", spins: "1", ignore_cap: false },
    gift_fallback: "send_without_gift",
    templates: { ru: emptyTemplate(), en: emptyTemplate() },
  };
}

const TPL_BIRTHDAY_RU = "🎂 <b>С днём рождения, {name}!</b>\n\nМы рады, что вы с нами.[[ Вам исполнилось {age}.]][[ Наш подарок: {gift}.]][[\nПромокод: <code>{promo_code}</code>]]";
const TPL_BIRTHDAY_RU_NO_GIFT = "🎂 <b>С днём рождения, {name}!</b>\n\nМы рады, что вы с нами. Хорошего дня!";

export const PRESETS = [
  {
    id: "birthday", title: "С днём рождения", note: "Каждый год в 10:00, подарок: промокод на скидку 20%",
    build: () => ({
      ...blankDraft("birthday"), name: "С днём рождения",
      gift: { ...blankDraft().gift, kind: "promo_discount", percent: "20", valid_days: "14", applies_to: "subscription" },
      templates: {
        ru: { ...emptyTemplate(), text: TPL_BIRTHDAY_RU, text_no_gift: TPL_BIRTHDAY_RU_NO_GIFT },
        en: { ...emptyTemplate(), text: "🎂 <b>Happy birthday, {name}!</b>\n\nWe are glad to have you with us.[[ Your gift: {gift}.]][[\nPromo code: <code>{promo_code}</code>]]", text_no_gift: "🎂 <b>Happy birthday, {name}!</b>\n\nWe are glad to have you with us." },
      },
    }),
  },
  {
    id: "anniversary", title: "Годовщина с нами", note: "В день регистрации, начиная с 1 года, подарок: +7 дней",
    build: () => ({
      ...blankDraft("registration_anniversary"), name: "Годовщина с нами",
      params: { offset_days: 0, min_years: 1, date: "" },
      gift: { ...blankDraft().gift, kind: "days", days: "7" },
      audience: { segments: ["trial", "paid"], language: "any", tariff_keys: "", skip_blocked: true },
      templates: {
        ru: { ...emptyTemplate(), text: "🎉 <b>{name}, нам уже {years} лет вместе!</b>\n\nСпасибо, что остаётесь с нами.[[ Дарим вам {gift}.]]", text_no_gift: "🎉 <b>{name}, мы вместе уже {years} лет!</b>\n\nСпасибо, что остаётесь с нами." },
        en: { ...emptyTemplate(), text: "🎉 <b>{name}, {years} years together!</b>\n\nThank you for staying with us.[[ Our gift: {gift}.]]", text_no_gift: "🎉 <b>{name}, {years} years together!</b>\n\nThank you for staying with us." },
      },
    }),
  },
  {
    id: "newyear", title: "Новый год", note: "Каждый год 31 декабря в 18:00, подарок: +3 дня",
    build: () => ({
      ...blankDraft("fixed_date"), name: "Новый год",
      params: { offset_days: 0, min_years: 1, date: "12-31" }, send_time: "18:00",
      gift: { ...blankDraft().gift, kind: "days", days: "3" },
      templates: {
        ru: { ...emptyTemplate(), text: "🎄 <b>С наступающим Новым годом, {name}!</b>\n\nЖелаем стабильного соединения и хорошего настроения.[[ Новогодний подарок: {gift}.]]", text_no_gift: "🎄 <b>С наступающим Новым годом, {name}!</b>\n\nЖелаем стабильного соединения и хорошего настроения." },
        en: { ...emptyTemplate(), text: "🎄 <b>Happy New Year, {name}!</b>\n\nWishing you a stable connection and a great mood.[[ Our gift: {gift}.]]", text_no_gift: "🎄 <b>Happy New Year, {name}!</b>\n\nWishing you a stable connection and a great mood." },
      },
    }),
  },
  {
    id: "promo", title: "Анонс акции", note: "Разовая рассылка в выбранный день, подарок: промокод на скидку 15%",
    build: () => {
      const d = new Date(Date.now() + 7 * 864e5);
      return {
        ...blankDraft("one_time"), name: "Анонс акции",
        params: { offset_days: 0, min_years: 1, date: `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` },
        gift: { ...blankDraft().gift, kind: "promo_discount", percent: "15", valid_days: "7", applies_to: "subscription" },
        templates: {
          ru: { ...emptyTemplate(), text: "🔥 <b>{name}, у нас акция!</b>\n\nСкидка {promo_discount} на подписку по вашему личному промокоду: <code>{promo_code}</code>", text_no_gift: "🔥 <b>{name}, у нас акция!</b>\n\nЗагляните в раздел тарифов." },
          en: { ...emptyTemplate(), text: "🔥 <b>{name}, a special offer!</b>\n\n{promo_discount} off with your personal promo code: <code>{promo_code}</code>", text_no_gift: "🔥 <b>{name}, a special offer!</b>\n\nHave a look at the plans." },
        },
      };
    },
  },
  {
    id: "manual", title: "Ручная рассылка", note: "Запускается кнопкой, подарок: +1 ГБ трафика",
    build: () => ({
      ...blankDraft("manual"), name: "Ручная рассылка",
      gift: { ...blankDraft().gift, kind: "traffic", gb: "1" },
      templates: {
        ru: { ...emptyTemplate(), text: "👋 <b>{name}, есть новости!</b>\n\nНапишите здесь текст объявления.[[ Дарим вам {gift}.]]", text_no_gift: "👋 <b>{name}, есть новости!</b>\n\nНапишите здесь текст объявления." },
        en: emptyTemplate(),
      },
    }),
  },
];

const trimTemplate = (tpl) => ({
  text: tpl.text || "",
  text_no_gift: tpl.text_no_gift || "",
  media: (tpl.media || []).map((m) => (m.id ? { kind: m.kind, id: m.id } : m.url ? { kind: m.kind, url: m.url } : { kind: m.kind, file_id: m.file_id })),
  buttons: (tpl.buttons || []).map((row) => row.map((b) => ({
    kind: b.kind, label: b.label || "", url: b.kind === "section" ? "" : b.url || "", section: b.section || "home",
    style: b.style || "", icon_custom_emoji_id: b.icon_custom_emoji_id || "", icon_emoji: b.icon_emoji || "",
  }))).filter((row) => row.length),
  disable_notification: !!tpl.disable_notification,
  protect_content: !!tpl.protect_content,
  disable_preview: tpl.disable_preview !== false,
});

export const templateIsEmpty = (tpl) => !tpl || (!String(tpl.text || "").trim() && !(tpl.media || []).length);

function giftPayload(g) {
  const toNum = (v) => (String(v).trim() === "" ? null : Number(String(v).replace(",", ".")));
  switch (g.kind) {
    case "traffic": return { kind: "traffic", gb: toNum(g.gb) };
    case "days": return { kind: "days", days: toNum(g.days) };
    case "balance": return { kind: "balance", amount_minor: majorToMinor(g.amount) };
    case "promo_discount": return { kind: "promo_discount", percent: toNum(g.percent), valid_days: toNum(g.valid_days), applies_to: g.applies_to || "subscription" };
    case "wheel_spins": return { kind: "wheel_spins", spins: toNum(g.spins), ignore_cap: !!g.ignore_cap };
    default: return { kind: "none" };
  }
}

/** Editor draft -> body for POST/PUT /events and POST /preview. */
export function toPayload(d, { forPreview = false } = {}) {
  const params = {};
  if (d.kind === "birthday" || d.kind === "registration_anniversary") params.offset_days = Number(d.params.offset_days) || 0;
  if (d.kind === "registration_anniversary") params.min_years = Number(d.params.min_years) || 1;
  if (d.kind === "fixed_date" || d.kind === "one_time") params.date = d.params.date || "";
  const templates = {};
  for (const lang of ["ru", "en"]) if (!templateIsEmpty(d.templates[lang])) templates[lang] = trimTemplate(d.templates[lang]);
  return {
    name: String(d.name || "").trim() || (forPreview ? "draft" : ""),
    kind: d.kind,
    enabled: forPreview ? false : !!d.enabled,
    params,
    audience: {
      segments: d.audience.segments.slice(),
      language: d.audience.language,
      tariff_keys: Array.isArray(d.audience.tariff_keys) ? d.audience.tariff_keys : String(d.audience.tariff_keys || "").split(/[,\s]+/).filter(Boolean),
      skip_blocked: d.audience.skip_blocked !== false,
    },
    send_time: d.send_time,
    feb29: d.feb29,
    gift: giftPayload(d.gift),
    gift_fallback: d.gift_fallback,
    templates,
  };
}

/** API event -> editor draft. */
export function fromEvent(ev) {
  const base = blankDraft(ev.kind);
  const g = ev.gift || { kind: "none" };
  const gift = { ...base.gift, kind: g.kind };
  if (g.kind === "traffic") gift.gb = String(g.gb);
  if (g.kind === "days") gift.days = String(g.days);
  if (g.kind === "balance") gift.amount = minorToMajor(g.amount_minor);
  if (g.kind === "promo_discount") Object.assign(gift, { percent: String(g.percent), valid_days: String(g.valid_days ?? 14), applies_to: g.applies_to || "subscription" });
  if (g.kind === "wheel_spins") Object.assign(gift, { spins: String(g.spins), ignore_cap: !!g.ignore_cap });
  const templates = { ru: emptyTemplate(), en: emptyTemplate() };
  for (const lang of ["ru", "en"]) if (ev.templates && ev.templates[lang]) templates[lang] = { ...emptyTemplate(), ...clone(ev.templates[lang]) };
  return {
    ...base,
    id: ev.id,
    name: ev.name,
    enabled: !!ev.enabled,
    params: { offset_days: ev.params?.offset_days ?? 0, min_years: ev.params?.min_years ?? 1, date: ev.params?.date || "" },
    audience: { segments: (ev.audience?.segments || base.audience.segments).slice(), language: ev.audience?.language || "any", tariff_keys: (ev.audience?.tariff_keys || []).join(", "), skip_blocked: ev.audience?.skip_blocked !== false },
    send_time: ev.send_time || "10:00",
    feb29: ev.feb29 || "feb28",
    gift,
    gift_fallback: ev.gift_fallback || "send_without_gift",
    templates,
  };
}

/** Which form block an API error code belongs to (for inline messages). */
export const FIELD_OF_ERROR = {
  name_required: "name", invalid_kind: "kind", invalid_send_time: "send_time", invalid_offset_days: "params", invalid_min_years: "params",
  invalid_date: "params", invalid_feb29: "send_time", invalid_audience: "audience", audience_empty: "audience", invalid_audience_language: "audience",
  invalid_gift_kind: "gift", invalid_gift_gb: "gift", invalid_gift_days: "gift", invalid_gift_amount: "gift", invalid_gift_percent: "gift",
  invalid_gift_valid_days: "gift", invalid_gift_applies_to: "gift", invalid_gift_spins: "gift", wheel_not_installed: "gift", invalid_gift_fallback: "gift",
  invalid_media: "media", invalid_media_url: "media", invalid_buttons: "buttons", invalid_button_kind: "buttons", invalid_button_style: "buttons", invalid_button_emoji: "buttons",
};

/** Client-side checks that save a round trip; the API stays the authority. */
export function draftProblems(d, caps = {}) {
  const out = [];
  if (!String(d.name || "").trim()) out.push(["name", "Укажите название события"]);
  if (d.kind === "fixed_date" && !/^\d{2}-\d{2}$/.test(d.params.date || "")) out.push(["params", "Выберите день и месяц праздника"]);
  if (d.kind === "one_time" && !/^\d{4}-\d{2}-\d{2}$/.test(d.params.date || "")) out.push(["params", "Выберите дату отправки"]);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(d.send_time || "")) out.push(["send_time", "Время отправки: ЧЧ:ММ"]);
  if (!d.audience.segments.length) out.push(["audience", "Выберите хотя бы одну категорию получателей"]);
  if (d.gift.kind === "balance" && majorToMinor(d.gift.amount) === null) out.push(["gift", "Сумма: число, до двух знаков после запятой"]);
  if (d.gift.kind === "wheel_spins" && caps.wheel_installed === false) out.push(["gift", ERRORS.wheel_not_installed]);
  if (d.enabled && templateIsEmpty(d.templates.ru) && templateIsEmpty(d.templates.en)) out.push(["templates", "Включённому событию нужен хотя бы один шаблон"]);
  return out;
}

/** Split API issues ("[ru] text") per language so they sit next to the matching template. */
export function splitIssues(list) {
  const out = { ru: [], en: [], general: [] };
  for (const raw of list || []) {
    const m = /^\[(ru|en)\]\s*(.*)$/s.exec(String(raw));
    if (m) out[m[1]].push(m[2]);
    else out.general.push(String(raw));
  }
  return out;
}

export function newBatchId() {
  const bytes = new Uint8Array(8);
  (globalThis.crypto || { getRandomValues: (a) => a.map(() => Math.floor(Math.random() * 256)) }).getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function tzOffsetLabel(tz) {
  return tz || "Europe/Moscow";
}

/** Next 30 days of upcoming birthdays -> bars [{date,label,count,height}] */
export function sparkBars(days) {
  const max = Math.max(1, ...days.map((d) => d.count));
  return days.map((d) => {
    const dt = new Date(`${d.date}T00:00:00`);
    return { date: d.date, count: d.count, height: d.count ? Math.max(8, Math.round((d.count / max) * 100)) : 3, label: `${dt.getDate()} ${MONTHS_RU[dt.getMonth()]}: ${d.count}` };
  });
}

// ----------------------------------------------------------------------------------------------- draft autosave + tab memory
// Storage objects are passed in (localStorage / sessionStorage or a test stub); every access is wrapped, storage may be blocked.
export const DRAFT_KEY = "kiro-push:draft:v1";
export const TAB_KEY = "kiro-push:tab";
export const EDITOR_OPEN_KEY = "kiro-push:editor-open";
export const PAGEHIDE_KEY = "kiro-push:pagehide";
export const MOUNTED_AT_KEY = "kiro-push:mounted-at";
export const ERRORS_SENT_KEY = "kiro-push:errors-sent";
export const DRAFT_MAX_BYTES = 300 * 1024;
export const DRAFT_TTL_MS = 7 * 24 * 3600 * 1000;
const DRAFT_SCOPE = "event-editor";

export function storageGet(storage, key) {
  try { return storage ? storage.getItem(key) : null; } catch { return null; }
}
export function storageSet(storage, key, value) {
  try { if (!storage) return false; storage.setItem(key, String(value)); return true; } catch { return false; }
}
export function storageRemove(storage, key) {
  try { storage?.removeItem(key); } catch { /* storage blocked */ }
}

/** Draft record -> JSON string, or null when it is over the size limit. Media are referenced by id only (templates hold ids, never bytes). */
export function serializeDraft({ eventId = null, name = "", draft }, now = Date.now()) {
  let raw;
  try {
    raw = JSON.stringify({ v: 1, scope: DRAFT_SCOPE, eventId: eventId ?? null, name: String(name || "").slice(0, 120), draft, savedAt: now });
  } catch { return null; }
  return raw.length > DRAFT_MAX_BYTES ? null : raw;
}

/** Draft from the stored API shape, filled with defaults; null if it is not a usable editor draft. */
export function normalizeDraft(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.kind !== "string" || !KIND_RU[raw.kind]) return null;
  if (!raw.templates || typeof raw.templates !== "object") return null;
  const base = blankDraft(raw.kind);
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  const tpl = (v) => {
    const t = { ...emptyTemplate(), ...obj(v) };
    t.media = Array.isArray(t.media) ? t.media : [];
    t.buttons = Array.isArray(t.buttons) ? t.buttons : [];
    return t;
  };
  const d = {
    ...base,
    ...raw,
    id: Number.isInteger(raw.id) ? raw.id : null,
    name: String(raw.name || ""),
    enabled: !!raw.enabled,
    params: { ...base.params, ...obj(raw.params) },
    audience: { ...base.audience, ...obj(raw.audience) },
    gift: { ...base.gift, ...obj(raw.gift) },
    templates: { ru: tpl(raw.templates.ru), en: tpl(raw.templates.en) },
  };
  if (!Array.isArray(d.audience.segments)) d.audience.segments = base.audience.segments.slice();
  return d;
}

/** Stored JSON -> {eventId, name, draft, savedAt} or null (corrupt, wrong version, expired, too large). */
export function parseDraft(raw, now = Date.now()) {
  if (typeof raw !== "string" || !raw || raw.length > DRAFT_MAX_BYTES) return null;
  let rec;
  try { rec = JSON.parse(raw); } catch { return null; }
  if (!rec || rec.v !== 1 || rec.scope !== DRAFT_SCOPE || !Number.isFinite(rec.savedAt)) return null;
  if (now - rec.savedAt > DRAFT_TTL_MS || rec.savedAt - now > 3600 * 1000) return null;
  const draft = normalizeDraft(rec.draft);
  if (!draft) return null;
  const eventId = Number.isInteger(rec.eventId) && rec.eventId > 0 ? rec.eventId : null;
  return { eventId, name: String(rec.name || draft.name || ""), draft, savedAt: rec.savedAt };
}

/** Returns "saved" | "too_large" | "error". */
export function saveDraft(storage, rec, now = Date.now()) {
  const raw = serializeDraft(rec, now);
  if (raw === null) return "too_large";
  return storageSet(storage, DRAFT_KEY, raw) ? "saved" : "error";
}

/** Reads the draft; an expired or corrupt record is removed. */
export function loadDraft(storage, now = Date.now()) {
  const raw = storageGet(storage, DRAFT_KEY);
  if (raw === null) return null;
  const rec = parseDraft(raw, now);
  if (!rec) storageRemove(storage, DRAFT_KEY);
  return rec;
}
export const clearDraft = (storage) => storageRemove(storage, DRAFT_KEY);

export function loadTab(storage, allowed, fallback = "overview") {
  const t = storageGet(storage, TAB_KEY);
  return t && allowed.includes(t) ? t : fallback;
}
export const saveTab = (storage, tab) => storageSet(storage, TAB_KEY, tab);

export const getFlag = (storage, key) => storageGet(storage, key) === "1";
export const setFlag = (storage, key, on) => (on ? storageSet(storage, key, "1") : (storageRemove(storage, key), true));

// ----------------------------------------------------------------------------------------------- client diagnostics payload
// Mirrors the backend allowlist (kiro_push.clean_client_detail): a few scalar keys, never draft content or event text.
export const CLIENT_EVENTS = ["page_reload_detected", "editor_restored", "js_error", "unhandled_rejection"];
const CLIENT_STR = { nav_type: 24, visibility: 16, ua: 160, tab: 24, message: 200, last_pagehide: 16 };
const CLIENT_BOOL = ["in_telegram", "editor_open"];

export function cleanClientDetail(raw) {
  const out = {};
  for (const [k, v] of Object.entries(raw || {})) {
    if (k in CLIENT_STR && typeof v === "string") out[k] = v.replace(/\s+/g, " ").trim().slice(0, CLIENT_STR[k]);
    else if (CLIENT_BOOL.includes(k) && typeof v === "boolean") out[k] = v;
    else if (k === "ms_since_mount" && typeof v === "number" && Number.isFinite(v)) out[k] = Math.max(0, Math.min(Math.round(v), 1e9));
  }
  return out;
}
