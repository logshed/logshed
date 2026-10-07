"""
Pytest configuration and global fixtures for LogShed backend test suite.
"""

import pytest
from app.core.config import invalidate_settings_cache


@pytest.fixture(autouse=True)
def auto_reset_settings_cache(monkeypatch):
    """Ensure in-memory settings cache is invalidated between test cases and on env changes."""
    invalidate_settings_cache()
    orig_setenv = monkeypatch.setenv
    orig_delenv = monkeypatch.delenv

    def custom_setenv(*args, **kwargs):
        orig_setenv(*args, **kwargs)
        invalidate_settings_cache()

    def custom_delenv(*args, **kwargs):
        orig_delenv(*args, **kwargs)
        invalidate_settings_cache()

    monkeypatch.setenv = custom_setenv
    monkeypatch.delenv = custom_delenv
    yield
    invalidate_settings_cache()
