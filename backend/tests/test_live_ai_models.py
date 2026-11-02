import os
from pathlib import Path
import httpx
import pytest
from app.services.ai_engine import fetch_available_models, is_text_generation_model, NON_TEXT_MODEL_KEYWORDS
from google import genai
from openai import AsyncOpenAI

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
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_API_KEY"):
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
# A model with no shutdown date in GET /v1/models on 2026-11-02 (o4-mini shut down on 2026-10-23, gpt-5-mini shuts down on 2026-12-11)
@pytest.mark.parametrize("model", ["gpt-5.6-luna"])
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
    summary, model_used, fallback_attempts = result[0], result[9], result[10]
    print(f"\n{model}: {summary[:200]!r}")
    assert summary.strip()
    assert model_used == model
    assert fallback_attempts == []
