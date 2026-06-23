from __future__ import annotations

import markdown
import nh3

# Tags/attributes permitted in rendered legal pages. Markdown can embed raw
# HTML (including <script>), so the output is sanitized before it is marked
# safe in the template to prevent stored XSS.
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "del", "div", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title"},
    "td": {"align"},
    "th": {"align"},
    "ol": {"start"},
}


def render_markdown(text: str) -> str:
    if not text.strip():
        return ""
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        output_format="html5",
    )
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )
