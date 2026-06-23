from __future__ import annotations

from jinja2.sandbox import SandboxedEnvironment

from app.i18n import _
from app.models import AppSettings

# Admin-editable templates are untrusted-ish: a compromised or careless admin
# must not be able to escape into Python (SSTI -> RCE). The sandbox blocks
# access to unsafe attributes and builtins.
_sandbox = SandboxedEnvironment(autoescape=False)

TEMPLATE_KEYS = (
    "share",
    "request",
    "upload_notify",
    "download_notify",
    "expired_unused",
    "purge_reminder",
)

DEFAULTS: dict[str, str] = {
    "share": """<p>You have received files via {{ app_name }}.</p>
<p><strong>{{ title }}</strong></p>
{% if message %}<p>{{ message }}</p>{% endif %}
<p><a href="{{ link }}">Download files</a></p>
{% if password %}<p>Password: <strong>{{ password }}</strong></p>{% endif %}
<p>This link expires on {{ expires_at }}.</p>""",
    "request": """<p>{{ sender }} has requested files from you via {{ app_name }}.</p>
<p><strong>{{ title }}</strong></p>
{% if instructions %}<p>{{ instructions }}</p>{% endif %}
<p><a href="{{ link }}">Upload files</a></p>
{% if password %}<p>Password: <strong>{{ password }}</strong></p>{% endif %}
<p>This link expires on {{ expires_at }}.</p>""",
    "upload_notify": """<p>New files were uploaded to your file request <strong>{{ title }}</strong>.</p>
<p><a href="{{ dashboard_link }}">View in dashboard</a></p>""",
    "download_notify": """<p>Your transfer <strong>{{ title }}</strong> was downloaded.</p>
<p>Downloads: {{ download_count }} / {{ max_downloads }}</p>""",
    "expired_unused": """<p>Your {{ resource_label }} <strong>{{ title }}</strong> has expired without any {% if resource_label == 'transfer' %}downloads{% else %}uploads{% endif %}.</p>
<p>Expired on {{ expires_at }}.</p>
<p><a href="{{ edit_link }}">Extend expiry or delete it</a></p>""",
    "purge_reminder": """<p>Your expired {{ resource_label }} <strong>{{ title }}</strong> will be permanently deleted on {{ purge_at }} (in {{ days_until_purge }} day{% if days_until_purge != 1 %}s{% endif %}).</p>
<p><a href="{{ edit_link }}">Extend expiry to keep it</a></p>""",
}

DEFAULT_SUBJECTS: dict[str, str] = {
    "share": "{{ app_name }}: Files shared with you",
    "request": "{{ app_name }}: File upload requested",
    "upload_notify": "{{ app_name }}: New upload received",
    "download_notify": "{{ app_name }}: Transfer downloaded",
    "expired_unused": "{{ app_name }}: {{ resource_label }} expired unused",
    "purge_reminder": "{{ app_name }}: {{ resource_label }} will be deleted soon",
}

TEMPLATE_FIELD_MAP = {
    "share": "email_tpl_share",
    "request": "email_tpl_request",
    "upload_notify": "email_tpl_upload_notify",
    "download_notify": "email_tpl_download_notify",
    "expired_unused": "email_tpl_expired_unused",
    "purge_reminder": "email_tpl_purge_reminder",
}

SUBJECT_FIELD_MAP = {
    "share": "email_subj_share",
    "request": "email_subj_request",
    "upload_notify": "email_subj_upload_notify",
    "download_notify": "email_subj_download_notify",
    "expired_unused": "email_subj_expired_unused",
    "purge_reminder": "email_subj_purge_reminder",
}

TEMPLATE_VARIABLES: dict[str, list[str]] = {
    "share": ["app_name", "title", "message", "link", "password", "expires_at"],
    "request": ["app_name", "sender", "title", "instructions", "link", "password", "expires_at"],
    "upload_notify": ["app_name", "title", "dashboard_link"],
    "download_notify": ["app_name", "title", "download_count", "max_downloads"],
    "expired_unused": ["app_name", "title", "resource_label", "expires_at", "edit_link"],
    "purge_reminder": ["app_name", "title", "resource_label", "expires_at", "edit_link", "purge_at", "days_until_purge"],
}


def get_template_source(app_settings: AppSettings, key: str) -> str:
    field = TEMPLATE_FIELD_MAP[key]
    custom = getattr(app_settings, field, None)
    if custom and custom.strip():
        return custom
    return _(DEFAULTS[key])


def get_subject_source(app_settings: AppSettings, key: str) -> str:
    field = SUBJECT_FIELD_MAP[key]
    custom = getattr(app_settings, field, None)
    if custom and custom.strip():
        return custom
    return _(DEFAULT_SUBJECTS[key])


def render_email_template(app_settings: AppSettings, key: str, **context) -> str:
    source = get_template_source(app_settings, key)
    return _sandbox.from_string(source).render(**context)


def render_email_subject(app_settings: AppSettings, key: str, **context) -> str:
    source = get_subject_source(app_settings, key)
    return _sandbox.from_string(source).render(**context).strip()


def templates_for_admin(app_settings: AppSettings) -> dict[str, str]:
    return {key: get_template_source(app_settings, key) for key in TEMPLATE_KEYS}


def subjects_for_admin(app_settings: AppSettings) -> dict[str, str]:
    return {key: get_subject_source(app_settings, key) for key in TEMPLATE_KEYS}
