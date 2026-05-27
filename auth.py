from __future__ import annotations

import hmac
import os
from urllib.parse import urlparse

from langgraph_sdk import Auth


DEPLOY_API_KEY_ENV = "NPC_BRAIN_DEPLOY_API_KEY"
DEPLOY_IDENTITY_ENV = "NPC_BRAIN_DEPLOY_IDENTITY"
PUBLIC_DEMO_ORIGINS_ENV = "NPC_BRAIN_PUBLIC_DEMO_ORIGINS"
DEFAULT_PUBLIC_DEMO_ORIGINS = {
    "https://eaglhuang.github.io",
    "http://localhost:7456",
    "http://127.0.0.1:7456",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8123",
    "http://127.0.0.1:8123",
}

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


def _parse_origin(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return value


def _extract_request_origin(headers: dict[str, str]) -> str:
    origin = _parse_origin(headers.get("origin", ""))
    if origin:
        return origin

    referer = headers.get("referer", "").strip()
    if referer:
        return _parse_origin(referer)

    return ""


def _allowed_demo_origins() -> set[str]:
    raw = os.getenv(PUBLIC_DEMO_ORIGINS_ENV, "")
    values = {
        _parse_origin(item)
        for item in raw.split(",")
        if _parse_origin(item)
    }
    return values or set(DEFAULT_PUBLIC_DEMO_ORIGINS)


@auth.authenticate
async def authenticate(headers: dict[object, object] | None = None) -> Auth.types.MinimalUserDict:
    normalized_headers = _normalize_headers(headers)
    request_origin = _extract_request_origin(normalized_headers)
    if request_origin and request_origin in _allowed_demo_origins():
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
