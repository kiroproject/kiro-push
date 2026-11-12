"""Telegram side: birthdate lookup (getChat), message delivery and error classification.

Everything the rest of the plugin needs is behind the small ``Gateway`` interface, so the scheduler, the
backfill and the tests run on a fake. ``BotGateway`` is the real implementation on top of Core's aiogram bot;
it imports aiogram lazily and works with Core's custom-emoji fallback middleware (and repeats the same
fallback itself when it is not installed).
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Protocol

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- results


@dataclass
class SendResult:
    status: str  # sent | blocked | failed | retry_after
    error: str = ""
    message_id: int | None = None
    retry_after: int = 0
    fallback_emoji: bool = False  # custom emoji were replaced by plain ones
    fallback_button_style: bool = False  # the button `style` was dropped


@dataclass
class BirthdateLookup:
    status: str  # found | absent | blocked | error | retry_after
    day: int | None = None
    month: int | None = None
    year: int | None = None
    retry_after: int = 0
    error: str = ""


class Gateway(Protocol):
    async def lookup_birthdate(self, chat_id: int) -> BirthdateLookup: ...

    async def send(self, session: Any, *, chat_id: int, user_id: int, message: Any) -> SendResult: ...


# ---------------------------------------------------------------- parsing / classification


def parse_birthdate(raw: Any, *, now: datetime | None = None) -> tuple[int, int, int | None] | None:
    """Telegram ``Birthdate`` (object or dict) -> (day, month, year|None); None when absent/invalid.

    A year outside 1900..current year is dropped (the day and month are kept).
    """
    if raw is None:
        return None
    get = raw.get if isinstance(raw, dict) else lambda k, d=None: getattr(raw, k, d)
    try:
        day, month = int(get("day")), int(get("month"))
    except (TypeError, ValueError):
        return None
    year_raw = get("year", None)
    year: int | None
    try:
        year = int(year_raw) if year_raw not in (None, "", 0) else None
    except (TypeError, ValueError):
        year = None
    current = (now or datetime.now(UTC)).year
    if year is not None and not 1900 <= year <= current:
        year = None
    from .birthdates import valid_calendar_date

    if not valid_calendar_date(day, month, year):
        if year is not None and valid_calendar_date(day, month, None):
            year = None  # e.g. 29 Feb with a non-leap year: keep the date only
        else:
            return None
    return day, month, year


_RETRY_RE = re.compile(r"retry after (\d+)", re.I)
_BLOCKED_HINTS = (
    "bot was blocked",
    "user is deactivated",
    "chat not found",
    "peer_id_invalid",
    "forbidden",
    "have no rights",
    "can't initiate conversation",
    "bot can't send messages to the bot",
)


def classify_error(exc: BaseException) -> tuple[str, str, int]:
    """-> (kind, message, retry_after) with kind in retry_after | blocked | failed. No aiogram import needed."""
    name = type(exc).__name__
    message = str(getattr(exc, "message", "") or exc)[:200]
    retry = getattr(exc, "retry_after", None)
    if retry is not None or name == "TelegramRetryAfter":
        return "retry_after", message, int(retry or 1)
    found = _RETRY_RE.search(message)
    if found and "too many requests" in message.lower():
        return "retry_after", message, int(found.group(1))
    lowered = message.lower()
    if name == "TelegramForbiddenError" or any(hint in lowered for hint in _BLOCKED_HINTS):
        return "blocked", message, 0
    return "failed", message, 0


def custom_emoji_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "custom emoji", "custom_emoji", "emoji_id_invalid", "icon_custom_emoji_id", "premium_account_required",
            "premium account required",
        )
    )


def button_style_error(message: str) -> bool:
    lowered = message.lower()
    return "button_style" in lowered or "button style" in lowered or ("style" in lowered and "button" in lowered)


class _UnicodeFallback(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "tg-emoji":
            self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if tag != "tg-emoji":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")


def unicode_html(value: str) -> str:
    """Replace every ``<tg-emoji emoji-id=..>X</tg-emoji>`` by its plain emoji X (mirrors Core's fallback)."""
    if "tg-emoji" not in value.lower():
        return value
    parser = _UnicodeFallback()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


# ---------------------------------------------------------------- real gateway

CAPTION_MAX = 1024


class BotGateway:
    """Core's aiogram ``Bot`` behind the Gateway interface."""

    def __init__(self, bot: Any, settings: Any = None, i18n: Any = None) -> None:
        self.bot = bot
        self.settings = settings
        self.i18n = i18n
        self._username: str | None = None

    # -------------------------------------------------------------- lookup

    async def lookup_birthdate(self, chat_id: int) -> BirthdateLookup:
        try:
            chat = await self.bot.get_chat(chat_id)
        except Exception as exc:  # noqa: BLE001
            kind, message, retry = classify_error(exc)
            if kind == "retry_after":
                return BirthdateLookup("retry_after", retry_after=retry, error=message)
            if kind == "blocked":
                return BirthdateLookup("blocked", error=message)
            return BirthdateLookup("error", error=message)
        raw = getattr(chat, "birthdate", None)
        if raw is None:
            extra = getattr(chat, "model_extra", None) or {}
            raw = extra.get("birthdate")
        parsed = parse_birthdate(raw)
        if parsed is None:
            return BirthdateLookup("absent")
        return BirthdateLookup("found", day=parsed[0], month=parsed[1], year=parsed[2])

    # -------------------------------------------------------------- sending

    async def _bot_username(self) -> str:
        if self._username is None:
            try:
                self._username = (await self.bot.get_me()).username or ""
            except Exception:  # noqa: BLE001
                self._username = ""
        return self._username

    async def _markup(
        self, rows: list[list[dict[str, Any]]], *, strip_custom: bool, strip_style: bool = False, notes: set[str] | None = None
    ) -> Any:
        if not rows:
            return None
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

        keyboard = []
        for row in rows:
            built = []
            for b in row:
                label = b["label"]
                icon = None if strip_custom else (b.get("icon_custom_emoji_id") or None)
                if b.get("icon_emoji") and not icon:
                    label = f"{b['icon_emoji']} {label}"
                elif strip_custom and b.get("icon_custom_emoji_id") and not b.get("icon_emoji"):
                    label = f"🙂 {label}"
                kwargs: dict[str, Any] = {"text": label}
                if b["kind"] == "web_app":
                    kwargs["web_app"] = WebAppInfo(url=b["url"])
                elif b["kind"] == "section":
                    web_url, url = await self._section_urls(b)
                    if web_url:
                        kwargs["web_app"] = WebAppInfo(url=web_url)
                    else:
                        kwargs["url"] = url
                else:
                    kwargs["url"] = b["url"]
                if icon:
                    kwargs["icon_custom_emoji_id"] = icon
                if b.get("style") and not strip_style:
                    kwargs["style"] = b["style"]
                try:
                    built.append(InlineKeyboardButton(**kwargs))
                except Exception:  # noqa: BLE001 - an aiogram without the `style` field
                    kwargs.pop("style", None)
                    if notes is not None and b.get("style"):
                        notes.add("button_style")
                    built.append(InlineKeyboardButton(**kwargs))
            keyboard.append(built)
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    async def _section_urls(self, button: dict[str, Any]) -> tuple[str | None, str]:
        from bot.services.message_composition import MessageButtonInput, resolve_message_buttons

        resolved = resolve_message_buttons(
            [MessageButtonInput(kind="webapp_section", label=button["label"], section=button.get("section") or "home")],
            mini_app_url=getattr(self.settings, "SUBSCRIPTION_MINI_APP_URL", ""),
            bot_username=await self._bot_username(),
            language=None,
            translate=None,
            default_language=getattr(self.settings, "DEFAULT_LANGUAGE", "ru"),
        )
        first = resolved[0]
        return first.telegram_web_app_url, first.url

    async def _file(self, session: Any, item: dict[str, Any]) -> Any:
        from aiogram.types import BufferedInputFile

        if item.get("url"):
            return item["url"]
        if item.get("file_id"):
            return item["file_id"]
        row = (
            await session.execute(
                text("select filename, body, tg_file_id from ext_kiro_push_media where id = :i and complete"),
                {"i": item.get("id")},
            )
        ).first()
        if row is None:
            return None
        if row[2]:
            return row[2]
        return BufferedInputFile(bytes(row[1]), filename=row[0] or str(item.get("id")))

    @staticmethod
    def _file_id(message: Any, kind: str) -> str | None:
        try:
            return message.photo[-1].file_id if kind == "photo" else getattr(message, kind).file_id
        except Exception:  # noqa: BLE001
            return None

    async def _remember(self, session: Any, item: dict[str, Any], file_id: str | None) -> None:
        if file_id and item.get("id"):
            await session.execute(
                text("update ext_kiro_push_media set tg_file_id = :f where id = :i and tg_file_id is null"),
                {"f": file_id, "i": item["id"]},
            )

    async def send(self, session: Any, *, chat_id: int, user_id: int, message: Any) -> SendResult:
        strip_custom = strip_style = False
        notes: set[str] = set()
        try:
            for _attempt in range(3):
                try:
                    result = await self._send(session, chat_id, message, strip_custom=strip_custom, strip_style=strip_style, notes=notes)
                    result.fallback_emoji = strip_custom
                    result.fallback_button_style = strip_style or "button_style" in notes
                    return result
                except Exception as exc:  # noqa: BLE001
                    kind, text_, _ = classify_error(exc)
                    if kind != "failed":
                        raise
                    # Same recovery as Core's middleware: plain emoji instead of custom ones / no button style, then retry.
                    if custom_emoji_error(text_) and not strip_custom:
                        strip_custom = True
                    elif button_style_error(text_) and not strip_style:
                        strip_style = True
                    else:
                        raise
            raise RuntimeError("send_retries_exhausted")
        except Exception as exc:  # noqa: BLE001
            kind, message_text, retry = classify_error(exc)
            if kind == "retry_after":
                return SendResult("retry_after", f"retry_after:{retry}", retry_after=retry)
            if kind == "blocked":
                return SendResult("blocked", message_text)
            logger.warning("kiro-push: send to user %s failed: %s", user_id, message_text)
            return SendResult("failed", message_text)

    async def _send(
        self, session: Any, chat_id: int, message: Any, *, strip_custom: bool, strip_style: bool = False, notes: set[str] | None = None
    ) -> SendResult:
        body = unicode_html(message.text) if strip_custom else message.text
        common = {"disable_notification": bool(message.disable_notification), "protect_content": bool(message.protect_content)}
        markup = await self._markup(message.buttons, strip_custom=strip_custom, strip_style=strip_style, notes=notes)
        media = message.media
        if not media:
            sent = await self.bot.send_message(
                chat_id, body, parse_mode="HTML", disable_web_page_preview=bool(message.disable_preview),
                reply_markup=markup, **common,
            )
            return SendResult("sent", message_id=sent.message_id)
        if visible_length(body) > CAPTION_MAX:
            first = await self.bot.send_message(
                chat_id, body, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup, **common
            )
            await self._send_media(session, chat_id, media, "", None, common)
            return SendResult("sent", message_id=first.message_id)
        sent_id = await self._send_media(session, chat_id, media, body, markup, common)
        return SendResult("sent", message_id=sent_id)

    async def _send_media(
        self, session: Any, chat_id: int, media: list[dict[str, Any]], caption: str, markup: Any, common: dict[str, Any]
    ) -> int | None:
        files = []
        for item in media:
            file = await self._file(session, item)
            if file is None:
                raise RuntimeError("media_missing")
            files.append((file, item["kind"], item))
        if len(files) == 1:
            file, kind, item = files[0]
            sender = getattr(self.bot, f"send_{kind}")
            sent = await sender(
                chat_id, file, caption=caption or None, parse_mode="HTML" if caption else None, reply_markup=markup, **common
            )
            await self._remember(session, item, self._file_id(sent, kind))
            return sent.message_id
        from aiogram.types import InputMediaDocument, InputMediaPhoto, InputMediaVideo

        group = []
        for index, (file, kind, _item) in enumerate(files):
            extra = {"caption": caption, "parse_mode": "HTML"} if index == 0 and caption else {}
            cls = {"photo": InputMediaPhoto, "video": InputMediaVideo, "document": InputMediaDocument}[kind]
            group.append(cls(media=file, **extra))
        sent_group = await self.bot.send_media_group(chat_id, media=group, **common)
        for (_f, kind, item), msg in zip(files, sent_group, strict=False):
            await self._remember(session, item, self._file_id(msg, kind))
        return sent_group[0].message_id if sent_group else None


_TAG = re.compile(r"<[^>]*>")


def visible_length(markup: str) -> int:
    """Length Telegram counts for an HTML text: tags stripped, entities decoded."""
    return len(html.unescape(_TAG.sub("", markup)))
