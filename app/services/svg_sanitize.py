from __future__ import annotations

import re

# Best-effort SVG sanitization. SVG is XML rendered by the browser and can carry
# active content (<script>, event handlers, javascript: URLs, <foreignObject>
# embedding HTML). We strip those constructs here; the logo endpoint additionally
# serves the file under a locked-down Content-Security-Policy so that any residual
# active content cannot execute. We deliberately use a blocklist scrub (rather
# than an HTML sanitizer) to preserve case-sensitive SVG attributes such as
# viewBox so logos keep rendering correctly.

_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_FOREIGN_OBJECT_RE = re.compile(r"<foreignObject\b[^>]*>.*?</foreignObject\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_SCRIPT_RE = re.compile(r"<script\b[^>]*/?>", re.IGNORECASE)
_FOREIGN_OBJECT_OPEN_RE = re.compile(r"</?foreignObject\b[^>]*>", re.IGNORECASE)
# on*="..." / on*='...' / on*=value event-handler attributes
_EVENT_HANDLER_RE = re.compile(
    r"""\son[a-zA-Z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)
# javascript: (optionally with whitespace/entities) inside any attribute value
_JS_URI_RE = re.compile(r"(?i)javascript\s*:")
# <!DOCTYPE ...> and <!ENTITY ...> declarations (XXE / entity expansion vectors)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL)
_ENTITY_RE = re.compile(r"<!ENTITY[^>]*>", re.IGNORECASE | re.DOTALL)


def sanitize_svg(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="ignore")

    text = _DOCTYPE_RE.sub("", text)
    text = _ENTITY_RE.sub("", text)
    text = _SCRIPT_BLOCK_RE.sub("", text)
    text = _OPEN_SCRIPT_RE.sub("", text)
    text = _FOREIGN_OBJECT_RE.sub("", text)
    text = _FOREIGN_OBJECT_OPEN_RE.sub("", text)
    text = _EVENT_HANDLER_RE.sub("", text)
    text = _JS_URI_RE.sub("", text)

    return text.encode("utf-8")
