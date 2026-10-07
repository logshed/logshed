import os
from pathlib import Path
import httpx
import pytest
from app.services.ai_engine import fetch_available_models, is_text_generation_model, NON_TEXT_MODEL_KEYWORDS
from google import genai
from openai import AsyncOpenAI


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
