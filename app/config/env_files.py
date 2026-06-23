from __future__ import annotations

import os
from pathlib import Path

_RESOLVED = False


def _collect_env(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file.is_file():
        from dotenv import dotenv_values

        for key, value in dotenv_values(env_file).items():
            if key and value is not None:
                values[key] = value
    values.update(os.environ)
    return values


def resolve_env_file_variables(*, env_file: str | Path = ".env", force: bool = False) -> None:
    """Populate VAR from VAR_FILE for any environment variable.

    If SECRET_KEY_FILE=/run/secrets/secret_key is set, the file contents are
    assigned to SECRET_KEY before settings are loaded. When both are set, the
    file value takes precedence.
    """
    global _RESOLVED
    if _RESOLVED and not force:
        return

    env_path = Path(env_file)
    for key, path in _collect_env(env_path).items():
        if not key.endswith("_FILE"):
            continue
        target_key = key[:-5]
        if not target_key or not path.strip():
            continue
        file_path = Path(path.strip())
        try:
            value = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read environment variable {target_key} from {key}={path!r}: {exc}"
            ) from exc
        os.environ[target_key] = value.rstrip("\r\n")

    _RESOLVED = True
