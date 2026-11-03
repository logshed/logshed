# Specification: AI Analysis Engine

> This document is part of the modular LogShed technical specification suite. For the full system specification, see [docs/SPEC.md](../../docs/SPEC.md).

LogShed provides two distinct AI analysis modes: user-initiated on-demand log analysis and automated alert incident enrichment. Outbound calls connect to external LLM providers through a unified multi-provider abstraction layer with automated redaction, dynamic model discovery, thinking levels, and full audit tracking.

---

## 1. Analysis Modes

### 1.1 On-Demand Analysis (User-Initiated)

- **Implementation Reference:** [backend/app/services/ai_service.py](../../backend/app/services/ai_service.py) and [frontend/src/components/ai/AiAnalysisModal.tsx](../../frontend/src/components/ai/AiAnalysisModal.tsx)
- **Selection:** The user selects one or multiple log entries in the UI across single or multiple hosts. Cross-host log selection is fully supported. The prompt builder annotates each dispatched log line with its severity code and dual timestamps citation (`[{time_utc} UTC ({time_local} {tz_name})] [{source_alias}] [{app_name}] [{severity}] {message}`), alongside originating host and alias metadata.
- **On-Demand Redaction Pass:** The backend filters selected logs through [backend/app/core/redactor.py](../../backend/app/core/redactor.py) (scrubbing tokens, passwords, JWTs, cloud API keys, and credentials) before returning the preview payload to the UI.
- **Payload Inspection & User Context:**
  - The UI opens an analysis modal displaying:
    * The scrubbed, redacted text preview exactly as it will be dispatched to the LLM.
    * Active provider and model name.
    * Estimated token count.
    * Free-text user context input field.
- **Execution Gate:** Outbound API calls occur **only** when the user explicitly clicks **"Run Analysis"** (or starts streaming via `/api/ai/diagnose/stream`).
- **Audit Logging:** Every on-demand request is recorded in `ai_audit_log` with `trigger_source = 'on-demand'` (prompt, user context, response, tokens used, and thoughts).

### 1.2 Automated Alert Enrichment (Background Incident Enrichment)

- **Implementation Reference:** [backend/app/services/alert_evaluator.py](../../backend/app/services/alert_evaluator.py)
- **Rule Configuration:** Alert rules can optionally enable automated background incident enrichment (`ai_enrichment = 1` in `alert_rules`).
- **Trigger Execution:** When an enabled alert rule with AI enrichment fires, `AlertEvaluator` redacts the triggering log sample through [backend/app/core/redactor.py](../../backend/app/core/redactor.py) and dispatches it to the configured LLM.
- **Incident Summary & Remediation:** The model generates a concise incident summary, root-cause diagnosis, and actionable remediation steps. This structured analysis is embedded directly into outbound notification payloads (dispatched via configured Apprise notification channels) and saved in `alert_history`.
- **Multi-Model Failover:** If the primary model encounters a transient error, timeout, or rate limit, the engine automatically attempts evaluation against configured fallback models.
- **Audit Tracking:** Automated alert analysis runs are recorded in `ai_audit_log` with `trigger_source = 'alert'` and linked to `alert_history.ai_audit_id`.
- **Privacy and Token Safeguards:** Clear privacy, credential redaction, and token usage warnings are presented in the UI when enabling this option (inside `AlertRuleModal` and notice banners on the Rules & Alerts Hub). The UI explicitly informs administrators that triggering logs are automatically dispatched to external AI providers without manual pre-screening, that automated scrubbing operates on a best-effort basis, and that automated triggers consume API tokens. If an AI provider is not configured in Settings, AI enrichment cannot be enabled and displays a direct link to Settings.

### 1.3 Follow-up Conversations

- **Implementation Reference:** `execute_ai_follow_up` in [backend/app/services/ai_engine.py](../../backend/app/services/ai_engine.py), `build_follow_up_context` and `save_follow_up_exchange` in [backend/app/services/ai_service.py](../../backend/app/services/ai_service.py), and [frontend/src/components/ai/AiFollowUpThread.tsx](../../frontend/src/components/ai/AiFollowUpThread.tsx).
- **Scope:** Any analysis with an `ai_audit_log` record (on-demand analyses and alert enrichments) accepts follow-up questions, from the on-demand modal or from Incident History.
- **Turn Reconstruction:** Each request rebuilds the conversation as the stored `prompt_sent` (user turn), the stored `response_text` (assistant turn), the earlier follow-up messages from `ai_conversation_messages` in order, and the new question.
- **Redaction:** The operator question passes through [backend/app/core/redactor.py](../../backend/app/core/redactor.py) before dispatch and is stored in its redacted form.
- **System Prompt:** A dedicated follow-up system prompt (`FOLLOW_UP_SYSTEM_PROMPT`) keeps the SRE persona, the passive-log-data guard and the dual UTC/local timestamp guidance, but asks for direct conversational Markdown without the three diagnosis section headers.
- **Provider & Model Resolution:** Provider, model and fallback models are resolved from the current settings (optionally overridden per request), never from the audit record, so follow-ups keep working after the original model is retired or reconfigured. The on-demand modal sends the provider, model and fallback order chosen for the diagnosis, so a follow-up fails over in the same order; Incident History uses the current settings. The thinking level and timeout settings apply as for diagnoses.
- **Streaming Dispatch:** `dispatch_multi_turn_gemini`, `dispatch_multi_turn_openai` (OpenAI and OpenAI-compatible, with `stream_options.include_usage`) and `dispatch_multi_turn_anthropic` stream text chunks to the caller and return token usage.
- **Multi-Model Failover:** Retryable failures (timeouts, 5xx, overload, rate limit, model not found) fail over through the configured fallback models, as for diagnoses. The next model streams its reply from the start.
- **Limits:** At most 10 follow-up questions per analysis (`400` once reached; the UI shows an advisory and disables the input). Questions are 1 to 2000 characters. Follow-ups share the 10 requests per minute AI rate limit with diagnoses.
- **Persistence:** The redacted question and the reply (with model and token usage) are saved together only after a successful reply. Clearing a thread keeps the diagnosis; deleting the audit record removes the thread by cascade.

---

## 2. AI Provider Abstraction

- **Implementation Reference:** [backend/app/services/ai_engine.py](../../backend/app/services/ai_engine.py)
- **Supported Providers & SDKs:**
  - Google Gemini via the `google-genai` SDK.
  - Anthropic Claude via the `anthropic` SDK.
  - OpenAI-compatible endpoints via the `openai` SDK (with configurable `base_url`: routers and hosted APIs such as OpenRouter, Groq, Mistral and DeepSeek, and self-hosted engines such as Ollama, LM Studio, vLLM or LocalAI; the Settings base URL field offers presets for the common ones). Requests to an `openrouter.ai` base URL carry OpenRouter's attribution headers `HTTP-Referer: https://logshed.dev` and `X-OpenRouter-Title: LogShed` (never the configured `APP_URL`). Routed model IDs such as `openai/o3` are treated like the bare IDs. The SDK client connects through the outbound address check (`GuardedOpenAIHttpClient` in `backend/app/core/net_guard.py`), which refuses cloud metadata and link-local addresses and allows private and loopback ones; see the SSRF policy in [security.md](security.md).
- **Model Configuration:** Configurable default model per provider (such as `gemini-3.7-flash`, `gpt-6.1-sol`, `claude-sonnet-5-5`, `llama3.2`), with fallback model lists and optional per-request overrides in the UI modal.
- **Dynamic Model Discovery & Refresh Worker:**
  - Queries provider endpoints live to discover available models from the configured provider, cached in SQLite for 24 hours.
  - Non-text models (such as speech, audio transcription, image generation, video, embeddings, and safety moderation) are filtered out automatically using keyword detection.
  - A background worker (`ModelRefreshWorker`) refreshes the model cache every 12 hours.
  - Manual cache refresh can be initiated on demand via `POST /api/ai/models/refresh`. The base URL used is the one in the request, else the stored base URL of that provider; the single global `ai_base_url` belongs to the active provider only, so a refresh for another provider never sends its key to that URL. A key typed into the refresh request is stored only when the request carries no different base URL; otherwise it is used for that refresh only and Save stores both.

---

## 3. Prompt Templates & Structures

### 3.1 Diagnostic Analysis Prompt

Constructed by `build_analysis_prompt` in [backend/app/services/ai_engine.py](../../backend/app/services/ai_engine.py):

- **System Role Prompt:** Establishes the LLM role as an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator. Directs the model to cite both UTC and local homelab timestamps when referencing event timelines.
- **Context Blocks:**
  1. **System Metadata:** Originating host alias, container/app name, and total selected log line count.
  2. **Timeline Reference:** UTC baseline (`+00:00`) alongside the operator local time zone and the UTC offsets in force across the selected logs, in time order (two, such as `UTC+01:00, then UTC+00:00`, when a daylight saving change falls inside them).
  3. **Host Notes:** Optional operator notes associated with the matching IP or alias from `host_aliases` (passed through credential redaction).
  4. **Situational Context:** Optional operator notes entered in the UI or alert incident parameters (passed through credential redaction).
  5. **Chronological Log Stream:** Sequenced log block enclosed in code blocks with passive data instructions directing the model never to interpret log contents as executable instructions.
- **Dual Timestamp Citation:** Each log line is formatted as `[{time_display}] [{source}] [{app_name}] [{severity}] {message}` where `time_display` includes UTC time and corresponding local time (e.g. `01:05:00 UTC (02:05:00 local)`).
  The local time uses the offset in force at that log's own moment, from the zone named by `client_timezone` (an IANA name such as `Europe/London`). `client_utc_offset_minutes` is a fixed offset used only when `client_timezone` is not a known zone, and the server zone is used when neither is sent.

### 3.2 Structured Output Parsing (Summary, Explanation, Suggestion)

The model response is formatted in Markdown with three designated sections, parsed by `parse_structured_ai_response`:

1. **Summary:** 1 - 2 sentence concise overview of the detected condition (`## Summary`).
2. **Root Cause Analysis (Explanation):** In-depth technical explanation of why the event or failure occurred based on the log evidence (`## Root Cause`).
3. **Actionable Remediation (Suggestion):** Concrete, step-by-step shell commands, configuration adjustments, or diagnostic steps to resolve the issue (`## Actionable Remediation`).

---

## 4. Thinking Level & Reasoning Effort

- **Configuration:** `ai_thinking_level` in settings: `off`, `low` (default), `medium` or `high` (environment variable `LOGSHED_AI_THINKING_LEVEL`). `LOGSHED_AI_THINKING_BUDGET`, the token budget used up to 1.2.x, is still read when `LOGSHED_AI_THINKING_LEVEL` is unset: `0` is `off`, up to 2,048 `low`, up to 8,192 `medium`, above that `high`. On upgrade, a stored `ai_thinking_budget` is replaced once with its level by the same bands (`convert_thinking_budget` in `backend/app/core/migrations.py`, run by `repair_v3`); a level saved already is kept. `off` asks for the lowest the model allows, since some models always think. The levels are mapped per provider in one section of `backend/app/services/ai_engine.py` (`_openai_reasoning_effort`, `_claude_thinking_kwargs`, `_gemini_thinking_config`); a missing or unknown level runs at `low`.
- **Capability Detection and Mapping:**
  - `supports_gemini_thinking`: Automatically detected for Gemini 2.5, 3.x and later, the `gemini-flash-latest`, `gemini-flash-lite-latest` and `gemini-pro-latest` aliases, and thinking-designated models. Gemini 3 and later and the aliases (which follow Google's current release) get `thinking_config.thinking_level`: `MINIMAL`, `LOW`, `MEDIUM` or `HIGH` for `off` to `high`, since Google deprecated the token budget there (Gemini 4 refuses it). The accepted levels differ by model (Gemini 4, Gemini 3.7 and 3.8 Flash and the Pro models have no `MINIMAL`), so when Gemini refuses the level with a `400` that names the thinking config, the request is sent again: at `LOW` when `MINIMAL` was refused, otherwise without a thinking config, at the model's default. A follow-up stream is retried the same way (the refusal comes before the first chunk). Gemini 2.5 and other thinking models get `thinking_config.thinking_budget`: 0, 1,024, 8,192 or 24,576; Gemini 2.5 Pro cannot turn thinking off, so `off` sends its minimum of 128.
  - `supports_claude_thinking`: Automatically detected for Claude 3.7+ models. Up to Claude 4.5, `low`, `medium` and `high` send `thinking={"type": "enabled", "budget_tokens": ...}` with 1,024, 8,192 or 16,000 tokens (`max_tokens` at least 2,048 above the budget), and `off` sends no thinking and `temperature: 0.2`.
  - Claude 4.6 and later (`_claude_version` reads the version from the model name; an 8-digit date suffix is not a version): `thinking={"type": "adaptive"}` with `output_config={"effort": "low" | "medium" | "high"}` and `max_tokens` 16,000, since these models refuse a token budget from 4.7 on. With `off`, Claude 4.6 gets no thinking and `temperature: 0.2`; Claude 4.7 and later get no `thinking` and no `temperature` (they refuse sampling parameters) and run at `low` effort, so models that always think (Claude 5 and later) still do. A response with `stop_reason: refusal` and no text fails with "Anthropic model declined the request".
  - `supports_openai_reasoning`: True for OpenAI reasoning models: the o-series (`o1`, `o3`, `o3-mini`, also routed as `openai/o3`), GPT-5 and later (`gpt-5`, `gpt-5-mini`, `gpt-5.5`, `gpt-6-astra`, `gpt-6.1-sol`, also behind a provider prefix such as `openai/`) except the chat variants (names containing `chat`, such as `gpt-5-chat-latest`), and names containing `reason`. These are reported as thinking models in the model list and get `reasoning_effort` `low`, `medium` or `high`; `off` sends `none`, except to the o-series, which do not accept it and get `low`. The accepted effort values differ by model (GPT-6 Sol refuses `none`, some pro models accept only `high`), so when the endpoint refuses the value with a `400` naming `reasoning_effort`, the request is sent again: at `low` when `none` was refused, otherwise without it, at the model's default effort.
  - `openai_model_takes_temperature`: OpenAI and OpenAI-compatible requests use `temperature: 0.2`, except for the reasoning models above, which get no `temperature` at any level; the chat variants and local models keep `0.2`.
- **Token Accounting:** Safe token count extraction extracts `tokens_in` (prompt), `tokens_out` (candidate completion), and `tokens_thoughts` (reasoning/thought tokens), recording them accurately in `ai_audit_log` and `alert_history`.

---

## 5. Sensitive Token Redaction Before Dispatch

- **Implementation Reference:** [backend/app/core/redactor.py](../../backend/app/core/redactor.py)
- **Redaction Pipeline:** Applied to all log lines, host notes, and operator context strings prior to LLM dispatch.
- **Pattern Coverage:** Matches and scrubs high-entropy credentials, including:
  - Cloud provider API keys (Google, OpenAI, Anthropic, AWS access keys).
  - Authentication tokens (Bearer tokens, JWT headers and signatures, GitHub and Slack personal access tokens).
  - Private keys, certificates, and hashed credential lines.
  - Common password assignments and key-value secret pairs.
  - Secrets in JSON and quoted keys (double or single quotes, escaped JSON inside a log message, quoted values with spaces), `Cookie` and `Set-Cookie` headers (the rest of the line after `Cookie:`), the user name in any `scheme://user@host` URL, Discord webhook URLs, Telegram bot tokens, Google API keys, Stripe live keys, GitLab and GitHub fine-grained tokens, bare LogShed API tokens, encrypted and PGP private keys (text after a lone `-----BEGIN ... PRIVATE KEY-----` up to the next punctuation), and signed-URL `sig=` and `signature=` parameters.
- **Bounded Cost:** Matching runs in linear time on hostile input. Lines longer than 16,384 characters in redacted output (AI prompts, MCP and external API responses, alert samples) are cut and end with a `...[truncated]` marker, and any single input longer than 500,000 characters is cut at a line break and marked truncated before matching. The log lines of an AI analysis (up to 200) and of an alert enrichment (up to 50) are redacted in one call in a worker thread, so a large batch cannot stall the event loop.
- **Known Limit:** The redactor sees one message at a time, so a private key split across separate syslog messages (one line each) is not caught. Redaction is best-effort.
- **Preview Assurance:** On-demand requests present the scrubbed preview text in the modal prior to execution, ensuring the operator can review redacted text before dispatch.

---

## 6. Audit Logging

Every AI interaction (whether user-initiated on demand or triggered by automated alert rules) is persisted to SQLite in the `ai_audit_log` table:

- `trigger_source`: `'on-demand'` or `'alert'`.
- `prompt_sent`: Full rendered prompt envelope delivered to the LLM.
- `user_context`: Redacted operator context or alert rule parameters.
- `response_text`: Complete raw text response from the model.
- `tokens_in`: Number of input tokens.
- `tokens_out`: Number of generated output tokens.
- `tokens_thoughts`: Number of reasoning or thinking tokens consumed.
- `tokens_used`: Total token count recorded for the request.
- `created_at`: UTC timestamp of the request.
