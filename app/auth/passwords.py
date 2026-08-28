from __future__ import annotations

import bcrypt

MIN_PASSWORD_LENGTH = 8


def is_password_long_enough(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH


def is_share_password_valid(password: str, required_length: int) -> bool:
    return len(password) >= max(MIN_PASSWORD_LENGTH, required_length)


def required_share_password_length(required_length: int) -> int:
    return max(MIN_PASSWORD_LENGTH, required_length)


def share_password_too_short_message(required_length: int) -> str:
    from app.i18n import _

    return _("Password must be at least %(n)s characters") % {
        "n": required_share_password_length(required_length)
    }


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False
