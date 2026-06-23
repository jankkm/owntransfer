from __future__ import annotations

import os

import pytest

from app.config.env_files import resolve_env_file_variables


def test_resolve_env_file_sets_target_variable(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret_key"
    secret_file.write_text("super-secret\n")

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY_FILE", str(secret_file))

    resolve_env_file_variables(env_file=tmp_path / "missing.env", force=True)

    assert os.environ["SECRET_KEY"] == "super-secret"


def test_resolve_env_file_overrides_existing_value(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret_key"
    secret_file.write_text("from-file")

    monkeypatch.setenv("SECRET_KEY", "from-env")
    monkeypatch.setenv("SECRET_KEY_FILE", str(secret_file))

    resolve_env_file_variables(env_file=tmp_path / "missing.env", force=True)

    assert os.environ["SECRET_KEY"] == "from-file"


def test_resolve_env_file_reads_from_dotenv(tmp_path, monkeypatch):
    secret_file = tmp_path / "db_password"
    secret_file.write_text("postgres-secret")
    env_file = tmp_path / ".env"
    env_file.write_text(f"POSTGRES_PASSWORD_FILE={secret_file}\n")

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    resolve_env_file_variables(env_file=env_file, force=True)

    assert os.environ["POSTGRES_PASSWORD"] == "postgres-secret"


def test_resolve_env_file_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="Failed to read environment variable SECRET_KEY"):
        resolve_env_file_variables(env_file=tmp_path / "missing.env", force=True)


def test_resolve_env_file_ignores_max_file_size_mb(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "2048")

    resolve_env_file_variables(env_file=tmp_path / "missing.env", force=True)

    assert os.environ["MAX_FILE_SIZE_MB"] == "2048"
