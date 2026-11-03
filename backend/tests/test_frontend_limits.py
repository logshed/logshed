"""The frontend's copies of backend field limits, token scopes and the queue size match the backend."""

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.api.docker_hosts import DockerEndpointCreateRequest
from app.core.api_tokens import KNOWN_SCOPES
from app.models import (
    AiDiagnosisRequest,
    AiFollowUpRequest,
    ApiTokenCreateRequest,
    DropRuleCreate,
    DropRuleUpdate,
    PasswordChangeRequest,
    SavedViewCreate,
    SetupRequest,
)

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _frontend_limits() -> dict[str, int]:
    text = (FRONTEND_SRC / "utils" / "fieldLimits.ts").read_text(encoding="utf-8")
    return {name: int(value) for name, value in re.findall(r"export const (\w+) = (\d+);", text)}


def _field_limit(model: type[BaseModel], field: str, kind: str) -> int:
    for constraint in model.model_fields[field].metadata:
        value = getattr(constraint, kind, None)
        if value is not None:
            return value
    raise AssertionError(f"{model.__name__}.{field} has no {kind}")


# Each frontend constant and the backend fields it must equal
LIMITS = {
    "NAME_MAX_LENGTH": [
        (DropRuleCreate, "name", "max_length"),
        (SavedViewCreate, "name", "max_length"),
        (ApiTokenCreateRequest, "name", "max_length"),
        (DockerEndpointCreateRequest, "name", "max_length"),
    ],
    "HOST_ALIAS_MAX_LENGTH": [(DockerEndpointCreateRequest, "source_alias", "max_length")],
    "RULE_FILTER_MAX_LENGTH": [
        (DropRuleCreate, "source_pattern", "max_length"),
        (DropRuleCreate, "app_pattern", "max_length"),
    ],
    "MESSAGE_PATTERN_MAX_LENGTH": [
        (DropRuleCreate, "message_pattern", "max_length"),
        (DropRuleUpdate, "message_pattern", "max_length"),
    ],
    "PASSWORD_MIN_LENGTH": [
        (SetupRequest, "password", "min_length"),
        (PasswordChangeRequest, "new_password", "min_length"),
    ],
    "AI_FOLLOW_UP_MAX_LENGTH": [(AiFollowUpRequest, "message", "max_length")],
    "AI_USER_CONTEXT_MAX_LENGTH": [(AiDiagnosisRequest, "user_context", "max_length")],
}


def test_every_frontend_limit_is_checked():
    assert set(_frontend_limits()) == set(LIMITS)


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_frontend_limit_matches_the_backend(name: str):
    frontend_value = _frontend_limits()[name]
    for model, field, kind in LIMITS[name]:
        assert _field_limit(model, field, kind) == frontend_value, f"{name} differs from {model.__name__}.{field}"


def test_frontend_scope_catalog_lists_the_backend_scopes():
    text = (FRONTEND_SRC / "components" / "settings" / "tokenScopes.ts").read_text(encoding="utf-8")
    catalog = text.split("export const SCOPE_CATALOG", 1)[1].split("];", 1)[0]
    assert set(re.findall(r"scope: '([a-z]+:[a-z]+)'", catalog)) == set(KNOWN_SCOPES)



def test_frontend_masked_secret_matches_the_backend():
    from app.core.security import MASKED_SECRET

    text = (FRONTEND_SRC / "api" / "settings.ts").read_text(encoding="utf-8")
    assert re.search(r"export const MASKED_SECRET = '([^']+)';", text).group(1) == MASKED_SECRET

def test_navbar_queue_warning_is_half_the_ingestion_queue():
    from app.core.pipeline import _QUEUE_MAXSIZE

    text = (FRONTEND_SRC / "components" / "common" / "Navbar.tsx").read_text(encoding="utf-8")
    depth = int(re.search(r"export const QUEUE_WARNING_DEPTH = (\d+);", text).group(1))
    assert depth == _QUEUE_MAXSIZE // 2
