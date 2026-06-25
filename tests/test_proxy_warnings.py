from __future__ import annotations

import logging

import pytest

from app.http.proxy_warnings import warn_unrestricted_proxy_trust

CONFIG_LOGGER = "owntransfer.config"


def test_warns_when_proxy_trust_has_no_allowed_ips(monkeypatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr("app.http.proxy_warnings.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.http.proxy_warnings.settings.trusted_proxy_ips", "")

    with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
        warn_unrestricted_proxy_trust()

    assert any("TRUSTED_PROXY_IPS is empty" in record.message for record in caplog.records)


def test_no_warning_when_proxy_headers_disabled(monkeypatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr("app.http.proxy_warnings.settings.trust_proxy_headers", False)
    monkeypatch.setattr("app.http.proxy_warnings.settings.trusted_proxy_ips", "")

    with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
        warn_unrestricted_proxy_trust()

    assert caplog.records == []


def test_no_warning_when_trusted_proxy_ips_configured(monkeypatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr("app.http.proxy_warnings.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.http.proxy_warnings.settings.trusted_proxy_ips", "127.0.0.1")

    with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
        warn_unrestricted_proxy_trust()

    assert caplog.records == []
