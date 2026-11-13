"""
Tests for the keys derived from the master key (one for session cookies, one for stored secrets),
the slow derivation of passphrase keys, and the upgrade that re-encrypts stored secrets.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from unittest import mock

import pytest
from cryptography.fernet import Fernet, InvalidToken

import app.core.migrations as migrations_mod
from app.core import security
from app.core.migrations import MIGRATIONS, get_user_version, run_migrations
from app.core.security import (
    create_session_token,
    decrypt_value,
    encrypt_value,
    get_legacy_fernet,
    get_or_create_master_key,
    get_secrets_fernet,
    get_session_fernet,
    reset_crypto_cache,
    verify_session_token,
)
from tests.db_helpers import open_db

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def key_env(tmp_path: Path, monkeypatch):
    reset_crypto_cache()
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "logs.db"))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.setenv("AUTO_BACKUP_ON_MIGRATION", "false")
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    yield key_file
    reset_crypto_cache()


def _legacy_session_token() -> str:
    """A session cookie as LogShed 1.3.x made it: signed with the master key itself."""
    now = time.time()
    payload = {"user_id": 1, "iat": now, "exp": now + 3600, "iss": "logshed"}
    return get_legacy_fernet().encrypt(json.dumps(payload).encode()).decode()


class TestDerivedKeys:

    def test_session_and_secrets_keys_differ_from_each_other_and_from_the_master_key(self, key_env):
        master = get_or_create_master_key(key_env)
        session = get_session_fernet().encrypt(b"x")
        stored = get_secrets_fernet().encrypt(b"x")

        with pytest.raises(InvalidToken):
            get_secrets_fernet().decrypt(session)
        with pytest.raises(InvalidToken):
            get_session_fernet().decrypt(stored)
        with pytest.raises(InvalidToken):
            Fernet(master).decrypt(session)
        with pytest.raises(InvalidToken):
            Fernet(master).decrypt(stored)

    def test_a_stored_secret_is_not_a_session_cookie(self, key_env):
        get_or_create_master_key(key_env)
        now = time.time()
        payload = json.dumps({"user_id": 1, "iat": now, "exp": now + 3600, "iss": "logshed", "sv": 0})
        assert verify_session_token(encrypt_value(payload)) is None
        assert verify_session_token(create_session_token()) is not None

    def test_a_session_cookie_cannot_be_read_as_a_stored_secret(self, key_env):
        get_or_create_master_key(key_env)
        with pytest.raises(ValueError):
            decrypt_value(create_session_token())

    def test_a_1_3_session_cookie_is_refused(self, key_env):
        """Every browser signs in again once after the upgrade."""
        get_or_create_master_key(key_env)
        assert verify_session_token(_legacy_session_token()) is None

    def test_the_key_file_is_unchanged_and_the_keys_survive_a_restart(self, key_env):
        get_or_create_master_key(key_env)
        file_before = key_env.read_bytes()
        stored = encrypt_value("sk-test-value")
        cookie = create_session_token()

        reset_crypto_cache()
        get_or_create_master_key(key_env)

        assert key_env.read_bytes() == file_before
        assert decrypt_value(stored) == "sk-test-value"
        assert verify_session_token(cookie) is not None

    def test_a_secret_stored_before_the_upgrade_is_still_read(self, key_env):
        get_or_create_master_key(key_env)
        legacy = get_legacy_fernet().encrypt(b"https://hooks.example/abc").decode()
        assert decrypt_value(legacy) == "https://hooks.example/abc"


class TestPassphraseKeys:

    def test_a_generated_key_skips_scrypt(self, key_env, monkeypatch):
        monkeypatch.setattr(security.hashlib, "scrypt", mock.Mock(side_effect=AssertionError("scrypt called")))
        get_or_create_master_key(key_env)
        monkeypatch.setenv("LOGSHED_SECRET_KEY", Fernet.generate_key().decode())
        reset_crypto_cache()
        get_or_create_master_key(key_env)

    def test_a_passphrase_goes_through_scrypt_with_the_fixed_salt(self, key_env, monkeypatch):
        monkeypatch.setenv("LOGSHED_SECRET_KEY", PASSPHRASE)
        calls = []
        real_scrypt = hashlib.scrypt

        def _recording_scrypt(password, **kwargs):
            calls.append((password, kwargs))
            return real_scrypt(password, **kwargs)

        monkeypatch.setattr(security.hashlib, "scrypt", _recording_scrypt)
        get_or_create_master_key(key_env)

        ((password, kwargs),) = calls
        assert password == PASSPHRASE.encode()
        assert kwargs["salt"] == b"LogShed passphrase key 1"
        assert (kwargs["n"], kwargs["r"], kwargs["p"], kwargs["dklen"]) == (security.SCRYPT_N, 8, 1, 32)
        # No key file is written for a key given in the environment
        assert not key_env.exists()

    def test_a_passphrase_written_to_the_key_file_also_goes_through_scrypt(self, key_env, monkeypatch):
        key_env.write_text(PASSPHRASE)
        scrypt = mock.Mock(wraps=hashlib.scrypt)
        monkeypatch.setattr(security.hashlib, "scrypt", scrypt)
        get_or_create_master_key(key_env)
        assert scrypt.call_count == 1

    def test_the_same_passphrase_gives_the_same_keys_on_another_machine(self, key_env, monkeypatch):
        monkeypatch.setenv("LOGSHED_SECRET_KEY", PASSPHRASE)
        get_or_create_master_key(key_env)
        stored = encrypt_value("sk-test-value")
        cookie = create_session_token()

        reset_crypto_cache()
        get_or_create_master_key(key_env)
        assert decrypt_value(stored) == "sk-test-value"
        assert verify_session_token(cookie) is not None

        monkeypatch.setenv("LOGSHED_SECRET_KEY", PASSPHRASE + " ")  # surrounding spaces are ignored, as before
        reset_crypto_cache()
        get_or_create_master_key(key_env)
        assert decrypt_value(stored) == "sk-test-value"

    def test_another_passphrase_reads_nothing(self, key_env, monkeypatch):
        monkeypatch.setenv("LOGSHED_SECRET_KEY", PASSPHRASE)
        get_or_create_master_key(key_env)
        stored = encrypt_value("sk-test-value")

        monkeypatch.setenv("LOGSHED_SECRET_KEY", "another passphrase")
        reset_crypto_cache()
        get_or_create_master_key(key_env)
        with pytest.raises(ValueError):
            decrypt_value(stored)

    def test_production_cost_is_2_to_the_17(self, fast_passphrase_kdf):
        """128 MiB once per start: OWASP's minimum for scrypt (the tests run with a lower cost)."""
        assert fast_passphrase_kdf == 2**17
        assert 128 * fast_passphrase_kdf * security.SCRYPT_R == 128 * 1024**2


# ===================================================================
# The upgrade to schema 4 re-encrypts the stored secrets
# ===================================================================

def _v3_database(path: Path) -> None:
    with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
        run_migrations(path)


def _seed_legacy_secrets(path: Path) -> dict[str, str]:
    """A 1.3.x database's secrets, encrypted with the master key as 1.3.x did. Returns the plain values."""
    legacy = get_legacy_fernet()

    def enc(text: str) -> str:
        return legacy.encrypt(text.encode()).decode()

    plain = {
        "ai_api_key": "sk-ant-api03-EXAMPLEEXAMPLE",
        "ai_api_key_openai": "sk-proj-EXAMPLEEXAMPLE",
        "channel": "tgram://123456:EXAMPLE/987654",
        "client_key": "-----BEGIN PRIVATE KEY-----\nEXAMPLE\n-----END PRIVATE KEY-----",
    }
    with open_db(path) as conn:
        for key in ("ai_api_key", "ai_api_key_openai"):
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, datetime('now'), 1)",
                (key, enc(plain[key])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('server_name', 'nas', datetime('now'), 0)"
        )
        conn.execute(
            "INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at) VALUES ('Phone', ?, 1, datetime('now'), datetime('now'))",
            (enc(plain["channel"]),),
        )
        conn.execute(
            "INSERT INTO docker_endpoints (name, url, source_alias, enabled, tls_enabled, client_key, created_at, updated_at) "
            "VALUES ('NAS', 'tcp://10.0.0.5:2376', 'nas-docker', 1, 1, ?, datetime('now'), datetime('now'))",
            (enc(plain["client_key"]),),
        )
    return plain


def _stored(path: Path) -> dict[str, str]:
    with open_db(path) as conn:
        values = dict(conn.execute("SELECT key, value FROM system_settings WHERE key IN ('ai_api_key', 'ai_api_key_openai', 'server_name')"))
        values["channel"] = conn.execute("SELECT url FROM notification_channels WHERE name = 'Phone'").fetchone()[0]
        values["client_key"] = conn.execute("SELECT client_key FROM docker_endpoints WHERE name = 'NAS'").fetchone()[0]
        return values


class TestReencryptionAtUpgrade:

    @pytest.mark.parametrize("master", ["generated", "passphrase"])
    def test_every_stored_secret_moves_to_the_secrets_key(self, tmp_path, key_env, monkeypatch, master):
        if master == "passphrase":
            monkeypatch.setenv("LOGSHED_SECRET_KEY", PASSPHRASE)
        get_or_create_master_key(key_env)
        db = tmp_path / "logs.db"
        _v3_database(db)
        plain = _seed_legacy_secrets(db)
        before = _stored(db)

        run_migrations(db)

        after = _stored(db)
        with open_db(db) as conn:
            assert get_user_version(conn) == 4
        assert after["server_name"] == "nas"
        for name, text in plain.items():
            assert after[name] != before[name]
            assert get_secrets_fernet().decrypt(after[name].encode()).decode() == text
            with pytest.raises(InvalidToken):
                get_legacy_fernet().decrypt(after[name].encode())
            assert decrypt_value(after[name]) == text

    def test_a_secret_no_key_reads_is_left_as_it_is_with_a_warning(self, tmp_path, key_env, caplog):
        get_or_create_master_key(key_env)
        db = tmp_path / "logs.db"
        _v3_database(db)
        _seed_legacy_secrets(db)
        foreign = Fernet(Fernet.generate_key()).encrypt(b"from another key").decode()
        with open_db(db) as conn:
            conn.execute("UPDATE notification_channels SET url = ? WHERE name = 'Phone'", (foreign,))

        with caplog.at_level(logging.WARNING, logger="app.core.migrations"):
            run_migrations(db)

        after = _stored(db)
        assert after["channel"] == foreign
        assert decrypt_value(after["ai_api_key"]) == "sk-ant-api03-EXAMPLEEXAMPLE"
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("notification_channels.url" in w for w in warnings)
        assert not any("from another key" in w for w in warnings)

    def test_a_secret_already_under_the_new_key_is_not_touched(self, tmp_path, key_env):
        get_or_create_master_key(key_env)
        db = tmp_path / "logs.db"
        _v3_database(db)
        _seed_legacy_secrets(db)
        current = encrypt_value("sk-already-new")
        with open_db(db) as conn:
            conn.execute("UPDATE system_settings SET value = ? WHERE key = 'ai_api_key'", (current,))

        run_migrations(db)

        assert _stored(db)["ai_api_key"] == current

    def test_a_restored_1_3_backup_is_re_encrypted_again(self, tmp_path, key_env):
        """Going back by a restore and upgrading again works like the first upgrade."""
        get_or_create_master_key(key_env)
        db = tmp_path / "logs.db"
        _v3_database(db)
        _seed_legacy_secrets(db)
        old_copy = db.read_bytes()
        run_migrations(db)

        db.write_bytes(old_copy)
        for suffix in ("-wal", "-shm"):
            db.with_name(db.name + suffix).unlink(missing_ok=True)
        run_migrations(db)
        assert decrypt_value(_stored(db)["channel"]) == "tgram://123456:EXAMPLE/987654"

    def test_a_new_database_needs_no_key(self, tmp_path, monkeypatch):
        """A new database has nothing to re-encrypt, so creating it never reads or creates the key."""
        monkeypatch.setattr(security, "get_or_create_master_key", mock.Mock(side_effect=AssertionError("key read")))
        run_migrations(tmp_path / "new.db")

    def test_a_failure_part_way_rolls_back_every_value(self, tmp_path, key_env, monkeypatch):
        get_or_create_master_key(key_env)
        db = tmp_path / "logs.db"
        _v3_database(db)
        _seed_legacy_secrets(db)
        before = _stored(db)
        real = security.reencrypt_legacy_secret
        calls = []

        def _fail_on_third(value):
            calls.append(value)
            if len(calls) == 3:
                raise RuntimeError("stopped part way")
            return real(value)

        monkeypatch.setattr(security, "reencrypt_legacy_secret", _fail_on_third)
        with pytest.raises(RuntimeError, match="stopped part way"):
            run_migrations(db)

        assert _stored(db) == before
        with open_db(db) as conn:
            assert get_user_version(conn) == 3

