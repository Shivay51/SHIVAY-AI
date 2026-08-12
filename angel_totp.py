"""Pure-stdlib RFC 6238 TOTP generator for Angel One SmartAPI login.

Angel One requires a 6-digit TOTP for every ``loginByPassword`` call. Storing a
static code is useless because it expires in 30 seconds, so SHIVAY AI stores the
base32 *TOTP secret* (``ANGEL_TOTP_SECRET``) and derives a fresh code on demand.

Implemented with the standard library only (hmac/hashlib/base64/struct) so the
production runtime gains no extra dependency and the logic stays unit-testable.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time

DIGITS = 6
PERIOD = 30


class TOTPError(ValueError):
    """The configured Angel TOTP secret is not usable."""


def normalize_secret(secret: str) -> str:
    """Strip spacing/padding noise from a user-pasted base32 secret."""
    value = str(secret or "").strip().replace(" ", "").replace("-", "").upper()
    return value.rstrip("=")


def decode_secret(secret: str) -> bytes:
    value = normalize_secret(secret)
    if not value:
        raise TOTPError("angel_totp_secret_missing")
    padding = "=" * (-len(value) % 8)
    try:
        return base64.b32decode(value + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise TOTPError("angel_totp_secret_invalid_base32") from exc


def generate_totp(
    secret: str,
    timestamp: float | None = None,
    digits: int = DIGITS,
    period: int = PERIOD,
    digest: str = "sha1",
) -> str:
    """Return the current TOTP code for ``secret`` as a zero-padded string."""
    key = decode_secret(secret)
    counter = int((timestamp if timestamp is not None else time.time()) // max(1, period))
    mac = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def seconds_remaining(timestamp: float | None = None, period: int = PERIOD) -> int:
    """Seconds until the current TOTP window rolls over."""
    now = timestamp if timestamp is not None else time.time()
    return int(max(1, period) - (now % max(1, period)))


def is_valid_secret(secret: str) -> bool:
    try:
        decode_secret(secret)
        return True
    except TOTPError:
        return False
