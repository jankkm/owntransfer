from __future__ import annotations

import gettext as stdlib_gettext
from contextvars import ContextVar
from pathlib import Path

from babel import Locale, negotiate_locale
from babel.core import UnknownLocaleError
from starlette.requests import Request

from app.config import settings

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "de")
LOCALE_COOKIE = "locale"
LOCALE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60

JS_MESSAGE_KEYS = (
    "Request failed",
    "Uploading…",
    "Upload failed",
    "Upload timed out — try Retry",
    "Removing…",
    "Yes, remove file",
    "Cancel",
    "Enter a password to enable protection",
    "Drop files here or click to browse",
    "No files selected",
    "No files added yet.",
    "Add at least one file",
    "Add at least one file to upload",
    "Wait for uploads to finish",
    "Ready",
    "Waiting…",
    "Retry",
    "Remove",
    "Network error",
    "Sending…",
    "Could not remove file",
    "Could not show confirmation dialog.",
    "Remove file?",
    "Remove file from transfer?",
    "this file",
    "Transfer must have at least one file.",
    "Files upload automatically. You can remove them before submitting.",
    '"%(name)s" will be permanently removed from this transfer. This cannot be undone.',
    'Remove "%(name)s" from the list?',
    '"%(name)s" will be permanently removed. This cannot be undone.',
    "You have unsaved changes. Leave this page?",
)

_locale_var: ContextVar[str] = ContextVar("locale", default="en")
_translations: dict[str, stdlib_gettext.GNUTranslations] = {}


def _load_translation(locale: str) -> stdlib_gettext.GNUTranslations:
    if locale not in _translations:
        try:
            _translations[locale] = stdlib_gettext.translation(
                "messages",
                localedir=str(LOCALES_DIR),
                languages=[locale],
                fallback=True,
            )
        except FileNotFoundError:
            _translations[locale] = stdlib_gettext.NullTranslations()
    return _translations[locale]


def normalize_locale(locale: str | None) -> str | None:
    if not locale:
        return None
    code = locale.strip().lower().replace("_", "-")
    if not code:
        return None
    primary = code.split("-", 1)[0]
    if primary in SUPPORTED_LOCALES:
        return primary
    return None


def negotiate_from_header(accept_language: str | None) -> str | None:
    if not accept_language:
        return None
    preferred: list[str] = []
    for part in accept_language.split(","):
        token = part.strip().split(";")[0].strip()
        if token:
            preferred.append(token)
    if not preferred:
        return None
    matched = negotiate_locale(preferred, SUPPORTED_LOCALES, sep="-")
    return normalize_locale(matched)


def resolve_locale(request: Request) -> str:
    cookie_locale = normalize_locale(request.cookies.get(LOCALE_COOKIE))
    if cookie_locale:
        return cookie_locale
    header_locale = negotiate_from_header(request.headers.get("accept-language"))
    if header_locale:
        return header_locale
    return normalize_locale(settings.default_locale) or "en"


def activate(locale: str) -> str:
    resolved = normalize_locale(locale) or normalize_locale(settings.default_locale) or "en"
    _locale_var.set(resolved)
    return resolved


def get_locale() -> str:
    return _locale_var.get()


def gettext(message: str) -> str:
    return _load_translation(get_locale()).gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _load_translation(get_locale()).ngettext(singular, plural, n)


def _(message: str) -> str:
    return gettext(message)


def locale_display_name(locale: str) -> str:
    try:
        return Locale.parse(locale).get_display_name(locale) or locale
    except UnknownLocaleError:
        return locale


def js_messages() -> dict[str, str]:
    return {key: gettext(key) for key in JS_MESSAGE_KEYS}
