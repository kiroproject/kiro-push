# HTTP API и настройки KIRO Push

Контракт плагина `kiro-push` 0.1.2. Общий обзор: [README](../README.md), устройство: [architecture.md](architecture.md).

## Настройки (`PUT /config`)

`enabled`, `timezone` (IANA, по умолчанию Europe/Moscow), `default_language` (ru), `send_rate_per_second` (15, 1-28),
`scan_rate_per_second` (20, 1-28), `batch_size` (400 доставок за запуск), `catchup_hours` (24), `max_send_attempts` (3),
`retry_minutes` (10), `auto_backfill` (false), `scan_new_users` (true), `rescan_missing_days` (30),
`rescan_max_attempts` (3), `blocked_retry_days` (90), `user_entry_enabled` (true), `consent_text`,
`user_attempts_per_hour` (5), `debug_log`.

## HTTP API

Все ответы: `{"ok": true, ...}` или `{"ok": false, "error": "<код>", ...}`. Админские пути требуют роли администратора
(Core проверяет сессию и CSRF), клиентские — сессии пользователя Mini App. Тела запросов JSON, кроме загрузки чанка.

### Клиентские (webapp)

Через SDK: `host.request("/birthdate")` обращается к `/api/plugins/kiro-push/birthdate`.

| Метод и путь | Тело | Ответ |
|---|---|---|
| `GET /api/plugins/kiro-push/birthdate` | | `{enabled, consent_text, state: "set"\|"none", day?, month?, year? (нет, если год неизвестен), source?, locked, can_set}` |
| `POST /api/plugins/kiro-push/birthdate` | `{day, month, year?, consent: true}` | то же, что GET (уже `state: "set"`) |

Ошибки POST: 401 `unauthorized`, 403 `disabled`, 400 `consent_required` / `invalid_date` / `invalid_year` (возраст 5-110,
не в будущем) / `invalid_json`, 409 `already_set`, 429 `rate_limited`. Пользователь может записать только свою строку;
`user_id` в теле игнорируется. `enabled = false` означает «скрыть карточку».

### Администратор, префикс `/api/admin/kiro-push`

Обзор и настройки
- `GET /overview` → `{version, currency, config, wheel_installed, bot_available, birthdates, upcoming, deliveries, backfill, events_total, capabilities}`.
  `capabilities`: виды событий, виды подарков (`wheel_spins.available`), правила 29 февраля, сегменты, виды медиа, кнопок,
  стили, разделы магазина, список подстановок с примерами, лимиты, разрешённые теги, адреса библиотеки эмодзи Core.
- `PUT /config` (любое подмножество настроек) → `{config}`. Ошибки `invalid_<ключ>`, `invalid_timezone`,
  `invalid_default_language`, `invalid_consent_text`.

События
- `GET /events` → `{events: [{id, name, kind, kind_label, enabled, params, audience, send_time, feb29, gift, gift_fallback,
  templates, created_at, updated_at, stats}]}`.
- `POST /events` → 201 `{id, warnings}`; `GET /events/{id}` → `{event}`; `PUT /events/{id}` → `{id, warnings}`;
  `DELETE /events/{id}` (мягкое удаление, история доставок остаётся).
- Тело события: `{name, kind, enabled, params, audience, send_time: "HH:MM", feb29: "feb28"|"mar1", gift, gift_fallback:
  "send_without_gift"|"skip", templates}`.
  - `params`: `birthday` `{offset_days: -30..30}`; `registration_anniversary` `{offset_days, min_years: 1..50}`;
    `fixed_date` `{date: "MM-DD"}`; `one_time` `{date: "YYYY-MM-DD"}`; `manual` `{}`.
  - `audience`: `{segments: ["none","trial","paid"], language: "any"|"ru"|"en", tariff_keys: []}` (допустим также
    `{segment: "all"|"paid"|"trial"|"none"}`). `tariff_keys` ограничивают только платных.
    `skip_blocked` (boolean, по умолчанию `true`; не-boolean → 400 `invalid_audience`): при `true` исключаются пользователи со статусом
    Minishop `users.telegram_notifications_status = 'blocked'` и пользователи из `ext_kiro_push_blocked` моложе `blocked_retry_days`
    дней; при `false` не исключается никто из них (забанённые исключены всегда). Ключ возвращается нормализованным в `GET /events`,
    `GET /events/{id}` и в `capabilities.default_audience` (для старых событий без ключа он равен `true`). Поле также принимает
    `audience` в `POST /events/{id}/run`.
  - `gift`: `{kind: "none"}`, `{kind: "traffic", gb}`, `{kind: "days", days}`, `{kind: "balance", amount_minor}`,
    `{kind: "promo_discount", percent, valid_days (14), applies_to: "subscription"|"all"}`,
    `{kind: "wheel_spins", spins, ignore_cap}`.
  - `templates`: `{ru: Template, en: Template}`; `Template` = `{text, text_no_gift, media: [{kind, id|url|file_id}],
    buttons: [[{kind: "url"|"web_app"|"section", label, url, section, style: ""|"primary"|"success"|"danger",
    icon_custom_emoji_id, icon_emoji}]], disable_notification, protect_content, disable_preview}`.
- Ошибки: `invalid_event` (400, `issues: [...]` понятные тексты, `warnings`), `wheel_not_installed` (409), `name_required`,
  `invalid_kind`, `invalid_send_time`, `invalid_offset_days`, `invalid_min_years`, `invalid_date`, `invalid_feb29`,
  `invalid_audience`, `audience_empty`, `invalid_audience_language`, `invalid_gift_kind`, `invalid_gift_*`,
  `invalid_gift_fallback`, `invalid_language`, `invalid_media`, `invalid_media_url`, `invalid_buttons`,
  `invalid_button_kind`, `invalid_button_style`, `invalid_button_emoji`, `event_not_found` (404).
- `POST /preview` `{event, language, sample_user?, with_gift?}` — предпросмотр несохранённого события;
  `POST /events/{id}/preview` `{language, sample_user?, with_gift?}`. `sample_user`: `@username`/id/telegram id
  (реальные значения пользователя) или объект переопределений `{age: "", name: "..."}`.
  Ответ: `{rendered: {text, media, buttons, disable_notification, protect_content, disable_preview, language,
  used_no_gift, visible_length, warnings}, errors, warnings, gift_text}`. Для черновика с ошибками
  `rendered = null`, `errors` заполнен. 404 `template_missing`, `user_not_found`.
- `POST /events/{id}/test` `{language?, with_gift?, sample_user?}` отправляет сообщение только запросившему админу
  (его Telegram id берётся из сессии: `request["admin_telegram_id"]`), подарок **не выдаётся**. Ошибки 403
  `admin_telegram_unavailable`, 503 `bot_unavailable`, 502 `test_failed` (`detail`).
- `POST /events/{id}/run` `{dry_run?, batch_id?, audience?}` — ручной запуск (kind `manual`, `one_time`, `fixed_date`;
  для `birthday` и `registration_anniversary` 400 `run_not_supported`). Пробный запуск →
  `{dry_run: true, matched, sample: ["@name", ...]}`. Настоящий → `{dry_run: false, batch_id, queued, already, matched, sample}`;
  повтор с тем же `batch_id` ничего не отправит дважды. Ошибки `invalid_batch_id`, `no_recipients`, `too_many_recipients`,
  `templates_required`. Работает и для выключенного события.

Медиа (загрузка по частям, как в kiro-mailer)
- `POST /media` `{kind, size, filename?, content_type?}` → `{id, chunk}` (размер части до 4 МБ; лимиты: фото 10 МБ, остальное 50 МБ).
- `PUT /media/{id}/chunk` — сырые байты, заголовок `X-Offset: <уже загружено>` → `{received}`.
- `POST /media/{id}/finish` → `{id, kind}` (проверка сигнатуры файла). `GET /media` → `{media: [...]}`.
  `GET /media/{id}` — файл. `DELETE /media/{id}` (409 `media_in_use`, если файл в шаблоне).
- В шаблоне ссылка на файл: `{kind, id}`. Ошибки `invalid_media`, `media_too_large`, `invalid_chunk`,
  `chunk_out_of_order` (409), `incomplete_upload`, `unsupported_file`, `not_found`.

Даты рождения
- `GET /birthdates?q=&status=&source=&month=&page=&per_page=` — `q`: id, Telegram id, `@username` или часть имени.
  `status`: `set` (по умолчанию; `all`, если задан `q`), `unknown` (строки нет), `absent`, `blocked`, `error`, `cleared`, `all`.
  → `{total, page, per_page, items: [{user_id, telegram_id, username, first_name, day, month, year, date, source, locked,
  status, attempts, last_attempt_at, fetched_at}]}`.
- `PUT /birthdates/{user_ref}` `{day, month, year?}` — установить или заменить (`user_ref`: id, Telegram id, `@username`, `ms_...`).
  → `{user_id, day, month, year, source: "admin", locked: true}`. Ошибки `invalid_date`, `invalid_year`, `user_not_found` (404).
- `DELETE /birthdates/{user_ref}` — очистить (пользователь сможет ввести заново). 404 `birthdate_not_found`.
- `POST /birthdates/import` `{items: [{user, day, month, year?}], overwrite?}` → `{saved, skipped, errors, results}` (до 2000).

Сканирование Telegram
- `POST /backfill/start` `{dry_run?, rate?, resume?}` → `{backfill}`. Ошибки 409 `already_running`, 409 `nothing_to_resume`,
  400 `invalid_rate` (1-28). Выполняет задание в течение нескольких минут (по ~45 секунд на запуск).
- `POST /backfill/stop`, `GET /backfill/status` → `{backfill: {status: idle|running|stopped|done, dry_run, rate, total,
  scanned, found, absent, blocked, errors, rate_limited, progress, hit_rate, cursor, can_resume, started_at,
  finished_at, updated_at, paused_until, last_error}}`.

Журнал и статистика
- `GET /deliveries?event_id=&status=&period=&user=&page=&per_page=` → `{total, page, per_page, items: [{id, event_id,
  event_name, user_id, username, first_name, period_key, status: pending|sending|sent|skipped|failed|blocked, reason,
  gift_kind, gift_status: pending|granted|not_eligible|unavailable|failed|null, gift_note, promo_code, promo_code_id,
  message_id, attempts, language, scheduled_for, sent_at, created_at, updated_at}]}`.
- `GET /deliveries/download?limit=&mask=1` — последние N доставок файлом JSON; идентификаторы и промокоды маскируются
  (`mask=0` отключает).
- `GET /stats` → `{birthdates, upcoming, deliveries}`.
- `GET /logs?level=&limit=`, `GET /logs/download?mask=1&limit=` (text/plain), `POST /logs/clear`.
- `POST /client-log` `{event, detail}` — диагностика браузера (страница админки перезагрузилась во время правки события, ошибка
  JavaScript). Только администратор, нужен CSRF-токен, ответ `{ok: true}`. `event` — одно из `page_reload_detected`,
  `editor_restored`, `js_error`, `unhandled_rejection`, иначе `400 invalid_client_event`. `detail` — необязательный объект
  размером не более 1 КБ в JSON, значения только строки, числа или `true`/`false`; разрешены ключи `nav_type`, `visibility`,
  `in_telegram`, `ua` (до 160 символов), `editor_open`, `tab`, `message` (до 200), `ms_since_mount`, `last_pagehide`; любой
  другой ключ, неверный тип или превышение размера дают `400 invalid_client_detail`. Не более 20 запросов в минуту на
  администратора (`429 rate_limited`). Запись попадает в журнал плагина с уровнем `warn` и событием `client_<event>`
  (например `client_page_reload_detected`); текст событий, содержимое черновиков и персональные данные не принимаются и не
  хранятся.

Библиотека custom emoji: плагин свой список не держит. Редактор использует API Core (та же сессия администратора):
`GET /api/admin/telegram-emoji/library`, `/catalog`, `/previews`, `/media/{emoji_id}`. В HTML-тексте эмодзи записывается как
`<tg-emoji emoji-id="ID">🎁</tg-emoji>`, в кнопке — полем `icon_custom_emoji_id`.
