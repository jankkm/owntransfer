from __future__ import annotations

import bcrypt

MIN_PASSWORD_LENGTH = 8


def is_password_long_enough(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False
