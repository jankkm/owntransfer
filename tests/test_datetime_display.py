from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services.datetime_display import format_date, input_date


def test_input_date_uses_iso_format_for_html_date_inputs(monkeypatch):
    monkeypatch.setattr(
        "app.services.datetime_display.display_timezone",
        lambda: ZoneInfo("Europe/Berlin"),
    )
    monkeypatch.setattr("app.services.datetime_display.get_locale", lambda: "de")

    value = datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc)
    assert input_date(value) == "2026-06-24"
    assert format_date(value) != input_date(value)
