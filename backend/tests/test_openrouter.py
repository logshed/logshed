"""
OpenRouter as an AI provider: its registry entry and fixed address, the model list read from its
/models metadata, and the reasoning and temperature sent with each request.
"""
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.ai_providers import (
    AI_PROVIDERS,
    provider_base_url,
    provider_has_fixed_base_url,
)
from app.core.migrations import run_migrations
from app.core.rate_limiter import ai_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    decrypt_value,
    get_or_create_master_key,
    hash_password,
    reset_crypto_cache,
)
from app.main import create_app
from app.services import ai_engine
from app.services.ai_service import resolve_ai_provider_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1"
FIXTURE = Path(__file__).parent / "fixtures" / "openrouter_models.json"
# Real records from OpenRouter's /models (2026-11-16), trimmed to the fields LogShed reads
RAW_MODELS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
FAKE_KEY = "openrouter-test-" + "key-1234"


def _raw(model_id: str) -> dict[str, Any]:
    return next(m for m in RAW_MODELS if m["id"] == model_id)


class _Page:
    """Stands in for the openai SDK's model paginator: iterated with `async for`."""

    def __init__(self, items: list[Any]):
        self._items = items

    def __aiter__(self):
        async def gen():
            for item in self._items:
                yield item
        return gen()


def _sdk_model(raw: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.id = raw["id"]
    m.to_dict.return_value = raw
    return m


@pytest.fixture(autouse=True)
def fresh_server(tmp_path: Path, monkeypatch):
    ai_rate_limiter.reset()
    reset_crypto_cache()
    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(key_file)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, '2026-11-16T10:00:00Z', '2026-11-16T10:00:00Z')",
        (hash_password("SuperSecretAdminPassword123!"),),
    )
    conn.commit()
    conn.close()
    yield db_file
    ai_rate_limiter.reset()
    reset_crypto_cache()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={SESSION_COOKIE_NAME: create_session_token(user_id=1)},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as ac:
        yield ac


def _stored(db_file: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_file))
    try:
        return {k: v for k, v in conn.execute("SELECT key, value FROM system_settings")}
    finally:
        conn.close()


class TestRegistry:
    def test_openrouter_uses_the_openai_sdk_at_its_own_address(self):
        entry = AI_PROVIDERS["openrouter"]
        assert (entry.label, entry.sdk, entry.needs_api_key) == ("OpenRouter", "openai", True)
        assert entry.base_url == OPENROUTER_URL
        assert entry.model_params_from_list is True

    def test_a_fixed_address_wins_over_any_stored_base_url(self):
        assert provider_has_fixed_base_url("openrouter") is True
        assert provider_base_url("openrouter", "http://evil.example/v1") == OPENROUTER_URL
        assert provider_base_url("OpenRouter", None) == OPENROUTER_URL

    def test_other_providers_keep_their_stored_base_url(self):
        for provider in ("openai", "openai_compatible", "gemini", "anthropic"):
            assert provider_has_fixed_base_url(provider) is False
        assert provider_base_url("openai_compatible", "http://ollama:11434/v1") == "http://ollama:11434/v1"
        assert provider_base_url("openai_compatible", "") is None
        assert provider_base_url("mystery", "http://x/v1") == "http://x/v1"


class TestModelList:
    def test_keeps_chat_models_with_price_context_and_what_they_accept(self):
        entry = ai_engine.openrouter_model_entry(_raw("google/gemini-3.7-flash"))
        assert entry == {
            "id": "google/gemini-3.7-flash",
            "name": _raw("google/gemini-3.7-flash")["name"],
            "description": None,
            "supports_thinking": True,
            "is_deprecated": False,
            "context_length": 1048576,
            "prompt_price": 0.75,
            "completion_price": 3.75,
            "params": {
                "supports_reasoning": True,
                "reasoning_mandatory": True,
                "reasoning_efforts": ["high", "medium", "low"],
                "takes_temperature": True,
            },
        }

    def test_leaves_out_batch_variants_and_models_that_give_no_text(self):
        assert ai_engine.openrouter_model_entry(_raw("google/gemini-3.7-flash:batch")) is None
        image_model = _raw("google/gemini-nano-banana-2.1")
        assert "image" in image_model["architecture"]["output_modalities"]
        assert ai_engine.openrouter_model_entry(image_model) is None
        # A model that takes no text cannot read logs
        no_text_in = {**_raw("openai/gpt-6-luna"), "architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]}}
        assert ai_engine.openrouter_model_entry(no_text_in) is None
        # Without the chat endpoint (a future variant like :batch), the model is left out too
        no_chat = {**_raw("openai/gpt-6-luna"), "id": "openai/gpt-6-luna:async", "endpoints": ["/api/v1/batches"]}
        assert ai_engine.openrouter_model_entry(no_chat) is None

    def test_keeps_free_models_and_routers_whose_price_varies(self):
        free = next(m for m in RAW_MODELS if m["id"].endswith(":free"))
        entry = ai_engine.openrouter_model_entry(free)
        assert entry is not None and (entry["prompt_price"], entry["completion_price"]) == (0.0, 0.0)
        # openrouter/auto says it may answer with images too; a text request gets text
        assert _raw("openrouter/auto")["architecture"]["output_modalities"] == ["text", "image"]
        router = ai_engine.openrouter_model_entry(_raw("openrouter/auto"))
        assert router is not None
        assert (router["prompt_price"], router["completion_price"]) == (None, None)
        assert router["supports_thinking"] is True

    def test_marks_a_model_with_an_end_date_as_deprecated(self):
        assert _raw("z-ai/glm-5.1")["expiration_date"]
        assert ai_engine.openrouter_model_entry(_raw("z-ai/glm-5.1"))["is_deprecated"] is True

    def test_a_model_without_reasoning_takes_temperature(self):
        entry = ai_engine.openrouter_model_entry(_raw("openai/gpt-5.2-chat"))
        assert entry["supports_thinking"] is False
        assert entry["params"]["supports_reasoning"] is False

    @pytest.mark.asyncio
    async def test_the_list_is_fetched_from_openrouter_whatever_base_url_is_passed(self):
        with patch("app.services.ai_engine.AsyncOpenAI") as mock_cls:
            inst = MagicMock()
            inst.close = AsyncMock()
            inst.models.list.return_value = _Page([_sdk_model(m) for m in RAW_MODELS])
            mock_cls.return_value = inst
            models = await ai_engine.fetch_available_models("openrouter", api_key=FAKE_KEY, base_url="http://evil.example/v1")
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == OPENROUTER_URL
        assert kwargs["api_key"] == FAKE_KEY
        assert kwargs["default_headers"] == ai_engine.OPENROUTER_ATTRIBUTION_HEADERS
        ids = [m["id"] for m in models]
        assert ids == sorted(ids, key=str.lower, reverse=True)
        assert "google/gemini-3.7-flash" in ids and "openrouter/auto" in ids
        assert not any(i.endswith(":batch") for i in ids)
        inst.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_key_lists_nothing_and_calls_nothing(self):
        with patch("app.services.ai_engine.AsyncOpenAI") as mock_cls:
            assert await ai_engine.fetch_available_models("openrouter", api_key=None) == []
        mock_cls.assert_not_called()

    def test_model_params_are_read_back_from_the_cached_list(self):
        entries = [ai_engine.openrouter_model_entry(m) for m in RAW_MODELS]
        cached = json.dumps([e for e in entries if e])
        params = ai_engine.model_params_by_id(cached)
        assert params["google/gemini-3.7-flash"]["reasoning_efforts"] == ["high", "medium", "low"]
        assert "google/gemini-3.7-flash:batch" not in params
        # A cache written by other providers (no params) or not readable gives nothing
        assert ai_engine.model_params_by_id(json.dumps([{"id": "gpt-6.1-sol", "name": "x"}])) == {}
        assert ai_engine.model_params_by_id("not json") == {}
        assert ai_engine.model_params_by_id(None) == {}


GEMINI_FLASH = {"supports_reasoning": True, "reasoning_mandatory": True, "reasoning_efforts": ["high", "medium", "low"], "takes_temperature": True}
LUNA = {"supports_reasoning": True, "reasoning_mandatory": False, "reasoning_efforts": ["max", "xhigh", "high", "medium", "low", "none"], "takes_temperature": False}
CHAT = {"supports_reasoning": False, "reasoning_mandatory": False, "reasoning_efforts": None, "takes_temperature": True}
NO_EFFORT_LIST = {"supports_reasoning": True, "reasoning_mandatory": False, "reasoning_efforts": None, "takes_temperature": True}
MINIMAL_ONLY_UP = {"supports_reasoning": True, "reasoning_mandatory": True, "reasoning_efforts": ["minimal", "high"], "takes_temperature": False}


class TestReasoning:
    @pytest.mark.parametrize(
        "params, level, effort",
        [
            # The level itself when the model lists it
            (LUNA, "high", "high"),
            (LUNA, "low", "low"),
            # Off is none where the model can stop reasoning
            (LUNA, "off", "none"),
            # A model that always reasons gets its lowest effort for off
            (GEMINI_FLASH, "off", "low"),
            # The nearest listed effort, the lower one on a tie
            (MINIMAL_ONLY_UP, "low", "minimal"),
            (MINIMAL_ONLY_UP, "medium", "high"),
            (MINIMAL_ONLY_UP, "off", "minimal"),
            # No list of efforts: the level as asked
            (NO_EFFORT_LIST, "medium", "medium"),
            (NO_EFFORT_LIST, "off", "none"),
            # No reasoning at all: nothing sent
            (CHAT, "high", None),
            (CHAT, "off", None),
            # An unknown thinking level is the default (low)
            (LUNA, "bogus", "low"),
        ],
    )
    def test_effort_for_each_thinking_level(self, params, level, effort):
        kwargs = ai_engine._openai_sampling_kwargs("some/model", level, {"some/model": params})
        assert "reasoning_effort" not in kwargs
        sent = kwargs.get("extra_body", {}).get("reasoning", {}).get("effort")
        assert sent == effort

    def test_temperature_only_when_not_reasoning_and_the_model_takes_one(self):
        def kwargs_for(params, level):
            return ai_engine._openai_sampling_kwargs("m", level, {"m": params})
        assert kwargs_for(CHAT, "high")["temperature"] == 0.2
        assert kwargs_for(NO_EFFORT_LIST, "off")["temperature"] == 0.2
        assert "temperature" not in kwargs_for(NO_EFFORT_LIST, "high")
        assert "temperature" not in kwargs_for(GEMINI_FLASH, "off")  # always reasons
        assert "temperature" not in kwargs_for(LUNA, "off")  # does not take one

    def test_a_model_not_in_the_list_gets_the_level_or_nothing_when_off(self):
        on = ai_engine._openai_sampling_kwargs("typed/by-hand", "high", {})
        assert on == {"extra_body": {"reasoning": {"effort": "high"}}}
        off = ai_engine._openai_sampling_kwargs("typed/by-hand", "off", {})
        assert off == {"temperature": 0.2}
        # A name that looks like an OpenAI reasoning model gets no temperature
        assert ai_engine._openai_sampling_kwargs("openai/gpt-6-luna", "off", {}) == {}

    def test_other_openai_sdk_providers_keep_reasoning_effort(self):
        kwargs = ai_engine._openai_sampling_kwargs("gpt-6.1-sol", "high")
        assert kwargs == {"reasoning_effort": "high"}
        assert "extra_body" not in kwargs

    @pytest.mark.asyncio
    async def test_a_refused_reasoning_setting_is_sent_again_without_it(self):
        refusal = openai.BadRequestError(
            "Reasoning is not supported for this model",
            response=MagicMock(status_code=400),
            body={"error": {"message": "Reasoning is not supported for this model"}},
        )
        answer = MagicMock()
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=[refusal, answer])
        result = await ai_engine._create_chat_completion(
            client, {"model": "m", "messages": [], "extra_body": {"reasoning": {"effort": "high"}}}
        )
        assert result is answer
        assert "extra_body" not in client.chat.completions.create.call_args_list[1].kwargs

    @pytest.mark.asyncio
    async def test_other_refusals_are_not_retried(self):
        refusal = openai.BadRequestError(
            "model not found", response=MagicMock(status_code=400), body={"error": {"message": "model not found"}}
        )
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=refusal)
        with pytest.raises(openai.BadRequestError):
            await ai_engine._create_chat_completion(
                client, {"model": "m", "messages": [], "extra_body": {"reasoning": {"effort": "high"}}}
            )
        assert client.chat.completions.create.await_count == 1

    @pytest.mark.asyncio
    async def test_call_openai_sends_the_openrouter_reasoning_and_attribution(self):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="## Summary\nok"))]
        response.usage = None
        with patch("app.services.ai_engine.AsyncOpenAI") as mock_cls:
            inst = MagicMock()
            inst.close = AsyncMock()
            inst.chat.completions.create = AsyncMock(return_value=response)
            mock_cls.return_value = inst
            await ai_engine.call_openai(
                api_key=FAKE_KEY,
                model="openai/gpt-6-luna",
                prompt="logs",
                base_url=OPENROUTER_URL,
                thinking_level="off",
                model_params={"openai/gpt-6-luna": LUNA},
            )
        sent = inst.chat.completions.create.call_args.kwargs
        assert sent["extra_body"] == {"reasoning": {"effort": "none"}}
        assert "reasoning_effort" not in sent and "temperature" not in sent
        assert mock_cls.call_args.kwargs["default_headers"] == ai_engine.OPENROUTER_ATTRIBUTION_HEADERS

    @pytest.mark.asyncio
    async def test_analysis_and_follow_up_pass_the_model_params_on(self):
        params = {"m": LUNA}
        reply = "## Summary\nok\n## Root Cause\nx\n## Remediation\ny"
        with patch("app.services.ai_engine.call_openai", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = (reply, 1, 1, 0, 2)
            await ai_engine.execute_ai_analysis(
                provider="openrouter", model="m", api_key=FAKE_KEY, base_url=OPENROUTER_URL,
                source_alias="router", app_name="app", redacted_logs="logs", log_count=1, model_params=params,
            )
        assert mock_call.call_args.kwargs["model_params"] is params
        with patch("app.services.ai_engine.dispatch_multi_turn_openai", new_callable=AsyncMock) as mock_turn:
            mock_turn.return_value = ("reply", 1, 1, 0, 2)
            await ai_engine.execute_ai_follow_up(
                provider="openrouter", model="m", api_key=FAKE_KEY, base_url=OPENROUTER_URL,
                messages=[{"role": "user", "content": "q"}], model_params=params,
            )
        assert mock_turn.call_args.kwargs["model_params"] is params


class TestSettings:
    def test_resolved_settings_use_the_fixed_address_and_the_cached_params(self):
        entry = ai_engine.openrouter_model_entry(_raw("openai/gpt-6-luna"))
        settings = {
            "ai_provider": "openrouter",
            "ai_api_key_openrouter": FAKE_KEY,
            "ai_model_openrouter": "openai/gpt-6-luna",
            # Left over from the provider used before: never used for OpenRouter
            "ai_base_url": "http://ollama.lan:11434/v1",
            "ai_models_cache_openrouter": json.dumps([entry]),
        }
        resolved = resolve_ai_provider_settings(settings)
        assert resolved["base_url"] == OPENROUTER_URL
        assert resolved["model"] == "openai/gpt-6-luna"
        assert resolved["model_params"] == {"openai/gpt-6-luna": entry["params"]}

    def test_resolved_settings_default_to_the_registry_model_and_empty_params(self):
        resolved = resolve_ai_provider_settings({"ai_provider": "openrouter", "ai_api_key_openrouter": FAKE_KEY})
        assert resolved["model"] == "google/gemini-3.7-flash"
        assert resolved["model_params"] == {}

    def test_other_providers_have_no_model_params(self):
        resolved = resolve_ai_provider_settings({
            "ai_provider": "openai_compatible", "ai_base_url": "https://openrouter.ai/api/v1",
        })
        assert resolved["model_params"] is None
        assert resolved["base_url"] == "https://openrouter.ai/api/v1"

    @pytest.mark.asyncio
    async def test_a_base_url_sent_for_openrouter_is_not_stored(self, client, fresh_server):
        res = await client.post("/api/settings", json={
            "ai_provider": "openrouter",
            "ai_base_url": "http://evil.example/v1",
            "ai_providers_config": {"openrouter": {"ai_api_key": FAKE_KEY, "ai_base_url": "http://evil.example/v1"}},
        })
        assert res.status_code == 200, res.text
        stored = _stored(fresh_server)
        assert not stored.get("ai_base_url_openrouter")
        assert stored.get("ai_base_url", "") != "http://evil.example/v1"
        # The key was not cleared as if the endpoint had changed
        assert decrypt_value(stored["ai_api_key_openrouter"]) == FAKE_KEY

    @pytest.mark.asyncio
    async def test_refresh_lists_from_openrouter_even_when_the_request_names_another_address(self, client, fresh_server):
        discovered = [ai_engine.openrouter_model_entry(_raw("openai/gpt-6-luna"))]
        with patch("app.api.ai.fetch_available_models", new=AsyncMock(return_value=discovered)) as mock_fetch:
            res = await client.post(
                "/api/ai/models/refresh?provider=openrouter",
                json={"api_key": FAKE_KEY, "base_url": "http://evil.example/v1"},
            )
        assert res.status_code == 200, res.text
        assert mock_fetch.call_args.kwargs["base_url"] == OPENROUTER_URL
        assert mock_fetch.call_args.kwargs["api_key"] == FAKE_KEY
        # The typed key goes with OpenRouter's own address, so it is kept
        assert decrypt_value(_stored(fresh_server)["ai_api_key_openrouter"]) == FAKE_KEY
        model = res.json()["models"][0]
        assert (model["id"], model["context_length"], model["prompt_price"], model["completion_price"]) == (
            "openai/gpt-6-luna", 1050000, 0.1, 0.5,
        )

        # The cached list gives the same price and context fields
        res = await client.get("/api/ai/models?provider=openrouter")
        cached = res.json()["models"][0]
        assert (cached["context_length"], cached["prompt_price"]) == (1050000, 0.1)
        assert "params" not in cached

    @pytest.mark.asyncio
    async def test_switching_between_openai_compatible_at_openrouter_and_openrouter_keeps_each_key(self, client, fresh_server):
        compat_key = "compat-test-" + "key-1"
        res = await client.post("/api/settings", json={
            "ai_provider": "openai_compatible",
            "ai_providers_config": {"openai_compatible": {"ai_api_key": compat_key, "ai_base_url": OPENROUTER_URL, "ai_model": "meta/llama"}},
        })
        assert res.status_code == 200, res.text
        res = await client.post("/api/settings", json={
            "ai_provider": "openrouter",
            "ai_providers_config": {"openrouter": {"ai_api_key": FAKE_KEY, "ai_model": "openai/gpt-6-luna"}},
        })
        assert res.status_code == 200, res.text
        settings = (await client.get("/api/settings")).json()
        assert settings["ai_provider"] == "openrouter" and settings["ai_model"] == "openai/gpt-6-luna"
        res = await client.post("/api/settings", json={"ai_provider": "openai_compatible"})
        assert res.status_code == 200, res.text

        stored = _stored(fresh_server)
        assert decrypt_value(stored["ai_api_key_openai_compatible"]) == compat_key
        assert decrypt_value(stored["ai_api_key_openrouter"]) == FAKE_KEY
        assert stored["ai_base_url_openai_compatible"] == OPENROUTER_URL
        settings = (await client.get("/api/settings")).json()
        assert settings["ai_provider"] == "openai_compatible"
        assert settings["ai_base_url"] == OPENROUTER_URL and settings["ai_model"] == "meta/llama"
        resolved = resolve_ai_provider_settings({k: v for k, v in stored.items() if not k.startswith("ai_api_key")})
        assert resolved["base_url"] == OPENROUTER_URL and resolved["model_params"] is None

    @pytest.mark.asyncio
    async def test_openrouter_without_a_key_asks_for_one(self, client):
        res = await client.get("/api/ai/models?provider=openrouter")
        assert res.status_code == 200
        assert res.json()["has_api_key"] is False
        assert "No API key configured for OpenRouter" in res.json()["error"]
