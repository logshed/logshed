"""Tests for the reset-admin CLI: password checks, prompts and the stored admin row."""

from pathlib import Path

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

import app.cli as cli_mod
from app.cli import reset_admin
from app.core.migrations import get_connection, run_migrations


def _run(monkeypatch, db_path: Path, *extra: str):
    monkeypatch.setattr("sys.argv", ["app.cli", "reset-admin", "--db-path", str(db_path), *extra])
    cli_mod.main()


def _stored_hash(db_path: Path):
    conn = get_connection(str(db_path))
    try:
        row = conn.execute("SELECT password_hash FROM admin_auth WHERE id=1").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


class TestResetAdminPassword:
    def test_short_password_flag_exits_1(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "a.db"
        with pytest.raises(SystemExit) as exc:
            _run(monkeypatch, db, "--password", "short")
        assert exc.value.code == 1
        assert "8" in capsys.readouterr().err
        assert not db.exists() or _stored_hash(db) is None

    def test_too_long_password_exits_1(self, tmp_path, monkeypatch):
        db = tmp_path / "b.db"
        with pytest.raises(SystemExit) as exc:
            _run(monkeypatch, db, "--password", "x" * 129)
        assert exc.value.code == 1

    def test_mismatched_prompts_exit_1(self, tmp_path, monkeypatch):
        db = tmp_path / "c.db"
        answers = iter(["FirstPassword123", "SecondPassword456"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
        with pytest.raises(SystemExit) as exc:
            _run(monkeypatch, db)
        assert exc.value.code == 1
        assert not db.exists() or _stored_hash(db) is None

    def test_matching_prompts_set_password(self, tmp_path, monkeypatch):
        db = tmp_path / "d.db"
        prompts = []

        def fake(prompt=""):
            prompts.append(prompt)
            return "MatchingPassword123"

        monkeypatch.setattr("getpass.getpass", fake)
        _run(monkeypatch, db)
        assert len(prompts) == 2
        assert PasswordHasher().verify(_stored_hash(db), "MatchingPassword123")

    def test_password_flag_warns_but_works(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "e.db"
        _run(monkeypatch, db, "--password", "ValidPassword123")
        assert "warning" in capsys.readouterr().err.lower()
        assert PasswordHasher().verify(_stored_hash(db), "ValidPassword123")


class TestResetAdminFunction:
    def test_sets_the_password_on_an_empty_table(self, tmp_path):
        db = tmp_path / "empty.db"
        run_migrations(db)
        reset_admin("newpassword123", str(db))

        stored = _stored_hash(db)
        assert stored.startswith("$argon2id$")
        assert PasswordHasher().verify(stored, "newpassword123")

    def test_replaces_an_existing_password_and_keeps_created_at(self, tmp_path):
        db = tmp_path / "existing.db"
        run_migrations(db)
        ph = PasswordHasher()
        conn = get_connection(str(db))
        try:
            conn.execute(
                "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
                "VALUES (1, ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
                (ph.hash("oldpassword"),),
            )
            conn.commit()
        finally:
            conn.close()

        reset_admin("newpassword456", str(db))

        conn = get_connection(str(db))
        try:
            new_hash, created_at, updated_at = conn.execute(
                "SELECT password_hash, created_at, updated_at FROM admin_auth WHERE id=1"
            ).fetchone()
        finally:
            conn.close()
        assert new_hash.startswith("$argon2id$")
        with pytest.raises(VerifyMismatchError):
            ph.verify(new_hash, "oldpassword")
        assert ph.verify(new_hash, "newpassword456")
        assert created_at == "2024-01-01T00:00:00"
        assert updated_at != "2024-01-01T00:00:00"

    def test_repeated_resets_keep_one_row(self, tmp_path):
        db = tmp_path / "onerow.db"
        run_migrations(db)
        for password in ("pass1", "pass2", "pass3"):
            reset_admin(password, str(db))

        conn = get_connection(str(db))
        try:
            assert conn.execute("SELECT COUNT(*) FROM admin_auth").fetchone()[0] == 1
        finally:
            conn.close()
        assert PasswordHasher().verify(_stored_hash(db), "pass3")


class TestNewerDatabase:
    def test_reset_admin_refuses_a_newer_database_without_changing_it(self, tmp_path, monkeypatch, capsys):
        import sqlite3

        db = tmp_path / "newer.db"
        run_migrations(db)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        before = db.read_bytes()

        with pytest.raises(SystemExit) as exc:
            _run(monkeypatch, db, "--password", "ValidPassword123")

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "newer version of LogShed" in err and "Traceback" not in err
        assert db.read_bytes() == before
