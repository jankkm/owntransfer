from __future__ import annotations

import markdown


def render_markdown(text: str) -> str:
    if not text.strip():
        return ""
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        output_format="html5",
    )
