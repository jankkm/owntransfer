from __future__ import annotations

import html

import pytest

from app.models import AppSettings
from app.services.email import _wrap_email_html
from app.services.email_templates import render_email_subject, render_email_template


@pytest.fixture
def app_settings() -> AppSettings:
    return AppSettings(id=1, app_name="OwnTransfer")


def test_render_email_subject_allows_duplicate_app_name_in_context(app_settings: AppSettings):
    subject = render_email_subject(
        app_settings,
        "download_notify",
        app_name=app_settings.app_name,
        title="My Transfer",
        download_count=1,
        max_downloads=5,
    )
    assert subject == "OwnTransfer: Transfer downloaded"


def test_render_email_template_keeps_strong_tags(app_settings: AppSettings):
    body = render_email_template(
        app_settings,
        "download_notify",
        title="My Transfer",
        download_count=1,
        max_downloads=5,
    )
    assert "<strong>My Transfer</strong>" in body
    assert "&lt;strong&gt;" not in body


def test_render_email_template_escapes_unsafe_variable_content(app_settings: AppSettings):
    body = render_email_template(
        app_settings,
        "download_notify",
        title='<script>alert("x")</script>',
        download_count=1,
        max_downloads=5,
    )
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_render_email_template_includes_base_url(app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.services.email_templates.settings.base_url",
        "https://files.example.com/",
    )
    app_settings.email_tpl_share = '<p>{{ base_url }}/d/token</p>'
    body = render_email_template(
        app_settings,
        "share",
        title="T",
        message=None,
        link="https://files.example.com/d/abc",
        password=None,
        expires_at="2026-01-01",
    )
    assert "https://files.example.com/d/token" in body


def test_get_template_source_unescapes_stored_entities(app_settings: AppSettings):
    app_settings.email_tpl_share = "<p>&lt;strong&gt;{{ title }}&lt;/strong&gt;</p>"
    body = render_email_template(
        app_settings,
        "share",
        title="Bold",
        message=None,
        link="https://example.com/d/x",
        password=None,
        expires_at="2026-01-01",
    )
    assert "<strong>Bold</strong>" in body


def test_wrap_email_html_produces_document():
    wrapped = _wrap_email_html("<p>Hello</p>")
    assert wrapped.startswith("<!DOCTYPE html>")
    assert "<body" in wrapped
    assert "<p>Hello</p>" in wrapped
    assert "font-weight:700" in wrapped


def test_html_unescape_roundtrip():
    escaped = html.escape("<strong>x</strong>")
    assert html.unescape(escaped) == "<strong>x</strong>"
