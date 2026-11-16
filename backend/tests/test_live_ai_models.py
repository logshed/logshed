import os
from pathlib import Path
import pytest
from app.services.ai_engine import fetch_available_models
from google import genai

pytestmark = pytest.mark.e2e


def load_env_keys():
    keys = {}
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip("'\"")
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_KEY"):
        if k in os.environ:
            keys[k] = os.environ[k]
    return keys


@pytest.mark.asyncio
async def test_live_claude_models():
    keys = load_env_keys()
    claude_key = keys.get("CLAUDE_API_KEY") or keys.get("ANTHROPIC_API_KEY")
    if not claude_key:
        pytest.skip("CLAUDE_API_KEY not configured in .env or environment")

    # Test LogShed's fetch_available_models for anthropic
    models = await fetch_available_models("anthropic", api_key=claude_key)
    print("\n--- ANTHROPIC DISCOVERED MODELS BY LOGSHED ---")
    for m in models:
        print(f"  + {m['id']} ({m['name']}, thinking: {m['supports_thinking']})")
    assert len(models) > 0


@pytest.mark.asyncio
async def test_live_gemini_models():
    keys = load_env_keys()
    gemini_key = keys.get("GEMINI_API_KEY")
    if not gemini_key:
        pytest.skip("GEMINI_API_KEY not configured in .env or environment")

    client = genai.Client(api_key=gemini_key)
    pager = await client.aio.models.list()
    async for m in pager:
        raw_name = getattr(m, "name", "") or ""
        mid = raw_name.replace("models/", "").replace("publishers/google/models/", "")
        if "banana" in mid.lower():
            print(f"\nBANANA MODEL FOUND: id={mid}")
            print(f"  display_name: {getattr(m, 'display_name', None)}")
            print(f"  description: {getattr(m, 'description', None)}")
            actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None)
            print(f"  actions: {actions}")

    filtered_models = await fetch_available_models("gemini", api_key=gemini_key)
    print("\nAccepted Gemini models:", [m['id'] for m in filtered_models])
    assert not any("banana" in m['id'] for m in filtered_models)
    assert len(filtered_models) > 0


@pytest.mark.asyncio
async def test_live_openai_models():
    keys = load_env_keys()
    openai_key = keys.get("OPENAI_API_KEY")
    if not openai_key:
        pytest.skip("OPENAI_API_KEY not configured in .env or environment")

    filtered_models = await fetch_available_models("openai", api_key=openai_key)
    print(f"\nTotal OpenAI models accepted: {len(filtered_models)}")
    for m in filtered_models:
        print(f"  + {m['id']}")
    assert len(filtered_models) > 0


@pytest.mark.asyncio
# Models with no shutdown date in GET /v1/models on 2026-11-02 (o4-mini shut down on 2026-10-23, gpt-5-mini shuts down on 2026-12-11)
@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-6.1-terra"])
async def test_live_openai_reasoning_model_runs_an_analysis(model):
    """A reasoning model as the only model (no fallback) completes an analysis."""
    from app.services.ai_engine import execute_ai_analysis

    keys = load_env_keys()
    openai_key = keys.get("OPENAI_API_KEY")
    if not openai_key:
        pytest.skip("OPENAI_API_KEY not configured in .env or environment")

    result = await execute_ai_analysis(
        provider="openai",
        model=model,
        api_key=openai_key,
        base_url=None,
        source_alias="nas-01",
        app_name="smartd",
        redacted_logs="2026-11-02T10:00:00Z nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors",
        log_count=1,
        timeout=120,
    )
    summary, model_used, fallback_attempts = result.summary, result.model_used, result.fallback_attempts
    tokens_in, tokens_out, tokens_thoughts = result.tokens_in, result.tokens_out, result.tokens_thoughts
    print(f"\n{model}: in {tokens_in}, out {tokens_out}, thinking {tokens_thoughts}: {summary[:200]!r}")
    assert summary.strip()
    assert model_used == model
    assert fallback_attempts == []


@pytest.mark.asyncio
# claude-sonnet-5-5 is the default Claude model (listed by GET /v1/models on 2026-11-02). Claude 4.6 and
# later think adaptively at the level's effort; off runs at low effort (Claude 5 models always think)
@pytest.mark.parametrize("thinking_level", ["low", "off"])
async def test_live_claude_runs_an_analysis_at_a_thinking_level(thinking_level):
    """The default Claude model completes an analysis at the default level and with thinking off."""
    from app.services.ai_engine import execute_ai_analysis

    keys = load_env_keys()
    claude_key = keys.get("CLAUDE_API_KEY") or keys.get("ANTHROPIC_API_KEY")
    if not claude_key:
        pytest.skip("CLAUDE_API_KEY not configured in .env or environment")

    result = await execute_ai_analysis(
        provider="anthropic",
        model="claude-sonnet-5-5",
        api_key=claude_key,
        base_url=None,
        source_alias="nas-01",
        app_name="smartd",
        redacted_logs="2026-11-02T10:00:00Z nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors",
        log_count=1,
        timeout=120,
        thinking_level=thinking_level,
    )
    summary, model_used, fallback_attempts = result.summary, result.model_used, result.fallback_attempts
    tokens_in, tokens_out, tokens_thoughts = result.tokens_in, result.tokens_out, result.tokens_thoughts
    print(f"\nclaude-sonnet-5-5 level {thinking_level}: in {tokens_in}, out {tokens_out}, thinking {tokens_thoughts}: {summary[:160]!r}")
    assert summary.strip()
    assert model_used == "claude-sonnet-5-5"
    assert fallback_attempts == []


@pytest.mark.asyncio
async def test_live_older_claude_without_thinking_runs_an_analysis():
    """Claude Haiku 4.5 with thinking off is sent no temperature: the SDK no longer takes one and the API refuses it."""
    from app.services.ai_engine import call_anthropic

    keys = load_env_keys()
    claude_key = keys.get("CLAUDE_API_KEY") or keys.get("ANTHROPIC_API_KEY")
    if not claude_key:
        pytest.skip("CLAUDE_API_KEY not configured in .env or environment")

    text, tokens_in, tokens_out, tokens_thoughts, _tokens_used, served = await call_anthropic(
        api_key=claude_key,
        model="claude-haiku-4-5-20251001",
        prompt="2026-11-02T10:00:00Z nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors",
        timeout=120,
        thinking_level="off",
    )
    print(f"\nclaude-haiku-4-5 off: in {tokens_in}, out {tokens_out}, thinking {tokens_thoughts}: {text[:160]!r}")
    assert text.strip()
    assert served == "claude-haiku-4-5-20251001"
    assert tokens_thoughts == 0


@pytest.mark.asyncio
# Off asks for the lowest level. gemini-3.5-flash takes minimal; on 2026-11-03 gemini-3.8-flash refused it (as does gemini-3.7-flash, the default
# Gemini model, which was overloaded that day) and OpenAI documented that gpt-6.1-sol (the default OpenAI model) refuses none, so both
# requests are retried at low. None of these models has a shutdown date.
@pytest.mark.parametrize(
    "provider, key_name, model",
    [("gemini", "GEMINI_API_KEY", "gemini-3.5-flash"), ("gemini", "GEMINI_API_KEY", "gemini-3.8-flash"), ("openai", "OPENAI_API_KEY", "gpt-6.1-sol")],
)
async def test_live_thinking_off_runs_an_analysis(provider, key_name, model, caplog):
    """A model completes an analysis with thinking off, also when it refuses the lowest level and is asked at low."""
    import logging

    from app.services.ai_engine import execute_ai_analysis

    api_key = load_env_keys().get(key_name)
    if not api_key:
        pytest.skip(f"{key_name} not configured in .env or environment")

    with caplog.at_level(logging.INFO, logger="app.services.ai_engine"):
        result = await execute_ai_analysis(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=None,
            source_alias="nas-01",
            app_name="smartd",
            redacted_logs="2026-11-02T10:00:00Z nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors",
            log_count=1,
            timeout=120,
            thinking_level="off",
        )
    summary, model_used, fallback_attempts = result.summary, result.model_used, result.fallback_attempts
    tokens_in, tokens_out, tokens_thoughts = result.tokens_in, result.tokens_out, result.tokens_thoughts
    retries = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    print(f"\n{model} off: in {tokens_in}, out {tokens_out}, thinking {tokens_thoughts}, retries {retries}: {summary[:160]!r}")
    assert summary.strip()
    assert model_used == model
    assert fallback_attempts == []


async def _openrouter_models(api_key: str) -> list[dict]:
    """OpenRouter's model list as LogShed keeps it (listing models is free)."""
    return await fetch_available_models("openrouter", api_key=api_key)


@pytest.mark.asyncio
async def test_live_openrouter_models():
    """The live list holds chat models only, with prices, context sizes and reasoning settings."""
    api_key = load_env_keys().get("OPENROUTER_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_KEY not configured in .env or environment")
    models = await _openrouter_models(api_key)
    by_id = {m["id"]: m for m in models}
    print(f"\n--- OPENROUTER: {len(models)} models kept ---")
    assert len(models) > 100
    assert not any(m["id"].endswith(":batch") for m in models)
    default = by_id["google/gemini-3.7-flash"]
    assert default["context_length"] and default["prompt_price"] is not None
    assert default["params"]["reasoning_efforts"]


@pytest.mark.asyncio
# Free models only, so the maintainer's free-tier key can run these (2026-11-16, no end date):
# particle-1 can stop reasoning (off is none); ernie-5.5-flash-preview always reasons and lists only
# high, so off asks for high, the nearest effort it lists. (inkling-2-small, also free, refused with
# 403 "not available in your region" on 2026-11-16.)
@pytest.mark.parametrize(
    "model, thinking_level, effort",
    [
        ("particle-ai/particle-1:free", "off", "none"),
        ("particle-ai/particle-1:free", "high", "high"),
        ("baidu/ernie-5.5-flash-preview:free", "off", "high"),
    ],
)
async def test_live_openrouter_runs_an_analysis(model, thinking_level, effort, caplog):
    """An analysis through OpenRouter with the reasoning setting from its model list, with no retry needed."""
    import logging

    from app.services.ai_engine import execute_ai_analysis, model_params_by_id

    api_key = load_env_keys().get("OPENROUTER_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_KEY not configured in .env or environment")
    import json

    from app.services.ai_engine import _openai_sampling_kwargs

    params = model_params_by_id(json.dumps(await _openrouter_models(api_key)))
    assert model in params
    assert _openai_sampling_kwargs(model, thinking_level, params)["extra_body"]["reasoning"]["effort"] == effort
    with caplog.at_level(logging.INFO, logger="app.services.ai_engine"):
        result = await execute_ai_analysis(
            provider="openrouter",
            model=model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            source_alias="nas-01",
            app_name="smartd",
            redacted_logs="2026-11-16T10:00:00Z nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors",
            log_count=1,
            timeout=120,
            thinking_level=thinking_level,
            model_params=params,
        )
    retries = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    print(
        f"\n{model} {thinking_level}: in {result.tokens_in}, out {result.tokens_out}, thinking {result.tokens_thoughts}, "
        f"retries {retries}: {result.summary[:160]!r}"
    )
    assert result.summary.strip()
    assert result.fallback_attempts == []
    assert retries == []


@pytest.mark.asyncio
async def test_live_openrouter_streams_a_follow_up():
    """A follow-up question streams its answer through OpenRouter (a free model)."""
    import json

    from app.services.ai_engine import execute_ai_follow_up, model_params_by_id

    api_key = load_env_keys().get("OPENROUTER_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_KEY not configured in .env or environment")
    params = model_params_by_id(json.dumps(await _openrouter_models(api_key)))
    deltas: list[str] = []

    async def on_progress(event):
        if event.get("stage") == "delta":
            deltas.append(event["content"])

    reply, tokens_in, tokens_out, _thoughts, _used, model_used, _attempts = await execute_ai_follow_up(
        provider="openrouter",
        model="particle-ai/particle-1:free",
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        messages=[
            {"role": "user", "content": "nas-01 smartd: Device /dev/sda, 8 Currently unreadable (pending) sectors"},
            {"role": "assistant", "content": "## Summary\nThe disk /dev/sda has unreadable sectors."},
            {"role": "user", "content": "Which one command shows the disk's SMART attributes? Answer in one line."},
        ],
        timeout=120,
        thinking_level="low",
        on_progress=on_progress,
        model_params=params,
    )
    print(f"\nfollow-up: in {tokens_in}, out {tokens_out}, {len(deltas)} chunks: {reply[:160]!r}")
    assert reply.strip() and len(deltas) >= 1
    assert model_used == "particle-ai/particle-1:free"
