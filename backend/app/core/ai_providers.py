"""
The AI providers LogShed can call: one entry per provider.

Everything that differs between providers as data lives here (label, SDK, whether a key is
needed, default model). The code each SDK needs (requests, streaming, model
discovery) lives in `services/ai_engine.py`, keyed by the entry's `sdk`. The web UI keeps the
same list in `frontend/src/utils/aiProviders.ts`, and `test_frontend_limits.py` keeps the two
in step.
"""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class AiProvider:
    id: str
    label: str
    # Which SDK makes the calls
    sdk: Literal["gemini", "openai", "anthropic"]
    # False for servers that may run without a key (a local model server, for example)
    needs_api_key: bool
    # The model used when none is chosen
    default_model: str


AI_PROVIDERS: dict[str, AiProvider] = {
    p.id: p
    for p in (
        AiProvider(id="gemini", label="Google Gemini", sdk="gemini", needs_api_key=True, default_model="gemini-3.7-flash"),
        AiProvider(id="openai", label="OpenAI", sdk="openai", needs_api_key=True, default_model="gpt-6.1-sol"),
        AiProvider(id="anthropic", label="Anthropic Claude", sdk="anthropic", needs_api_key=True, default_model="claude-sonnet-5-5"),
        AiProvider(id="openai_compatible", label="OpenAI-Compatible", sdk="openai", needs_api_key=False, default_model="llama3.2"),
    )
}

# The provider used when none was ever saved
DEFAULT_AI_PROVIDER = "gemini"
# The default model for a provider name that is not in the registry
UNKNOWN_PROVIDER_DEFAULT_MODEL = AI_PROVIDERS["openai_compatible"].default_model


def get_ai_provider(provider_id: Optional[str]) -> Optional[AiProvider]:
    """The registry entry for a provider id (any case), or None for an unknown id."""
    return AI_PROVIDERS.get((provider_id or "").lower())


def normalize_provider_id(provider_id: Optional[str]) -> str:
    """A provider id as stored and compared: lower case, the default provider when empty."""
    return (provider_id or DEFAULT_AI_PROVIDER).lower()


def default_model_for(provider_id: str) -> str:
    """The model a provider uses when none is chosen."""
    entry = get_ai_provider(provider_id)
    return entry.default_model if entry else UNKNOWN_PROVIDER_DEFAULT_MODEL


def provider_needs_api_key(provider_id: str) -> bool:
    """Whether calls to this provider need an API key. Unknown ids count as needing one."""
    entry = get_ai_provider(provider_id)
    return entry.needs_api_key if entry else True


def stored_provider_setting(
    settings: dict[str, str], provider: str, name: str, active_provider: Optional[str] = None
) -> str:
    """
    A provider's stored value for `name` (`ai_api_key`, `ai_model`, `ai_base_url` or
    `ai_fallback_models`): its own `<name>_<provider>` key, or for the active provider the
    single `<name>` key that LogShed versions before per-provider settings used. "" when unset.
    """
    active = active_provider if active_provider is not None else normalize_provider_id(settings.get("ai_provider"))
    value = settings.get(f"{name}_{provider}")
    if not value and provider == active:
        value = settings.get(name)
    return value or ""
