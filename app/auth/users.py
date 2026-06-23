from __future__ import annotations

from app.models import User


def uses_local_auth(user: User) -> bool:
    return not user.oauth_provider


def oauth_display_name(userinfo: dict) -> str | None:
    name = (userinfo.get("name") or "").strip()
    if name:
        return name[:255]
    given = (userinfo.get("given_name") or "").strip()
    family = (userinfo.get("family_name") or "").strip()
    combined = f"{given} {family}".strip()
    if combined:
        return combined[:255]
    if given:
        return given[:255]
    return None


def _name_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in text.replace(".", " ").replace("_", " ").replace("-", " ").split():
        cleaned = "".join(char for char in chunk if char.isalnum())
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _initials_from_tokens(tokens: list[str]) -> str:
    if len(tokens) >= 2:
        return f"{tokens[0][0]}{tokens[-1][0]}".upper()
    if len(tokens) == 1:
        token = tokens[0]
        if len(token) >= 2:
            return token[:2].upper()
        return token[0].upper()
    return "?"


def user_initials(label: str | None) -> str:
    if not label:
        return "?"
    base = label.split("|", 1)[0].strip()
    if "@" in base:
        local = base.split("@", 1)[0].strip()
        return _initials_from_tokens(_name_tokens(local))
    if "," in base:
        last, first = base.split(",", 1)
        letters = f"{first.strip()[:1]}{last.strip()[:1]}"
        if len(letters) == 2:
            return letters.upper()
    return _initials_from_tokens(_name_tokens(base))

