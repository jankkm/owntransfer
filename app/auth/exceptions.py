from __future__ import annotations

from fastapi import HTTPException

from app.i18n import _


class NotAuthenticated(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=401, detail=_("Not authenticated"))
