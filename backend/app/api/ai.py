"""
AI Preview, Analysis, and Audit Log API endpoints for LogShed.
"""

import asyncio
import datetime
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
    AiAuditDeleteResponse,
    AiAuditListResponse,
    AiDiagnosisRequest,
    AiDiagnosisResponse,
    AiModelInfo,
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
    clear_ai_audit_logs,
    delete_ai_audit_item,
    is_cache_fresh,
    list_ai_audit_logs,
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
    provider: Optional[str] = Query(None, description="AI Provider ('gemini', 'openai', 'openai_compatible')"),
    user: dict = Depends(get_current_user),
) -> AiModelsResponse:
    """
    Retrieve active text models for the specified or configured provider from SQLite cache.
    Strictly read-only; does not query external providers or perform database writes.
    If no API key is configured, returns has_api_key=False with an empty list.
    """
    stored_settings, updated_map = await run_db_query(read_ai_settings)
    clean_provider = (provider or stored_settings.get("ai_provider") or "gemini").lower()
    api_key = stored_settings.get("ai_api_key", "").strip()

    # If the provider requires an API key and none is set, prompt user
    if clean_provider in ("gemini", "openai") and not api_key:
        provider_name = "Google Gemini" if clean_provider == "gemini" else "OpenAI"
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
    provider: Optional[str] = Query(None, description="AI Provider ('gemini', 'openai', 'openai_compatible')"),
    user: dict = Depends(get_current_user),
) -> AiModelsResponse:
    """
    Force live refresh of available models from external provider API.
    Updates the cache in system_settings and returns discovered models.
    Requires authentication and CSRF header.
    """
    stored_settings, updated_map = await run_db_query(read_ai_settings)
    clean_provider = (provider or stored_settings.get("ai_provider") or "gemini").lower()
    api_key = stored_settings.get("ai_api_key", "").strip()
    base_url = stored_settings.get("ai_base_url")

    # If the provider requires an API key and none is set, prompt user
    if clean_provider in ("gemini", "openai") and not api_key:
        provider_name = "Google Gemini" if clean_provider == "gemini" else "OpenAI"
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

        now_str = await run_db_query(lambda conn: save_models_cache(conn, cache_key, discovered))
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


@router.get("/audit", response_model=AiAuditListResponse)
async def list_ai_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    trigger_source: Optional[str] = Query(None, description="Filter by trigger origin ('on-demand' or 'alert')"),
    user: dict = Depends(get_current_user),
) -> AiAuditListResponse:
    """
    Retrieve historical AI analyses from ai_audit_log.
    """
    items, total = await list_ai_audit_logs(limit=limit, offset=offset, trigger_source=trigger_source)
    return AiAuditListResponse(items=items, total=total)


@router.delete("/audit/{audit_id}", response_model=AiAuditDeleteResponse)
async def delete_ai_audit_item_endpoint(
    audit_id: int,
    user: dict = Depends(get_current_user),
) -> AiAuditDeleteResponse:
    """
    Delete a single AI audit log entry by ID.
    """
    found = await delete_ai_audit_item(audit_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI audit entry not found")
    return AiAuditDeleteResponse(status="ok", deleted_id=audit_id, deleted_count=1)


@router.delete("/audit", response_model=AiAuditDeleteResponse)
async def clear_ai_audit_log_endpoint(
    user: dict = Depends(get_current_user),
) -> AiAuditDeleteResponse:
    """
    Clear all AI audit log entries.
    """
    count = await clear_ai_audit_logs()
    return AiAuditDeleteResponse(status="ok", deleted_count=count)
