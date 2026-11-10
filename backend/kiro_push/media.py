"""Chunked media uploads for templates (adapted from kiro-mailer): init -> chunks -> finish, then reference by id."""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import text

from .errors import PushError
from .templates import MEDIA_KINDS

MEDIA_LIMITS = {
    "photo": 10 * 1024 * 1024, "video": 50 * 1024 * 1024, "animation": 50 * 1024 * 1024, "document": 50 * 1024 * 1024,
}
MAX_CHUNK = 4 * 1024 * 1024


def magic_ok(kind: str, head: bytes) -> bool:
    if kind == "photo":
        return head.startswith((b"\xff\xd8\xff", b"\x89PNG")) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    if kind in ("video", "animation"):
        return head[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3" or head[:3] == b"GIF"
    return True


def served_type(kind: str, head: bytes) -> tuple[str, bool]:
    """Server-derived Content-Type for a stored file and whether it must be sent as an attachment.

    The client-supplied content type is never trusted: it would let an admin upload "text/html" and have it served
    from the admin origin. Only images/videos recognised by their magic bytes are rendered inline; everything else
    (documents, unknown signatures) is an opaque download.
    """
    if kind == "photo":
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", False
        if head.startswith(b"\x89PNG"):
            return "image/png", False
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp", False
    elif kind in ("video", "animation"):
        if head[:3] == b"GIF":
            return "image/gif", False
        if head[4:8] == b"ftyp":
            return "video/mp4", False
        if head[:4] == b"\x1aE\xdf\xa3":
            return "video/webm", False
    return "application/octet-stream", True


def safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip(" .")
    return (cleaned or "file")[:80]


async def init(session: Any, body: dict[str, Any]) -> dict[str, Any]:
    kind = str(body.get("kind") or "")
    if kind not in MEDIA_KINDS:
        raise PushError("invalid_media")
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        raise PushError("invalid_media") from None
    if size <= 0:
        raise PushError("invalid_media")
    if size > MEDIA_LIMITS[kind]:
        raise PushError("media_too_large")
    media_id = secrets.token_hex(16)
    await session.execute(
        text(
            "insert into ext_kiro_push_media (id, kind, filename, content_type, expected_size) "
            "values (:i, :k, :f, :c, :s)"
        ),
        {"i": media_id, "k": kind, "f": str(body.get("filename") or "")[:120], "c": str(body.get("content_type") or "")[:80], "s": size},
    )
    return {"id": media_id, "chunk": MAX_CHUNK}


async def chunk(session: Any, media_id: str, offset: int, data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_CHUNK or offset < 0:
        raise PushError("invalid_chunk")
    result = await session.execute(
        text(
            "update ext_kiro_push_media set body = body || :d, size = size + :n "
            "where id = :i and not complete and size = :o and size + :n <= expected_size"
        ),
        {"d": data, "n": len(data), "i": media_id, "o": offset},
    )
    if not result.rowcount:
        raise PushError("chunk_out_of_order", 409)
    return {"received": offset + len(data)}


async def finish(session: Any, media_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "select kind, size, expected_size, substring(body from 1 for 16) as head from ext_kiro_push_media "
                "where id = :i and not complete"
            ),
            {"i": media_id},
        )
    ).mappings().first()
    if row is None:
        raise PushError("not_found", 404)
    if row["size"] != row["expected_size"]:
        raise PushError("incomplete_upload")
    if not magic_ok(row["kind"], bytes(row["head"])):
        await session.execute(text("delete from ext_kiro_push_media where id = :i"), {"i": media_id})
        await session.commit()
        raise PushError("unsupported_file")
    await session.execute(text("update ext_kiro_push_media set complete = true where id = :i"), {"i": media_id})
    return {"id": media_id, "kind": row["kind"]}


async def listing(session: Any) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "select id, kind, filename, content_type, size, created_at from ext_kiro_push_media where complete "
                "order by created_at desc limit 200"
            )
        )
    ).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


async def referenced(session: Any, media_id: str) -> bool:
    return bool(
        await session.scalar(
            text("select 1 from ext_kiro_push_events where deleted_at is null and templates::text like :p limit 1"),
            {"p": f'%"{media_id}"%'},
        )
    )
