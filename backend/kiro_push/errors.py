"""Plugin error type: a stable machine code plus an HTTP status."""

from __future__ import annotations


class PushError(Exception):
    def __init__(self, code: str, status: int = 400, **extra: object) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra
