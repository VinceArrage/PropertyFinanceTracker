"""PIN protection for the web app.

The PIN is stored only as a salted scrypt hash. Changing it bumps a version number
that each login cookie carries, so every device that was signed in must sign in again.
"""

import hashlib
import hmac
import secrets
import sqlite3
import time

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 12
# scrypt is deliberately slow (~50 ms here), so guessing PINs from a stolen database is costly.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class PinError(ValueError):
    pass


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, **_SCRYPT)


def validate_pin(pin: str) -> None:
    if not (pin.isdigit() and PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH):
        raise PinError(f"The PIN must be {PIN_MIN_LENGTH} to {PIN_MAX_LENGTH} digits.")


def pin_is_set(conn: sqlite3.Connection) -> bool:
    return _get(conn, "pin_hash") is not None


def set_pin(conn: sqlite3.Connection, pin: str) -> None:
    """Set or change the PIN. Signs out every device."""
    validate_pin(pin)
    salt = secrets.token_bytes(16)
    with conn:
        _set(conn, "pin_hash", f"{salt.hex()}:{_hash(pin, salt).hex()}")
        _set(conn, "pin_version", str(pin_version(conn) + 1))


def check_pin(conn: sqlite3.Connection, pin: str) -> bool:
    stored = _get(conn, "pin_hash")
    if stored is None or not pin.isdigit() or len(pin) > PIN_MAX_LENGTH:
        return False
    salt_hex, hash_hex = stored.split(":")
    return hmac.compare_digest(_hash(pin, bytes.fromhex(salt_hex)), bytes.fromhex(hash_hex))


def pin_version(conn: sqlite3.Connection) -> int:
    return int(_get(conn, "pin_version") or 0)


def session_secret(conn: sqlite3.Connection) -> str:
    """The key that signs login cookies, created on first use."""
    secret = _get(conn, "session_secret")
    if secret is None:
        secret = secrets.token_hex(32)
        with conn:
            _set(conn, "session_secret", secret)
    return secret


class LoginThrottle:
    """Slow down PIN guessing: after 5 wrong tries from one device, lock it out for
    1 minute, doubling with each further wrong try, up to 1 hour."""

    def __init__(self, max_failures: int = 5, first_lock: float = 60, max_lock: float = 3600, clock=time.monotonic):
        self.max_failures = max_failures
        self.first_lock = first_lock
        self.max_lock = max_lock
        self.clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def seconds_locked(self, key: str) -> int:
        return max(0, int(self._locked_until.get(key, 0) - self.clock() + 0.999))

    def failed(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.max_failures:
            lock = min(self.first_lock * 2 ** (count - self.max_failures), self.max_lock)
            self._locked_until[key] = self.clock() + lock

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
