from app.auth.users import oauth_display_name


def test_oauth_display_name_from_name_claim():
    assert oauth_display_name({"name": "Jane Doe"}) == "Jane Doe"


def test_oauth_display_name_from_given_and_family():
    assert oauth_display_name({"given_name": "Jane", "family_name": "Doe"}) == "Jane Doe"


def test_oauth_display_name_from_given_only():
    assert oauth_display_name({"given_name": "Jane"}) == "Jane"


def test_oauth_display_name_missing():
    assert oauth_display_name({}) is None


def test_oauth_display_name_prefers_name_claim():
    assert oauth_display_name({"name": "Full Name", "given_name": "Jane", "family_name": "Doe"}) == "Full Name"


def test_user_initials_from_last_first_format():
    from app.auth.users import user_initials

    assert user_initials("Doe, Jane | Acme Corp") == "JD"


def test_user_initials_from_email():
    from app.auth.users import user_initials

    assert user_initials("jane.doe@example.com") == "JD"


def test_user_initials_from_short_email():
    from app.auth.users import user_initials

    assert user_initials("a@a.de") == "A"
