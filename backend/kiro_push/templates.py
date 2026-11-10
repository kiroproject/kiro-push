"""Message templates: data model, validation, placeholder rendering and preview.

Template (one per language)::

    {"text": "...", "text_no_gift": "...",            # Telegram HTML subset, as Core broadcasts
     "media": [{"kind": "photo|video|animation|document", "id"|"url"|"file_id": "..."}],
     "buttons": [[{"kind": "url|web_app|section", "label": "...", "url": "...", "section": "home",
                   "style": "primary|success|danger", "icon_custom_emoji_id": "123", "icon_emoji": "🎁"}]],
     "disable_notification": false, "protect_content": false, "disable_preview": true}

Placeholders ``{name}`` are HTML-escaped. ``[[ ... {age} ... ]]`` is an optional block: it disappears when any
placeholder inside it is empty. Custom emoji: ``<tg-emoji emoji-id="123">🎁</tg-emoji>`` (the sender falls back to
the plain emoji when Telegram rejects it).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from .errors import PushError
from .telegram import visible_length

MAX_TEXT = 4096
MAX_CAPTION = 1024
STORED_TEXT_MAX = 12000
MAX_MEDIA = 10
MAX_BUTTON_ROWS = 5
MAX_BUTTONS_PER_ROW = 3
MAX_BUTTONS = 8
MAX_LABEL = 64
MEDIA_KINDS = ("photo", "video", "animation", "document")
BUTTON_KINDS = ("url", "web_app", "section")
BUTTON_STYLES = ("primary", "success", "danger")
SECTIONS = ("home", "plans", "install", "trial", "invite", "devices", "support", "settings")
GIFT_PLACEHOLDERS = ("gift", "promo_code", "promo_discount")

# name -> (description, sample value for previews)
PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "name": ("Имя: имя в Telegram, иначе @username, иначе «друг»", "Алексей"),
    "first_name": ("Имя из профиля (может быть пустым)", "Алексей"),
    "age": ("Возраст (только если известен год рождения)", "30"),
    "years": ("Сколько лет с регистрации (годовщина)", "3"),
    "days_left": ("Дней до конца подписки (0, если подписки нет)", "12"),
    "tariff": ("Название тарифа подписки", "Premium"),
    "gift": ("Подарок текстом, например «+7 дней»", "+7 дней"),
    "promo_code": ("Персональный промокод (подарок «скидка»)", "PUSH-AB12CD"),
    "promo_discount": ("Размер скидки по промокоду", "20%"),
    "balance": ("Баланс пользователя", "150 ₽"),
    "spins": ("Билеты колеса удачи (если плагин установлен)", "3"),
}

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_BLOCK = re.compile(r"\[\[(.*?)\]\]", re.S)
_DROPPED = "\x00"
_ID = re.compile(r"^[A-Za-z0-9]{1,40}$")
_EMOJI_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{8,200}$")

ALLOWED_TAGS = frozenset(
    {"a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "tg-spoiler",
     "tg-emoji", "span"}
)
_VOID: frozenset[str] = frozenset()
_HREF_OK = ("https://", "http://", "tg://")


# ------------------------------------------------------------------ html lint


class _Lint(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[str] = []
        self._emoji: list[str] | None = None

    def _fail(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            self._fail(f"Тег <{tag}> не поддерживается Telegram")
            return
        attrs_d = dict(attrs)
        if self._emoji is not None and tag != "tg-emoji":
            self._fail("Внутри <tg-emoji> не должно быть другой разметки")
        if tag == "a":
            href = (attrs_d.get("href") or "").strip()
            if not href:
                self._fail("У ссылки <a> нужен href")
            elif "{" not in href and not href.lower().startswith(_HREF_OK):
                self._fail(f"Ссылка должна начинаться с https://, http:// или tg:// ({href[:40]})")
        elif tag == "tg-emoji":
            if len(attrs) != 1 or not _EMOJI_ID.match(attrs_d.get("emoji-id") or ""):
                self._fail("У <tg-emoji> нужен числовой emoji-id")
            if "tg-emoji" in self.stack:
                self._fail("<tg-emoji> нельзя вкладывать друг в друга")
            self._emoji = []
        elif tag == "span" and attrs_d.get("class") != "tg-spoiler":
            self._fail('Тег <span> допустим только как <span class="tg-spoiler">')
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ALLOWED_TAGS:
            self._fail(f"Тег </{tag}> не поддерживается Telegram")
            return
        if not self.stack or self.stack[-1] != tag:
            self._fail(f"Незакрытый или перепутанный тег </{tag}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()
        if tag == "tg-emoji":
            content = "".join(self._emoji or [])
            if not content or len(content) > 16 or any(c.isalnum() or c.isspace() for c in content):
                self._fail("Внутри <tg-emoji> должен быть один обычный эмодзи (запасной вариант)")
            self._emoji = None

    def handle_data(self, data: str) -> None:
        if self._emoji is not None:
            self._emoji.append(data)

    def finish(self) -> list[str]:
        self.close()
        if self.stack:
            self._fail(f"Не закрыт тег <{self.stack[-1]}>")
        return self.errors


def html_errors(markup: str) -> list[str]:
    linter = _Lint()
    try:
        linter.feed(markup or "")
        return linter.finish()
    except Exception:  # noqa: BLE001
        return ["Не удалось разобрать HTML-разметку"]


# ------------------------------------------------------------------ rendering


def placeholders_in(source: str) -> list[str]:
    return _PLACEHOLDER.findall(source or "")


def unknown_placeholders(source: str) -> list[str]:
    seen: dict[str, None] = {}
    for name in placeholders_in(source):
        if name not in PLACEHOLDERS:
            seen.setdefault(name)
    return list(seen)


def render_text(source: str, values: dict[str, str | None], *, escape: bool = True) -> str:
    """Substitute placeholders; optional ``[[ ]]`` blocks vanish when a placeholder inside has no value."""

    def value_of(name: str) -> str:
        raw = values.get(name)
        return "" if raw is None else str(raw)

    def sub(match: re.Match[str], inner: str | None = None) -> str:
        name = match.group(1)
        if name not in PLACEHOLDERS:
            return match.group(0)
        value = value_of(name)
        return html.escape(value, quote=False) if escape else value

    def block(match: re.Match[str]) -> str:
        inner = match.group(1)
        names = [n for n in placeholders_in(inner) if n in PLACEHOLDERS]
        if any(not value_of(n).strip() for n in names):
            return _DROPPED
        return _PLACEHOLDER.sub(sub, inner)

    rendered = _BLOCK.sub(block, source or "")
    # a dropped block that sat alone on its line takes the line with it
    rendered = re.sub(r"^[ \t]*(?:\x00[ \t]*)+(?:\n|\Z)", "", rendered, flags=re.M)
    rendered = rendered.replace(_DROPPED, "")
    rendered = _PLACEHOLDER.sub(sub, rendered)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def render_url(source: str, values: dict[str, str | None]) -> str:
    """Placeholders inside a button URL are URL-quoted instead of HTML-escaped."""
    return _PLACEHOLDER.sub(
        lambda m: quote(str(values.get(m.group(1)) or ""), safe="") if m.group(1) in PLACEHOLDERS else m.group(0), source or ""
    )


@dataclass
class Rendered:
    text: str
    media: list[dict[str, Any]] = field(default_factory=list)
    buttons: list[list[dict[str, Any]]] = field(default_factory=list)
    disable_notification: bool = False
    protect_content: bool = False
    disable_preview: bool = True
    language: str = "ru"
    used_no_gift: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "media": self.media, "buttons": self.buttons,
            "disable_notification": self.disable_notification, "protect_content": self.protect_content,
            "disable_preview": self.disable_preview, "language": self.language, "used_no_gift": self.used_no_gift,
            "visible_length": visible_length(self.text), "warnings": self.warnings,
        }


def is_empty(tpl: dict[str, Any] | None) -> bool:
    return not tpl or (not str(tpl.get("text") or "").strip() and not tpl.get("media"))


def pick_template(templates: dict[str, Any], language: str | None, default: str = "ru") -> tuple[str, dict[str, Any]] | None:
    """Template for a user language: exact, then the default language, then any non-empty one."""
    lang = str(language or "").lower().split("-")[0].split("_")[0]
    for candidate in (lang, default, *templates):
        tpl = templates.get(candidate)
        if tpl is not None and not is_empty(tpl):
            return candidate, tpl
    return None


def render_message(
    tpl: dict[str, Any], values: dict[str, str | None], *, gift_available: bool, language: str = "ru"
) -> Rendered:
    """Final text/media/buttons for one user. ``text_no_gift`` replaces ``text`` when no gift was given."""
    vals = dict(values)
    use_alt = not gift_available and bool(str(tpl.get("text_no_gift") or "").strip())
    if not gift_available:
        for name in GIFT_PLACEHOLDERS:
            vals[name] = ""
    source = tpl["text_no_gift"] if use_alt else tpl.get("text", "")
    rows: list[list[dict[str, Any]]] = []
    for row in tpl.get("buttons") or []:
        out_row = []
        for b in row:
            item = dict(b)
            if item["kind"] in ("url", "web_app"):
                item["url"] = render_url(item["url"], vals)
            item["label"] = render_text(item["label"], vals, escape=False)
            out_row.append(item)
        rows.append(out_row)
    return Rendered(
        text=render_text(source, vals),
        media=[dict(m) for m in tpl.get("media") or []],
        buttons=rows,
        disable_notification=bool(tpl.get("disable_notification")),
        protect_content=bool(tpl.get("protect_content")),
        disable_preview=bool(tpl.get("disable_preview", True)),
        language=language,
        used_no_gift=use_alt,
    )


# ------------------------------------------------------------------ cleaning / validation


def _s(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def clean_template(raw: Any) -> dict[str, Any]:
    """Normalise one template (structure only; content problems are reported by ``validate_template``)."""
    raw = raw if isinstance(raw, dict) else {}
    media: list[dict[str, str]] = []
    for item in (raw.get("media") or [])[:MAX_MEDIA]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in MEDIA_KINDS:
            raise PushError("invalid_media")
        entry: dict[str, str] = {"kind": kind}
        if item.get("id"):
            if not _ID.match(str(item["id"])):
                raise PushError("invalid_media")
            entry["id"] = str(item["id"])
        elif item.get("url"):
            url = _s(item["url"], 1000)
            if not url.startswith("https://"):
                raise PushError("invalid_media_url")
            entry["url"] = url
        elif item.get("file_id"):
            if not _FILE_ID.match(str(item["file_id"])):
                raise PushError("invalid_media")
            entry["file_id"] = str(item["file_id"])
        else:
            raise PushError("invalid_media")
        media.append(entry)
    rows: list[list[dict[str, str]]] = []
    for raw_row in (raw.get("buttons") or [])[:MAX_BUTTON_ROWS]:
        if not isinstance(raw_row, list):
            raise PushError("invalid_buttons")
        row = []
        for item in raw_row[:MAX_BUTTONS_PER_ROW]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "url")
            if kind not in BUTTON_KINDS:
                raise PushError("invalid_button_kind")
            style = str(item.get("style") or "")
            if style and style not in BUTTON_STYLES:
                raise PushError("invalid_button_style")
            emoji_id = str(item.get("icon_custom_emoji_id") or "")
            if emoji_id and not _EMOJI_ID.match(emoji_id):
                raise PushError("invalid_button_emoji")
            section = str(item.get("section") or "home")
            row.append(
                {
                    "kind": kind,
                    "label": _s(item.get("label"), 120),
                    "url": _s(item.get("url"), 512) if kind != "section" else "",
                    "section": section if section in SECTIONS else "home",
                    "style": style,
                    "icon_custom_emoji_id": emoji_id,
                    "icon_emoji": _s(item.get("icon_emoji"), 16),
                }
            )
        if row:
            rows.append(row)
    return {
        "text": str(raw.get("text") or "").strip()[:STORED_TEXT_MAX],
        "text_no_gift": str(raw.get("text_no_gift") or "").strip()[:STORED_TEXT_MAX],
        "media": media,
        "buttons": rows,
        "disable_notification": bool(raw.get("disable_notification")),
        "protect_content": bool(raw.get("protect_content")),
        "disable_preview": bool(raw.get("disable_preview", True)),
    }


def _long_values() -> dict[str, str]:
    """Worst-case values: long names and amounts, so a length check is not fooled by short samples."""
    return {
        "name": "Н" * 64, "first_name": "Н" * 64, "age": "99", "years": "20", "days_left": "9999",
        "tariff": "Т" * 40, "gift": "+1000 ГБ трафика и 365 дней", "promo_code": "P" * 24, "promo_discount": "100%",
        "balance": "1 000 000,00 ₽", "spins": "99",
    }


def validate_template(tpl: dict[str, Any], *, gift_kind: str = "none") -> tuple[list[str], list[str]]:
    """-> (errors, warnings). ``tpl`` must come from ``clean_template``."""
    errors: list[str] = []
    warnings: list[str] = []
    media, buttons = tpl["media"], tpl["buttons"]
    if not tpl["text"] and not media:
        errors.append("Добавьте текст или медиа")
    limit = MAX_CAPTION if media else MAX_TEXT
    for key, label in (("text", "Текст"), ("text_no_gift", "Текст без подарка")):
        source = tpl[key]
        if not source:
            continue
        unknown = unknown_placeholders(source)
        if unknown:
            errors.append(f"{label}: неизвестные подстановки {', '.join('{' + n + '}' for n in unknown)}")
        if source.count("[[") != source.count("]]"):
            errors.append(f"{label}: незакрытый необязательный блок [[ ... ]]")
        for sample_name, vals in (("полный", _long_values()), ("без необязательных", {n: "" for n in PLACEHOLDERS})):
            for problem in html_errors(render_text(source, vals)):
                msg = f"{label}: {problem}"
                if msg not in errors:
                    errors.append(msg)
        size = visible_length(render_text(source, _long_values()))
        if size > limit:
            errors.append(
                f"{label}: {size} знаков при допустимых {limit}"
                + (" (подпись к медиа не длиннее 1024)" if media else "")
            )
        elif size > limit * 0.9:
            warnings.append(f"{label}: {size} из {limit} знаков, подстановки могут не поместиться")
        if gift_kind == "none" and any(n in GIFT_PLACEHOLDERS for n in placeholders_in(source)):
            warnings.append(f"{label}: подстановки подарка есть, а подарок в событии не выбран")
        if gift_kind != "promo_discount" and any(n in ("promo_code", "promo_discount") for n in placeholders_in(source)):
            warnings.append(f"{label}: {{promo_code}} и {{promo_discount}} заполняются только подарком «скидка»")
    if len(media) > 1:
        kinds = {m["kind"] for m in media}
        if "animation" in kinds:
            errors.append("GIF-анимацию нельзя отправлять в альбоме")
        elif "document" in kinds and kinds != {"document"}:
            errors.append("Документы можно объединять в альбом только с другими документами")
        if buttons:
            errors.append("К альбому нельзя прикрепить кнопки: оставьте одно медиа или уберите кнопки")
    total = 0
    for row in buttons:
        for b in row:
            total += 1
            if not b["label"]:
                errors.append("У кнопки нужна подпись")
            elif len(b["label"]) > MAX_LABEL:
                errors.append(f"Подпись кнопки длиннее {MAX_LABEL} знаков: {b['label'][:20]}…")
            for name in unknown_placeholders(b["label"]) + unknown_placeholders(b["url"]):
                errors.append(f"Кнопка «{b['label'][:20]}»: неизвестная подстановка {{{name}}}")
            if b["kind"] == "url" and not b["url"].startswith(("https://", "http://", "tg://")):
                errors.append(f"Ссылка кнопки «{b['label'][:20]}» должна начинаться с https://")
            if b["kind"] == "web_app" and not b["url"].startswith("https://"):
                errors.append(f"Адрес Mini App кнопки «{b['label'][:20]}» должен начинаться с https://")
            if b["icon_custom_emoji_id"] and b["icon_emoji"]:
                warnings.append(f"Кнопка «{b['label'][:20]}»: задан и custom emoji, и обычный; обычный станет запасным")
    if total > MAX_BUTTONS:
        errors.append(f"Кнопок не больше {MAX_BUTTONS}")
    return errors, warnings


def clean_templates(raw: Any, *, gift_kind: str = "none") -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Validate the per-language map. Returns (templates, errors, warnings); empty templates are dropped."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for lang, item in raw.items():
        lang = str(lang).lower()
        if lang not in ("ru", "en"):
            raise PushError("invalid_language")
        tpl = clean_template(item)
        if is_empty(tpl):
            continue
        errs, warns = validate_template(tpl, gift_kind=gift_kind)
        errors.extend(f"[{lang}] {e}" for e in errs)
        warnings.extend(f"[{lang}] {w}" for w in warns)
        out[lang] = tpl
    return out, errors, warnings


def preview_warnings(tpl: dict[str, Any], event: dict[str, Any], *, has_year_sample: bool = True) -> list[str]:
    """Hints for the editor about a template that is valid but may read badly for some users."""
    warnings: list[str] = []
    gift_kind = (event.get("gift") or {}).get("kind", "none")
    source = tpl.get("text", "")
    outside = _BLOCK.sub("", source)
    names_outside = placeholders_in(outside)
    if "age" in names_outside:
        warnings.append("{age} вне блока [[ ]]: у пользователей без года рождения подстановка будет пустой")
    if gift_kind != "none" and event.get("gift_fallback") == "send_without_gift" and not tpl.get("text_no_gift"):
        if any(n in GIFT_PLACEHOLDERS for n in names_outside):
            warnings.append(
                "Текст без подарка не задан: если подарок выдать нельзя, фраза с подарком окажется пустой. "
                "Заключите её в [[ ]] или заполните «текст без подарка»"
            )
    if event.get("kind") != "registration_anniversary" and "years" in names_outside:
        warnings.append("{years} имеет смысл только в событии «годовщина регистрации»")
    if event.get("kind") not in ("birthday",) and "age" in placeholders_in(source):
        warnings.append("{age} заполняется только в событии «день рождения»")
    return warnings
