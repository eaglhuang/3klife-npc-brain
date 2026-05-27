from __future__ import annotations

import hmac
import os
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        return False

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "FastAPI is not installed. Activate the server venv, or set PYTHON_BIN / "
        "SANGUO_RAG_PYTHON, then install API dependencies with: python -m pip install fastapi uvicorn"
    ) from exc

from fastapi.middleware.cors import CORSMiddleware

from .interaction_memory import (
    GeneralMemoryData,
    InteractionEventCreateRequest,
    InteractionEventWriteResponse,
    MemoryCompressRequest,
    MemoryWriteResponse,
)
from .npc_dialogue_service import (
    ContextOptionsResponse,
    DialogueRequest,
    DialogueResponse,
    KeywordOptionsResponse,
    NarrativeProfileResponse,
    NpcDialogueService,
    SceneDirectorRequest,
    SceneDirectorResponse,
    SceneIllustrationRequest,
    SceneIllustrationResponse,
)
from .llm_dialogue_renderer import ProviderOutputError, ProviderUnavailableError


DEPLOY_API_KEY_ENV = "NPC_BRAIN_DEPLOY_API_KEY"
PUBLIC_DEMO_MODE_ENV = "NPC_BRAIN_PUBLIC_DEMO_MODE"
PUBLIC_DEMO_ORIGINS_ENV = "NPC_BRAIN_PUBLIC_DEMO_ORIGINS"
DEV_CORS_ORIGINS = [
    "null",
    "http://localhost:7456",
    "http://127.0.0.1:7456",
    "http://localhost:8787",
    "http://127.0.0.1:8787",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8123",
    "http://127.0.0.1:8123",
    "https://smith.langchain.com",
    "https://eaglhuang.github.io",
]
DEFAULT_PUBLIC_DEMO_ORIGINS = [
    "https://eaglhuang.github.io",
    "http://localhost:7456",
    "http://127.0.0.1:7456",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8123",
    "http://127.0.0.1:8123",
]
DEV_CORS_ORIGIN_REGEX = (
    r"^(null|file://.*|app://.*|"
    r"http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?|"
    r"https://eaglhuang\.github\.io|https://smith\.langchain\.com)$"
)

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    override=True,
)


def resolve_dev_cors_origins() -> list[str]:
    extra_origins_raw = str(os.environ.get("NPC_CORS_EXTRA_ORIGINS") or "").strip()
    extras = [origin.strip() for origin in extra_origins_raw.split(",") if origin.strip()]
    return list(dict.fromkeys([*DEV_CORS_ORIGINS, *extras]))


def normalize_origin(value: str | None) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.lower() == "null":
        return "null"
    parsed = urlparse(raw_value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return raw_value.lower()


def resolve_public_demo_origins() -> set[str]:
    raw_value = str(os.environ.get(PUBLIC_DEMO_ORIGINS_ENV) or "").strip()
    if raw_value:
        candidates = [item.strip() for item in raw_value.split(",") if item.strip()]
    else:
        candidates = DEFAULT_PUBLIC_DEMO_ORIGINS
    normalized = [normalize_origin(origin) for origin in candidates]
    return {origin for origin in normalized if origin}


def public_demo_mode_enabled() -> bool:
    raw_value = str(os.environ.get(PUBLIC_DEMO_MODE_ENV) or "").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def extract_request_origin(request: Request | None) -> str | None:
    if request is None:
        return None
    origin = normalize_origin(request.headers.get("origin"))
    if origin:
        return origin
    referer = str(request.headers.get("referer") or "").strip()
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return normalize_origin(referer)


def is_public_demo_request(request: Request | None) -> bool:
    if not public_demo_mode_enabled():
        return False
    request_origin = extract_request_origin(request)
    if not request_origin:
        return False
    return request_origin in resolve_public_demo_origins()


def create_app() -> FastAPI:
    app = FastAPI(title="3KLife NPC Brain", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolve_dev_cors_origins(),
        allow_origin_regex=DEV_CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    service = NpcDialogueService()

    @app.get("/healthz")
    def healthz():
        return service.get_health()

    @app.post("/v1/npc/interaction-events", response_model=InteractionEventWriteResponse)
    def interaction_events(
        request: InteractionEventCreateRequest,
        service_auth: None = Depends(require_service_api_key),
    ):
        return service.record_interaction_event(request)

    @app.get("/v1/npc/general-memory", response_model=GeneralMemoryData)
    def general_memory(
        saveId: str,
        generalId: str,
        service_auth: None = Depends(require_service_api_key),
    ):
        return service.get_general_memory(saveId, generalId)

    @app.post("/v1/npc/general-memory", response_model=MemoryWriteResponse)
    def save_general_memory(
        memory: GeneralMemoryData,
        service_auth: None = Depends(require_service_api_key),
    ):
        return service.save_general_memory(memory)

    @app.post("/v1/npc/memory/compress", response_model=GeneralMemoryData)
    def compress_memory(
        request: MemoryCompressRequest,
        service_auth: None = Depends(require_service_api_key),
    ):
        return service.compress_general_memory(request)

    @app.get("/v1/npc/context-options", response_model=ContextOptionsResponse)
    def context_options(
        generalId: str,
        limit: int | None = Query(default=None, ge=0),
        service_auth: None = Depends(require_service_api_key),
    ):
        return service.get_context_options(generalId, limit=limit)

    @app.get("/v1/npc/keyword-options", response_model=KeywordOptionsResponse)
    def keyword_options(
        generalId: str,
        categories: str | None = None,
        limitPerCategory: int | None = Query(default=None, ge=0),
        service_auth: None = Depends(require_service_api_key),
    ):
        category_list = parse_categories(categories)
        return service.get_keyword_options(generalId, categories=category_list, limit_per_category=limitPerCategory)

    @app.get("/v1/npc/narrative-profile", response_model=NarrativeProfileResponse)
    def narrative_profile(generalId: str):
        return service.get_narrative_profile(generalId)

    @app.post("/v1/npc/dialogue", response_model=DialogueResponse)
    def dialogue(request: DialogueRequest):
        try:
            return service.build_dialogue(request)
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/v1/npc/scene-director", response_model=SceneDirectorResponse)
    def scene_director(
        request: SceneDirectorRequest,
        service_auth: None = Depends(require_service_api_key),
    ):
        try:
            return service.build_scene_director(request)
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/v1/npc/scene-illustration", response_model=SceneIllustrationResponse)
    def scene_illustration(
        scene_request: SceneIllustrationRequest,
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None),
    ):
        require_service_api_key(
            request=request,
            x_api_key=x_api_key,
            authorization=authorization,
        )
        try:
            return service.render_scene_illustration(scene_request)
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProviderOutputError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


def extract_api_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def require_service_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    if is_public_demo_request(request):
        return
    expected_key = str(os.environ.get(DEPLOY_API_KEY_ENV) or "").strip()
    if not expected_key:
        raise HTTPException(status_code=503, detail=f"{DEPLOY_API_KEY_ENV} is not configured")
    provided_key = extract_api_key(x_api_key, authorization)
    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def parse_categories(categories: str | None) -> list[str] | None:
    if not categories:
        return None
    return [category.strip() for category in categories.split(",") if category.strip()]


app = create_app()
