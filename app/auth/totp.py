from __future__ import annotations

import base64
import io
import re

import pyotp
import qrcode

_CODE_RE = re.compile(r"\D")


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def normalize_totp_code(code: str) -> str:
    return _CODE_RE.sub("", code.strip())


def verify_totp(secret: str | None, code: str) -> bool:
    if not secret:
        return False
    normalized = normalize_totp_code(code)
    if len(normalized) != 6:
        return False
    return pyotp.TOTP(secret).verify(normalized, valid_window=1)


def totp_qr_data_uri(*, email: str, secret: str, issuer: str) -> str:
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
