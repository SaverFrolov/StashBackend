import base64
import hashlib
import hmac
import os
import secrets
import time
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from .config import cfg

_PBKDF2_ROUNDS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(cfg.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_api_key(key: str) -> str:
    return _fernet().encrypt(key.encode()).decode()


def decrypt_api_key(enc: str) -> str | None:
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        return None


def sign_file_url(user_id: UUID, item_id: UUID, ttl: int | None = None) -> str:
    exp = int(time.time()) + (ttl if ttl is not None else cfg.file_url_ttl)
    msg = f"{user_id}:{item_id}:{exp}"
    sig = hmac.new(cfg.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"/files/{item_id}?u={user_id}&exp={exp}&sig={sig}"


def verify_file_url(user_id: str, item_id: str, exp: str, sig: str) -> bool:
    try:
        if int(exp) < time.time():
            return False
    except ValueError:
        return False
    msg = f"{user_id}:{item_id}:{exp}"
    want = hmac.new(cfg.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)
