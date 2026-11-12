"""Templates: placeholders, optional blocks, escaping, validation (HTML, length, media, buttons), custom emoji fallback,
the Telegram sender (fake bot + fake aiogram types).

Run:  python tests/run_templates_tests.py     (no database needed)
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

errors, tpl, tg = h.load("errors", "templates", "telegram")
PushError = errors.PushError

VALUES = {
    "name": "Алексей", "first_name": "Алексей", "age": "30", "years": "3", "days_left": "12", "tariff": "Premium",
    "gift": "+7 дней", "promo_code": "PUSH-1", "promo_discount": "20%", "balance": "150 ₽", "spins": "2",
}


def render_tests() -> None:
    print("rendering")
    r = tpl.render_text
    h.ok(r("Привет, {name}!", VALUES) == "Привет, Алексей!", "placeholder substituted")
    h.ok(r("{name} {first_name} {age} {years} {days_left} {tariff} {gift} {promo_code} {promo_discount} {balance} {spins}", VALUES)
         == "Алексей Алексей 30 3 12 Premium +7 дней PUSH-1 20% 150 ₽ 2", "every placeholder")
    h.ok(r("{name}", {**VALUES, "name": '<b>"x"</b> & co'}) == "&lt;b&gt;\"x\"&lt;/b&gt; &amp; co", "user values are HTML-escaped")
    h.ok(r("{name}", {**VALUES, "name": '<script>alert(1)</script>'}) == "&lt;script&gt;alert(1)&lt;/script&gt;", "script in a name is neutralised")
    h.ok(r("<b>{name}</b>", VALUES) == "<b>Алексей</b>", "template markup is kept")
    h.ok(r("{unknown} {name}", VALUES) == "{unknown} Алексей", "unknown placeholder is left as written")
    h.ok(r("a{age}b", {**VALUES, "age": ""}) == "ab", "empty placeholder outside a block renders empty")
    h.ok(r("Вам [[исполнилось {age} лет]]!", VALUES) == "Вам исполнилось 30 лет!", "optional block kept when its values exist")
    h.ok(r("Вам [[исполнилось {age} лет]]!", {**VALUES, "age": ""}) == "Вам !", "optional block dropped when a value is empty")
    h.ok(r("Вам [[исполнилось {age} лет]]!", {**VALUES, "age": None}) == "Вам !", "…also for a missing (None) value")
    h.ok(r("A[[ {age} и {years}]]B", {**VALUES, "years": ""}) == "AB", "any empty placeholder drops the whole block")
    h.ok(r("[[x]]y", VALUES) == "xy", "block without placeholders is always kept")
    h.ok(r("Привет\n[[Подарок: {gift}]]\nПока", {**VALUES, "gift": ""}) == "Привет\nПока", "dropped line does not leave a blank gap")
    h.ok(r("Привет\n\n[[Подарок: {gift}]]\n\nПока", {**VALUES, "gift": ""}) == "Привет\n\nПока", "blank gaps collapse")
    h.ok(r("[[{age}]] [[{years}]]", {**VALUES, "age": ""}) == "3", "several blocks are independent")
    h.ok(r("{promo_code}", VALUES, escape=False) == "PUSH-1", "raw mode")
    h.ok(tpl.render_url("https://x.io/?c={promo_code}&n={name}", {**VALUES, "name": "А б&"}) == "https://x.io/?c=PUSH-1&n=%D0%90%20%D0%B1%26", "URL placeholders are quoted")
    h.ok(tpl.unknown_placeholders("{name} {foo} {bar} {foo}") == ["foo", "bar"], "unknown placeholders listed once")

    t = tpl.clean_template({"text": "Hi {name}[[ {gift}]]", "text_no_gift": "Hi {name} (no gift)", "buttons": [[{"kind": "url", "label": "Get {gift}", "url": "https://x.io/?p={promo_code}"}]]})
    out = tpl.render_message(t, VALUES, gift_available=True)
    h.ok(out.text == "Hi Алексей +7 дней" and out.buttons[0][0]["url"] == "https://x.io/?p=PUSH-1" and out.buttons[0][0]["label"] == "Get +7 дней" and not out.used_no_gift, "message with a gift")
    out = tpl.render_message(t, VALUES, gift_available=False)
    h.ok(out.text == "Hi Алексей (no gift)" and out.used_no_gift and out.buttons[0][0]["url"] == "https://x.io/?p=", "without a gift: text_no_gift, gift placeholders empty")
    t2 = tpl.clean_template({"text": "Hi {name}[[ {gift}]]"})
    h.ok(tpl.render_message(t2, VALUES, gift_available=False).text == "Hi Алексей", "without a gift and no alt text: gift block dropped")
    h.ok(tpl.pick_template({"ru": {"text": "a"}, "en": {"text": "b"}}, "en-US", "ru")[0] == "en", "pick: exact language")
    h.ok(tpl.pick_template({"ru": {"text": "a"}, "en": {"text": "b"}}, "de", "ru")[0] == "ru", "pick: default language")
    h.ok(tpl.pick_template({"en": {"text": "b"}}, "ru", "ru")[0] == "en", "pick: whatever exists")
    h.ok(tpl.pick_template({"ru": {"text": "", "media": []}}, "ru", "ru") is None, "pick: empty templates do not count")


def validation_tests() -> None:
    print("validation")

    def check(text_: str, **extra) -> tuple[list[str], list[str]]:
        return tpl.validate_template(tpl.clean_template({"text": text_, **extra}))

    h.ok(check("<b>Привет</b>, {name}! <i>x</i> <u>y</u> <s>z</s> <code>c</code> <a href=\"https://x.io\">l</a>")[0] == [], "valid Telegram HTML")
    h.ok(check("<tg-spoiler>s</tg-spoiler> <blockquote>q</blockquote> <pre>p</pre>")[0] == [], "spoiler / quote / pre")
    for bad, label in (
        ("<b>открыт", "unclosed tag"),
        ("<b><i>x</b></i>", "crossed tags"),
        ("x</b>", "stray closing tag"),
        ("<div>x</div>", "div is not allowed"),
        ("<script>x</script>", "script is not allowed"),
        ("<a>no href</a>", "link without href"),
        ('<a href="javascript:alert(1)">x</a>', "javascript: link"),
        ('<tg-emoji emoji-id="abc">🎁</tg-emoji>', "non-numeric emoji id"),
        ('<tg-emoji emoji-id="0123">🎁</tg-emoji>', "emoji id with leading zero"),
        ('<tg-emoji emoji-id="5">text</tg-emoji>', "emoji fallback must be an emoji"),
        ('<tg-emoji emoji-id="5"></tg-emoji>', "empty emoji fallback"),
        ('<tg-emoji emoji-id="5"><b>🎁</b></tg-emoji>', "markup inside tg-emoji"),
        ('<span>x</span>', "span without tg-spoiler class"),
    ):
        h.ok(bool(check(bad)[0]), f"rejects {label}")
    h.ok(check('<tg-emoji emoji-id="5368324170671202286">🎁</tg-emoji> привет')[0] == [], "custom emoji with a plain-emoji fallback is valid")
    h.ok(check('<span class="tg-spoiler">x</span>')[0] == [], 'span class="tg-spoiler"')
    errs, _ = check("Привет {nmae}")
    h.ok(any("{nmae}" in e for e in errs), "unknown placeholder reported by name")
    errs, _ = check("[[ не закрыт {age}")
    h.ok(any("[[" in e for e in errs), "unbalanced optional block reported")
    errs, _ = check("[[ <b>{age}]] </b>")
    h.ok(bool(errs), "optional block that unbalances the HTML when dropped is caught")
    h.ok(check("я" * 4096)[0] == [], "exactly 4096 chars is fine")
    errs, _ = check("я" * 5000)
    h.ok(any("5000" in e or "допустим" in e for e in errs), "text over 4096 rejected")
    errs, _ = check("я" * 4090 + "{name}")
    h.ok(bool(errs), "length is checked with worst-case placeholder values")
    h.ok(check("я" * 3000)[0] == [], "3000 chars fine without media")
    errs, _ = check("я" * 1100, media=[{"kind": "photo", "id": "abc123"}])
    h.ok(bool(errs), "caption over 1024 with media rejected")
    h.ok(check("я" * 900, media=[{"kind": "photo", "id": "abc123"}])[0] == [], "caption 900 fine")
    h.ok(check("", media=[{"kind": "photo", "url": "https://x.io/a.jpg"}])[0] == [], "media without text is fine")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": ""}))[0]), "empty template rejected")
    # tags don't count towards the limit, entities are decoded
    h.ok(check("<b>" + "я" * 4000 + "</b>")[0] == [], "tags are not counted")
    h.ok(check("&amp;" * 800, media=[{"kind": "photo", "id": "abc"}])[0] == [], "entities count as one character")
    # media
    album = [{"kind": "photo", "id": "a1"}, {"kind": "video", "id": "a2"}]
    h.ok(check("x", media=album)[0] == [], "photo + video album")
    h.ok(bool(check("x", media=album, buttons=[[{"kind": "url", "label": "l", "url": "https://x.io"}]])[0]), "album with buttons rejected")
    h.ok(bool(check("x", media=[{"kind": "animation", "id": "a1"}, {"kind": "photo", "id": "a2"}])[0]), "GIF in an album rejected")
    h.ok(bool(check("x", media=[{"kind": "document", "id": "a1"}, {"kind": "photo", "id": "a2"}])[0]), "document mixed with photo rejected")
    for bad_media, code in (
        ([{"kind": "audio", "id": "a"}], "invalid_media"),
        ([{"kind": "photo"}], "invalid_media"),
        ([{"kind": "photo", "id": "../x"}], "invalid_media"),
        ([{"kind": "photo", "url": "http://x.io/a.jpg"}], "invalid_media_url"),
    ):
        try:
            tpl.clean_template({"text": "x", "media": bad_media})
        except PushError as exc:
            h.ok(exc.code == code, f"media {bad_media[0]} -> {code}")
        else:
            raise AssertionError("accepted bad media")
    c = tpl.clean_template({"text": "x", "media": [{"kind": "photo", "file_id": "AgACAgIAAxkBAAIB"}, {"kind": "video", "url": "https://x.io/v.mp4"}]})
    h.ok(len(c["media"]) == 2, "media by file_id and by url accepted")
    # buttons
    btn = {"kind": "url", "label": "Открыть", "url": "https://x.io", "style": "success", "icon_custom_emoji_id": "5368324170671202286", "icon_emoji": "🎁"}
    c = tpl.clean_template({"text": "x", "buttons": [[btn, {"kind": "web_app", "label": "App", "url": "https://app.x.io"}], [{"kind": "section", "label": "План", "section": "plans"}]]})
    h.ok(tpl.validate_template(c)[0] == [] and c["buttons"][0][0]["style"] == "success" and c["buttons"][1][0]["section"] == "plans", "url / web_app / section buttons with style and icon")
    for patch, code in (({"style": "pink"}, "invalid_button_style"), ({"kind": "callback"}, "invalid_button_kind"), ({"icon_custom_emoji_id": "x1"}, "invalid_button_emoji")):
        try:
            tpl.clean_template({"text": "x", "buttons": [[{**btn, **patch}]]})
        except PushError as exc:
            h.ok(exc.code == code, f"button {list(patch)[0]} -> {code}")
        else:
            raise AssertionError("accepted bad button")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": [[{**btn, "url": "ftp://x"}]]}))[0]), "button url scheme checked")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": [[{**btn, "kind": "web_app", "url": "http://x.io"}]]}))[0]), "web_app must be https")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": [[{**btn, "label": ""}]]}))[0]), "button needs a label")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": [[{**btn, "label": "я" * 70}]]}))[0]), "label over 64")
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": [[{**btn, "label": "{foo}"}]]}))[0]), "unknown placeholder in a label")
    many = [[btn] * 3] * 3
    h.ok(bool(tpl.validate_template(tpl.clean_template({"text": "x", "buttons": many}))[0]), "more than 8 buttons rejected")
    h.ok(len(tpl.clean_template({"text": "x", "buttons": [[btn] * 5] * 9})["buttons"]) == 5, "rows / buttons per row capped")
    # templates map and warnings
    out, errs, warns = tpl.clean_templates({"ru": {"text": "Привет {nmae}"}, "en": {"text": ""}})
    h.ok(list(out) == ["ru"] and errs and errs[0].startswith("[ru]"), "empty language dropped, errors prefixed with the language")
    try:
        tpl.clean_templates({"de": {"text": "x"}})
    except PushError as exc:
        h.ok(exc.code == "invalid_language", "unsupported language rejected")
    out, errs, warns = tpl.clean_templates({"ru": {"text": "Подарок {gift} {promo_code}"}}, gift_kind="days")
    h.ok(any("promo_code" in w for w in warns) and not errs, "warning: promo placeholders without a promo gift")
    out, errs, warns = tpl.clean_templates({"ru": {"text": "x {gift}"}}, gift_kind="none")
    h.ok(any("подарок в событии не выбран" in w for w in warns), "warning: gift placeholder but no gift")
    ev = {"kind": "birthday", "gift": {"kind": "days"}, "gift_fallback": "send_without_gift"}
    w = tpl.preview_warnings(tpl.clean_template({"text": "Вам {age}! Подарок {gift}"}), ev)
    h.ok(len(w) == 2, f"preview hints: {{age}} outside block + gift without fallback text ({len(w)})")
    w = tpl.preview_warnings(tpl.clean_template({"text": "Вам[[ {age}]]! [[Подарок {gift}]]"}), ev)
    h.ok(w == [], "no hints when optional blocks are used")


class FakeBot:
    def __init__(self, fail_first: str | None = None, always: str | None = None) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail_first, self.always = fail_first, always

    async def send_message(self, chat_id, text_, **kw):
        self.calls.append(("send_message", (chat_id, text_), kw))
        if self.always:
            raise Exception(self.always)
        if self.fail_first and len([c for c in self.calls if c[0] == "send_message"]) == 1:
            raise Exception(self.fail_first)
        return SimpleNamespace(message_id=55)

    async def send_photo(self, chat_id, file, **kw):
        self.calls.append(("send_photo", (chat_id, file), kw))
        if self.always:
            raise Exception(self.always)
        return SimpleNamespace(message_id=56, photo=[SimpleNamespace(file_id="FILEID")])

    async def get_chat(self, chat_id):
        raise Exception("Too Many Requests: retry after 9")


def install_fake_aiogram() -> None:
    class Btn:
        def __init__(self, **kw):
            if "style" in kw and Btn.reject_style:
                raise ValueError("style not supported")
            self.__dict__.update(kw)

        reject_style = False

    class Markup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    for name in ("aiogram", "aiogram.types"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["aiogram.types"].__dict__.update(
        InlineKeyboardButton=Btn, InlineKeyboardMarkup=Markup, WebAppInfo=lambda url: SimpleNamespace(url=url),
        BufferedInputFile=lambda data, filename=None: SimpleNamespace(data=data, filename=filename),
    )
    sys.modules["aiogram"].types = sys.modules["aiogram.types"]
    return Btn


async def sender_tests() -> None:
    print("telegram sender")
    Btn = install_fake_aiogram()
    msg = tpl.Rendered(text='Привет <tg-emoji emoji-id="5368324170671202286">🎁</tg-emoji>', disable_notification=True, protect_content=True)
    bot = FakeBot()
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg)
    kw = bot.calls[0][2]
    h.ok(res.status == "sent" and res.message_id == 55 and kw["parse_mode"] == "HTML" and kw["disable_notification"] and kw["protect_content"], "plain text message with flags")
    h.ok("tg-emoji" in bot.calls[0][1][1], "custom emoji sent as written first")
    bot = FakeBot(fail_first="Bad Request: DOCUMENT_INVALID custom emoji identifier is invalid")
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg)
    h.ok(res.status == "sent" and len(bot.calls) == 2 and "tg-emoji" not in bot.calls[1][1][1] and "🎁" in bot.calls[1][1][1], "Telegram rejects the custom emoji -> retried with the plain emoji")
    h.ok(res.fallback_emoji is True and res.fallback_button_style is False, "result flags the custom-emoji fallback")
    bot = FakeBot()
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg)
    h.ok(res.fallback_emoji is False and res.fallback_button_style is False, "no fallback flags on a clean send")
    bot = FakeBot(always="Bad Request: custom emoji are not allowed")
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg)
    h.ok(res.status == "failed" and len(bot.calls) == 2, "…and only once")
    bot = FakeBot(always="Forbidden: bot was blocked by the user")
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=tpl.Rendered(text="x"))
    h.ok(res.status == "blocked" and len(bot.calls) == 1, "blocked classified, not retried")
    bot = FakeBot(always="Too Many Requests: retry after 11")
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=tpl.Rendered(text="x"))
    h.ok(res.status == "retry_after" and res.retry_after == 11, "429 classified with retry_after")
    # buttons: custom emoji icon stripped on fallback, style passed, plain emoji prefixed
    buttons = [[
        {"kind": "url", "label": "Открыть", "url": "https://x.io", "style": "primary", "icon_custom_emoji_id": "5368324170671202286", "icon_emoji": ""},
        {"kind": "web_app", "label": "App", "url": "https://app.x.io", "style": "", "icon_custom_emoji_id": "", "icon_emoji": "🎁"},
    ]]
    msg2 = tpl.Rendered(text="x", buttons=buttons)
    bot = FakeBot()
    await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg2)
    kb = bot.calls[0][2]["reply_markup"].inline_keyboard[0]
    h.ok(kb[0].icon_custom_emoji_id == "5368324170671202286" and kb[0].style == "primary" and kb[0].url == "https://x.io", "url button: custom emoji icon + style")
    h.ok(kb[1].web_app.url == "https://app.x.io" and kb[1].text == "🎁 App" and not hasattr(kb[1], "style"), "web_app button: plain emoji prefixed, no style")
    bot = FakeBot(fail_first="Bad Request: icon_custom_emoji_id is invalid")
    await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg2)
    kb = bot.calls[1][2]["reply_markup"].inline_keyboard[0]
    h.ok(len(bot.calls) == 2 and not hasattr(kb[0], "icon_custom_emoji_id") and kb[0].text.startswith("🙂 "), "rejected button icon -> retried without the custom icon")
    Btn.reject_style = True
    bot = FakeBot()
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg2)
    h.ok(res.status == "sent" and not hasattr(bot.calls[0][2]["reply_markup"].inline_keyboard[0][0], "style"), "an aiogram without button styles still sends (style dropped)")
    h.ok(res.fallback_button_style is True and res.fallback_emoji is False, "dropped button style is flagged")
    Btn.reject_style = False
    bot = FakeBot(fail_first="Bad Request: BUTTON_STYLE_INVALID")
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg2)
    h.ok(res.status == "sent" and len(bot.calls) == 2 and not hasattr(bot.calls[1][2]["reply_markup"].inline_keyboard[0][0], "style")
         and res.fallback_button_style and not res.fallback_emoji, "Telegram rejects the button style -> retried without it, flagged")
    # media with a long text splits into text + media
    msg3 = tpl.Rendered(text="я" * 1500, media=[{"kind": "photo", "url": "https://x.io/a.jpg"}], buttons=[[buttons[0][0]]])
    bot = FakeBot()
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg3)
    h.ok([c[0] for c in bot.calls] == ["send_message", "send_photo"] and "reply_markup" in bot.calls[0][2] and bot.calls[1][2]["caption"] is None, "caption over 1024 at delivery: text with buttons, then the media")
    msg4 = tpl.Rendered(text="короткий", media=[{"kind": "photo", "url": "https://x.io/a.jpg"}])
    bot = FakeBot()
    res = await tg.BotGateway(bot).send(None, chat_id=7, user_id=1, message=msg4)
    h.ok([c[0] for c in bot.calls] == ["send_photo"] and bot.calls[0][2]["caption"] == "короткий" and bot.calls[0][2]["parse_mode"] == "HTML", "photo with a caption")
    lookup = await tg.BotGateway(FakeBot()).lookup_birthdate(5)
    h.ok(lookup.status == "retry_after" and lookup.retry_after == 9, "getChat 429 -> retry_after lookup")
    bot = SimpleNamespace(get_chat=lambda cid: _chat(SimpleNamespace(birthdate=SimpleNamespace(day=1, month=2, year=1999))))
    lookup = await tg.BotGateway(bot).lookup_birthdate(5)
    h.ok((lookup.status, lookup.day, lookup.month, lookup.year) == ("found", 1, 2, 1999), "getChat with a birthdate")
    bot = SimpleNamespace(get_chat=lambda cid: _chat(SimpleNamespace(birthdate=None, model_extra={"birthdate": {"day": 4, "month": 5}})))
    lookup = await tg.BotGateway(bot).lookup_birthdate(5)
    h.ok((lookup.status, lookup.day, lookup.year) == ("found", 4, None), "birthdate read from model_extra (older aiogram)")
    bot = SimpleNamespace(get_chat=lambda cid: _chat(SimpleNamespace()))
    h.ok((await tg.BotGateway(bot).lookup_birthdate(5)).status == "absent", "getChat without a birthdate -> absent")

    async def blocked(cid):
        raise Exception("Bad Request: chat not found")

    h.ok((await tg.BotGateway(SimpleNamespace(get_chat=blocked)).lookup_birthdate(5)).status == "blocked", "chat not found -> blocked")

    async def broken(cid):
        raise ConnectionError("net")

    h.ok((await tg.BotGateway(SimpleNamespace(get_chat=broken)).lookup_birthdate(5)).status == "error", "network error -> error")
    h.ok(tg.visible_length("<b>a&amp;b</b>") == 3, "visible length")


async def _chat(obj):
    return obj


def main() -> None:
    render_tests()
    validation_tests()
    asyncio.run(sender_tests())
    h.finish()


if __name__ == "__main__":
    main()
