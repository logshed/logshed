"""
Security and cryptography utilities for LogShed.
Includes Argon2id password hashing, Fernet secret encryption at rest,
and secure session token generation and verification.
"""

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_secret_key_override, get_secret_key_path

logger = logging.getLogger(__name__)

# Argon2id password hasher singleton
_password_hasher = PasswordHasher()

# Cached fernet instance
_cached_fernet: Optional[Fernet] = None
_cached_key: Optional[bytes] = None

SESSION_COOKIE_NAME = "session"
DEFAULT_SESSION_DURATION_SECONDS = 7 * 24 * 3600  # 7 days


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """
    Verify a plaintext password against an Argon2id hash.
    Returns True if valid, False otherwise.
    """
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _derive_fernet_key(raw_key: str | bytes) -> bytes:
    """
    Ensure the provided key is a valid 32-byte urlsafe-base64 Fernet key.
    If arbitrary string is passed, derive a 32-byte key using SHA-256 and base64url encode.
    """
    if isinstance(raw_key, str):
        raw_bytes = raw_key.strip().encode("utf-8")
    else:
        raw_bytes = raw_key.strip()

    # If it's already a valid 44-byte base64-encoded Fernet key
    try:
        decoded = base64.urlsafe_b64decode(raw_bytes)
        if len(decoded) == 32:
            return raw_bytes
    except Exception:
        pass

    # Derive 32-byte SHA256 digest and base64-urlsafe encode it
    digest = hashlib.sha256(raw_bytes).digest()
    return base64.urlsafe_b64encode(digest)


def get_or_create_master_key(custom_key_path: Optional[Path] = None) -> bytes:
    """
    Retrieves or generates the master Fernet encryption key.
    
    1. Checks LOGSHED_SECRET_KEY environment variable.
    2. Reads from .secret_key file if present.
    3. If neither exists, generates a new Fernet key and writes to file with 0600 permissions.
    """
    global _cached_fernet, _cached_key

    # 1. Environment variable override
    env_override = get_secret_key_override()
    if env_override:
        derived = _derive_fernet_key(env_override)
        _cached_key = derived
        _cached_fernet = Fernet(derived)
        return derived

    key_path = custom_key_path or get_secret_key_path()
    key_path = Path(key_path)

    # 2. Key file exists
    if key_path.is_file():
        try:
            content = key_path.read_bytes().strip()
            if content:
                derived = _derive_fernet_key(content)
                _cached_key = derived
                _cached_fernet = Fernet(derived)
                return derived
        except Exception as e:
            logger.error(f"Failed to read secret key from {key_path}: {e}")

    # 3. Generate new key and persist with 0600 permissions
    new_key = Fernet.generate_key()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(str(key_path), flags, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(new_key)
        logger.debug(f"Generated new encryption master key at {key_path}")
    except Exception as e:
        logger.warning(f"Could not persist secret key to {key_path}: {e}")

    _cached_key = new_key
    _cached_fernet = Fernet(new_key)
    return new_key


def get_fernet(key: Optional[bytes] = None) -> Fernet:
    """Returns a Fernet instance using the master key or provided key."""
    if key:
        return Fernet(_derive_fernet_key(key))
    global _cached_fernet
    if _cached_fernet is None:
        get_or_create_master_key()
    return _cached_fernet  # type: ignore


def reset_crypto_cache() -> None:
    """Reset cached Fernet instance and key (primarily for testing)."""
    global _cached_fernet, _cached_key
    _cached_fernet = None
    _cached_key = None


def encrypt_value(plain_text: str, key: Optional[bytes] = None) -> str:
    """Encrypt a string at rest using Fernet."""
    if not plain_text:
        return ""
    fernet = get_fernet(key)
    encrypted_bytes = fernet.encrypt(plain_text.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_value(cipher_text: str, key: Optional[bytes] = None) -> str:
    """Decrypt a Fernet encrypted string."""
    if not cipher_text:
        return ""
    fernet = get_fernet(key)
    try:
        decrypted_bytes = fernet.decrypt(cipher_text.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt value: Invalid token/key.")
        raise ValueError("Decryption failed: invalid key or corrupted payload.")


def mask_secret(value: Optional[str]) -> str:
    """Returns '********' if value is non-empty, otherwise empty string."""
    if value and len(value.strip()) > 0:
        return "********"
    return ""


def create_session_token(
    user_id: int = 1,
    duration_seconds: int = DEFAULT_SESSION_DURATION_SECONDS,
    key: Optional[bytes] = None,
) -> str:
    """
    Creates a signed, encrypted session token containing user id and expiration.
    """
    now = time.time()
    payload = {
        "user_id": user_id,
        "iat": now,
        "exp": now + duration_seconds,
        "iss": "logshed",
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    fernet = get_fernet(key)
    return fernet.encrypt(json_bytes).decode("utf-8")


def verify_session_token(token: str, key: Optional[bytes] = None) -> Optional[dict[str, Any]]:
    """
    Verifies and decrypts a session token.
    Returns the decoded dict if valid and unexpired, or None if invalid/expired.
    """
    if not token:
        return None
    fernet = get_fernet(key)
    try:
        decrypted_bytes = fernet.decrypt(token.encode("utf-8"))
        payload = json.loads(decrypted_bytes.decode("utf-8"))

        # Verify issuer claim
        if payload.get("iss") != "logshed":
            logger.warning("Session token has invalid issuer.")
            return None

        # Check expiration
        exp = payload.get("exp", 0)
        if time.time() > exp:
            logger.warning("Session token has expired.")
            return None

        return payload
    except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        logger.debug(f"Invalid session token: {e}")
        return None
