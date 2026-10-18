"""Tests for the reset-admin CLI password handling."""

from pathlib import Path

import pytest
from argon2 import PasswordHasher

import app.cli as cli_mod
from app.core.migrations import get_connection


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
