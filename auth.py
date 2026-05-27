from __future__ import annotations

import hmac
import os
from urllib.parse import urlparse

from langgraph_sdk import Auth


DEPLOY_API_KEY_ENV = "NPC_BRAIN_DEPLOY_API_KEY"
DEPLOY_IDENTITY_ENV = "NPC_BRAIN_DEPLOY_IDENTITY"
PUBLIC_DEMO_MODE_ENV = "NPC_BRAIN_PUBLIC_DEMO_MODE"
PUBLIC_DEMO_ORIGINS_ENV = "NPC_BRAIN_PUBLIC_DEMO_ORIGINS"
DEFAULT_PUBLIC_DEMO_ORIGINS = [
    "https://eaglhuang.github.io",
    "http://localhost:7456",
    "http://127.0.0.1:7456",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8123",
    "http://127.0.0.1:8123",
]

auth = Auth()


def _normalize_headers(headers: dict[object, object] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not headers:
        return normalized

    for raw_key, raw_value in headers.items():
        if raw_value is None:
            continue

        key = raw_key.decode("latin-1") if isinstance(raw_key, bytes) else str(raw_key)
        value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else str(raw_value)
        normalized[key.lower()] = value

    return normalized


def _extract_api_key(headers: dict[str, str]) -> str | None:
    direct_key = headers.get("x-api-key")
    if direct_key:
        return direct_key.strip()

    authorization = headers.get("authorization", "").strip()
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    return token.strip()


def _normalize_origin(value: str | None) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.lower() == "null":
        return "null"
    parsed = urlparse(raw_value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return raw_value.lower()


def _resolve_public_demo_origins() -> set[str]:
    raw_value = str(os.environ.get(PUBLIC_DEMO_ORIGINS_ENV) or "").strip()
    if raw_value:
        candidates = [item.strip() for item in raw_value.split(",") if item.strip()]
    else:
        candidates = DEFAULT_PUBLIC_DEMO_ORIGINS
    normalized = [_normalize_origin(origin) for origin in candidates]
    return {origin for origin in normalized if origin}


def _public_demo_mode_enabled() -> bool:
    raw_value = str(os.environ.get(PUBLIC_DEMO_MODE_ENV) or "").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _extract_request_origin(headers: dict[str, str]) -> str | None:
    origin = _normalize_origin(headers.get("origin"))
    if origin:
        return origin
    referer = str(headers.get("referer") or "").strip()
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return _normalize_origin(referer)


def _is_public_demo_request(headers: dict[str, str]) -> bool:
    if not _public_demo_mode_enabled():
        return False
    request_origin = _extract_request_origin(headers)
    if not request_origin:
        return False
    return request_origin in _resolve_public_demo_origins()


@auth.authenticate
async def authenticate(headers: dict[object, object] | None = None) -> Auth.types.MinimalUserDict:
    normalized_headers = _normalize_headers(headers)
    if _is_public_demo_request(normalized_headers):
        return {
            "identity": os.getenv(DEPLOY_IDENTITY_ENV, "npc-brain-public-demo"),
            "is_authenticated": True,
            "is_public_demo": True,
        }

    expected_key = os.getenv(DEPLOY_API_KEY_ENV, "").strip()
    if not expected_key:
        raise Auth.exceptions.HTTPException(
            status_code=503,
            detail=f"{DEPLOY_API_KEY_ENV} is not configured",
        )

    provided_key = _extract_api_key(normalized_headers)
    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid API key")

    return {
        "identity": os.getenv(DEPLOY_IDENTITY_ENV, "npc-brain-external-tester"),
        "is_authenticated": True,
    }
