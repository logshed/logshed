"""The AI provider registry and the per-provider settings lookup every caller shares."""

from app.core.ai_providers import (
    AI_PROVIDERS,
    default_model_for,
    provider_needs_api_key,
    stored_provider_setting,
)
from app.services.ai_service import is_ai_enabled, resolve_ai_provider_settings


def test_every_provider_has_a_default_model_and_a_known_sdk():
    for entry in AI_PROVIDERS.values():
        assert entry.default_model
        assert entry.sdk in ("gemini", "openai", "anthropic")


def test_an_unknown_provider_needs_a_key_and_uses_the_compatible_default_model():
    assert provider_needs_api_key("not-a-provider") is True
    assert default_model_for("not-a-provider") == AI_PROVIDERS["openai_compatible"].default_model


def test_own_key_wins_over_the_single_key_of_older_versions():
    settings = {"ai_provider": "openai", "ai_api_key": "old", "ai_api_key_openai": "new"}
    assert stored_provider_setting(settings, "openai", "ai_api_key") == "new"


def test_single_key_of_older_versions_belongs_to_the_active_provider_only():
    settings = {"ai_provider": "openai", "ai_api_key": "old"}
    assert stored_provider_setting(settings, "openai", "ai_api_key") == "old"
    assert stored_provider_setting(settings, "anthropic", "ai_api_key") == ""


def test_active_provider_defaults_to_gemini_when_never_saved():
    assert stored_provider_setting({"ai_model": "m"}, "gemini", "ai_model") == "m"


def test_ai_is_on_without_a_key_only_for_providers_that_need_none():
    assert is_ai_enabled({}, "openai_compatible", "") is True
    assert is_ai_enabled({}, "openai", "") is False


def test_resolver_reads_the_registry_default_model():
    resolved = resolve_ai_provider_settings({"ai_provider": "openai"})
    assert resolved["model"] == AI_PROVIDERS["openai"].default_model
