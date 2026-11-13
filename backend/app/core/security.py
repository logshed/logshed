"""
Security and cryptography utilities for LogShed.
Includes Argon2id password hashing, the keys derived from the master key, Fernet secret encryption
at rest, and session token generation and verification.
"""

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_secret_key_override, get_secret_key_path

logger = logging.getLogger(__name__)

# Argon2id password hasher singleton
_password_hasher = PasswordHasher()


# The master key (LOGSHED_SECRET_KEY, or the .secret_key file) is never used as it is. Two keys are
# derived from it with HKDF: one encrypts session cookies, the other the secrets stored in the
# database (AI keys, notification URLs, Docker TLS keys), so neither can be used for the other's job.
# A master key that is not a generated key (a passphrase) first goes through scrypt, so each guess
# costs an attacker time and memory. Before 1.4.0 the master key (or one SHA-256 of a passphrase)
# did both jobs; that legacy key still reads secrets stored before the upgrade (a database waiting in
# recovery mode, for example), and the upgrade to schema 4 re-encrypts them under the new key.

# scrypt cost for passphrase keys: 2^17 x 8 x 128 bytes = 128 MiB for about half a second, once per
# start (the tests lower it)
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
# A fixed salt keeps a passphrase key reproducible from the environment variable alone. It belongs to
# LogShed, so a precomputed table would have to be built for LogShed and still pay scrypt per guess.
_SCRYPT_SALT = b"LogShed passphrase key 1"
_SESSION_KEY_INFO = b"LogShed session cookies 1"
_SECRETS_KEY_INFO = b"LogShed stored secrets 1"


@dataclass(frozen=True)
class _Keys:
    session: Fernet
    secrets: Fernet
    legacy: Fernet  # the pre-1.4.0 key, which only reads secrets stored before the upgrade
    secrets_reader: MultiFernet  # the stored-secrets key first, then the legacy key


_cached_keys: Optional[_Keys] = None
_cached_raw_key: Optional[bytes] = None

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
    The legacy (pre-1.4.0) key: a generated Fernet key as it is, anything else through one SHA-256.
    Used only to read secrets stored before the upgrade to schema 4.
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


def _key_bytes(raw_key: str | bytes) -> bytes:
    return raw_key.strip().encode("utf-8") if isinstance(raw_key, str) else raw_key.strip()


def _generated_key_bytes(raw: bytes) -> Optional[bytes]:
    """The 32 key bytes of a generated (urlsafe base64) Fernet key, or None for anything else."""
    try:
        decoded = base64.urlsafe_b64decode(raw)
    except (ValueError, TypeError):
        return None
    return decoded if len(decoded) == 32 else None


def _master_secret(raw: bytes) -> bytes:
    """32 bytes of key material: a generated key as it is, anything else (a passphrase) through scrypt."""
    generated = _generated_key_bytes(raw)
    if generated is not None:
        return generated
    return hashlib.scrypt(
        raw,
        salt=_SCRYPT_SALT,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=128 * SCRYPT_N * SCRYPT_R + (16 << 20),
        dklen=32,
    )


def _hkdf_fernet(master: bytes, info: bytes) -> Fernet:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master)
    return Fernet(base64.urlsafe_b64encode(derived))


def _use_master_key(raw_key: str | bytes) -> bytes:
    """Derive and cache the session, stored-secrets and legacy keys; return the legacy Fernet key."""
    global _cached_keys, _cached_raw_key
    raw = _key_bytes(raw_key)
    legacy_key = _derive_fernet_key(raw)
    # Start-up reads the key more than once (before an upgrade, then again with the services):
    # the same master key keeps the keys already derived, so scrypt runs once per start.
    if _cached_keys is not None and _cached_raw_key == raw:
        return legacy_key
    master = _master_secret(raw)
    secrets_fernet = _hkdf_fernet(master, _SECRETS_KEY_INFO)
    legacy = Fernet(legacy_key)
    _cached_keys = _Keys(
        session=_hkdf_fernet(master, _SESSION_KEY_INFO),
        secrets=secrets_fernet,
        legacy=legacy,
        secrets_reader=MultiFernet([secrets_fernet, legacy]),
    )
    _cached_raw_key = raw
    return legacy_key


def get_or_create_master_key(custom_key_path: Optional[Path] = None) -> bytes:
    """
    Retrieves or generates the master Fernet encryption key.
    
    1. Checks LOGSHED_SECRET_KEY environment variable.
    2. Reads from .secret_key file if present.
    3. If neither exists, generates a new Fernet key and writes to file with 0600 permissions.
    """
    # 1. Environment variable override
    env_override = get_secret_key_override()
    if env_override:
        return _use_master_key(env_override)

    key_path = custom_key_path or get_secret_key_path()
    key_path = Path(key_path)

    # 2. Key file exists. A key that exists but cannot be read is never replaced: every stored
    # secret is encrypted with it, so a new key would lose them all for good. A symlink to a key
    # file (a mounted secret, for example) is read; a dangling link or a link to a directory is not.
    if os.path.lexists(key_path):
        if not key_path.is_file():
            raise RuntimeError(
                f"The encryption key path {key_path} is not a regular file. LogShed will not replace it; "
                "fix or remove it, then start LogShed again."
            )
        try:
            content = key_path.read_bytes().strip()
        except OSError as e:
            raise RuntimeError(
                f"Cannot read the encryption key file {key_path}: {e}. LogShed will not start, so that the key "
                "and the secrets encrypted with it are not lost. Check the file and its permissions, then start LogShed again."
            ) from e
        if content:
            return _use_master_key(content)
        # An empty file holds no key, so nothing can depend on it. An empty file reached through a
        # link is refused instead: removing the link would leave the mounted secret silently unused.
        if key_path.is_symlink():
            raise RuntimeError(
                f"The encryption key file {key_path} is empty, and it is a symbolic link. LogShed will not "
                "replace it; put a key in the file it links to, or remove the link, then start LogShed again."
            )
        key_path.unlink()

    # 3. Generate new key and persist with 0600 permissions, never through a symlink or over another file
    new_key = Fernet.generate_key()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        mode = 0o600
        fd = os.open(str(key_path), flags, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(new_key)
        logger.debug(f"Generated new encryption master key at {key_path}")
    except Exception as e:
        logger.warning(f"Could not persist secret key to {key_path}: {e}")

    return _use_master_key(new_key)


def _keys() -> _Keys:
    if _cached_keys is None:
        get_or_create_master_key()
    return _cached_keys  # type: ignore[return-value]


def get_session_fernet() -> Fernet:
    """The key that encrypts session cookies."""
    return _keys().session


def get_secrets_fernet() -> Fernet:
    """The key that encrypts secrets stored in the database."""
    return _keys().secrets


def get_legacy_fernet() -> Fernet:
    """The key LogShed before 1.4.0 used for everything; it only reads secrets stored back then."""
    return _keys().legacy


def reset_crypto_cache() -> None:
    """Forget the derived keys (primarily for testing); the next use reads the master key again."""
    global _cached_keys, _cached_raw_key
    _cached_keys = None
    _cached_raw_key = None


def encrypt_value(plain_text: str) -> str:
    """Encrypt a secret for storage in the database."""
    if not plain_text:
        return ""
    return get_secrets_fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_value(cipher_text: str) -> str:
    """Decrypt a stored secret, written by this version or (until it is re-encrypted) before 1.4.0."""
    if not cipher_text:
        return ""
    try:
        return _keys().secrets_reader.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt value: Invalid token/key.")
        raise ValueError("Decryption failed: invalid key or corrupted payload.")


def reencrypt_legacy_secret(cipher_text: str) -> Optional[str]:
    """
    The stored secret encrypted under the stored-secrets key: re-encrypted when the legacy key wrote
    it, unchanged when it already uses the new key. None when neither key reads it.
    """
    if not cipher_text:
        return cipher_text
    token = cipher_text.encode("utf-8")
    keys = _keys()
    try:
        keys.secrets.decrypt(token)
        return cipher_text
    except InvalidToken:
        pass
    try:
        plain = keys.legacy.decrypt(token)
    except InvalidToken:
        return None
    return keys.secrets.encrypt(plain).decode("utf-8")


# Shown in place of a stored secret. A form that sends it back unchanged keeps the stored value.
MASKED_SECRET = "********"


def mask_secret(value: Optional[str]) -> str:
    """Returns MASKED_SECRET if value is non-empty, otherwise empty string."""
    if value and len(value.strip()) > 0:
        return MASKED_SECRET
    return ""


def keeps_stored_secret(submitted: Optional[str]) -> bool:
    """True when a submitted secret field asks to keep the stored value: not sent, or sent back as MASKED_SECRET."""
    return submitted is None or submitted == MASKED_SECRET


def create_session_token(
    user_id: int = 1,
    duration_seconds: int = DEFAULT_SESSION_DURATION_SECONDS,
    session_epoch: int = 0,
) -> str:
    """
    Creates a signed, encrypted session token containing user id, expiration and the session
    epoch it was issued under (a later logout or password change raises the epoch and ends it).
    """
    now = time.time()
    payload = {
        "user_id": user_id,
        "iat": now,
        "exp": now + duration_seconds,
        "iss": "logshed",
        "sv": session_epoch,
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    return get_session_fernet().encrypt(json_bytes).decode("utf-8")


def verify_session_token(token: str) -> Optional[dict[str, Any]]:
    """
    Verifies and decrypts a session token.
    Returns the decoded dict if valid and unexpired, or None if invalid/expired.
    """
    if not token:
        return None
    try:
        decrypted_bytes = get_session_fernet().decrypt(token.encode("utf-8"))
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
