"""
On-demand secret redactor for LogShed.

This module provides regex-based scrubbing of sensitive tokens, passwords,
API keys, and credentials from log text before it is dispatched to an
external LLM for analysis.

IMPORTANT: This is NOT applied during ingestion or SQLite insertion.
Raw logs remain unredacted in the database. Redaction is applied
on-demand only when preparing log text for AI preview/diagnosis
(per docs/SPEC.md §3 and §4.1).

Redactor input is attacker-controlled (anyone on the LAN can send syslog), so
every pattern must run in linear time on hostile input:
- No unbounded scan may restart from many positions inside the same run of
  text. Starts are pinned by lookbehinds or by delimiters the scan cannot
  cross (for example, a URL userinfo scan stops at `/`, which every new
  `scheme://` start contains).
- Input longer than MAX_REDACT_INPUT_CHARS, and lines longer than
  MAX_REDACT_LINE_CHARS, are truncated before matching.
  Redacted text is only ever sent outside the box, never stored, so this
  loses nothing that is kept.
"""

import re
from typing import Union

# Placeholder used for redacted values
REDACTED = "[REDACTED]"

# Any single line longer than this is cut before matching, with TRUNCATION_MARKER appended.
# Applied per line (not per field) so that whole AI prompts made of many log lines are kept.
MAX_REDACT_LINE_CHARS = 16_384
TRUNCATION_MARKER = "...[truncated]"
# Any single string longer than this is cut (at the last line break before the cap) before
# matching, with "\n" + TRUNCATION_MARKER appended. Matching costs about 0.1 s per 100,000
# characters, so this bounds one call at about half a second. The largest legitimate input
# is a whole AI prompt (log text is budgeted at 100,000 characters), well under the cap.
MAX_REDACT_INPUT_CHARS = 500_000
# When cutting, back off to a delimiter within this many characters so no fragment of a
# token is left at the cut (a partial token no longer matches its pattern).
_CUT_BACKOFF_CHARS = 512
_CUT_DELIMITERS = " \t,;&\"'="

# Optional quote (possibly backslash-escaped, as in JSON embedded in a log string) that may
# close a key name or open/close a value: `"password":"x"`, `{\"password\":\"x\"}`, `'password': 'x'`.
_Q = r"""(?:\\?["'])?"""
# Separator between a key name and its value, allowing the key's closing quote.
_SEP = _Q + r"\s*[:=]\s*"
# A password-like value: a whole quoted string (spaces allowed), or a bare value.
_SECRET_VALUE = (
    r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|\\"[^"\r\n]+?\\"|"""
    + _Q
    + r"""[^\s,;"'}{)(\]]+"""
    + _Q
    + ")"
)

# PEM / PGP private key armor: RSA, EC, DSA, OPENSSH, ENCRYPTED, PGP ... PRIVATE KEY [BLOCK]
_PRIVATE_KEY_LABEL = r"(?:[A-Z0-9]+\s+){0,3}PRIVATE\s+KEY(?:\s+BLOCK)?-----"

# Compiled regex patterns for secret detection.
# Each tuple: (pattern_name, compiled_regex, replacement_template)
# Replacement templates use \1 etc. to preserve context around the redacted value.
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # --- Private Keys ---
    # Runs first so the whole block goes before any other pattern edits its body.
    # The body is any text up to the next `-----` (single dashes allowed for
    # `Proc-Type:` / `DEK-Info:` headers). The atomic group never backtracks, and
    # each scan stops at the next `-----`, which every new BEGIN marker contains.
    # Without an END marker (key cut off or truncated), the base64 run after the
    # BEGIN marker is redacted instead.
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s+" + _PRIVATE_KEY_LABEL
            + r"(?:(?>[^-]*(?:-(?!----)[^-]*)*)-----END\s+" + _PRIVATE_KEY_LABEL
            + r"|[A-Za-z0-9+/=\s]*)",
        ),
        REDACTED,
    ),

    # --- Authentication Headers ---
    # Bearer tokens in Authentication headers (matching RFC 7235 Authorization header syntax)
    (
        "bearer_token",
        re.compile(
            r"(authorization" + _SEP + _Q + r"Bearer\s+)\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # Basic auth in Authentication headers
    (
        "basic_auth",
        re.compile(
            r"(authorization" + _SEP + _Q + r"Basic\s+)\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # Cookie / Set-Cookie header values (whole quoted value, or the rest of the line)
    (
        "cookie_header",
        re.compile(
            r"(\b(?:set-)?cookie" + _Q + r"\s*:\s*)"
            r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|\\"[^"\r\n]+?\\"|[^\r\n]+)""",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- API Keys ---
    # Generic API key patterns (key=value, key: value)
    (
        "api_key",
        re.compile(
            r"((?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token|access[_-]?key|secret[_-]?key)"
            + _SEP + r")" + _Q + r"[\w\-./+=]{8,}" + _Q,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # X-API-Key header
    (
        "x_api_key_header",
        re.compile(
            r"(X-API-Key" + _SEP + _Q + r")\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Password Fields ---
    # password=..., password: ..., passwd=..., "password": "...", etc.
    (
        "password_field",
        re.compile(
            r"((?:password|passwd|pwd|pass)" + _SEP + r")" + _SECRET_VALUE,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Tokens (generic) ---
    # token=..., access_token=..., refresh_token=..., auth_token=...
    (
        "token_field",
        re.compile(
            r"((?:access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token|token)"
            + _SEP + r")" + _Q + r"[\w\-./+=]{8,}" + _Q,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- JWTs ---
    # Three base64url-encoded sections separated by dots. The lookbehind allows one
    # start per run of base64url characters, so a long `eyJ-eyJ-...` run is scanned once.
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        ),
        REDACTED,
    ),

    # --- AWS Credentials ---
    # AWS Access Key ID (starts with AKIA, ASIA, AIDA, AROA, or ANPA)
    (
        "aws_access_key",
        re.compile(
            r"\b((?:A3T[A-Z0-9]|AKIA|ASIA|AIDA|AROA|ANPA|AIPA|ANVA|APKA)[A-Z0-9]{16})\b",
        ),
        REDACTED,
    ),
    # AWS Secret Key (40-char base64-ish string after aws_secret prefix)
    (
        "aws_secret_key",
        re.compile(
            r"((?:aws_secret_access_key|aws_secret_key|secret_access_key)" + _SEP + r")"
            + _Q + r"[A-Za-z0-9/+=]{40}" + _Q,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Connection Strings ---
    # URLs with embedded credentials (user:pass@host, :pass@host or token@host) for any
    # scheme (postgres, mongodb+srv, rediss, amqp, https, ...). As in URL parsing, the
    # authority ends at `/`, `?`, `#` or whitespace, and the userinfo ends at its last `@`,
    # so passwords containing `@` are covered. The scans cannot cross `/`, which every
    # new `scheme://` start contains, so each region is scanned a bounded number of times.
    (
        "connection_string",
        re.compile(
            r"""(\b[a-z][a-z0-9+.-]{0,20}://(?:[^\s:/?#"'<>]*:)?)[^\s/?#"'<>]*(@)(?=[^\s/?#@"'<>])""",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}\2",
    ),

    # --- CLI & Command Auth ---
    (
        "curl_auth",
        re.compile(r"(--?(?:u|user)\s+[^:\s]+:)\S+", re.IGNORECASE),
        rf"\1{REDACTED}",
    ),

    # --- Standalone API & Platform Tokens ---
    (
        "logshed_api_token",
        re.compile(r"\bls_live_[0-9a-f]{40}\b"),
        REDACTED,
    ),
    (
        "sk_token",
        re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{20,})\b"),
        REDACTED,
    ),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,}"),
        REDACTED,
    ),
    (
        "github_token",
        re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36})\b"),
        REDACTED,
    ),
    (
        "github_fine_grained_pat",
        re.compile(r"\bgithub_pat_[0-9A-Za-z_]{22,}"),
        REDACTED,
    ),
    (
        "gitlab_pat",
        re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}"),
        REDACTED,
    ),
    (
        "google_api_key",
        re.compile(r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
        REDACTED,
    ),
    # Telegram bot tokens: <bot id>:<35 chars>, often as /bot<token>/ in API URLs
    (
        "telegram_bot_token",
        re.compile(r"\b(bot)?\d{6,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
        rf"\1{REDACTED}",
    ),

    # --- Pushover / Generic Service Tokens ---
    (
        "pushover_token",
        re.compile(
            r"((?:pushover[_-]?(?:user[_-]?key|app[_-]?token|token|key))" + _SEP + r")"
            + _Q + r"\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Webhooks ---
    (
        "slack_webhook",
        re.compile(
            r"(https?://hooks\.slack\.com/services/)[A-Za-z0-9+/_=-]+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    (
        "discord_webhook",
        re.compile(
            r"(discord(?:app)?\.com/api/(?:v\d+/)?webhooks/)"
            r"\d+/[A-Za-z0-9_-]+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Signed URL query parameters (Azure SAS sig=, X-Amz-Signature=, signature=) ---
    (
        "url_signature",
        re.compile(
            r"""(\b(?:sig|signature)=)[^&\s#"'<>]+""",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Generic Secret Assignments ---
    (
        "generic_secret",
        re.compile(
            r"((?:client[_-]?secret|app[_-]?secret|shared[_-]?secret|secret)" + _SEP + r")"
            + _SECRET_VALUE,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- CLI Tools (sshpass) ---
    # The option list is bounded so a run of `-sshpass -sshpass ...` cannot be rescanned
    # from every `sshpass` inside it.
    (
        "sshpass_auth",
        re.compile(
            r"(sshpass\s+(?:-\w+\s+){0,8}-p\s*)(?:'[^']*'|\"[^\"]*\"|\S+)",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Docker Registry Auth ---
    (
        "docker_registry_auth",
        re.compile(
            r"((?:\"?X-Registry-(?:Auth|Token)\"?|docker[_-]?registry[_-]?(?:token|auth)|registry[_-]?(?:auth|token))"
            + _SEP + r")" + _SECRET_VALUE,
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
]


def redact(text: Union[str, list[str]]) -> Union[str, list[str]]:
    """
    Scrub sensitive secrets from log text.

    Input longer than MAX_REDACT_INPUT_CHARS is cut at a line break (with a new line and
    TRUNCATION_MARKER appended), and lines longer than MAX_REDACT_LINE_CHARS are cut (with
    TRUNCATION_MARKER), before matching, which bounds the work done on hostile input.
    A list is processed item by item, and the cap applies to each item.

    Args:
        text: A single log string or a list of log strings.

    Returns:
        The redacted text with secrets replaced by [REDACTED].
        Returns the same type as the input (str or list[str]).
    """
    if isinstance(text, list):
        return [_redact_single(line) for line in text]
    return _redact_single(text)


def _truncate_line(line: str) -> str:
    """Cut one over-long line, backing off to a nearby delimiter so no token fragment is left."""
    limit = MAX_REDACT_LINE_CHARS
    cut = max(line.rfind(ch, limit - _CUT_BACKOFF_CHARS, limit) for ch in _CUT_DELIMITERS)
    if cut <= 0:
        cut = limit
    return line[:cut] + TRUNCATION_MARKER


def _truncate_long_lines(text: str) -> str:
    """Truncate every line longer than MAX_REDACT_LINE_CHARS, keeping all other lines."""
    if len(text) <= MAX_REDACT_LINE_CHARS:
        return text
    if "\n" not in text:
        return _truncate_line(text)
    lines = text.split("\n")
    if all(len(line) <= MAX_REDACT_LINE_CHARS for line in lines):
        return text
    return "\n".join(
        _truncate_line(line) if len(line) > MAX_REDACT_LINE_CHARS else line
        for line in lines
    )


def _cap_input(text: str) -> str:
    """Cut text longer than MAX_REDACT_INPUT_CHARS at the last line break before the cap."""
    if len(text) <= MAX_REDACT_INPUT_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_REDACT_INPUT_CHARS)
    if cut <= 0:
        # No line break: _truncate_long_lines then cuts the over-long line at a delimiter
        cut = MAX_REDACT_INPUT_CHARS
    return text[:cut] + "\n" + TRUNCATION_MARKER


def _redact_single(text: str) -> str:
    """Apply all redaction patterns to a single string."""
    result = _truncate_long_lines(_cap_input(text))
    for _name, pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
