"""
AI Preview and Analysis API endpoints for LogShed.
"""

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, run_db_query
from app.core.config import is_debug_or_dev
from app.core.rate_limiter import ai_rate_limiter
from app.core.redactor import redact
from app.core.security import SESSION_COOKIE_NAME
from app.models import (
    AiDiagnosisRequest,
    AiDiagnosisResponse,
    AiModelInfo,
    AiModelRefreshRequest,
    AiModelsResponse,
    AiPreviewRequest,
    AiPreviewResponse,
)
from app.services.ai_engine import (
    DEFAULT_SYSTEM_PROMPT,
    build_analysis_prompt,
    execute_ai_analysis,
    fetch_available_models,
    is_text_generation_model,
)
from app.services.ai_service import (
    build_diagnosis_context,
    is_cache_fresh,
    read_ai_settings,
    save_diagnosis_audit,
    save_models_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI"])


def _check_ai_rate_limit(request: Request, user: dict) -> None:
    """Enforce sliding-window rate limit for AI diagnosis requests."""
    session_key = request.cookies.get(SESSION_COOKIE_NAME) or str(user.get("user_id", "default"))
    if not ai_rate_limiter.check_and_record(session_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Maximum 10 AI diagnosis requests per minute.",
        )


@router.get("/models", response_model=AiModelsResponse)
async def list_available_models(
    provider: Optional[str] = Query(None, description="AI Provider ('gemini', 'openai', 'anthropic', 'openai_compatible')"),
    user: dict = Depends(get_current_user),
) -> AiModelsResponse:
    """
    Retrieve active text models for the specified or configured provider from SQLite cache.
    Strictly read-only; does not query external providers or perform database writes.
    If no API key is configured, returns has_api_key=False with an empty list.
    """
    stored_settings, updated_map = await run_db_query(read_ai_settings)
    active_db_provider = (stored_settings.get("ai_provider") or "gemini").lower()
    clean_provider = (provider or active_db_provider).lower()

    # Provider-specific key check
    api_key = stored_settings.get(f"ai_api_key_{clean_provider}", "").strip()
    if not api_key and clean_provider == active_db_provider:
        api_key = stored_settings.get("ai_api_key", "").strip()

    # If the provider requires an API key and none is set, prompt user
    if clean_provider in ("gemini", "openai", "anthropic") and not api_key:
        provider_names = {
            "gemini": "Google Gemini",
            "openai": "OpenAI",
            "anthropic": "Anthropic Claude",
        }
        provider_name = provider_names.get(clean_provider, clean_provider)
        return AiModelsResponse(
            provider=clean_provider,
            models=[],
            has_api_key=False,
            is_live=False,
            error=f"No API key configured for {provider_name}. Please configure your API key in Settings to view available models.",
        )

    cache_key = f"ai_models_cache_{clean_provider}"
    cached_json = stored_settings.get(cache_key)
    cached_updated_at = updated_map.get(cache_key)

    if cached_json:
        try:
            cached_items = json.loads(cached_json)
            clean_items = [
                item for item in cached_items
                if is_text_generation_model(item.get("id", ""), item.get("description", ""))
            ]
            return AiModelsResponse(
                provider=clean_provider,
                models=[AiModelInfo(**item) for item in clean_items],
                has_api_key=True,
                cached_at=str(cached_updated_at) if cached_updated_at else None,
                is_live=False,
                error=None,
            )
        except Exception as e:
            logger.warning(f"Failed to parse cached models for {clean_provider}: {e}")

    return AiModelsResponse(
        provider=clean_provider,
        models=[],
        has_api_key=True,
        cached_at=None,
        is_live=False,
        error=None,
    )


@router.post("/models/refresh", response_model=AiModelsResponse)
async def refresh_available_models(
    provider: Optional[str] = Query(None, description="AI Provider ('gemini', 'openai', 'anthropic', 'openai_compatible')"),
    payload: Optional[AiModelRefreshRequest] = None,
    user: dict = Depends(get_current_user),
) -> AiModelsResponse:
    """
    Force live refresh of available models from external provider API.
    Updates the cache in system_settings and returns discovered models.
    Optionally accepts a fresh API key in payload to test and store without a prior save.
    Requires authentication and CSRF header.
    """
    stored_settings, updated_map = await run_db_query(read_ai_settings)
    active_db_provider = (stored_settings.get("ai_provider") or "gemini").lower()
    clean_provider = (provider or active_db_provider).lower()

    key_from_payload = False
    if payload and payload.api_key and payload.api_key != "********" and payload.api_key.strip():
        api_key = payload.api_key.strip()
        key_from_payload = True
    else:
        api_key = stored_settings.get(f"ai_api_key_{clean_provider}", "").strip()
        if not api_key and clean_provider == active_db_provider:
            api_key = stored_settings.get("ai_api_key", "").strip()

    if payload and payload.base_url and payload.base_url.strip():
        base_url = payload.base_url.strip()
    else:
        base_url = stored_settings.get(f"ai_base_url_{clean_provider}") or stored_settings.get("ai_base_url")

    # If the provider requires an API key and none is set, prompt user
    if clean_provider in ("gemini", "openai", "anthropic") and not api_key:
        provider_names = {
            "gemini": "Google Gemini",
            "openai": "OpenAI",
            "anthropic": "Anthropic Claude",
        }
        provider_name = provider_names.get(clean_provider, clean_provider)
        return AiModelsResponse(
            provider=clean_provider,
            models=[],
            has_api_key=False,
            is_live=False,
            error=f"No API key configured for {provider_name}. Please configure your API key in Settings to view available models.",
        )

    cache_key = f"ai_models_cache_{clean_provider}"
    cached_json = stored_settings.get(cache_key)
    cached_updated_at = updated_map.get(cache_key)

    # Query provider live
    try:
        discovered = await fetch_available_models(
            provider=clean_provider,
            api_key=api_key if api_key else None,
            base_url=base_url if base_url else None,
        )

        def _persist_refresh(conn):
            now_iso = save_models_cache(conn, cache_key, discovered)
            if key_from_payload:
                from app.core.security import encrypt_value
                from app.core.config import invalidate_settings_cache
                enc = encrypt_value(api_key)
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, is_encrypted = 1
                    """,
                    (f"ai_api_key_{clean_provider}", enc, now_iso),
                )
                if clean_provider == active_db_provider:
                    cur.execute(
                        """
                        INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                        VALUES ('ai_api_key', ?, ?, 1)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, is_encrypted = 1
                        """,
                        (enc, now_iso),
                    )
                conn.commit()
                invalidate_settings_cache()
            return now_iso

        now_str = await run_db_query(_persist_refresh)
        return AiModelsResponse(
            provider=clean_provider,
            models=[AiModelInfo(**m) for m in discovered],
            has_api_key=True,
            cached_at=now_str,
            is_live=True,
            error=None,
        )
    except Exception as exc:
        logger.warning(f"Failed to fetch live models from provider '{clean_provider}': {exc}")
        # If we have a stale cache, return it with a non-fatal warning error
        if cached_json:
            try:
                stale_items = json.loads(cached_json)
                clean_stale = [
                    item for item in stale_items
                    if is_text_generation_model(item.get("id", ""), item.get("description", ""))
                ]
                return AiModelsResponse(
                    provider=clean_provider,
                    models=[AiModelInfo(**item) for item in clean_stale],
                    has_api_key=True,
                    cached_at=str(cached_updated_at) if cached_updated_at else None,
                    is_live=False,
                    error=f"Could not refresh models from {clean_provider}: {exc}. Displaying cached list.",
                )
            except Exception:
                pass

        return AiModelsResponse(
            provider=clean_provider,
            models=[],
            has_api_key=True,
            is_live=False,
            error=f"Failed to load models from {clean_provider}: {exc}",
        )


@router.post("/preview", response_model=AiPreviewResponse)
async def preview_ai_prompt(
    req: AiPreviewRequest,
    user: dict = Depends(get_current_user),
) -> AiPreviewResponse:
    """
    Generate a redacted preview of selected logs with token estimation.
    Supports single or multi-host log selections.
    Makes NO outbound LLM calls.
    """
    ctx = await build_diagnosis_context(
        log_ids=req.log_ids,
        user_context=req.user_context,
        prompt_override=req.prompt_override,
        client_timezone=req.client_timezone,
        client_utc_offset_minutes=req.client_utc_offset_minutes,
    )

    return AiPreviewResponse(
        redacted_prompt=ctx["full_prompt"],
        estimated_tokens=ctx["estimated_tokens"],
        provider=ctx["provider"],
        model=ctx["model"],
        fallback_models=ctx["fallback_models"],
        log_count=len(ctx["rows"]),
        source_alias=ctx["source_alias"],
        app_name=ctx["app_name"],
        system_prompt=ctx["system_prompt"],
        ai_enabled=ctx.get("ai_enabled", True),
        has_ai_api_key=ctx.get("has_ai_api_key", True),
    )


@router.post("/diagnose", response_model=AiDiagnosisResponse)
async def diagnose_logs(
    req: AiDiagnosisRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> AiDiagnosisResponse:
    """
    Executes full on-demand AI root-cause diagnosis on selected logs.
    Enforces rate limiting (10 req/min per user/session).
    Persists diagnosis in ai_audit_log and returns structured output.
    """
    _check_ai_rate_limit(request, user)

    try:
        ctx = await build_diagnosis_context(
            log_ids=req.log_ids,
            user_context=req.user_context,
            prompt_override=req.prompt_override,
            system_prompt_override=req.system_prompt_override,
            provider_override=req.provider,
            model_override=req.model,
            fallback_models_override=req.fallback_models,
            client_timezone=req.client_timezone,
            client_utc_offset_minutes=req.client_utc_offset_minutes,
        )

        if not ctx.get("ai_enabled", True):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI provider is disabled in settings. Please enable an AI provider in Settings to inspect logs.",
            )

        ai_res = await execute_ai_analysis(
            provider=ctx["provider"],
            model=ctx["model"],
            api_key=ctx["api_key"],
            base_url=ctx["base_url"],
            source_alias=ctx["source_alias"],
            app_name=ctx["app_name"],
            redacted_logs=ctx["redacted_logs"],
            log_count=len(ctx["rows"]),
            user_context=ctx["redacted_user_context"],
            host_notes=ctx["redacted_host_notes"],
            prompt_override=ctx["redacted_prompt_override"],
            system_prompt=ctx["system_prompt"],
            fallback_models=ctx["fallback_models"],
            timeout=ctx.get("ai_timeout", 45.0),
            thinking_budget=ctx.get("ai_thinking_budget", 1024),
        )

        if len(ai_res) == 11:
            summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used, actual_model, fallback_attempts = ai_res
        else:
            summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = ai_res[:9]
            actual_model = ctx["model"]
            fallback_attempts = []

        audit_id = await save_diagnosis_audit(
            source_alias=ctx["source_alias"],
            app_name=ctx["app_name"],
            log_count=len(ctx["rows"]),
            user_context=ctx["redacted_user_context"],
            actual_model=actual_model,
            prompt_sent=prompt_sent,
            raw_response=raw_response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
            system_prompt=ctx["system_prompt"],
        )

        return AiDiagnosisResponse(
            summary=summary,
            root_cause=root_cause,
            remediation=remediation,
            model_used=actual_model,
            fallback_used=(actual_model != ctx["model"]),
            fallback_attempts=fallback_attempts,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
            audit_id=audit_id,
        )
    except HTTPException:
        raise
    except ValueError as ve:
        clean_err = str(redact(str(ve)[:500]))
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=clean_err)
    except (asyncio.TimeoutError, TimeoutError) as te:
        clean_err = str(te) if str(te) else "Request timed out after deadline"
        if is_debug_or_dev():
            logger.warning(f"AI analysis request timed out: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request timed out: {clean_err}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"AI analysis request timed out: {clean_err}",
        )
    except Exception as exc:
        clean_err = str(redact(str(exc)[:500]))
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI analysis failed: {clean_err}",
        )


@router.post("/diagnose/stream")
async def diagnose_logs_stream(
    req: AiDiagnosisRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Executes full on-demand AI root-cause diagnosis, streaming SSE progress events
    (calling, failover, complete, error) to provide immediate live feedback to operators.
    Enforces rate limiting (10 req/min per user/session).
    """
    _check_ai_rate_limit(request, user)

    ctx = await build_diagnosis_context(
        log_ids=req.log_ids,
        user_context=req.user_context,
        prompt_override=req.prompt_override,
        system_prompt_override=req.system_prompt_override,
        provider_override=req.provider,
        model_override=req.model,
        fallback_models_override=req.fallback_models,
        client_timezone=req.client_timezone,
        client_utc_offset_minutes=req.client_utc_offset_minutes,
    )

    if not ctx.get("ai_enabled", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI provider is disabled in settings. Please enable an AI provider in Settings to inspect logs.",
        )

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_progress(evt: dict):
            await queue.put(evt)

        # Emit initial event
        await queue.put({
            "stage": "init",
            "model": ctx["model"],
            "fallback_models": ctx["fallback_models"],
            "message": f"Initiating diagnosis with {ctx['model']}...",
        })

        async def worker():
            try:
                ai_res = await execute_ai_analysis(
                    provider=ctx["provider"],
                    model=ctx["model"],
                    api_key=ctx["api_key"],
                    base_url=ctx["base_url"],
                    source_alias=ctx["source_alias"],
                    app_name=ctx["app_name"],
                    redacted_logs=ctx["redacted_logs"],
                    log_count=len(ctx["rows"]),
                    user_context=ctx["redacted_user_context"],
                    host_notes=ctx["redacted_host_notes"],
                    prompt_override=ctx["redacted_prompt_override"],
                    system_prompt=ctx["system_prompt"],
                    fallback_models=ctx["fallback_models"],
                    on_progress=on_progress,
                    timeout=ctx.get("ai_timeout", 45.0),
                    thinking_budget=ctx.get("ai_thinking_budget", 1024),
                )

                if len(ai_res) == 11:
                    summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used, actual_model, fallback_attempts = ai_res
                else:
                    summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = ai_res[:9]
                    actual_model = ctx["model"]
                    fallback_attempts = []

                audit_id = await save_diagnosis_audit(
                    source_alias=ctx["source_alias"],
                    app_name=ctx["app_name"],
                    log_count=len(ctx["rows"]),
                    user_context=ctx["redacted_user_context"],
                    actual_model=actual_model,
                    prompt_sent=prompt_sent,
                    raw_response=raw_response,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_thoughts=tokens_thoughts,
                    tokens_used=tokens_used,
                    system_prompt=ctx["system_prompt"],
                )

                resp = AiDiagnosisResponse(
                    summary=summary,
                    root_cause=root_cause,
                    remediation=remediation,
                    model_used=actual_model,
                    fallback_used=(actual_model != ctx["model"]),
                    fallback_attempts=fallback_attempts,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_thoughts=tokens_thoughts,
                    tokens_used=tokens_used,
                    audit_id=audit_id,
                )
                await queue.put({
                    "stage": "complete",
                    "result": resp.model_dump(),
                })
            except Exception as exc:
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or not str(exc).strip():
                    clean_err = "Request timed out"
                else:
                    clean_err = str(redact(str(exc)[:500]))
                await queue.put({
                    "stage": "error",
                    "message": clean_err,
                })
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())

        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



