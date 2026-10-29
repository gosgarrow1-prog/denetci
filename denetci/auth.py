"""Parola (scrypt) ve TOTP tabanlı iki aşamalı doğrulama. Harici bağımlılık yok."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, for_time: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((for_time if for_time is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return False
    now = time.time()
    return any(hmac.compare_digest(totp(secret, now + i * 30), code) for i in range(-window, window + 1))


def otpauth_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/Denetci:{quote(username)}?secret={secret}&issuer=Denetci"


class LoginThrottle:
    """5 hatalı denemeden sonra 5 dakika bekletir."""

    def __init__(self, limit: int = 5, lock_seconds: int = 300):
        self.limit, self.lock_seconds = limit, lock_seconds
        self.failures: dict[str, list[float]] = {}

    def locked(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self.failures.get(key, []) if now - t < self.lock_seconds]
        self.failures[key] = recent
        return len(recent) >= self.limit

    def fail(self, key: str):
        self.failures.setdefault(key, []).append(time.time())

    def reset(self, key: str):
        self.failures.pop(key, None)
