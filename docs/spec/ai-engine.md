# Specification: AI Analysis Engine

> This document is part of the modular LogShed technical specification suite. For the full system specification, see [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md).

LogShed provides two distinct AI analysis modes: user-initiated on-demand log analysis and automated alert incident enrichment. Outbound calls connect to external LLM providers through a unified multi-provider abstraction layer with automated redaction, dynamic model discovery, thinking token budgets, and full audit tracking.

---

## 1. Analysis Modes

### 1.1 On-Demand Analysis (User-Initiated)

- **Implementation Reference:** [backend/app/services/ai_service.py](file:///home/ben/workspace/logshed/backend/app/services/ai_service.py) and [frontend/src/components/ai/AiAnalysisModal.tsx](file:///home/ben/workspace/logshed/frontend/src/components/ai/AiAnalysisModal.tsx)
- **Selection:** The user selects one or multiple log entries in the UI across single or multiple hosts. Cross-host log selection is fully supported. The prompt builder annotates each dispatched log line with its severity code and dual timestamps citation (`[{time_utc} UTC ({time_local} {tz_name})] [{source_alias}] [{app_name}] [{severity}] {message}`), alongside originating host and alias metadata.
- **On-Demand Redaction Pass:** The backend filters selected logs through [backend/app/core/redactor.py](file:///home/ben/workspace/logshed/backend/app/core/redactor.py) (scrubbing tokens, passwords, JWTs, cloud API keys, and credentials) before returning the preview payload to the UI.
- **Payload Inspection & User Context:**
  - The UI opens an analysis modal displaying:
    * The scrubbed, redacted text preview exactly as it will be dispatched to the LLM.
    * Active provider and model name.
    * Estimated token count.
    * Free-text user context input field.
- **Execution Gate:** Outbound API calls occur **only** when the user explicitly clicks **"Run Analysis"** (or starts streaming via `/api/ai/diagnose/stream`).
- **Audit Logging:** Every on-demand request is recorded in `ai_audit_log` with `trigger_source = 'on-demand'` (prompt, user context, response, tokens used, and thoughts).

### 1.2 Automated Alert Enrichment (Background Incident Enrichment)

- **Implementation Reference:** [backend/app/services/alert_evaluator.py](file:///home/ben/workspace/logshed/backend/app/services/alert_evaluator.py)
- **Rule Configuration:** Alert rules can optionally enable automated background incident enrichment (`ai_enrichment = 1` in `alert_rules`).
- **Trigger Execution:** When an enabled alert rule with AI enrichment fires, `AlertEvaluator` redacts the triggering log sample through [backend/app/core/redactor.py](file:///home/ben/workspace/logshed/backend/app/core/redactor.py) and dispatches it to the configured LLM.
- **Incident Summary & Remediation:** The model generates a concise incident summary, root-cause diagnosis, and actionable remediation steps. This structured analysis is embedded directly into outbound notification payloads (dispatched via configured Apprise notification channels) and saved in `alert_history`.
- **Multi-Model Failover:** If the primary model encounters a transient error, timeout, or rate limit, the engine automatically attempts evaluation against configured fallback models.
- **Audit Tracking:** Automated alert analysis runs are recorded in `ai_audit_log` with `trigger_source = 'alert'` and linked to `alert_history.ai_audit_id`.
- **Privacy and Token Safeguards:** Clear privacy, credential redaction, and token usage warnings are presented in the UI when enabling this option (inside `AlertRuleModal` and notice banners on the Rules & Alerts Hub). The UI explicitly informs administrators that triggering logs are automatically dispatched to external AI providers without manual pre-screening, that automated scrubbing operates on a best-effort basis, and that automated triggers consume API tokens. If an AI provider is not configured in Settings, AI enrichment cannot be enabled and displays a direct link to Settings.

---

## 2. AI Provider Abstraction

- **Implementation Reference:** [backend/app/services/ai_engine.py](file:///home/ben/workspace/logshed/backend/app/services/ai_engine.py)
- **Supported Providers & SDKs:**
  - Google Gemini via the `google-genai` SDK.
  - Anthropic Claude via the `anthropic` SDK.
  - OpenAI-compatible endpoints via the `openai` SDK (with configurable `base_url` supporting self-hosted engines such as Ollama, vLLM, or LocalAI).
- **Model Configuration:** Configurable default model per provider (such as `gemini-3.7-flash`, `gpt-4o`, `claude-sonnet-4-6`, `llama3.2`), with fallback model lists and optional per-request overrides in the UI modal.
- **Dynamic Model Discovery & Refresh Worker:**
  - Queries provider endpoints live to discover available models from the configured provider, cached in SQLite for 24 hours.
  - Non-text models (such as speech, audio transcription, image generation, video, embeddings, and safety moderation) are filtered out automatically using keyword detection.
  - A background worker (`ModelRefreshWorker`) refreshes the model cache every 12 hours.
  - Manual cache refresh can be initiated on demand via `POST /api/ai/models/refresh`.

---

## 3. Prompt Templates & Structures

### 3.1 Diagnostic Analysis Prompt

Constructed by `build_analysis_prompt` in [backend/app/services/ai_engine.py](file:///home/ben/workspace/logshed/backend/app/services/ai_engine.py):

- **System Role Prompt:** Establishes the LLM role as an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator. Directs the model to cite both UTC and local homelab timestamps when referencing event timelines.
- **Context Blocks:**
  1. **System Metadata:** Originating host alias, container/app name, and total selected log line count.
  2. **Timeline Reference:** UTC baseline (`+00:00`) alongside operator local timezone offset.
  3. **Host Notes:** Optional operator notes associated with the matching IP or alias from `host_aliases` (passed through credential redaction).
  4. **Situational Context:** Optional operator notes entered in the UI or alert incident parameters (passed through credential redaction).
  5. **Chronological Log Stream:** Sequenced log block enclosed in code blocks with passive data instructions directing the model never to interpret log contents as executable instructions.
- **Dual Timestamp Citation:** Each log line is formatted as `[{time_display}] [{source}] [{app_name}] [{severity}] {message}` where `time_display` includes UTC time and corresponding local time (e.g. `01:05:00 UTC (02:05:00 local)`).

### 3.2 Structured Output Parsing (Summary, Explanation, Suggestion)

The model response is formatted in Markdown with three designated sections, parsed by `parse_structured_ai_response`:

1. **Summary:** 1 - 2 sentence concise overview of the detected condition (`## Summary`).
2. **Root Cause Analysis (Explanation):** In-depth technical explanation of why the event or failure occurred based on the log evidence (`## Root Cause`).
3. **Actionable Remediation (Suggestion):** Concrete, step-by-step shell commands, configuration adjustments, or diagnostic steps to resolve the issue (`## Actionable Remediation`).

---

## 4. Thinking Token Budgets & Reasoning Effort

- **Configuration:** Reasoning token budget configured via `ai_thinking_budget` in settings (Environment variable: `LOGSHED_AI_THINKING_BUDGET`, default: `1024`, `0` disables).
- **Capability Detection:**
  - `supports_gemini_thinking`: Automatically detected for Gemini 2.5, 3.x, and thinking-designated models. Configures `thinking_config` with the designated thinking budget.
  - `supports_claude_thinking`: Automatically detected for Claude 3.7+ models. Configures `thinking={"type": "enabled", "budget_tokens": ...}` with the designated thinking budget.
  - `supports_openai_reasoning`: Automatically detected for OpenAI o-series models (such as `o1`, `o3`, `o3-mini`). Configures `reasoning_effort` accordingly.
- **Token Accounting:** Safe token count extraction extracts `tokens_in` (prompt), `tokens_out` (candidate completion), and `tokens_thoughts` (reasoning/thought tokens), recording them accurately in `ai_audit_log` and `alert_history`.

---

## 5. Sensitive Token Redaction Before Dispatch

- **Implementation Reference:** [backend/app/core/redactor.py](file:///home/ben/workspace/logshed/backend/app/core/redactor.py)
- **Redaction Pipeline:** Applied to all log lines, host notes, and operator context strings prior to LLM dispatch.
- **Pattern Coverage:** Matches and scrubs high-entropy credentials, including:
  - Cloud provider API keys (Google, OpenAI, Anthropic, AWS access keys).
  - Authentication tokens (Bearer tokens, JWT headers and signatures, GitHub and Slack personal access tokens).
  - Private keys, certificates, and hashed credential lines.
  - Common password assignments and key-value secret pairs.
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
