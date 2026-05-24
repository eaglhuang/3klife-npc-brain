from __future__ import annotations

import json
import os
import re
import hashlib
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .interaction_memory import (
    CURRENT_MEMORY_SCHEMA_VERSION,
    GeneralMemoryContext,
    GeneralMemoryData,
    InteractionEventCreateRequest,
    InteractionEventWriteResponse,
    MemoryCompressRequest,
    MemoryWriteResponse,
    append_interaction_event,
    build_interaction_event,
    get_memory_runtime_config,
    has_memory_context_content,
    increment_uncompressed_count,
    load_general_memory,
    memory_context_from_data,
    save_general_memory,
)
from .evidence_resolver import EvidenceResolver, ResolvedEvidencePack
from .llm_dialogue_renderer import (
    DEFAULT_DEEPSEEK_REASONER_MODEL,
    DEFAULT_GEMINI_FLASH_LITE_MODEL,
    DEFAULT_GEMINI_FLASH_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_HISTORY_CACHE_PATH,
    DEFAULT_LOCALE,
    DEFAULT_LOCAL_LLAMA_MODEL,
    DEFAULT_LOCAL_LLAMA_NUM_CTX,
    DEFAULT_LOCAL_LLAMA_REPEAT_PENALTY,
    DEFAULT_LOCAL_LLAMA_REPAIR_RETRY_COUNT,
    DEFAULT_LOCAL_LLAMA_TEMPERATURE,
    DEFAULT_LOCAL_LLAMA_TOP_P,
    DEFAULT_SPEECH_CONTEXT_MODE,
    LOCALE_INSTRUCTIONS,
    DialogueGenerationResult,
    DialoguePromptPackage,
    DialogueProviderRouter,
    SPEECH_CONTEXT_INSTRUCTIONS,
    load_local_env,
    log_debug_event,
)
from .memory_compressor import compress_general_memory
from .runtime_profile_store import RuntimeProfileStore
from .scene_image_renderer import GeminiSceneImageRenderer
from .vector_config import load_vector_runtime_config


DEFAULT_ARTIFACT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/api-readiness")
DEFAULT_EVENT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/events")
DEFAULT_PERSONA_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/persona-cards")
DEFAULT_RUNTIME_PROFILE_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles")
DEFAULT_CHAPTER_ROOT = Path("artifacts/data-pipeline/sanguoyanyi-mao-hant-2026-04-28/body/chapters")
DEFAULT_SOURCE_EVENT_PACKET_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/source-event-packets/source-event-packets.jsonl")
DEFAULT_BAIHUA_PASSAGE_PATH = Path("artifacts/data-pipeline/sanguo-rag/anchor-index/sanguoyanyi-baihua-zh-tw-passages.jsonl")
DEFAULT_RELATIONSHIP_RUNTIME_CANON_POLICY_LOCAL_PATH = Path("data/sanguo/policies/policy-relationship-runtime-canon.json")
SPECIAL_SOURCE_REF_WINDOWS: dict[str, tuple[str, ...]] = {
    "041#changban-a-dou": tuple(f"041#p{index}" for index in range(10, 22)),
}
DEFAULT_STABLE_RELATIONSHIP_SOURCE_LAYERS = {
    "stable-bootstrap-seed",
    "generals-parent-summary",
    "claim-graph-a-history",
    "claim-graph-a-romance",
}
DEFAULT_A_CANON_RELATIONSHIP_GRADES = {"A-history", "A-history-cross-source", "A-romance"}
PERSONA_VERSION = "general_persona_v2"
LLM_HISTORY_PROVIDERS = {"gemini", "gemini_flash", "gemini_flash_lite", "local_llama"}
SUPPORTED_LOCALES = set(LOCALE_INSTRUCTIONS.keys())
SUPPORTED_SPEECH_CONTEXT_MODES = set(SPEECH_CONTEXT_INSTRUCTIONS.keys())
DEFAULT_LLM_MODEL_PRESET = "fallback_chain"
DEFAULT_SCENE_DIRECTOR_TOTAL_TIMEOUT_MS = 14000
HARD_RELATIONSHIP_PAIR_TYPES: dict[frozenset[str], str] = {
    frozenset({"liu-bei", "guan-yu"}): "sworn_sibling",
    frozenset({"liu-bei", "zhang-fei"}): "sworn_sibling",
    frozenset({"guan-yu", "zhang-fei"}): "sworn_sibling",
}
TARGET_ID_NAME_COLLISIONS: dict[str, dict[str, str]] = {
    "zhang-bao": {
        "張寶": "zhang-bao-enemy",
        "张宝": "zhang-bao-enemy",
        "地公將軍": "zhang-bao-enemy",
        "地公将军": "zhang-bao-enemy",
    },
}
YELLOW_TURBAN_TARGET_IDS = {"zhang-bao-enemy", "zhang-jiao", "zhang-liang"}
YELLOW_TURBAN_CONTEXT_TERMS = (
    "黃巾",
    "黄巾",
    "張角",
    "张角",
    "張梁",
    "张梁",
    "張寶",
    "张宝",
    "地公將軍",
    "地公将军",
    "賊",
    "贼",
    "對壘",
    "对垒",
    "追襲",
    "追袭",
    "死戰",
    "死战",
)
LLM_MODEL_PRESETS = {
    "fallback_chain": {
        "label": "Fallback Chain",
        "description": "Use NPC_LLM_PROVIDER_ORDER exactly as configured in the server environment.",
        "providerOrder": None,
        "modelOverrides": {},
        "allowDeterministicFallback": True,
    },
    "gemini_pro": {
        "label": "Gemini 2.5 Pro",
        "description": "Test Gemini Pro only; fail loudly if it is unavailable.",
        "providerOrder": ["gemini"],
        "modelOverrides": {"gemini": DEFAULT_GEMINI_MODEL},
        "allowDeterministicFallback": False,
    },
    "gemini_flash": {
        "label": "Gemini 2.5 Flash",
        "description": "Test Gemini Flash only; fail loudly if it is unavailable.",
        "providerOrder": ["gemini_flash"],
        "modelOverrides": {"gemini_flash": DEFAULT_GEMINI_FLASH_MODEL},
        "allowDeterministicFallback": False,
    },
    "gemini_flash_lite": {
        "label": "Gemini 2.5 Flash Lite",
        "description": "Test Gemini Flash Lite only; fail loudly if it is unavailable.",
        "providerOrder": ["gemini_flash_lite"],
        "modelOverrides": {"gemini_flash_lite": DEFAULT_GEMINI_FLASH_LITE_MODEL},
        "allowDeterministicFallback": False,
    },
    "scene_director_fast": {
        "label": "Scene Director Fast",
        "description": "Fast service-side scene director chain for demo rendering.",
        "providerOrder": ["gemini_flash_lite", "deterministic"],
        "modelOverrides": {"__timeoutMs": "6000", "__retryCount": "1"},
        "allowDeterministicFallback": True,
    },
    "qwen2_5_7b": {
        "label": "Qwen2.5 7B Local",
        "description": "Test local Ollama qwen2.5:7b only; fail loudly if Ollama or the model is unavailable.",
        "providerOrder": ["local_llama"],
        "modelOverrides": {"local_llama": "qwen2.5:7b"},
        "allowDeterministicFallback": False,
    },
    "qwen2_5_3b": {
        "label": "Qwen2.5 3B Local",
        "description": "Test local Ollama qwen2.5:3b only; fail loudly if Ollama or the model is unavailable.",
        "providerOrder": ["local_llama"],
        "modelOverrides": {"local_llama": "qwen2.5:3b"},
        "allowDeterministicFallback": False,
    },
        "deepseek_r1_7b": {
            "label": "DeepSeek R1 7B Reasoning",
            "description": "Reasoning test model for ETL/review experiments; fail loudly if Ollama or the model is unavailable.",
            "providerOrder": ["deepseek_reasoner"],
            "modelOverrides": {"deepseek_reasoner": DEFAULT_DEEPSEEK_REASONER_MODEL},
            "allowDeterministicFallback": False,
        },
    "local_llama_env": {
        "label": "Local Llama Env",
        "description": "Test local_llama only, using NPC_LLM_MODEL_LOCAL_LLAMA from .env; fail loudly if unavailable.",
        "providerOrder": ["local_llama"],
        "modelOverrides": {},
        "allowDeterministicFallback": False,
    },
}
SUPPORTED_LLM_MODEL_PRESETS = set(LLM_MODEL_PRESETS.keys())


def _governance_candidates(repo_root: Path, relative_path: str) -> list[Path]:
    return [repo_root / "data/sanguo" / relative_path]


def _resolve_optional_governance_path(repo_root: Path, env_name: str, relative_path: str) -> Path | None:
    override = os.environ.get(env_name)
    if override and override.strip():
        path = Path(override)
        resolved = path if path.is_absolute() else repo_root / path
        if not resolved.exists():
            raise ValueError(f"NPC dialogue governance file not found: {resolved}")
        return resolved
    for candidate in _governance_candidates(repo_root, relative_path):
        if candidate.exists():
            return candidate
    return None


def _read_optional_governance_json(repo_root: Path, env_name: str, relative_path: str, required_id: str) -> dict[str, Any] | None:
    path = _resolve_optional_governance_path(repo_root, env_name, relative_path)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"NPC dialogue governance JSON parse failed: {path}:{exc.lineno}:{exc.colno}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"NPC dialogue governance JSON must be object: {path}")
    if payload.get("id") != required_id:
        raise ValueError(f"NPC dialogue governance JSON id mismatch: {path}")
    return payload


def _read_optional_governance_jsonl(repo_root: Path, env_name: str, relative_path: str) -> list[dict[str, Any]] | None:
    path = _resolve_optional_governance_path(repo_root, env_name, relative_path)
    if path is None:
        return None
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"NPC dialogue governance JSONL parse failed: {path}:{line_no}:{exc.colno}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"NPC dialogue governance JSONL row must be object: {path}:{line_no}")
            row_id = str(row.get("id") or "").strip()
            if not row_id:
                raise ValueError(f"NPC dialogue governance JSONL missing id: {path}:{line_no}")
            if row_id in seen_ids:
                raise ValueError(f"NPC dialogue governance JSONL duplicate id: {path}:{line_no} id={row_id}")
            seen_ids.add(row_id)
            rows.append(row)
    return rows


def _non_empty_string_set(values: Any, fallback: set[str]) -> set[str]:
    if not isinstance(values, list):
        return set(fallback)
    normalized = {str(value).strip() for value in values if str(value).strip()}
    return normalized or set(fallback)


def _npc_dialogue_rule_by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("constantName") or ""): row for row in rows}


def apply_npc_dialogue_runtime_service_governance(repo_root: Path) -> None:
    global DEFAULT_LLM_MODEL_PRESET, LLM_HISTORY_PROVIDERS
    global DEFAULT_STABLE_RELATIONSHIP_SOURCE_LAYERS, DEFAULT_A_CANON_RELATIONSHIP_GRADES
    global LLM_MODEL_PRESETS, SUPPORTED_LLM_MODEL_PRESETS
    global HARD_RELATIONSHIP_PAIR_TYPES, TARGET_ID_NAME_COLLISIONS
    global YELLOW_TURBAN_TARGET_IDS, YELLOW_TURBAN_CONTEXT_TERMS

    policy = _read_optional_governance_json(repo_root, "NPC_DIALOGUE_RUNTIME_SERVICE_POLICY", "policies/policy-npc-dialogue-runtime-service.json", "Policy_NpcDialogueRuntimeService_P1")
    preset_rows = _read_optional_governance_jsonl(repo_root, "NPC_DIALOGUE_LLM_MODEL_PRESETS", "catalogs/catalog-npc-dialogue-llm-model-presets.jsonl")
    cue_rows = _read_optional_governance_jsonl(repo_root, "NPC_DIALOGUE_RUNTIME_CUE_RULES", "rules/rule-npc-dialogue-runtime-cues.jsonl")

    if policy:
        DEFAULT_LLM_MODEL_PRESET = str(policy.get("defaultLlmModelPreset") or DEFAULT_LLM_MODEL_PRESET)
        LLM_HISTORY_PROVIDERS = _non_empty_string_set(policy.get("llmHistoryProviders"), LLM_HISTORY_PROVIDERS)
        DEFAULT_STABLE_RELATIONSHIP_SOURCE_LAYERS = _non_empty_string_set(policy.get("stableRelationshipSourceLayers"), DEFAULT_STABLE_RELATIONSHIP_SOURCE_LAYERS)
        DEFAULT_A_CANON_RELATIONSHIP_GRADES = _non_empty_string_set(policy.get("aCanonRelationshipGrades"), DEFAULT_A_CANON_RELATIONSHIP_GRADES)
    if preset_rows:
        presets: dict[str, dict[str, Any]] = {}
        for row in preset_rows:
            preset = str(row.get("preset") or "").strip()
            if not preset:
                continue
            provider_order = row.get("providerOrder")
            if provider_order is not None:
                provider_order = [str(item) for item in provider_order]
            model_overrides = row.get("modelOverrides") if isinstance(row.get("modelOverrides"), dict) else {}
            presets[preset] = {
                "label": str(row.get("label") or preset),
                "description": str(row.get("description") or ""),
                "providerOrder": provider_order,
                "modelOverrides": {str(key): str(value) for key, value in model_overrides.items()},
                "allowDeterministicFallback": bool(row.get("allowDeterministicFallback")),
            }
        if presets:
            LLM_MODEL_PRESETS = presets
            SUPPORTED_LLM_MODEL_PRESETS = set(LLM_MODEL_PRESETS.keys())
    if cue_rows:
        by_name = _npc_dialogue_rule_by_name(cue_rows)
        pair_values = by_name.get("HARD_RELATIONSHIP_PAIR_TYPES", {}).get("value")
        if isinstance(pair_values, list) and pair_values:
            HARD_RELATIONSHIP_PAIR_TYPES = {
                frozenset(str(item) for item in row.get("generalIds") or []): str(row.get("relationshipType") or "")
                for row in pair_values
                if isinstance(row, dict) and len(row.get("generalIds") or []) >= 2 and row.get("relationshipType")
            }
        collision_value = by_name.get("TARGET_ID_NAME_COLLISIONS", {}).get("value")
        if isinstance(collision_value, dict) and collision_value:
            TARGET_ID_NAME_COLLISIONS = {
                str(target_id): {str(name): str(mapped_id) for name, mapped_id in mapping.items()}
                for target_id, mapping in collision_value.items()
                if isinstance(mapping, dict)
            }
        target_ids = by_name.get("YELLOW_TURBAN_TARGET_IDS", {}).get("value")
        if isinstance(target_ids, list) and target_ids:
            YELLOW_TURBAN_TARGET_IDS = {str(item) for item in target_ids if str(item).strip()}
        context_terms = by_name.get("YELLOW_TURBAN_CONTEXT_TERMS", {}).get("value")
        if isinstance(context_terms, list) and context_terms:
            YELLOW_TURBAN_CONTEXT_TERMS = tuple(str(item) for item in context_terms if str(item).strip())


class ContextOption(BaseModel):
    contextKey: str
    label: str
    sourceType: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidenceRefs: list[str] = Field(default_factory=list)


class ContextOptionsResponse(BaseModel):
    generalId: str
    options: list[ContextOption] = Field(default_factory=list)


class KeywordOption(BaseModel):
    keywordKey: str
    label: str
    fullLabel: str | None = None
    uiLabelMaxChars: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    sourceRefs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_ui_label_limit(self):
        if self.uiLabelMaxChars and self.uiLabelMaxChars > 0 and len(self.label) > self.uiLabelMaxChars:
            self.fullLabel = self.fullLabel or self.label
            self.label = self.label[: self.uiLabelMaxChars]
        return self


class KeywordOptionsResponse(BaseModel):
    generalId: str
    keywordVersion: str
    categories: dict[str, list[KeywordOption]] = Field(default_factory=dict)


class NarrativeInteractionTarget(BaseModel):
    targetId: str
    label: str
    role: str
    gender: str | None = None
    sourceType: str
    relationshipType: str | None = None
    confidence: float = Field(default=0.68, ge=0.0, le=1.0)
    evidenceRefs: list[str] = Field(default_factory=list)
    femaleFocus: bool = False


class NarrativeEvidenceCard(BaseModel):
    evidenceId: str
    contextKey: str | None = None
    angle: str
    title: str
    summary: str
    quote: str | None = None
    location: str | None = None
    chapterNo: int | None = None
    sourceType: str
    sourceRefs: list[str] = Field(default_factory=list)
    relatedTargetIds: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.72, ge=0.0, le=1.0)


class NarrativeActivitySeed(BaseModel):
    keywordKey: str
    label: str
    confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    sourceRefs: list[str] = Field(default_factory=list)
    rawTag: str | None = None


class NarrativeProfileResponse(BaseModel):
    generalId: str
    displayName: str
    sourceMode: str
    canonicalWrites: bool = False
    persona: dict[str, Any] = Field(default_factory=dict)
    keywords: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    relationshipEdges: list[dict[str, Any]] = Field(default_factory=list)
    evidenceCards: list[NarrativeEvidenceCard] = Field(default_factory=list)
    itemRelations: list[dict[str, Any]] = Field(default_factory=list)
    activitySeeds: list[NarrativeActivitySeed] = Field(default_factory=list)
    interactionTargets: list[NarrativeInteractionTarget] = Field(default_factory=list)
    illustrationPromptBase: str = ""


class DialogueRequest(BaseModel):
    generalId: str
    contextKey: str | None = None
    selectedKeywordKeys: list[str] = Field(default_factory=list)
    toneMode: str = "in-character"
    locale: str = DEFAULT_LOCALE
    speechContextMode: str = DEFAULT_SPEECH_CONTEXT_MODE
    llmModelPreset: str = DEFAULT_LLM_MODEL_PRESET
    maxChars: int = Field(default=80, ge=12, le=650)
    saveId: str | None = None
    memoryContext: GeneralMemoryContext | None = None

    @model_validator(mode="after")
    def normalize_request_fields(self):
        self.selectedKeywordKeys = list(dict.fromkeys(key for key in self.selectedKeywordKeys if key))
        if self.saveId is None and self.memoryContext is not None:
            self.saveId = self.memoryContext.saveId
        if self.memoryContext is not None and self.saveId is not None and self.memoryContext.saveId != self.saveId:
            self.memoryContext = self.memoryContext.model_copy(update={"saveId": self.saveId})
        if self.locale not in SUPPORTED_LOCALES:
            self.locale = DEFAULT_LOCALE
        if self.speechContextMode not in SUPPORTED_SPEECH_CONTEXT_MODES:
            self.speechContextMode = DEFAULT_SPEECH_CONTEXT_MODE
        if self.llmModelPreset not in SUPPORTED_LLM_MODEL_PRESETS:
            self.llmModelPreset = DEFAULT_LLM_MODEL_PRESET
        return self


class UsedKeyword(BaseModel):
    keywordKey: str
    category: str
    label: str
    sourceRefs: list[str] = Field(default_factory=list)


class PersonaCard(BaseModel):
    generalId: str
    personaVersion: str = PERSONA_VERSION
    displayName: str
    voiceStyle: list[str] = Field(default_factory=list)
    personalityTraits: list[str] = Field(default_factory=list)
    safeFallbackLine: str
    taboos: list[str] = Field(default_factory=list)
    evidenceRefs: list[str] = Field(default_factory=list)


class DialogueResponse(BaseModel):
    generalId: str
    contextKey: str | None
    locale: str = DEFAULT_LOCALE
    speechContextMode: str = DEFAULT_SPEECH_CONTEXT_MODE
    llmModelPreset: str = DEFAULT_LLM_MODEL_PRESET
    text: str
    evidenceRefs: list[str]
    usedEvidenceRefs: list[str] = Field(default_factory=list)
    unresolvedEvidenceRefs: list[str] = Field(default_factory=list)
    resolutionTrace: list[str] = Field(default_factory=list)
    usedKeywords: list[UsedKeyword] = Field(default_factory=list)
    rejectedKeywordKeys: list[str] = Field(default_factory=list)
    fallbackUsed: bool
    generationMode: str
    provider: str | None = None
    model: str | None = None
    providerTrace: list[str] = Field(default_factory=list)
    qualityWarnings: list[str] = Field(default_factory=list)
    repairUsed: bool = False


class SceneIllustrationRequest(BaseModel):
    generalId: str
    prompt: str | None = None
    aspectRatio: str = "4:5"
    imageSize: str = "1K"


class SceneIllustrationResponse(BaseModel):
    generalId: str
    canonicalWrites: bool = False
    provider: str
    model: str
    cacheHit: bool = False
    promptUsed: str
    mimeType: str
    imageBase64: str
    caption: str | None = None


class SceneDirectorRequest(BaseModel):
    generalId: str
    angle: str | None = None
    targetId: str | None = None
    evidenceId: str | None = None
    renderMode: str = "data_first"
    chorusTargetIds: list[str] = Field(default_factory=list)
    locale: str = DEFAULT_LOCALE
    llmModelPreset: str = DEFAULT_LLM_MODEL_PRESET
    maxStoryChars: int = Field(default=560, ge=160, le=650)
    maxChorusChars: int = Field(default=110, ge=24, le=180)

    @model_validator(mode="after")
    def normalize_scene_director_fields(self):
        self.chorusTargetIds = list(dict.fromkeys(target_id for target_id in self.chorusTargetIds if target_id))
        if self.renderMode not in {"data_first", "llm_polish"}:
            self.renderMode = "data_first"
        if self.locale not in SUPPORTED_LOCALES:
            self.locale = DEFAULT_LOCALE
        if self.llmModelPreset not in SUPPORTED_LLM_MODEL_PRESETS:
            self.llmModelPreset = DEFAULT_LLM_MODEL_PRESET
        return self


class ScenePresenceDecision(BaseModel):
    status: str
    reason: str
    sourceText: str | None = None


class SceneDirectorBeats(BaseModel):
    sceneText: str
    memoryText: str
    emotionText: str
    dialogueText: str
    intentText: str
    presence: ScenePresenceDecision
    sceneFacts: dict[str, Any] = Field(default_factory=dict)
    sceneSeeds: dict[str, Any] = Field(default_factory=dict)
    sourceRefs: list[str] = Field(default_factory=list)
    evidenceId: str | None = None


class SceneChorusLine(BaseModel):
    targetId: str
    label: str
    role: str
    text: str
    provider: str | None = None
    model: str | None = None
    fallbackUsed: bool = False
    evidenceRefs: list[str] = Field(default_factory=list)


class SceneEvidenceResolution(BaseModel):
    usedEvidenceRefs: list[str] = Field(default_factory=list)
    unresolvedEvidenceRefs: list[str] = Field(default_factory=list)
    resolutionTrace: list[str] = Field(default_factory=list)


class SceneDirectorResponse(BaseModel):
    generalId: str
    displayName: str
    canonicalWrites: bool = False
    angle: str | None = None
    requestedAngle: str | None = None
    resolvedAngle: str | None = None
    targetId: str | None = None
    targetLabel: str | None = None
    dataStatus: str = "empty"
    isEmpty: bool = False
    fallbackReason: str | None = None
    emptyReason: str | None = None
    evidenceResolution: SceneEvidenceResolution = Field(default_factory=SceneEvidenceResolution)
    beats: SceneDirectorBeats
    storyText: str
    storyProvider: str | None = None
    storyModel: str | None = None
    storyFallbackUsed: bool = False
    storyRepairUsed: bool = False
    chorusLines: list[SceneChorusLine] = Field(default_factory=list)
    providerTrace: list[str] = Field(default_factory=list)


class NpcDialogueService:
    def __init__(
        self,
        repo_root: Path | None = None,
        artifact_root: Path | None = None,
        persona_root: Path | None = None,
        runtime_profile_root: Path | None = None,
        event_root: Path | None = None,
        provider_router: DialogueProviderRouter | None = None,
    ) -> None:
        self.repo_root = repo_root or find_repo_root(Path.cwd())
        load_local_env(self.repo_root)
        apply_npc_dialogue_runtime_service_governance(self.repo_root)
        artifact_root_path = artifact_root or Path(os.environ.get("NPC_ARTIFACT_ROOT") or DEFAULT_ARTIFACT_ROOT)
        persona_root_path = persona_root or Path(os.environ.get("NPC_PERSONA_ROOT") or DEFAULT_PERSONA_ROOT)
        runtime_profile_root_path = runtime_profile_root or Path(
            os.environ.get("NPC_RUNTIME_PROFILE_ROOT") or DEFAULT_RUNTIME_PROFILE_ROOT
        )
        event_root_path = event_root or Path(os.environ.get("NPC_EVENT_ROOT") or DEFAULT_EVENT_ROOT)
        self.relationship_runtime_policy = self._load_relationship_runtime_canon_policy()
        self.store = RuntimeProfileStore(
            repo_root=self.repo_root,
            artifact_root=artifact_root_path,
            persona_root=persona_root_path,
            runtime_profile_root=runtime_profile_root_path,
            event_root=event_root_path,
        )
        self.artifact_root = self.store.artifact_root
        self.persona_root = self.store.persona_root
        self.runtime_profile_root = self.store.runtime_profile_root
        self.event_root = self.store.event_root
        self.history_cache_path = self._resolve_path(Path(os.environ.get("NPC_LLM_HISTORY_CACHE_PATH") or DEFAULT_HISTORY_CACHE_PATH))
        self.provider_router = provider_router or DialogueProviderRouter()
        self.evidence_resolver = EvidenceResolver(self.store)
        self.scene_image_renderer = GeminiSceneImageRenderer(cache_root=self.repo_root / "local/npc-scene-image-cache")
        self._roster_index_cache: dict[str, dict[str, Any]] | None = None
        self._chapter_paragraph_cache: dict[str, list[str]] = {}
        self._source_event_packet_cache: dict[str, dict[str, Any]] | None = None
        self._baihua_passage_cache: dict[tuple[str, int], str] | None = None
        self._scene_chorus_cache: dict[str, SceneChorusLine] = {}
        self._scene_chorus_cache_lock = Lock()

    def _load_relationship_runtime_canon_policy(self) -> dict[str, Any]:
        candidates = [self.repo_root / DEFAULT_RELATIONSHIP_RUNTIME_CANON_POLICY_LOCAL_PATH]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"Relationship runtime canon policy must be an object: {path}")
        if payload.get("id") != "Policy_RelationshipRuntimeCanon_P1":
            raise ValueError(f"Unexpected relationship runtime canon policy id: {path}")
        return payload

    def get_health(self) -> dict:
        provider_order = self.provider_router.provider_order
        vector_config = load_vector_runtime_config()
        memory_config = get_memory_runtime_config(self.repo_root)
        vector_health = vector_config.as_health()
        vector_health["runtimeMode"] = "artifact-first-vector-second"
        vector_health["offlineDegradeReady"] = False
        vector_second_plan = self.evidence_resolver.vector_second.describe()
        vector_health["vectorSecond"] = {
            **vector_second_plan,
            "enabled": True,
            "strategies": [
                "exact-ref-completion",
                "pinecone/qdrant backend query",
            ],
        }
        vector_health["notes"] = [
            "runtime profiles and ready events are the source of truth",
            "remote vector providers supplement recall only",
            "server currently runs exact-ref completion and a Pinecone/Qdrant backend second pass over vector-ready facts",
            "sqlite_vec remains stub and is not a production offline fallback",
        ]
        sqlite_health = dict(vector_health.get("sqliteVec") or {})
        sqlite_health["status"] = "stub"
        sqlite_health["productionReady"] = False
        vector_health["sqliteVec"] = sqlite_health
        return {
            "ok": True,
            "service": "npc-brain",
            "llm": {
                "providerOrder": provider_order,
                "geminiConfigured": bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")),
                "geminiModel": os.environ.get("NPC_LLM_MODEL_GEMINI") or os.environ.get("NPC_LLM_MODEL") or "gemini-2.5-pro",
                "geminiFlashModel": os.environ.get("NPC_LLM_MODEL_GEMINI_FLASH") or "gemini-2.5-flash",
                "geminiFlashLiteModel": os.environ.get("NPC_LLM_MODEL_GEMINI_FLASH_LITE") or "gemini-2.5-flash-lite",
                "geminiImageConfigured": bool(self.scene_image_renderer.api_key),
                "geminiImageModel": self.scene_image_renderer.model,
                "localLlamaEnabled": "local_llama" in provider_order,
                "localLlamaModel": os.environ.get("NPC_LLM_MODEL_LOCAL_LLAMA") or DEFAULT_LOCAL_LLAMA_MODEL,
                "localLlamaOptions": {
                    "temperature": float(os.environ.get("NPC_LLM_LOCAL_LLAMA_TEMPERATURE") or DEFAULT_LOCAL_LLAMA_TEMPERATURE),
                    "topP": float(os.environ.get("NPC_LLM_LOCAL_LLAMA_TOP_P") or DEFAULT_LOCAL_LLAMA_TOP_P),
                    "repeatPenalty": float(os.environ.get("NPC_LLM_LOCAL_LLAMA_REPEAT_PENALTY") or DEFAULT_LOCAL_LLAMA_REPEAT_PENALTY),
                    "numCtx": int(os.environ.get("NPC_LLM_LOCAL_LLAMA_NUM_CTX") or DEFAULT_LOCAL_LLAMA_NUM_CTX),
                    "repairRetryCount": int(os.environ.get("NPC_LLM_LOCAL_LLAMA_REPAIR_RETRY_COUNT") or DEFAULT_LOCAL_LLAMA_REPAIR_RETRY_COUNT),
                },
                "historyCacheEnabled": "history_cache" in provider_order,
                "historyCachePath": str(self.history_cache_path),
                "supportedLocales": sorted(SUPPORTED_LOCALES),
                "supportedSpeechContextModes": sorted(SUPPORTED_SPEECH_CONTEXT_MODES),
                "supportedModelPresets": [
                    {
                        "preset": preset,
                        "label": config["label"],
                        "description": config["description"],
                        "providerOrder": config["providerOrder"],
                        "modelOverrides": config["modelOverrides"],
                    }
                    for preset, config in LLM_MODEL_PRESETS.items()
                ],
            },
            "vector": vector_health,
            "memory": memory_config,
        }

    def record_interaction_event(self, request: InteractionEventCreateRequest) -> InteractionEventWriteResponse:
        event = build_interaction_event(request)
        append_success = append_interaction_event(self.repo_root, event)
        if not append_success:
            raise RuntimeError(f"failed to append interaction event: {request.saveId}/{request.generalId}")
        memory = increment_uncompressed_count(self.repo_root, request.saveId, request.generalId)
        memory_config = get_memory_runtime_config(self.repo_root)
        delta = memory.uncompressedCount - memory.lastCompressedIdx
        if delta >= int(memory_config["compressInterval"]):
            log_debug_event(
                "memory.compress.pending",
                saveId=request.saveId,
                generalId=request.generalId,
                delta=delta,
                compressInterval=memory_config["compressInterval"],
            )
        return InteractionEventWriteResponse(eventId=event.eventId)

    def get_general_memory(self, save_id: str, general_id: str) -> GeneralMemoryData:
        return load_general_memory(self.repo_root, save_id, general_id)

    def save_general_memory(self, memory: GeneralMemoryData) -> MemoryWriteResponse:
        save_general_memory(self.repo_root, memory)
        return MemoryWriteResponse(ok=True)

    def compress_general_memory(self, request: MemoryCompressRequest) -> GeneralMemoryData:
        persona = self.get_persona_card(request.generalId)
        return compress_general_memory(self.repo_root, request, self._persona_subset(persona))

    def get_context_options(self, general_id: str, limit: int | None = None) -> ContextOptionsResponse:
        runtime_persona = self.store.read_runtime_persona(general_id)
        if runtime_persona:
            options = [
                ContextOption(
                    contextKey=str(beat.get("eventKey") or beat.get("eventId") or f"story-{index}"),
                    label=str(beat.get("location") or beat.get("summary") or beat.get("eventKey") or beat.get("eventId")),
                    sourceType="romance-runtime-profile",
                    confidence=float(beat.get("confidence") or 0.72),
                    evidenceRefs=beat.get("sourceRefs") or [],
                )
                for index, beat in enumerate(runtime_persona.get("storyBeats") or [])
                if beat.get("sourceRefs")
            ]
            if not options:
                options = [
                    ContextOption(
                        contextKey=f"highlight:{highlight.get('sourceRef') or index}",
                        label=str(highlight.get("example") or highlight.get("sourceRef") or f"source-{index}"),
                        sourceType="runtime-source-highlight",
                        confidence=0.68,
                        evidenceRefs=[str(highlight.get("sourceRef"))] if highlight.get("sourceRef") else [],
                    )
                    for index, highlight in enumerate(runtime_persona.get("sourceHighlights") or [])
                    if highlight.get("sourceRef")
                ]
            if limit is not None:
                options = options[: max(limit, 0)]
            return ContextOptionsResponse(generalId=general_id, options=options)
        payload = self.store.read_api_fixture("context-options.response.json")
        response = ContextOptionsResponse.model_validate(payload)
        if response.generalId != general_id:
            return ContextOptionsResponse(generalId=general_id, options=[])
        if limit is not None:
            response.options = response.options[: max(limit, 0)]
        return response

    def get_keyword_options(
        self,
        general_id: str,
        categories: list[str] | None = None,
        limit_per_category: int | None = None,
    ) -> KeywordOptionsResponse:
        runtime_keywords = self.store.read_runtime_keywords(general_id)
        if runtime_keywords:
            response = KeywordOptionsResponse.model_validate({
                "generalId": runtime_keywords.get("generalId") or general_id,
                "keywordVersion": runtime_keywords.get("keywordVersion") or "general_runtime_keywords_v1",
                "categories": runtime_keywords.get("categories") or {},
            })
            if categories:
                response.categories = {category: response.categories[category] for category in categories if category in response.categories}
            if limit_per_category is not None:
                response.categories = {
                    category: options[: max(limit_per_category, 0)]
                    for category, options in response.categories.items()
                }
            return response
        payload = self.store.read_api_fixture("keyword-options.response.json")
        response = KeywordOptionsResponse.model_validate(payload)
        if response.generalId != general_id:
            return KeywordOptionsResponse(generalId=general_id, keywordVersion=response.keywordVersion, categories={})
        if categories:
            response.categories = {category: response.categories[category] for category in categories if category in response.categories}
        if limit_per_category is not None:
            response.categories = {
                category: options[: max(limit_per_category, 0)]
                for category, options in response.categories.items()
            }
        return response

    def get_narrative_profile(self, general_id: str) -> NarrativeProfileResponse:
        runtime_persona = self.store.read_runtime_persona(general_id) or {}
        runtime_keywords = self.store.read_runtime_keywords(general_id) or {}
        runtime_relationships = self.store.read_runtime_relationships(general_id) or {}
        persona_card = self.get_persona_card(general_id)
        roster_index = self._load_roster_index()
        display_name = (
            str(runtime_persona.get("displayName") or "").strip()
            or (persona_card.displayName if persona_card else "")
            or self._roster_name_for(general_id, roster_index)
            or general_id
        )
        interaction_targets = self._build_interaction_targets(
            general_id=general_id,
            runtime_persona=runtime_persona,
            runtime_keywords=runtime_keywords,
            runtime_relationships=runtime_relationships,
            roster_index=roster_index,
        )
        evidence_cards = self._build_narrative_evidence_cards(
            runtime_persona=runtime_persona,
            runtime_relationships=runtime_relationships,
            interaction_targets=interaction_targets,
        )
        activity_seeds = self._build_activity_seeds(runtime_persona=runtime_persona, runtime_keywords=runtime_keywords)
        persona_payload = {
            **runtime_persona,
            "displayName": display_name,
            "voiceStyle": (runtime_persona.get("voiceAndPrompt") or {}).get("voiceStyle")
            or (persona_card.voiceStyle if persona_card else []),
            "personalityTraits": (runtime_persona.get("profile") or {}).get("personalityTags")
            or (persona_card.personalityTraits if persona_card else []),
            "safeFallbackLine": (runtime_persona.get("voiceAndPrompt") or {}).get("safeFallbackLine")
            or (persona_card.safeFallbackLine if persona_card else ""),
            "taboos": (runtime_persona.get("voiceAndPrompt") or {}).get("taboos")
            or (persona_card.taboos if persona_card else []),
        }
        return NarrativeProfileResponse(
            generalId=general_id,
            displayName=display_name,
            sourceMode="runtime-general-profiles" if runtime_persona else "persona-card-fallback",
            canonicalWrites=False,
            persona=persona_payload,
            keywords=runtime_keywords.get("categories") or {},
            relationshipEdges=list(runtime_relationships.get("anchors") or []),
            evidenceCards=evidence_cards,
            itemRelations=[],
            activitySeeds=activity_seeds,
            interactionTargets=interaction_targets,
            illustrationPromptBase=self._build_illustration_prompt(display_name, runtime_persona, interaction_targets),
        )

    def build_dialogue(self, request: DialogueRequest) -> DialogueResponse:
        log_debug_event(
            "dialogue.build.start",
            generalId=request.generalId,
            contextKey=request.contextKey,
            selectedKeywordKeys=request.selectedKeywordKeys,
            toneMode=request.toneMode,
            locale=request.locale,
            speechContextMode=request.speechContextMode,
            llmModelPreset=request.llmModelPreset,
            maxChars=request.maxChars,
            saveId=request.saveId,
            hasMemoryContext=has_memory_context_content(request.memoryContext),
        )
        context_response = self.get_context_options(request.generalId)
        keyword_response = self.get_keyword_options(request.generalId)
        persona_card = self.get_persona_card(request.generalId)
        memory_context = self._resolve_memory_context(request)
        selected_context = self._select_context(context_response, request.contextKey)
        keyword_index = self._index_keywords(keyword_response)

        used_keywords: list[UsedKeyword] = []
        rejected_keyword_keys: list[str] = []
        for keyword_key in request.selectedKeywordKeys:
            keyword_entry = keyword_index.get(keyword_key)
            if keyword_entry is None:
                rejected_keyword_keys.append(keyword_key)
                continue
            category, keyword = keyword_entry
            used_keywords.append(
                UsedKeyword(
                    keywordKey=keyword.keywordKey,
                    category=category,
                    label=keyword.label,
                    sourceRefs=keyword.sourceRefs,
                )
            )

        evidence_refs = sorted(
            {
                ref
                for ref in ((selected_context.evidenceRefs if selected_context else []) + [ref for keyword in used_keywords for ref in keyword.sourceRefs])
            }
        )
        deterministic_text = self._render_deterministic_dialogue(
            request.generalId,
            selected_context,
            used_keywords,
            persona_card,
            request.maxChars,
            request.locale,
            request.speechContextMode,
        )
        evidence_pack = self._resolve_evidence(request.generalId, selected_context, used_keywords, evidence_refs)
        resolved_evidence = evidence_pack.resolvedEvidence
        log_debug_event(
            "dialogue.build.resolved",
            generalId=request.generalId,
            locale=request.locale,
            speechContextMode=request.speechContextMode,
            selectedContext=self._context_subset(selected_context),
            usedKeywords=[keyword.model_dump() for keyword in used_keywords],
            rejectedKeywordKeys=rejected_keyword_keys,
            evidenceRefs=evidence_refs,
            resolvedEvidenceRefs=[evidence.evidenceRef for evidence in resolved_evidence],
            unresolvedEvidenceRefs=evidence_pack.unresolvedEvidenceRefs,
            resolutionTrace=evidence_pack.resolutionTrace,
            deterministicPreview=deterministic_text,
        )
        preset_config = LLM_MODEL_PRESETS.get(request.llmModelPreset, LLM_MODEL_PRESETS[DEFAULT_LLM_MODEL_PRESET])
        generation = self.provider_router.generate(
            DialoguePromptPackage(
                generalId=request.generalId,
                personaCardSubset=self._persona_subset(persona_card),
                memoryContext=memory_context.model_dump(exclude_none=True) if memory_context else None,
                selectedContext=self._context_subset(selected_context),
                selectedKeywords=[keyword.model_dump() for keyword in used_keywords],
                resolvedEvidence=resolved_evidence,
                evidenceRefs=evidence_refs,
                deterministicText=deterministic_text,
                maxChars=request.maxChars,
                toneMode=request.toneMode,
                locale=request.locale,
                speechContextMode=request.speechContextMode,
            ),
            provider_order=preset_config["providerOrder"],
            model_overrides=preset_config["modelOverrides"],
            allow_deterministic_fallback=preset_config["allowDeterministicFallback"],
        )
        self._record_llm_dialogue_history(request, selected_context, used_keywords, evidence_refs, generation)
        log_debug_event(
            "dialogue.build.result",
            generalId=request.generalId,
            locale=request.locale,
            speechContextMode=request.speechContextMode,
            llmModelPreset=request.llmModelPreset,
            provider=generation.provider,
            model=generation.model,
            providerTrace=generation.providerTrace,
            qualityWarnings=generation.qualityWarnings,
            repairUsed=generation.repairUsed,
            usedEvidenceRefs=generation.usedEvidenceRefs,
            text=generation.text,
        )
        return DialogueResponse(
            generalId=request.generalId,
            contextKey=selected_context.contextKey if selected_context else request.contextKey,
            locale=request.locale,
            speechContextMode=request.speechContextMode,
            llmModelPreset=request.llmModelPreset,
            text=generation.text,
            evidenceRefs=evidence_refs,
            usedEvidenceRefs=generation.usedEvidenceRefs,
            unresolvedEvidenceRefs=evidence_pack.unresolvedEvidenceRefs,
            resolutionTrace=evidence_pack.resolutionTrace,
            usedKeywords=used_keywords,
            rejectedKeywordKeys=rejected_keyword_keys,
            fallbackUsed=generation.fallbackUsed,
            generationMode=generation.generationMode,
            provider=generation.provider,
            model=generation.model,
            providerTrace=generation.providerTrace,
            qualityWarnings=generation.qualityWarnings,
            repairUsed=generation.repairUsed,
        )

    def render_scene_illustration(self, request: SceneIllustrationRequest) -> SceneIllustrationResponse:
        runtime_persona = self.store.read_runtime_persona(request.generalId) or {}
        roster_index = self._load_roster_index()
        display_name = (
            str(runtime_persona.get("displayName") or "").strip()
            or self._roster_name_for(request.generalId, roster_index)
            or request.generalId
        )
        prompt = str(request.prompt or "").strip() or self._build_illustration_prompt(display_name, runtime_persona, [])
        result = self.scene_image_renderer.render(
            prompt,
            aspect_ratio=request.aspectRatio,
            image_size=request.imageSize,
        )
        return SceneIllustrationResponse(
            generalId=request.generalId,
            canonicalWrites=False,
            provider=result.provider,
            model=result.model,
            cacheHit=result.cache_hit,
            promptUsed=result.prompt_used,
            mimeType=result.mime_type,
            imageBase64=result.image_base64,
            caption=result.caption,
        )

    def build_scene_director(self, request: SceneDirectorRequest) -> SceneDirectorResponse:
        started_at = time.monotonic()
        profile = self.get_narrative_profile(request.generalId)
        target = self._select_narrative_target(profile.interactionTargets, request.targetId)
        target_invalid = bool(request.targetId and target is None)
        card = self._select_narrative_card(
            profile.evidenceCards,
            request.evidenceId,
            request.angle,
            target,
            actor_aliases=self._actor_label_aliases(profile),
        )
        evidence_invalid = bool(request.evidenceId and not any(card_item.evidenceId == request.evidenceId for card_item in profile.evidenceCards))
        semantic_empty_reason = self._scene_pair_empty_reason(request.angle, card, target)
        has_scene_data = card is not None and not target_invalid and not evidence_invalid and not semantic_empty_reason
        data_status, fallback_reason = self._scene_data_status(
            requested_angle=request.angle,
            requested_target_id=request.targetId,
            requested_evidence_id=request.evidenceId,
            target_invalid=target_invalid,
            evidence_invalid=evidence_invalid,
            semantic_empty_reason=semantic_empty_reason,
            card=card,
            target=target,
        )
        beats = self._build_scene_director_beats(profile, card, target, request.angle) if has_scene_data else self._build_empty_scene_director_beats()
        evidence_refs = sorted(set(beats.sourceRefs))
        evidence_resolution = SceneEvidenceResolution(
            usedEvidenceRefs=evidence_refs[:12],
            unresolvedEvidenceRefs=[],
            resolutionTrace=[
                "scene-director:data-first",
                f"renderMode:{request.renderMode}",
                f"dataStatus:{data_status}",
            ],
        )
        if has_scene_data:
            story_context = self._build_scene_director_selected_context(profile, target, card, beats, request.renderMode)
            story_keywords = self._scene_story_keywords(profile, target, card, beats)
            story_provider_config = self._scene_story_provider_config(request.llmModelPreset, request.renderMode)
            try:
                story_generation = self._generate_scene_director_text(
                    general_id=request.generalId,
                    persona_card=self.get_persona_card(request.generalId),
                    memory_context=self._build_scene_director_story_context(profile, target, card, beats),
                    selected_context=story_context,
                    evidence_refs=evidence_refs,
                    deterministic_text="",
                    max_chars=request.maxStoryChars,
                    locale=request.locale,
                    llm_model_preset=request.llmModelPreset,
                    tone_mode="narrative_fusion",
                    selected_keywords=story_keywords,
                    include_resolved_evidence=False,
                    provider_order=story_provider_config["providerOrder"],
                    model_overrides=story_provider_config["modelOverrides"],
                    allow_deterministic_fallback=story_provider_config["allowDeterministicFallback"],
                )
                story_generation = self._repair_complete_generation(
                    story_generation,
                    fallback_text="",
                    max_chars=request.maxStoryChars,
                    warning_code="scene_story_trimmed_to_complete_sentence",
                )
            except Exception as exc:
                log_debug_event(
                    "scene_director.story.error",
                    generalId=request.generalId,
                    targetId=target.targetId if target else None,
                    error=str(exc)[:240],
                )
                story_generation = DialogueGenerationResult(
                    text="",
                    provider="unavailable",
                    model=None,
                    generationMode="scene-director-empty",
                    fallbackUsed=True,
                    providerTrace=[*evidence_resolution.resolutionTrace, f"story-error:{str(exc)[:120]}"],
                    qualityWarnings=[],
                    repairUsed=False,
                )
            self._record_scene_story_history(
                request=request,
                context_key=str(story_context.get("contextKey") or "").strip() or None,
                selected_keywords=story_keywords,
                evidence_refs=evidence_refs,
                generation=story_generation,
            )
            beats = self._enrich_scene_director_beats_from_story(profile, beats, story_generation.text)
            chorus_targets = self._select_chorus_targets(profile.interactionTargets, request.chorusTargetIds, target.targetId if target else None)
            chorus_story_text = (
                str(story_generation.text or "").strip()
                or self._scene_seed_text(beats)
                or str(beats.sceneText or "").strip()
                or str(beats.memoryText or "").strip()
            )
            chorus_lines = self._build_scene_chorus_lines(
                request=request,
                profile=profile,
                targets=chorus_targets,
                main_target=target,
                card=card,
                beats=beats,
                story_text=chorus_story_text,
                timeout_seconds=self._scene_director_remaining_seconds(started_at),
            )
        else:
            empty_reason = "invalid_request" if data_status == "invalid_request" else "目前沒有可用資料"
            story_generation = DialogueGenerationResult(
                text="",
                provider="data_first",
                model=None,
                generationMode="data_first-empty",
                fallbackUsed=False,
                providerTrace=[*evidence_resolution.resolutionTrace, f"empty:{empty_reason}"],
                qualityWarnings=[],
                repairUsed=False,
            )
            chorus_lines = []
        is_empty = not has_scene_data or not any(
            [
                beats.sceneText,
                beats.memoryText,
                beats.emotionText,
                beats.dialogueText,
                beats.intentText,
                story_generation.text,
            ]
        )
        return SceneDirectorResponse(
            generalId=request.generalId,
            displayName=profile.displayName,
            canonicalWrites=False,
            angle=card.angle if card else request.angle,
            requestedAngle=request.angle,
            resolvedAngle=card.angle if card else None,
            targetId=target.targetId if target else request.targetId,
            targetLabel=target.label if target else None,
            dataStatus=data_status,
            isEmpty=is_empty,
            fallbackReason=fallback_reason,
            emptyReason=("invalid_request" if data_status == "invalid_request" else "目前沒有可用資料") if is_empty else None,
            evidenceResolution=evidence_resolution,
            beats=beats,
            storyText=story_generation.text,
            storyProvider=story_generation.provider,
            storyModel=story_generation.model,
            storyFallbackUsed=story_generation.fallbackUsed,
            storyRepairUsed=story_generation.repairUsed,
            chorusLines=chorus_lines,
            providerTrace=story_generation.providerTrace,
        )

    def _scene_director_remaining_seconds(self, started_at: float) -> float:
        raw_timeout_ms = os.environ.get("NPC_SCENE_DIRECTOR_TOTAL_TIMEOUT_MS")
        try:
            timeout_ms = int(raw_timeout_ms or DEFAULT_SCENE_DIRECTOR_TOTAL_TIMEOUT_MS)
        except ValueError:
            timeout_ms = DEFAULT_SCENE_DIRECTOR_TOTAL_TIMEOUT_MS
        total_seconds = max(1.0, timeout_ms / 1000)
        return max(0.0, total_seconds - (time.monotonic() - started_at))

    def _scene_data_status(
        self,
        *,
        requested_angle: str | None,
        requested_target_id: str | None,
        requested_evidence_id: str | None,
        target_invalid: bool,
        evidence_invalid: bool,
        semantic_empty_reason: str | None,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> tuple[str, str | None]:
        if target_invalid or evidence_invalid:
            reason = "targetId 不存在" if target_invalid else "evidenceId 不存在"
            return "invalid_request", reason
        if semantic_empty_reason:
            return "empty", semantic_empty_reason
        if card is None:
            return "empty", "目前沒有可用資料"
        angle_matches = not requested_angle or card.angle == requested_angle
        target_matches = target is None or target.targetId in set(card.relatedTargetIds or [])
        evidence_matches = not requested_evidence_id or card.evidenceId == requested_evidence_id
        if angle_matches and target_matches and evidence_matches:
            return "direct", None
        if requested_angle and not angle_matches:
            return "angle_empty_filled", f"requestedAngle={requested_angle}; resolvedAngle={card.angle}"
        if requested_target_id and not target_matches:
            return "target_empty_filled", f"requestedTarget={requested_target_id}; resolvedTarget={target.targetId if target else '-'}"
        return "direct", None

    def _scene_pair_empty_reason(
        self,
        requested_angle: str | None,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> str | None:
        if not card or not target:
            return None
        relationship_type = str(target.relationshipType or "")
        role = str(target.role or "")
        if requested_angle in {"people", "resource", "bond"} and (
            relationship_type in {"enemy_rival", "battlefield_opponent", "betrayal_surrender"}
            or "敵對" in role
            or "戰場對手" in role
        ):
            label = {"people": "眾生", "resource": "恩義", "bond": "情義"}.get(requested_angle, requested_angle)
            return f"{label}角度不使用敵對競爭目標；請改用宿敵、戰場或關係角度"
        return None

    def _select_narrative_card(
        self,
        cards: list[NarrativeEvidenceCard],
        evidence_id: str | None,
        angle: str | None,
        target: NarrativeInteractionTarget | None = None,
        actor_aliases: list[str] | None = None,
    ) -> NarrativeEvidenceCard | None:
        if evidence_id:
            for card in cards:
                if card.evidenceId == evidence_id:
                    if target is None:
                        return card
                    return card if self._card_matches_target(card, target) and self._card_source_matches_scene(card, target, actor_aliases) else None
            return None
        if angle:
            for card in cards:
                if (
                    card.angle == angle
                    and self._card_matches_target(card, target)
                    and self._card_source_matches_scene(card, target, actor_aliases)
                ):
                    return card
        if target is not None:
            return None
        return cards[0] if cards else None

    def _card_matches_target(
        self,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> bool:
        if card is None or target is None:
            return card is not None
        return target.targetId in set(card.relatedTargetIds or []) and self._card_source_mentions_target(card, target)

    def _card_source_mentions_target(
        self,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> bool:
        if not card or not target:
            return False
        return self._card_source_mentions_any(card, self._target_aliases_for_interaction(target))

    def _card_source_mentions_actor(
        self,
        card: NarrativeEvidenceCard | None,
        actor_aliases: list[str] | None,
    ) -> bool:
        return self._card_source_mentions_any(card, actor_aliases or [])

    def _card_source_matches_scene(
        self,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
        actor_aliases: list[str] | None,
    ) -> bool:
        if not card:
            return False
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        actor_aliases = actor_aliases or []
        if (
            card.sourceType == "runtime-relationship-edge"
            and self._coerce_float(card.confidence, default=0.0) >= 0.9
            and target is not None
            and target.targetId in set(card.relatedTargetIds or [])
        ):
            return True
        units = self._card_match_text_units(card)
        if target_aliases and actor_aliases:
            return self._card_pair_grounded_in_source(card, target_aliases, actor_aliases)
        if target_aliases:
            return any(any(alias and alias in unit for alias in target_aliases) for unit in units)
        if actor_aliases:
            return any(any(alias and alias in unit for alias in actor_aliases) for unit in units)
        return True

    def _card_pair_grounded_in_source(
        self,
        card: NarrativeEvidenceCard | None,
        target_aliases: list[str],
        actor_aliases: list[str],
    ) -> bool:
        if not card or not target_aliases or not actor_aliases:
            return False
        sentences = self._card_match_sentence_units(card)
        actor_indexes: list[int] = []
        target_indexes: list[int] = []
        for index, sentence in enumerate(sentences):
            has_actor = any(alias and alias in sentence for alias in actor_aliases)
            has_target = any(alias and alias in sentence for alias in target_aliases)
            if has_actor and has_target:
                return True
            if has_actor:
                actor_indexes.append(index)
            if has_target:
                target_indexes.append(index)
        if not actor_indexes or not target_indexes:
            return False
        for actor_index in actor_indexes:
            for target_index in target_indexes:
                if abs(actor_index - target_index) > 1:
                    continue
                left = min(actor_index, target_index)
                right = max(actor_index, target_index)
                window = "".join(sentences[left:right + 1])
                if len(window) <= 180 and self._source_window_has_interaction_signal(window):
                    return True
        return False

    def _source_window_has_interaction_signal(self, text: str) -> bool:
        return bool(re.search(r"見|問|曰|告|謂|命|令|召|拜|救|追|攻|戰|拒|降|託|送|迎|會|與|同|俱|隨|從|遣|報|諫|請|教|許|責|勸|謝|怒|笑|斬|殺|護", text))

    def _seed_text_is_pair_grounded(
        self,
        text: str,
        target: NarrativeInteractionTarget | None,
        actor_aliases: list[str],
    ) -> bool:
        if not target:
            return bool(str(text or "").strip())
        source = str(text or "")
        if not source:
            return False
        target_aliases = self._target_aliases_for_interaction(target)
        return bool(
            any(alias and alias in source for alias in actor_aliases)
            and any(alias and alias in source for alias in target_aliases)
        )

    def _seed_mentions_other_interaction_target(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        text: str,
    ) -> bool:
        source = str(text or "")
        if not source:
            return False
        active_id = target.targetId if target else None
        for other in profile.interactionTargets:
            if other.targetId == active_id:
                continue
            if any(alias and alias in source for alias in self._target_aliases_for_interaction(other)):
                return True
        return False

    def _card_source_mentions_any(
        self,
        card: NarrativeEvidenceCard | None,
        aliases: list[str],
    ) -> bool:
        if not card or not aliases:
            return True
        return any(any(alias and alias in unit for alias in aliases) for unit in self._card_match_text_units(card))

    def _card_match_text_units(self, card: NarrativeEvidenceCard | None) -> list[str]:
        if not card:
            return []
        raw_units = [
            str(card.summary or "").strip(),
            str(card.quote or "").strip(),
            str(card.title or "").strip(),
            str(card.location or "").strip() if card.location else "",
        ]
        raw_units.extend(self._source_ref_paragraph(ref) for ref in card.sourceRefs if ref)
        units: list[str] = []
        for raw_unit in raw_units:
            if not raw_unit:
                continue
            sentences = self._split_source_sentences(raw_unit)
            units.extend(sentences)
            units.append(raw_unit)
        return list(dict.fromkeys(unit for unit in units if unit))

    def _card_match_sentence_units(self, card: NarrativeEvidenceCard | None) -> list[str]:
        if not card:
            return []
        raw_units = [
            str(card.summary or "").strip(),
            str(card.quote or "").strip(),
            str(card.title or "").strip(),
            str(card.location or "").strip() if card.location else "",
        ]
        raw_units.extend(self._source_ref_paragraph(ref) for ref in card.sourceRefs if ref)
        sentences: list[str] = []
        for raw_unit in raw_units:
            if not raw_unit:
                continue
            sentences.extend(self._split_source_sentences(raw_unit))
        return list(dict.fromkeys(sentence for sentence in sentences if sentence))

    def _select_narrative_target(
        self,
        targets: list[NarrativeInteractionTarget],
        target_id: str | None,
    ) -> NarrativeInteractionTarget | None:
        if target_id:
            for target in targets:
                if target.targetId == target_id:
                    return target
            return None
        return targets[0] if targets else None

    def _select_chorus_targets(
        self,
        targets: list[NarrativeInteractionTarget],
        requested_ids: list[str],
        active_target_id: str | None,
    ) -> list[NarrativeInteractionTarget]:
        by_id = {target.targetId: target for target in targets}
        selected = [by_id[target_id] for target_id in requested_ids if target_id in by_id and target_id != active_target_id]
        if selected:
            return selected[:4]
        return [target for target in targets if target.targetId != active_target_id][:4]

    def _build_empty_scene_director_beats(self) -> SceneDirectorBeats:
        return SceneDirectorBeats(
            sceneText="",
            memoryText="",
            emotionText="",
            dialogueText="",
            intentText="",
            presence=ScenePresenceDecision(status="unknown", reason="", sourceText=None),
            sceneFacts={},
            sceneSeeds={},
            sourceRefs=[],
            evidenceId=None,
        )

    def _build_scene_director_beats(
        self,
        profile: NarrativeProfileResponse,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
        angle: str | None,
    ) -> SceneDirectorBeats:
        target_label = target.label if target else "互動對象"
        card_angle = card.angle if card else angle
        actor_aliases = self._actor_label_aliases(profile)
        candidate_cards = self._scene_seed_candidate_cards(profile, card, target, angle)
        source_refs = self._scene_seed_source_refs(candidate_cards, target)
        focused_source_refs = self._focus_scene_source_refs(source_refs or (card.sourceRefs if card else []))
        source_text_parts = [
            self._card_source_text(candidate)
            for candidate in candidate_cards
            if not focused_source_refs or (set(candidate.sourceRefs) & set(focused_source_refs))
        ]
        keyword_source_text = self._keyword_context_for_scene(profile, focused_source_refs, target)
        raw_source_text = self._merge_scene_source_text([keyword_source_text, *source_text_parts], max_chars=2200)
        modern_source_brief = self._source_modern_scene_brief(profile, target, focused_source_refs, raw_source_text)
        source_text = self._merge_scene_source_text([modern_source_brief, raw_source_text], max_chars=2400)
        primary_source_text = self._merge_scene_source_text(
            [modern_source_brief, keyword_source_text, self._card_source_text(card), source_text],
            max_chars=2400,
        )
        presence = self._infer_scene_presence(target_label, [primary_source_text] if primary_source_text else source_text_parts)
        scene_text = self._first_clean_scene_seed(candidate_cards, target, actor_aliases)
        contextual_scene_text = self._source_derived_scene_text(card_angle, target, primary_source_text)
        if contextual_scene_text and self._seed_text_is_pair_grounded(contextual_scene_text, target, actor_aliases):
            scene_text = contextual_scene_text
        pair_grounded_scene = self._seed_text_is_pair_grounded(scene_text, target, actor_aliases)
        memory_seed = self._source_derived_memory_text(profile, card_angle, target, primary_source_text, scene_text)
        if (
            memory_seed
            and pair_grounded_scene
            and not self._seed_text_is_pair_grounded(memory_seed, target, actor_aliases)
            and self._seed_mentions_other_interaction_target(profile, target, memory_seed)
        ):
            memory_seed = ""
        memory_text = self._sentence_or_default(
            memory_seed,
            "",
            max_chars=120,
        )
        if not memory_text:
            memory_text = scene_text
        emotion_seed = self._source_derived_emotion_text(card_angle, target, presence, primary_source_text)
        if (
            emotion_seed
            and pair_grounded_scene
            and not self._seed_text_is_pair_grounded(emotion_seed, target, actor_aliases)
        ):
            emotion_seed = ""
        emotion_text = self._sentence_or_default(
            emotion_seed,
            "",
            max_chars=96,
        )
        dialogue_seed = self._first_actor_dialogue_seed(
            candidate_cards,
            actor_aliases=actor_aliases,
            target=target,
            allow_secondary=not self._should_keep_dialogue_on_primary(primary_source_text, target),
        )
        dialogue_text = self._sentence_or_default(
            dialogue_seed,
            "",
            max_chars=80,
        )
        intent_seed = self._source_derived_intent_text(card_angle, target, primary_source_text)
        if (
            intent_seed
            and pair_grounded_scene
            and not self._seed_text_is_pair_grounded(intent_seed, target, actor_aliases)
        ):
            intent_seed = ""
        intent_text = self._sentence_or_default(
            intent_seed,
            "",
            max_chars=88,
        )
        scene_text, memory_text, emotion_text, dialogue_text, intent_text = self._backfill_scene_director_seed_texts(
            profile=profile,
            target=target,
            angle=card_angle,
            source_text=primary_source_text,
            scene_text=scene_text,
            memory_text=memory_text,
            emotion_text=emotion_text,
            dialogue_text=dialogue_text,
            intent_text=intent_text,
        )
        scene_facts = self._build_scene_facts(
            profile=profile,
            card=card,
            target=target,
            candidate_cards=candidate_cards,
            angle=card_angle,
            source_text=primary_source_text,
            scene_text=scene_text,
            dialogue_text=dialogue_text,
            source_refs=focused_source_refs or source_refs,
        )
        scene_seeds = self._build_scene_six_seeds(
            profile=profile,
            target=target,
            scene_facts=scene_facts,
            memory_text=memory_text,
            emotion_text=emotion_text,
            dialogue_text=dialogue_text,
            intent_text=intent_text,
        )
        return SceneDirectorBeats(
            sceneText=scene_text,
            memoryText=memory_text,
            emotionText=emotion_text,
            dialogueText=dialogue_text,
            intentText=intent_text,
            presence=presence,
            sceneFacts=scene_facts,
            sceneSeeds=scene_seeds,
            sourceRefs=(focused_source_refs or source_refs)[:12],
            evidenceId=card.evidenceId if card else None,
        )

    def _scene_seed_candidate_cards(
        self,
        profile: NarrativeProfileResponse,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
        angle: str | None,
    ) -> list[NarrativeEvidenceCard]:
        if not target:
            return [card] if card else []
        target_refs = set(target.evidenceRefs)
        target_aliases = self._target_aliases_for_interaction(target)
        requested_angle = angle or (card.angle if card else None)
        primary_refs = set(card.sourceRefs or []) if card else set()
        allow_target_context_expansion = bool(card and card.sourceType == "runtime-relationship-edge")

        def score(candidate: NarrativeEvidenceCard) -> tuple[float, str]:
            candidate_refs = set(candidate.sourceRefs)
            source_text = self._card_source_text(candidate)
            value = float(candidate.confidence)
            if card and candidate.evidenceId == card.evidenceId:
                value += 1000.0
            if primary_refs and candidate_refs and self._source_refs_are_near(candidate_refs, primary_refs, radius=3):
                value += 42.0
            if candidate.angle == requested_angle:
                value += 70.0
            if target.targetId in candidate.relatedTargetIds:
                value += 60.0
            if candidate_refs & target_refs:
                value += 35.0 + min(len(candidate_refs & target_refs), 6)
            if any(alias and alias in source_text for alias in target_aliases):
                value += 28.0
            if self._extract_quoted_dialogue(source_text):
                value += 14.0
            if candidate.angle == "emotion" or target.femaleFocus:
                if re.search(r"夫人|垂淚|煩惱|追兵|家|阿斗|嫂", source_text):
                    value += 10.0
            return (-value, candidate.evidenceId)

        candidates: list[NarrativeEvidenceCard] = []
        seen: set[str] = set()
        for candidate in [card, *profile.evidenceCards]:
            if candidate is None or candidate.evidenceId in seen:
                continue
            candidate_refs = set(candidate.sourceRefs)
            source_text = self._card_source_text(candidate)
            mentions_target = any(alias and alias in source_text for alias in target_aliases)
            near_primary = bool(
                primary_refs
                and candidate_refs
                and self._source_refs_are_near(candidate_refs, primary_refs, radius=3)
            )
            if (
                primary_refs
                and candidate.evidenceId != (card.evidenceId if card else None)
                and not (candidate_refs & primary_refs)
                and not near_primary
            ):
                if not (
                    allow_target_context_expansion
                    and target.targetId in candidate.relatedTargetIds
                    and bool(candidate_refs & target_refs)
                    and mentions_target
                    and candidate.sourceType != "runtime-relationship-edge"
                ):
                    continue
            is_related = (
                candidate.evidenceId == (card.evidenceId if card else None)
                or (target.targetId in candidate.relatedTargetIds and mentions_target)
                or (bool(candidate_refs & target_refs) and mentions_target)
                or (near_primary and (mentions_target or target.targetId in candidate.relatedTargetIds))
                or mentions_target
            )
            if not is_related:
                continue
            seen.add(candidate.evidenceId)
            candidates.append(candidate)
        return sorted(candidates, key=score)[:10]

    def _parse_source_ref_position(self, source_ref: str) -> tuple[str, int] | None:
        match = re.match(r"^(\d{3})#p(\d+)$", str(source_ref or "").strip())
        if not match:
            return None
        chapter_id, paragraph_index_text = match.groups()
        try:
            return chapter_id, int(paragraph_index_text)
        except ValueError:
            return None

    def _source_refs_are_near(self, left_refs: set[str] | list[str], right_refs: set[str] | list[str], radius: int = 2) -> bool:
        left_positions = [pos for ref in left_refs if (pos := self._parse_source_ref_position(ref))]
        right_positions = [pos for ref in right_refs if (pos := self._parse_source_ref_position(ref))]
        for left_chapter, left_index in left_positions:
            for right_chapter, right_index in right_positions:
                if left_chapter == right_chapter and abs(left_index - right_index) <= radius:
                    return True
        return False

    def _merge_scene_source_text(self, parts: list[str], max_chars: int = 1600) -> str:
        merged: list[str] = []
        for part in parts:
            text = " ".join(str(part or "").split()).strip()
            if not text:
                continue
            if any(text == item or text in item for item in merged):
                continue
            merged.append(text)
        return " ".join(merged)[:max_chars].strip()

    def _keyword_context_for_scene(
        self,
        profile: NarrativeProfileResponse,
        source_refs: list[str],
        target: NarrativeInteractionTarget | None,
    ) -> str:
        ref_set = {str(ref or "").strip() for ref in source_refs if str(ref or "").strip()}
        if not ref_set or not isinstance(profile.keywords, dict):
            return ""
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        matches: list[str] = []
        for options in profile.keywords.values():
            if not isinstance(options, list):
                continue
            for option in options:
                if not isinstance(option, dict):
                    continue
                option_refs = {str(ref or "").strip() for ref in option.get("sourceRefs") or [] if str(ref or "").strip()}
                if not option_refs:
                    continue
                if not (option_refs & ref_set) and not self._source_refs_are_near(option_refs, ref_set, radius=2):
                    continue
                text = " ".join(
                    str(value or "").strip()
                    for value in [option.get("fullLabel"), option.get("label")]
                    if str(value or "").strip()
                )
                if not text:
                    continue
                if target_aliases and not any(alias and alias in text for alias in target_aliases):
                    continue
                if re.search(r"\b[a-zA-Z_][a-zA-Z0-9_:\-.]{4,}\b", text):
                    continue
                if text not in matches:
                    matches.append(text)
                if len(matches) >= 4:
                    break
            if len(matches) >= 4:
                break
        return self._merge_scene_source_text(matches, max_chars=500)

    def _source_modern_scene_brief(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        source_refs: list[str],
        source_text: str,
    ) -> str:
        _ = (profile, target, source_refs, source_text)
        return ""

    def _scene_seed_source_refs(
        self,
        candidate_cards: list[NarrativeEvidenceCard],
        target: NarrativeInteractionTarget | None,
    ) -> list[str]:
        refs: list[str] = []
        for candidate in candidate_cards:
            refs.extend(candidate.sourceRefs)
        if not refs and target:
            refs.extend(target.evidenceRefs)
        return list(dict.fromkeys(ref for ref in refs if ref))[:12]

    def _focus_scene_source_refs(self, source_refs: list[str]) -> list[str]:
        refs = list(dict.fromkeys(str(ref or "").strip() for ref in source_refs if str(ref or "").strip()))
        special_refs = [ref for ref in refs if self._special_source_window_refs(ref)]
        if special_refs:
            return special_refs
        return refs

    def _card_source_text(self, card: NarrativeEvidenceCard | None) -> str:
        if not card:
            return ""
        summary = str(card.summary or "").strip()
        if re.search(r"stableKnowledgeBootstrap|targetId|contextKey|angle=", summary, re.I):
            summary = ""
        source_context = self._source_context_for_refs(card.sourceRefs, max_refs=1)
        return " ".join(
            part
            for part in [
                summary,
                str(card.quote or "").strip(),
                str(card.title or "").strip(),
                str(card.location or "").strip() if card.location else "",
                source_context,
            ]
            if part
        )

    def _source_context_for_refs(self, source_refs: list[str], max_refs: int = 2) -> str:
        paragraphs: list[str] = []
        limit = max_refs
        if any(self._special_source_window_refs(source_ref) for source_ref in source_refs):
            limit = max(limit, 10)
        for source_ref in source_refs:
            for paragraph in self._source_ref_context_window(source_ref):
                if paragraph and paragraph not in paragraphs:
                    paragraphs.append(paragraph)
                if len(paragraphs) >= limit:
                    break
            if len(paragraphs) >= limit:
                break
        return " ".join(paragraphs[:limit])

    def _source_ref_context_window(self, source_ref: str, radius: int = 1) -> list[str]:
        special_refs = self._special_source_window_refs(source_ref)
        if special_refs:
            result: list[str] = []
            for ref in special_refs:
                for paragraph in self._source_ref_context_window(ref, radius=0):
                    if paragraph and paragraph not in result:
                        result.append(paragraph)
            return result
        baihua_context = self._baihua_context_window(source_ref, radius=radius)
        if baihua_context:
            return baihua_context
        match = re.match(r"^(\d{3})#p(\d+)$", str(source_ref or "").strip())
        if not match:
            return []
        chapter_id, paragraph_index_text = match.groups()
        try:
            paragraph_index = int(paragraph_index_text)
        except ValueError:
            return []
        paragraphs = self._chapter_paragraphs(chapter_id)
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            return self._source_event_packet_examples(source_ref)
        indexes = [paragraph_index]
        for offset in range(1, radius + 1):
            indexes.extend([paragraph_index - offset, paragraph_index + offset])
        result: list[str] = []
        for index in indexes:
            if index < 0 or index >= len(paragraphs):
                continue
            paragraph = paragraphs[index]
            if paragraph.startswith("#"):
                continue
            result.append(paragraph)
        if result:
            return result
        return self._source_event_packet_examples(source_ref)

    def _source_ref_paragraph(self, source_ref: str) -> str:
        special_refs = self._special_source_window_refs(source_ref)
        if special_refs:
            return self._source_ref_paragraph(special_refs[0])
        baihua = self._baihua_passage_text(source_ref)
        if baihua:
            return baihua
        match = re.match(r"^(\d{3})#p(\d+)$", str(source_ref or "").strip())
        if not match:
            return ""
        chapter_id, paragraph_index_text = match.groups()
        try:
            paragraph_index = int(paragraph_index_text)
        except ValueError:
            return ""
        paragraphs = self._chapter_paragraphs(chapter_id)
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            examples = self._source_event_packet_examples(source_ref)
            return examples[0] if examples else ""
        paragraph = paragraphs[paragraph_index]
        if paragraph.startswith("#"):
            return ""
        return paragraph

    def _special_source_window_refs(self, source_ref: str) -> tuple[str, ...]:
        normalized = str(source_ref or "").strip()
        if not normalized:
            return ()
        return SPECIAL_SOURCE_REF_WINDOWS.get(normalized, ())

    def _source_event_packets_by_ref(self) -> dict[str, dict[str, Any]]:
        if self._source_event_packet_cache is not None:
            return self._source_event_packet_cache
        path = self.repo_root / DEFAULT_SOURCE_EVENT_PACKET_PATH
        packets: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    if not line.strip():
                        continue
                    packet = json.loads(line)
                    source_ref = str(packet.get("sourceRef") or "").strip()
                    if source_ref:
                        packets[source_ref] = packet
            except (OSError, json.JSONDecodeError):
                packets = {}
        self._source_event_packet_cache = packets
        return packets

    def _source_event_packet_examples(self, source_ref: str) -> list[str]:
        packet = self._source_event_packets_by_ref().get(str(source_ref or "").strip())
        if not packet:
            return []
        examples = packet.get("examples") if isinstance(packet, dict) else []
        if not isinstance(examples, list):
            return []
        return [" ".join(str(example or "").split()).strip() for example in examples if str(example or "").strip()]

    def _baihua_passages_by_ref(self) -> dict[tuple[str, int], str]:
        if self._baihua_passage_cache is not None:
            return self._baihua_passage_cache
        path = self.repo_root / DEFAULT_BAIHUA_PASSAGE_PATH
        passages: dict[tuple[str, int], str] = {}
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    locator = str(item.get("locator") or "")
                    match = re.search(r"chapter-(\d{3})#p(\d+)", locator)
                    if not match:
                        continue
                    chapter_id, paragraph_index_text = match.groups()
                    text = " ".join(str(item.get("normalizedText") or "").split()).strip()
                    if not text or text.startswith("#"):
                        continue
                    passages[(chapter_id, int(paragraph_index_text))] = text
            except (OSError, json.JSONDecodeError, ValueError):
                passages = {}
        self._baihua_passage_cache = passages
        return passages

    def _baihua_passage_text(self, source_ref: str) -> str:
        position = self._parse_source_ref_position(source_ref)
        if not position:
            return ""
        chapter_id, paragraph_index = position
        return self._baihua_passages_by_ref().get((chapter_id, paragraph_index), "")

    def _baihua_context_window(self, source_ref: str, radius: int = 1) -> list[str]:
        position = self._parse_source_ref_position(source_ref)
        if not position:
            return []
        chapter_id, paragraph_index = position
        passages = self._baihua_passages_by_ref()
        indexes = range(max(0, paragraph_index - radius), paragraph_index + radius + 1)
        result = [passages[(chapter_id, index)] for index in indexes if (chapter_id, index) in passages]
        return list(dict.fromkeys(result))

    def _chapter_paragraphs(self, chapter_id: str) -> list[str]:
        cached = self._chapter_paragraph_cache.get(chapter_id)
        if cached is not None:
            return cached
        path = self.repo_root / DEFAULT_CHAPTER_ROOT / f"{chapter_id}.md"
        if not path.exists():
            self._chapter_paragraph_cache[chapter_id] = []
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self._chapter_paragraph_cache[chapter_id] = []
            return []
        paragraphs = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        self._chapter_paragraph_cache[chapter_id] = paragraphs
        return paragraphs

    def _first_clean_scene_seed(
        self,
        candidate_cards: list[NarrativeEvidenceCard],
        target: NarrativeInteractionTarget | None,
        actor_aliases: list[str],
    ) -> str:
        options: list[tuple[float, str]] = []
        primary_options: list[tuple[float, str]] = []
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        for index, candidate in enumerate(candidate_cards):
            for value in [candidate.summary, candidate.quote, self._source_context_for_refs(candidate.sourceRefs), candidate.title]:
                cleaned = self._clean_source_fragment(str(value or ""), target, actor_aliases, max_chars=120)
                if cleaned:
                    source_text = self._card_source_text(candidate)
                    score = float(candidate.confidence) + max(0, 120 - index * 18)
                    if self._extract_quoted_dialogue(source_text):
                        score += 45.0
                    if any(alias and alias in cleaned for alias in target_aliases):
                        score += 30.0
                    if re.search(r"追兵|喊聲|垂淚|煩惱|心腹之言|如之奈何", cleaned):
                        score += 24.0
                    if re.match(r"^[雲肅瑜亮操權備飛羽]故意", cleaned):
                        score -= 18.0
                    option = (score, cleaned)
                    if index == 0:
                        primary_options.append(option)
                    options.append(option)
        if primary_options:
            return sorted(primary_options, key=lambda item: (-item[0], item[1]))[0][1]
        if not options:
            return ""
        return sorted(options, key=lambda item: (-item[0], item[1]))[0][1]

    def _first_dialogue_seed(
        self,
        candidate_cards: list[NarrativeEvidenceCard],
        allow_secondary: bool = True,
    ) -> str:
        for index, candidate in enumerate(candidate_cards):
            if index > 0 and not allow_secondary:
                break
            for value in [candidate.quote, candidate.summary]:
                dialogue = self._extract_quoted_dialogue(str(value or ""))
                if dialogue:
                    return dialogue
        return ""

    def _first_actor_dialogue_seed(
        self,
        candidate_cards: list[NarrativeEvidenceCard],
        actor_aliases: list[str],
        target: NarrativeInteractionTarget | None,
        allow_secondary: bool = True,
    ) -> str:
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        scored: list[tuple[float, str]] = []
        for index, candidate in enumerate(candidate_cards):
            if index > 0 and not allow_secondary:
                break
            for text in [self._source_context_for_refs(candidate.sourceRefs, max_refs=1), candidate.quote, candidate.summary]:
                scored.extend(
                    self._extract_actor_dialogue_candidates(
                        text=str(text or ""),
                        actor_aliases=actor_aliases,
                        target_aliases=target_aliases,
                        source_index=index,
                    )
                )
        if not scored:
            return ""
        return sorted(scored, key=lambda item: (-item[0], item[1]))[0][1]

    def _extract_actor_dialogue_candidates(
        self,
        text: str,
        actor_aliases: list[str],
        target_aliases: list[str],
        source_index: int,
    ) -> list[tuple[float, str]]:
        source = str(text or "")
        if not source or not actor_aliases:
            return []
        speaker_pattern = "|".join(re.escape(alias) for alias in actor_aliases if alias)
        if not speaker_pattern:
            return []
        candidates: list[tuple[float, str]] = []
        target_bridge_pattern = "|".join(re.escape(alias.replace(" ", "")) for alias in target_aliases if alias)
        for match in re.finditer(rf"({speaker_pattern})([^。！？「」]{{0,12}})(?:曰|道|告|泣告)[:：]?[「『]([^」』]{{2,140}})[」』]", source):
            bridge = str(match.group(2) or "")
            if "之" in bridge or re.search(r"其人|聞.*答|小童|童子|水鏡|先生", bridge):
                continue
            bridge_compact = re.sub(r"[，,、：:\s]", "", bridge)
            bridge_is_plain = bool(re.fullmatch(r"(?:又|復)?(?:問|答|告|謂|笑|泣|大叫|低聲)?", bridge_compact))
            bridge_addresses_target = bool(
                target_bridge_pattern
                and re.fullmatch(rf"(?:又|復)?(?:告|謂|問)(?:{target_bridge_pattern})", bridge_compact)
            )
            if not (bridge_is_plain or bridge_addresses_target):
                continue
            line = " ".join(match.group(3).split()).strip()
            if not line:
                continue
            sentence_start = max(0, source.rfind("。", 0, match.start()) + 1)
            sentence_end_candidates = [
                index
                for index in [
                    source.find("。", match.end()),
                    source.find("！", match.end()),
                    source.find("？", match.end()),
                ]
                if index != -1
            ]
            sentence_end = min(sentence_end_candidates) + 1 if sentence_end_candidates else min(len(source), match.end() + 80)
            near_sentence = source[sentence_start:sentence_end]
            if target_aliases and not any(alias and alias in near_sentence for alias in target_aliases):
                continue
            score = 100.0 - source_index * 18
            if target_aliases and any(alias and alias in near_sentence for alias in target_aliases):
                score += 35.0
            if re.search(r"夫人既知|備安敢相瞞|備欲不去|欲去，又捨不得夫人|荊州有失", line):
                score += 48.0
            if re.search(r"夫人|荊州|欲去|捨不得|煩惱|心腹之言|如之奈何|生死難忘", line):
                score += 22.0
            if re.search(r"切勿漏泄|若如此", line):
                score -= 42.0
            if re.search(r"有甚事|你且暫退|必須與夫人商議", line):
                score -= 30.0
            candidates.append((score, line))
        return candidates

    def _build_scene_facts(
        self,
        profile: NarrativeProfileResponse,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
        candidate_cards: list[NarrativeEvidenceCard],
        angle: str | None,
        source_text: str,
        scene_text: str,
        dialogue_text: str,
        source_refs: list[str],
    ) -> dict[str, Any]:
        target_by_id = {item.targetId: item for item in profile.interactionTargets}
        people: list[dict[str, str | None]] = [
            {"id": profile.generalId, "label": profile.displayName, "role": "主角"}
        ]
        seen_people = {profile.generalId}
        if target:
            people.append({"id": target.targetId, "label": target.label, "role": target.role})
            seen_people.add(target.targetId)
        for candidate in candidate_cards:
            for related_id in candidate.relatedTargetIds:
                if related_id in seen_people:
                    continue
                related_target = target_by_id.get(related_id)
                related_label = related_target.label if related_target else self._roster_name_for(related_id, self._load_roster_index())
                allow_family_titles = bool(
                    (related_target and (related_target.femaleFocus or self._is_female_gender(related_target.gender)))
                    or self._is_female_gender(self._roster_gender_for(related_id, self._load_roster_index()))
                )
                if not any(alias and alias in source_text for alias in self._target_label_aliases(related_label, allow_family_titles=allow_family_titles)):
                    continue
                people.append(
                    {
                        "id": related_id,
                        "label": related_label,
                        "role": related_target.role if related_target else "相關人物",
                    }
                )
                seen_people.add(related_id)
                if len(people) >= 8:
                    break
            if len(people) >= 8:
                break

        primary_locations = [card.location] if card and card.location else []
        related_locations = [
            candidate.location
            for candidate in candidate_cards
            if candidate.location and (not card or candidate.evidenceId != card.evidenceId)
        ]
        locations = self._unique_nonempty(
            [
                *primary_locations,
                *self._extract_location_markers(source_text),
                *related_locations,
            ],
            limit=6,
        )
        time_markers = self._unique_nonempty(self._extract_time_markers(source_text), limit=5)
        objects = self._unique_nonempty(self._extract_scene_objects(source_text), limit=8)
        event_text = self._ensure_sentence(
            scene_text or self._clean_source_fragment(source_text, target, self._actor_label_aliases(profile), max_chars=96)
        )
        return {
            "people": people,
            "event": event_text,
            "time": time_markers,
            "locations": locations,
            "objects": objects,
            "dialogue": dialogue_text,
            "angle": angle,
            "evidenceIds": [candidate.evidenceId for candidate in candidate_cards[:6]],
            "sourceRefs": source_refs[:10],
            "sourceText": source_text,
            "sourceBrief": self._source_modern_scene_brief(profile, target, source_refs, source_text),
        }

    def _unique_nonempty(self, values: list[str], limit: int) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = " ".join(str(value or "").split()).strip()
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) >= limit:
                break
        return result

    def _extract_time_markers(self, text: str) -> list[str]:
        return re.findall(
            r"(正談論間|正行間|正行之間|當夜|是夜|次日|是日|當日|五更|黎明|黃昏|既而|少頃|片刻|月餘|數月前|年終|歲旦|正旦|元旦|撤退時|亂軍中)",
            str(text or ""),
        )

    def _extract_location_markers(self, text: str) -> list[str]:
        source = str(text or "")
        markers: list[str] = []
        for literal in ["南徐", "東吳", "荊州", "新野", "古城", "芒碭山", "南漳", "莊外", "莊院", "草堂", "大溪", "江邊", "官道", "北門", "南門", "長阪坡", "長坂坡", "長阪", "長坂", "枯井", "景山"]:
            if literal in source:
                markers.append(literal)
        if re.search(r"正行間|正行之間|行間|行路", source):
            markers.append("行路途中")
        if re.search(r"追兵|喊聲大起|背後喊聲", source):
            markers.append("退路附近")
        if re.search(r"船中|船隻|船邊|渡口|津", source):
            markers.append("水路渡口")
        for match in re.findall(r"([\u4e00-\u9fff]{1,4}(?:江|津|城|寨|營|山|橋|關|郡|州|縣|渡|船中))", source):
            cleaned_match = re.sub(r"^.*已據", "", match)
            cleaned_match = re.sub(r"^.*(?:到|至|在|入|據)([\u4e00-\u9fff]{1,4}(?:江|津|城|寨|營|山|橋|關|郡|州|縣|渡|船中))$", r"\1", cleaned_match)
            if (
                len(cleaned_match) <= 6
                and not re.match(r"(只去|不想|殺奔|回|望|在|到|過|抹過|前面|使|龍|某夜來|可作速|恐有人|可乘|曹兵下|統眾將至|布上|倘|遂引兵攻)", cleaned_match)
                and not re.search(r"報說|說荊州|使荊州", cleaned_match)
            ):
                markers.append(cleaned_match)
        return markers

    def _extract_scene_objects(self, text: str) -> list[str]:
        source = str(text or "")
        rules = [
            ("荊州危急", r"荊州危急"),
            ("追兵", r"追兵"),
            ("喊聲", r"喊聲"),
            ("古城", r"古城"),
            ("錦囊", r"錦囊"),
            ("府堂", r"府堂"),
            ("官道", r"官道"),
            ("二嫂", r"二嫂|嫂嫂|二夫人"),
            ("縣印", r"縣印"),
            ("蛇矛", r"蛇矛|丈八蛇矛"),
            ("重圍", r"重圍|圍"),
            ("幼主", r"阿斗|阿鬥|幼主|小主人|後主|孩子|孩兒"),
            ("枯井", r"枯井"),
            ("後軍", r"另領一軍在後|軍在後|在後"),
            ("蔡瑁設謀", r"蔡瑁設謀|蔡瑁"),
            ("躍馬檀溪", r"檀溪|躍馬過溪|躍過檀溪"),
            ("車駕", r"車|推車"),
            ("船隻", r"船|船隻"),
            ("兵馬", r"兵|軍|人馬"),
            ("劍", r"劍"),
            ("馬", r"馬"),
            ("家眷", r"夫人|家眷|阿斗|阿鬥|嫂"),
        ]
        return [label for label, pattern in rules if re.search(pattern, source)]

    def _infer_scene_presence(self, target_label: str, text_parts: list[str]) -> ScenePresenceDecision:
        joined = " ".join(part for part in text_parts if part).strip()
        if not target_label or target_label not in joined:
            return ScenePresenceDecision(status="unknown", reason="原文沒有直接寫出同場動作", sourceText=joined[:120] or None)
        index = joined.find(target_label)
        near_target = joined[max(0, index - 36) : index + len(target_label) + 54]
        present_signals = re.compile(r"(見|謂|問|曰|坐|同|迎|至|入|引|救|護|追|戰|拜|會)")
        offstage_signals = re.compile(r"(聞|使|遣|報|書|召|念|思|失散|不見)")
        if present_signals.search(near_target):
            return ScenePresenceDecision(status="present", reason="原文附近有同場動作", sourceText=near_target)
        if offstage_signals.search(near_target):
            return ScenePresenceDecision(status="offstage", reason="原文附近偏向傳聞或不在場線索", sourceText=near_target)
        return ScenePresenceDecision(status="unknown", reason="原文提到對象，但同場狀態不明", sourceText=near_target)

    def _backfill_scene_director_seed_texts(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        angle: str | None,
        source_text: str,
        scene_text: str,
        memory_text: str,
        emotion_text: str,
        dialogue_text: str,
        intent_text: str,
    ) -> tuple[str, str, str, str, str]:
        source_summary = self._best_scene_source_summary(profile, target, source_text)
        if not scene_text or self._is_weak_scene_seed_text(scene_text):
            scene_text = source_summary
        if not memory_text or self._is_weak_scene_seed_text(memory_text):
            memory_text = scene_text or source_summary
        emotion_text = emotion_text or ""
        dialogue_text = dialogue_text or ""
        intent_text = intent_text or ""
        return (
            self._seed_display_sentence(profile, scene_text, 120),
            self._seed_display_sentence(profile, memory_text, 120),
            self._seed_display_sentence(profile, emotion_text, 96),
            self._seed_display_sentence(profile, dialogue_text, 80),
            self._seed_display_sentence(profile, intent_text, 88),
        )

    def _enrich_scene_director_beats_from_story(
        self,
        profile: NarrativeProfileResponse,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> SceneDirectorBeats:
        story = str(story_text or "").strip()
        if not story:
            return beats
        emotion_text = str(beats.emotionText or "").strip() or self._story_derived_emotion_text(story)
        dialogue_text = str(beats.dialogueText or "").strip() or self._story_derived_dialogue_text(story)
        intent_text = str(beats.intentText or "").strip() or self._story_derived_intent_text(story)
        scene_seeds = dict(beats.sceneSeeds or {})
        if emotion_text and not str(scene_seeds.get("emotion") or "").strip():
            scene_seeds["emotion"] = self._clean_seed_text(emotion_text, max_chars=90)
        updates = {
            "emotionText": self._seed_display_sentence(profile, emotion_text, 96) if emotion_text else str(beats.emotionText or ""),
            "dialogueText": self._seed_display_sentence(profile, dialogue_text, 80) if dialogue_text else str(beats.dialogueText or ""),
            "intentText": self._seed_display_sentence(profile, intent_text, 88) if intent_text else str(beats.intentText or ""),
            "sceneSeeds": scene_seeds,
        }
        return beats.model_copy(update=updates)

    def _seed_display_sentence(self, profile: NarrativeProfileResponse, text: str, max_chars: int) -> str:
        value = self._replace_actor_aliases_in_seed(profile, text)
        return self._sentence_or_default(value, "", max_chars=max_chars)

    def _story_derived_dialogue_text(self, story_text: str) -> str:
        quote = self._extract_quoted_dialogue(story_text)
        return self._clean_seed_text(quote, max_chars=80) if quote else ""

    def _story_derived_emotion_text(self, story_text: str) -> str:
        emotion_terms = (
            "心急",
            "焦急",
            "牽掛",
            "擔憂",
            "憂",
            "不安",
            "煩惱",
            "悲",
            "懼",
            "慌",
            "慰藉",
            "沉著",
            "堅定",
            "放不下",
            "掛念",
            "憂慮",
        )
        best = ""
        best_score = -1.0
        for clause in self._story_seed_clauses(story_text, max_chars=96):
            score = 0.0
            for term in emotion_terms:
                if term in clause:
                    score += 12.0
            if "心" in clause:
                score += 4.0
            if len(clause) <= 28:
                score += 2.0
            if score > best_score:
                best = clause
                best_score = score
        return best if best_score > 0 else ""

    def _story_derived_intent_text(self, story_text: str) -> str:
        intent_terms = (
            "必須",
            "要",
            "先",
            "設法",
            "盡快",
            "商議",
            "安排",
            "收攏",
            "保護",
            "接應",
            "找到",
            "尋回",
            "守住",
            "撤",
            "退",
        )
        best = ""
        best_score = -1.0
        for clause in self._story_seed_clauses(story_text, max_chars=88):
            score = 0.0
            for term in intent_terms:
                if term in clause:
                    score += 10.0
            if clause.startswith(("先", "必須", "要")):
                score += 6.0
            if "如何" in clause:
                score += 4.0
            if len(clause) <= 30:
                score += 2.0
            if score > best_score:
                best = clause
                best_score = score
        return best if best_score > 0 else ""

    def _story_seed_clauses(self, story_text: str, max_chars: int) -> list[str]:
        values: list[str] = []
        for sentence in self._split_source_sentences(story_text):
            for raw_clause in re.split(r"[，；;、]", sentence):
                clause = self._clean_seed_text(raw_clause, max_chars=max_chars)
                clause = clause.strip("。！？!? ，、；：")
                if len(clause) < 4 or self._contains_internal_symbolic_token(clause):
                    continue
                if clause not in values:
                    values.append(clause)
        return values

    def _is_weak_scene_seed_text(self, text: str) -> bool:
        value = str(text or "").strip("。！？!? ，、；：")
        if not value:
            return True
        if value.endswith(("欲", "不", "未", "曰", "說", "問", "見", "到")):
            return True
        if "..." in value or value.endswith("…"):
            return True
        return False

    def _replace_actor_aliases_in_seed(self, profile: NarrativeProfileResponse, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        for alias in self._actor_label_aliases(profile):
            if not alias:
                continue
            value = re.sub(rf"(?<![\u4e00-\u9fff]){re.escape(alias)}(?![\u4e00-\u9fff])", "此人", value)
            value = value.replace(alias, "此人")
        value = re.sub(r"此人此人+", "此人", value)
        value = re.sub(r"^\s*此人[，、]\s*", "", value)
        value = value.replace("\u70ba\u6b64\u4eba\u722d\u53d6", "\u722d\u53d6")
        value = value.replace("\u70ba\u6b64\u4eba", "")
        value = value.replace("\u66ff\u6b64\u4eba", "")
        return " ".join(value.split()).strip()

    def _best_scene_source_summary(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        source_text: str,
    ) -> str:
        actor_aliases = self._actor_label_aliases(profile)
        cleaned = self._clean_source_fragment(source_text, target, actor_aliases, max_chars=130)
        if cleaned and not self._is_weak_scene_seed_text(cleaned):
            return cleaned
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        candidates: list[tuple[int, str]] = []
        for sentence in self._split_source_sentences(source_text):
            normalized = self._normalize_source_sentence(sentence, target, actor_aliases)
            if not self._is_readable_source_sentence(normalized):
                continue
            score = 0
            if target_aliases and any(alias and alias in normalized for alias in target_aliases):
                score += 30
            if actor_aliases and any(alias and alias in normalized for alias in actor_aliases):
                score += 20
            if re.search(r"救|護|戰|圍|追|危|急|遣|使|領|見|問|曰|說", normalized):
                score += 12
            if re.search(r"stableKnowledgeBootstrap|^[a-zA-Z_]", normalized):
                score -= 40
            candidates.append((score, normalized))
        if not candidates:
            return ""
        return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1]

    def _extract_quoted_dialogue(self, quote: str) -> str:
        text = str(quote or "").strip()
        if not text:
            return ""
        matches = re.findall(r"[「『]([^」』]{2,80})[」』]", text)
        return matches[0].strip() if matches else ""

    def _actor_label_aliases(self, profile: NarrativeProfileResponse) -> list[str]:
        seeds = [profile.displayName]
        persona = profile.persona if isinstance(profile.persona, dict) else {}
        for key in ("aliases", "nameAliases"):
            value = persona.get(key)
            if isinstance(value, list):
                seeds.extend(str(item) for item in value if str(item).strip())
            elif value:
                seeds.append(str(value))
        aliases: list[str] = []
        for seed in seeds:
            for alias in self._target_label_aliases(seed, allow_family_titles=False):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return sorted(aliases, key=len, reverse=True)

    def _clean_source_fragment(
        self,
        text: str,
        target: NarrativeInteractionTarget | None,
        actor_aliases: list[str],
        max_chars: int,
    ) -> str:
        sentences = [
            self._normalize_source_sentence(sentence, target, actor_aliases)
            for sentence in self._split_source_sentences(text)
        ]
        readable = [(index, sentence) for index, sentence in enumerate(sentences) if self._is_readable_source_sentence(sentence)]
        if not readable:
            return ""
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        target_indexes = [
            index
            for index, sentence in readable
            if target_aliases and any(alias and alias in sentence for alias in target_aliases)
        ]
        actor_indexes = [
            index
            for index, sentence in readable
            if actor_aliases and any(alias and alias in sentence for alias in actor_aliases)
        ]
        joint_indexes = [index for index in target_indexes if index in set(actor_indexes)]
        selected: list[str] = []
        if joint_indexes:
            target_index = joint_indexes[0]
            previous = next((sentence for index, sentence in reversed(readable) if index < target_index), "")
            if previous and len(previous) <= 42 and not re.match(r"^[雲肅瑜亮操權備飛羽].{0,8}(故意|催|逼|曰|問|謂)", previous):
                selected.append(previous)
            selected.append(next(sentence for index, sentence in readable if index == target_index))
        elif actor_indexes:
            actor_index = actor_indexes[0]
            selected.append(next(sentence for index, sentence in readable if index == actor_index))
        elif target_indexes:
            target_index = target_indexes[0]
            selected.append(next(sentence for index, sentence in readable if index == target_index))
        else:
            selected.append(readable[0][1])
        return self._sentence_or_default("".join(selected), "", max_chars=max_chars)

    def _split_source_sentences(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", "", str(text or ""))
        normalized = re.sub(r"^(原文|線索|摘要)[:：]", "", normalized)
        if not normalized:
            return []
        matches = list(re.finditer(r"[^。！？!?]+[。！？!?][」』]?", normalized))
        sentences = [match.group(0).strip() for match in matches]
        tail_start = matches[-1].end() if matches else 0
        tail = normalized[tail_start:].strip("，、；：")
        if len(tail) >= 8 and self._has_balanced_quotes(tail):
            sentences.append(self._ensure_sentence(tail))
        return sentences

    def _normalize_source_sentence(
        self,
        sentence: str,
        target: NarrativeInteractionTarget | None,
        actor_aliases: list[str],
    ) -> str:
        normalized = str(sentence or "").strip()
        if not normalized:
            return ""
        for alias in actor_aliases:
            if alias:
                normalized = re.sub(rf"^{re.escape(alias)}", "", normalized)
        normalized = re.sub(r"^[又復]?(告|謂|問)([^曰：]{1,12})曰[:：]?", r"向\2說：", normalized)
        normalized = re.sub(r"^曰[:：]?", "", normalized)
        normalized = re.sub(r"^[，,、；：\s]+", "", normalized)
        normalized = normalized.lstrip("「『」』“”‘’\"' ")
        if target:
            for alias in self._target_aliases_for_interaction(target):
                if alias and normalized.startswith(f"{alias}曰"):
                    normalized = normalized.replace(f"{alias}曰", f"{alias}說", 1)
                    break
        return normalized

    def _is_readable_source_sentence(self, sentence: str) -> bool:
        normalized = str(sentence or "").strip()
        if not normalized:
            return False
        if re.search(r"stableKnowledgeBootstrap|targetId|evidenceId|contextKey|angle=", normalized, re.I):
            return False
        if normalized.count("「") != normalized.count("」") or normalized.count("『") != normalized.count("』"):
            return False
        core = re.sub(r"[。！？!?，「」『』：；、\s]", "", normalized)
        if len(core) < 6:
            return False
        if re.fullmatch(r"[\u4e00-\u9fff]{1,4}(夫人|氏)?[。！？!?]?", normalized):
            return False
        return True

    def _source_derived_scene_text(
        self,
        angle: str | None,
        target: NarrativeInteractionTarget | None,
        source_text: str,
    ) -> str:
        text = str(source_text or "")
        if not text or not target:
            return ""
        if angle not in {"emotion", "bond", "people", "rival", "battlefield", "resource", None}:
            return ""
        if not self._source_mentions_target(text, target):
            return ""
        return self._extract_target_source_excerpt(
            source_text=text,
            target=target,
            max_chars=120,
            include_previous=False,
            prefer_target_only=True,
        )

    def _source_derived_memory_text(
        self,
        profile: NarrativeProfileResponse,
        angle: str | None,
        target: NarrativeInteractionTarget | None,
        source_text: str,
        scene_text: str,
    ) -> str:
        text = str(source_text or "")
        if not text:
            return ""
        _ = angle
        excerpt = self._extract_memory_source_excerpt(
            profile=profile,
            target=target,
            source_text=text,
            scene_text=scene_text,
            max_chars=120,
        )
        if excerpt and excerpt != scene_text:
            return excerpt
        return ""

    def _should_keep_dialogue_on_primary(
        self,
        source_text: str,
        target: NarrativeInteractionTarget | None,
    ) -> bool:
        text = str(source_text or "")
        return bool(
            target
            and target.femaleFocus
            and self._source_mentions_target(text, target)
            and re.search(r"暗暗垂淚|垂淚|泣告|心腹之言", text)
        )

    def _source_mentions_target(self, source_text: str, target: NarrativeInteractionTarget) -> bool:
        text = str(source_text or "")
        return any(alias and alias in text for alias in self._target_aliases_for_interaction(target))

    def _source_target_label(self, source_text: str, target: NarrativeInteractionTarget) -> str:
        text = str(source_text or "")
        aliases = self._target_aliases_for_interaction(target)
        for alias in aliases:
            if alias and alias in text:
                return alias
        return target.label

    def _source_derived_emotion_text(
        self,
        angle: str | None,
        target: NarrativeInteractionTarget | None,
        presence: ScenePresenceDecision,
        source_text: str,
    ) -> str:
        _ = (angle, target, presence, source_text)
        return ""

    def _source_derived_intent_text(self, angle: str | None, target: NarrativeInteractionTarget | None, source_text: str) -> str:
        _ = (angle, target, source_text)
        return ""

    def _extract_memory_source_excerpt(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        source_text: str,
        scene_text: str,
        max_chars: int = 120,
    ) -> str:
        text = str(source_text or "").strip()
        if not text:
            return ""
        actor_aliases = self._actor_label_aliases(profile)
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        scene_compact = " ".join(str(scene_text or "").split()).strip("。！？!? ，、；：")
        options: list[tuple[float, str]] = []
        for raw_sentence in self._split_source_sentences(text):
            normalized = self._normalize_source_sentence(raw_sentence, target, actor_aliases)
            normalized = self._trim_target_reply_clause(normalized, target_aliases)
            if not self._is_readable_source_sentence(normalized):
                continue
            compact = " ".join(normalized.split()).strip("。！？!? ，、；：")
            if not compact or compact == scene_compact:
                continue
            if self._looks_like_target_reply_sentence(raw_sentence, target_aliases):
                continue
            score = 0.0
            if any(alias and alias in raw_sentence for alias in actor_aliases):
                score += 36.0
            if target_aliases and any(alias and alias in raw_sentence for alias in target_aliases):
                score += 18.0
            if re.search(r"入見|泣告|垂淚|說了情況|思量|尋|護|報來|商議|吩咐|勒住馬|慌忙|去找", normalized):
                score += 16.0
            if re.search(r"[「『]", raw_sentence):
                score -= 12.0
            if len(compact) <= 54:
                score += 6.0
            if re.search(r"丈夫何故|如何是好|怎麽辦|怎麼辦|何故煩惱", normalized):
                score -= 30.0
            options.append((score, normalized))
        if not options:
            return ""
        best = sorted(options, key=lambda item: (-item[0], item[1]))[0][1]
        if self._is_weak_scene_seed_text(best):
            return ""
        return self._sentence_or_default(best, "", max_chars=max_chars)

    def _trim_target_reply_clause(self, sentence: str, target_aliases: list[str]) -> str:
        value = str(sentence or "").strip()
        if not value:
            return ""
        cut_index: int | None = None
        for alias in target_aliases:
            if not alias:
                continue
            match = re.search(
                rf"[，,、；;。！？!?]\s*{re.escape(alias)}(?:曰|說|道|云|云曰|問|答|泣告|告)[:：]?",
                value,
            )
            if match and (cut_index is None or match.start() < cut_index):
                cut_index = match.start()
        if cut_index is not None:
            value = value[:cut_index]
        return value.lstrip("「『」』“”‘’\"' ，,、；;：")

    def _looks_like_target_reply_sentence(self, raw_sentence: str, target_aliases: list[str]) -> bool:
        sentence = str(raw_sentence or "").strip()
        if not sentence or not target_aliases:
            return False
        compact = re.sub(r"\s+", "", sentence)
        for alias in target_aliases:
            if not alias:
                continue
            if re.match(rf"^{re.escape(alias)}(?:曰|說|道|云|云曰|講|問|答|泣告|告)", compact):
                return True
            if re.match(rf"^[又復]?(?:告|謂|問){re.escape(alias)}(?:曰|說|道|云|講)?", compact):
                return True
        return False

    def _extract_target_source_excerpt(
        self,
        source_text: str,
        target: NarrativeInteractionTarget | None,
        max_chars: int = 120,
        include_previous: bool = True,
        prefer_target_only: bool = False,
        exclude_text: str = "",
    ) -> str:
        text = str(source_text or "").strip()
        if not text:
            return ""
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        sentences = [self._normalize_source_sentence(sentence, target, []) for sentence in self._split_source_sentences(text)]
        readable = [(index, sentence) for index, sentence in enumerate(sentences) if self._is_readable_source_sentence(sentence)]
        if not readable:
            return ""
        exclude = " ".join(str(exclude_text or "").split()).strip("。！？!? ，、；：")
        target_indexes = [
            index
            for index, sentence in readable
            if target_aliases and any(alias and alias in sentence for alias in target_aliases)
        ]
        candidate_indexes = target_indexes if target_indexes else ([] if prefer_target_only else [index for index, _ in readable])
        if not candidate_indexes:
            return ""
        selected: list[str] = []
        for candidate_index in candidate_indexes:
            current = next(sentence for index, sentence in readable if index == candidate_index)
            bundle: list[str] = []
            if include_previous:
                previous = next((sentence for index, sentence in reversed(readable) if index < candidate_index), "")
                previous_is_contextual = bool(
                    previous
                    and (
                        (target_aliases and any(alias and alias in previous for alias in target_aliases))
                        or self._extract_time_markers(previous)
                        or self._extract_location_markers(previous)
                        or re.search(r"先是|既而|是時|當時|次日|正旦|年終|長坂|古城|江邊|官道|南徐|莊外|重圍|追兵", previous)
                    )
                )
                if previous and len(previous) <= 42 and previous_is_contextual:
                    bundle.append(previous)
            bundle.append(current)
            excerpt = self._sentence_or_default("".join(bundle), "", max_chars=max_chars)
            compact = " ".join(str(excerpt or "").split()).strip("。！？!? ，、；：")
            if not compact or compact == exclude:
                continue
            selected.append(excerpt)
        if not selected:
            return ""
        return selected[0]

    def _build_scene_six_seeds(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        scene_facts: dict[str, Any],
        memory_text: str,
        emotion_text: str,
        dialogue_text: str,
        intent_text: str,
    ) -> dict[str, Any]:
        facts = scene_facts or {}
        people = self._scene_seed_people(profile, target, facts, extra_text=f"{memory_text} {intent_text}")
        objects = self._scene_seed_objects(facts)
        time_seed = self._scene_seed_time(facts)
        place_seed = self._scene_seed_place(facts, objects)
        event_seed = self._scene_seed_event(
            target=target,
            facts=facts,
            people=people,
            objects=objects,
            memory_text=memory_text,
            dialogue_text=dialogue_text,
            intent_text=intent_text,
        )
        return {
            "people": people,
            "event": event_seed,
            "time": time_seed,
            "place": place_seed,
            "objects": objects,
            "emotion": self._clean_seed_text(emotion_text, max_chars=90),
        }

    def _scene_seed_people(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        facts: dict[str, Any],
        extra_text: str = "",
    ) -> list[dict[str, str | None]]:
        result: list[dict[str, str | None]] = []
        seen: set[str] = set()

        def add(person_id: str | None, label: str | None, role: str | None) -> None:
            clean_label = str(label or "").strip()
            if not clean_label or clean_label in seen:
                return
            result.append({"id": person_id, "label": clean_label, "role": role})
            seen.add(clean_label)

        add(profile.generalId, profile.displayName, "主角")
        if target:
            add(target.targetId, target.label, target.role)
        fact_text = " ".join(str(value or "") for value in [facts.get("event"), facts.get("dialogue"), extra_text])
        for item in facts.get("people") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            if label and label not in {profile.displayName, target.label if target else ""} and label not in fact_text:
                continue
            add(
                str(item.get("id") or "") or None,
                label,
                str(item.get("role") or "") or None,
            )
            if len(result) >= 6:
                break
        return result[:6]

    def _scene_seed_objects(self, facts: dict[str, Any]) -> list[str]:
        raw_objects = [str(item).strip() for item in facts.get("objects") or [] if str(item).strip()]
        priority = [
            "荊州危急",
            "躍馬檀溪",
            "蔡瑁設謀",
            "古城",
            "二嫂",
            "蛇矛",
            "幼主",
            "枯井",
            "家眷",
            "追兵",
            "喊聲",
            "重圍",
            "後軍",
            "錦囊",
            "官道",
            "船隻",
            "車駕",
            "府堂",
            "劍",
        ]
        ordered = [item for item in priority if item in raw_objects]
        for item in raw_objects:
            if item in {"馬", "兵馬"} and not any(flag in raw_objects for flag in ["追兵", "喊聲"]):
                continue
            if item not in ordered:
                ordered.append(item)
        return ordered[:5]

    def _scene_seed_time(self, facts: dict[str, Any]) -> str:
        time_markers = [str(item).strip() for item in facts.get("time") or [] if str(item).strip()]
        if "正談論間" in time_markers:
            return "正談論間"
        if "月餘" in time_markers:
            return "據城月餘後"
        if "當日" in time_markers:
            return "當日"
        if "年終" in time_markers:
            return "年終前後"
        if "正旦" in time_markers:
            return "正旦前後"
        if "歲旦" in time_markers:
            return "歲旦前後"
        if "撤退時" in time_markers:
            return "撤退時"
        if "亂軍中" in time_markers:
            return "亂軍中"
        return time_markers[0] if time_markers else ""

    def _scene_seed_place(self, facts: dict[str, Any], objects: list[str]) -> str:
        locations = []
        for item in facts.get("locations") or []:
            location = str(item or "").strip()
            if not location:
                continue
            if re.search(r"報說|使荊州|龍報|不想|只去", location):
                continue
            if location == "入城":
                continue
            if "荊州危急" in objects and location == "荊州":
                continue
            locations.append(location)
        if "古城" in locations:
            return "古城"
        for location in ("長坂坡", "長阪坡", "長坂", "長阪"):
            if location in locations:
                return location
        if "枯井" in locations:
            return "枯井旁"
        if "躍馬檀溪" in objects:
            if "南漳" in locations and "莊外" in locations:
                return "南漳莊外"
            if "莊外" in locations:
                return "莊外"
        if "新野" in locations:
            return "新野"
        return locations[0] if locations else ""

    def _scene_seed_event(
        self,
        target: NarrativeInteractionTarget | None,
        facts: dict[str, Any],
        people: list[dict[str, str | None]],
        objects: list[str],
        memory_text: str,
        dialogue_text: str,
        intent_text: str,
    ) -> str:
        _ = (target, people, objects)
        for value in [facts.get("event"), memory_text, dialogue_text, intent_text]:
            seed = self._clean_seed_text(str(value or ""), max_chars=90)
            if seed and not self._is_weak_scene_seed_text(seed):
                return seed
        return ""

    def _clean_seed_text(self, text: str, max_chars: int = 96) -> str:
        value = " ".join(str(text or "").split()).strip()
        value = value.strip("。；，、 ")
        if len(value) <= max_chars:
            return value
        trimmed = value[:max_chars].rstrip("，、；： ")
        return trimmed

    def _scene_seed_text(self, beats: SceneDirectorBeats) -> str:
        pairs = [
            ("想起什麼", beats.memoryText),
            ("心裡怎麼變", beats.emotionText),
            ("對此人的一句話", beats.dialogueText),
            ("接下來想做什麼", beats.intentText),
        ]
        return "\n".join(f"{label}：{value}" for label, value in pairs if str(value or "").strip())

    def _build_scene_director_story_context(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, Any]:
        return {
            "saveId": f"demo-scene-director-{profile.generalId}",
            "shortTerm": beats.sceneText,
            "longTerm": beats.emotionText,
            "playerProfile": beats.dialogueText,
            "promises": beats.intentText,
        }

    def _build_scene_director_selected_context(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        render_mode: str,
    ) -> dict[str, Any]:
        context_key = self._scene_story_context_key(profile, target, card, beats, render_mode)
        return {
            "contextKey": context_key,
            "task": "scene-director-script",
            "renderMode": render_mode,
            "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
            "activeTarget": {
                "targetId": target.targetId if target else None,
                "label": target.label if target else None,
                "role": target.role if target else None,
            },
            "angle": card.angle if card else None,
            "directorSeed": self._scene_director_prompt_payload(profile, target, card, beats),
            "sceneSeeds": beats.sceneSeeds,
            "sceneFacts": beats.sceneFacts,
            "storyRoleGuard": {
                "mainActorIsNarrativeCenter": True,
                "mainActorOwnsSceneAction": True,
                "activeTargetIsCounterpart": bool(target),
                "doNotSwapMainActorAndTarget": True,
            },
            "sourceRefs": beats.sourceRefs,
        }

    def _scene_director_prompt_payload(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, Any]:
        seeds = beats.sceneSeeds or {}
        return {
            "mainActor": {"id": profile.generalId, "name": profile.displayName},
            "target": {
                "id": target.targetId if target else None,
                "name": target.label if target else None,
                "relationship": target.role if target else None,
            },
            "angle": card.angle if card else None,
            "people": seeds.get("people") or [],
            "event": seeds.get("event") or "",
            "time": seeds.get("time") or "",
            "place": seeds.get("place") or "",
            "objects": seeds.get("objects") or [],
            "emotion": seeds.get("emotion") or "",
            "sceneText": beats.sceneText,
            "memoryText": beats.memoryText,
            "emotionText": beats.emotionText,
            "dialogueText": beats.dialogueText,
            "intentText": beats.intentText,
        }

    def _scene_story_context_key(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        render_mode: str,
    ) -> str:
        payload = {
            "version": 3,
            "generalId": profile.generalId,
            "targetId": target.targetId if target else None,
            "targetRole": target.role if target else None,
            "angle": card.angle if card else None,
            "renderMode": render_mode,
            "sceneSeeds": beats.sceneSeeds or {},
            "beats": {
                "sceneText": beats.sceneText,
                "memoryText": beats.memoryText,
                "emotionText": beats.emotionText,
                "dialogueText": beats.dialogueText,
                "intentText": beats.intentText,
            },
            "sceneFacts": {
                "event": (beats.sceneFacts or {}).get("event") if isinstance(beats.sceneFacts, dict) else None,
                "dialogue": (beats.sceneFacts or {}).get("dialogue") if isinstance(beats.sceneFacts, dict) else None,
                "locations": (beats.sceneFacts or {}).get("locations") if isinstance(beats.sceneFacts, dict) else [],
                "time": (beats.sceneFacts or {}).get("time") if isinstance(beats.sceneFacts, dict) else [],
            },
            "sourceRefs": beats.sourceRefs,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"scene-story:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"

    def _scene_story_provider_config(self, llm_model_preset: str, render_mode: str) -> dict[str, Any]:
        preset_config = LLM_MODEL_PRESETS.get(llm_model_preset, LLM_MODEL_PRESETS[DEFAULT_LLM_MODEL_PRESET])
        base_order = list(preset_config.get("providerOrder") or self.provider_router.provider_order or [])
        provider_order = ["history_cache"]
        preferred = ["gemini_flash", "gemini_flash_lite", "gemini"] if render_mode == "data_first" else ["gemini_flash", "gemini", "gemini_flash_lite"]
        for provider_name in [*preferred, *base_order]:
            if provider_name in {"history_cache", "deterministic"}:
                continue
            if provider_name not in provider_order:
                provider_order.append(provider_name)
        model_overrides = dict(preset_config.get("modelOverrides") or {})
        model_overrides["__timeoutMs"] = "5000" if render_mode == "data_first" else "4500"
        model_overrides.setdefault("__retryCount", "1")
        return {
            "providerOrder": provider_order,
            "modelOverrides": model_overrides,
            "allowDeterministicFallback": False,
        }

    def _scene_story_keywords(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
    ) -> list[dict[str, Any]]:
        del profile
        keywords: list[dict[str, Any]] = []
        angle_label = str(card.angle if card else "").strip()
        if angle_label:
            keywords.append(
                {
                    "keywordKey": f"scene_angle.{angle_label}",
                    "category": "scene_angle",
                    "label": angle_label,
                    "sourceRefs": beats.sourceRefs[:4],
                }
            )
        if target and str(target.role or "").strip():
            keywords.append(
                {
                    "keywordKey": f"scene_target.{target.targetId}",
                    "category": "scene_target",
                    "label": target.label,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
            keywords.append(
                {
                    "keywordKey": f"scene_relationship.{target.targetId}.{hashlib.sha1(str(target.role).encode('utf-8')).hexdigest()[:10]}",
                    "category": "scene_relationship",
                    "label": target.role,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
        seeds = beats.sceneSeeds or {}
        for key, raw_value in [
            ("event", seeds.get("event")),
            ("place", seeds.get("place")),
            ("time", seeds.get("time")),
            ("emotion", seeds.get("emotion")),
        ]:
            label = str(raw_value or "").strip()
            if label:
                keywords.append(
                    {
                        "keywordKey": f"scene_seed.{key}.{hashlib.sha1(label.encode('utf-8')).hexdigest()[:10]}",
                        "category": "scene_seed",
                        "label": label,
                        "sourceRefs": beats.sourceRefs[:4],
                    }
                )
        return keywords[:6]

    def _build_scene_chorus_line(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> SceneChorusLine:
        persona_card = self.get_persona_card(target.targetId)
        speaker_context = self._speaker_persona_context(target, persona_card)
        cache_key = self._scene_chorus_cache_key(
            request,
            profile,
            target,
            main_target,
            card,
            beats,
            story_text,
            speaker_context,
        )
        with self._scene_chorus_cache_lock:
            cached = self._scene_chorus_cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(deep=True)

        evidence_refs = sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))
        selected_keywords = self._scene_chorus_keywords(profile, target, main_target, beats, speaker_context)
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        fallback_text = self._compose_scene_grounded_chorus_fallback(
            target=target,
            main_target=main_target,
            beats=beats,
            story_text=story_text,
            speaker_context=speaker_context,
        )
        prompt_payload = self._scene_chorus_prompt_payload(
            profile,
            target,
            main_target,
            beats,
            story_text,
            speaker_context,
            grounding_terms,
        )
        provider_config = self._scene_chorus_provider_config(request.llmModelPreset)
        generation = self._generate_scene_director_text(
            general_id=target.targetId,
            persona_card=persona_card,
            memory_context={
                "saveId": f"demo-chorus-{request.generalId}",
                "shortTerm": f"本幕短劇本：{story_text}",
                "longTerm": self._scene_seed_text(beats),
                "playerProfile": (
                    f"發話者與主角{profile.displayName}的關係：{target.role}。"
                    f"本幕互動對象：{main_target.label if main_target else '未指定'}。"
                    f"發話者人格資料：{self._speaker_persona_summary(speaker_context)}。"
                    f"發話時應著重：{self._speaker_persona_guidance(speaker_context, target)}。"
                    f"本幕必須對準的場景錨點：{'、'.join(grounding_terms[:6]) or '互動對象與當下動作'}。"
                ),
                "promises": (
                    "請以發話者視角說一句自然短對白；要讓 persona、關係與本幕短劇本共同決定語氣。"
                    "這句話必須直接對準互動對象，或本幕裡一個具體動作、地點、物件。"
                    "優先讓這句話先看見眼前發生了什麼，再帶出此人的判斷或情緒。"
                    "不要提到自己的名字；不要用旁白解釋資料；"
                    "不要回『先看證據、先說清楚、再談判斷、安住人心』這種可套到任何人的泛句。"
                    "不要把人格標籤、關係類型或內部欄位值直接拼成一句話。"
                ),
            },
            selected_context={
                "task": "chorus-line",
                **prompt_payload,
            },
            evidence_refs=evidence_refs,
            deterministic_text="",
            max_chars=request.maxChorusChars,
            locale=request.locale,
            llm_model_preset=request.llmModelPreset,
            speech_context_mode="inner_monologue",
            tone_mode="in-character",
            selected_keywords=selected_keywords,
            include_resolved_evidence=False,
            provider_order=provider_config["providerOrder"],
            model_overrides=provider_config["modelOverrides"],
            allow_deterministic_fallback=provider_config["allowDeterministicFallback"],
        )
        generation = self._repair_complete_generation(
            generation,
            fallback_text=fallback_text,
            max_chars=request.maxChorusChars,
            warning_code="scene_chorus_trimmed_to_complete_sentence",
        )
        generation = self._repair_chorus_generation(
            generation=generation,
            fallback_text=fallback_text,
            max_chars=request.maxChorusChars,
            target=target,
            speaker_context=speaker_context,
            main_target=main_target,
            beats=beats,
            story_text=story_text,
        )
        if generation.fallbackUsed:
            try:
                regenerated = self._rewrite_scene_chorus_from_fallback(
                    request=request,
                    profile=profile,
                    target=target,
                    main_target=main_target,
                    beats=beats,
                    story_text=story_text,
                    speaker_context=speaker_context,
                    selected_keywords=selected_keywords,
                    evidence_refs=evidence_refs,
                    fallback_text=fallback_text,
                )
            except Exception as exc:  # pragma: no cover - rewrite is best-effort
                log_debug_event(
                    "scene_director.chorus.rewrite_error",
                    targetId=target.targetId,
                    error=str(exc)[:240],
                )
                regenerated = None
            if regenerated is not None:
                generation = regenerated
        self._record_scene_chorus_history(
            request=request,
            target=target,
            context_key=str(prompt_payload.get("contextKey") or "").strip() or None,
            selected_keywords=selected_keywords,
            evidence_refs=evidence_refs,
            generation=generation,
        )
        line = SceneChorusLine(
            targetId=target.targetId,
            label=target.label,
            role=target.role,
            text=generation.text,
            provider=generation.provider,
            model=generation.model,
            fallbackUsed=generation.fallbackUsed,
            evidenceRefs=evidence_refs[:12],
        )
        with self._scene_chorus_cache_lock:
            self._scene_chorus_cache[cache_key] = line.model_copy(deep=True)
        return line

    def _build_scene_chorus_lines(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        targets: list[NarrativeInteractionTarget],
        main_target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        story_text: str,
        timeout_seconds: float | None = None,
    ) -> list[SceneChorusLine]:
        if not targets:
            return []
        def fallback_line(target: NarrativeInteractionTarget, reason: str) -> SceneChorusLine:
            evidence_refs = sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))
            persona_card = self.get_persona_card(target.targetId)
            speaker_context = self._speaker_persona_context(target, persona_card)
            log_debug_event(
                "scene_director.chorus.fallback",
                targetId=target.targetId,
                reason=reason,
            )
            return SceneChorusLine(
                targetId=target.targetId,
                label=target.label,
                role=target.role,
                text=self._compose_scene_grounded_chorus_fallback(
                    target=target,
                    main_target=main_target,
                    beats=beats,
                    story_text=story_text,
                    speaker_context=speaker_context,
                ),
                provider="unavailable",
                model=None,
                fallbackUsed=True,
                evidenceRefs=evidence_refs[:12],
            )

        if timeout_seconds is not None and timeout_seconds <= 0.05:
            return [fallback_line(target, "deadline-before-submit") for target in targets]
        results: list[SceneChorusLine | None] = [None] * len(targets)
        max_workers = min(4, len(targets))
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_map = {
                executor.submit(
                    self._build_scene_chorus_line,
                    request,
                    profile,
                    target,
                    main_target,
                    card,
                    beats,
                    story_text,
                ): index
                for index, target in enumerate(targets)
            }
            completed = (
                as_completed(future_map, timeout=timeout_seconds)
                if timeout_seconds is not None
                else as_completed(future_map)
            )
            try:
                for future in completed:
                    index = future_map[future]
                    target = targets[index]
                    try:
                        results[index] = future.result()
                    except Exception as exc:  # pragma: no cover - defensive fallback for provider failures
                        log_debug_event(
                            "scene_director.chorus.error",
                            targetId=target.targetId,
                            error=str(exc)[:240],
                        )
                        results[index] = fallback_line(target, "provider-error")
            except FutureTimeoutError:
                log_debug_event(
                    "scene_director.chorus.timeout",
                    timeoutSeconds=timeout_seconds,
                    targetCount=len(targets),
                )
            for future, index in future_map.items():
                if results[index] is None:
                    future.cancel()
                    results[index] = fallback_line(targets[index], "deadline-timeout")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return [line for line in results if line is not None]

    def _scene_chorus_cache_key(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "version": 7,
            "generalId": request.generalId,
            "speakerId": target.targetId,
            "speakerRole": target.role,
            "activeTargetId": main_target.targetId if main_target else None,
            "activeTargetRole": main_target.role if main_target else None,
            "evidenceId": card.evidenceId if card else None,
            "angle": card.angle if card else request.angle,
            "locale": request.locale,
            "maxChars": request.maxChorusChars,
            "seed": {
                "memoryText": beats.memoryText,
                "emotionText": beats.emotionText,
                "dialogueText": beats.dialogueText,
                "intentText": beats.intentText,
            },
            "storyText": self._clean_seed_text(story_text, max_chars=240),
            "speakerArchetype": str((speaker_context or {}).get("archetype") or ""),
            "speakerAnchors": self._speaker_persona_anchor_terms(speaker_context or {}),
            "sourceRefs": beats.sourceRefs,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _scene_chorus_prompt_payload(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
        grounding_terms: list[str],
    ) -> dict[str, Any]:
        context_key = self._scene_chorus_context_key(profile, target, main_target, beats, story_text)
        return {
            "contextKey": context_key,
            "speaker": {
                "generalId": target.targetId,
                "displayName": target.label,
                "relationshipToMain": target.role,
                "persona": speaker_context,
                "guidance": self._speaker_persona_guidance(speaker_context, target),
            },
            "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
            "activeTarget": {
                "targetId": main_target.targetId if main_target else None,
                "label": main_target.label if main_target else None,
                "role": main_target.role if main_target else None,
            },
            "sceneSeeds": beats.sceneSeeds,
            "sceneGrounding": grounding_terms[:8],
            "sceneScript": story_text,
        }

    def _scene_chorus_context_key(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> str:
        payload = {
            "version": 7,
            "mainActor": profile.generalId,
            "speaker": target.targetId,
            "relationship": target.role,
            "activeTarget": main_target.targetId if main_target else None,
            "activeTargetRole": main_target.role if main_target else None,
            "activeTargetAliases": self._target_aliases_for_interaction(main_target)[:6] if main_target else [],
            "sceneSeeds": beats.sceneSeeds or {},
            "sceneGrounding": self._scene_chorus_grounding_terms(main_target, beats, story_text)[:8],
            "storyText": self._clean_seed_text(story_text, max_chars=320),
            "sourceRefs": beats.sourceRefs,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"scene-chorus:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"

    def _scene_chorus_provider_config(self, llm_model_preset: str) -> dict[str, Any]:
        preset_config = LLM_MODEL_PRESETS.get(llm_model_preset, LLM_MODEL_PRESETS[DEFAULT_LLM_MODEL_PRESET])
        base_order = list(preset_config.get("providerOrder") or self.provider_router.provider_order or [])
        provider_order = ["history_cache"]
        preferred = ["gemini_flash", "gemini_flash_lite", "gemini"]
        for provider_name in preferred:
            if provider_name not in provider_order:
                provider_order.append(provider_name)
        for provider_name in base_order:
            if provider_name in {"history_cache", "deterministic"}:
                continue
            if provider_name not in provider_order:
                provider_order.append(provider_name)
        model_overrides = dict(preset_config.get("modelOverrides") or {})
        model_overrides.setdefault("__timeoutMs", "3200")
        model_overrides.setdefault("__retryCount", "0")
        return {
            "providerOrder": provider_order,
            "modelOverrides": model_overrides,
            "allowDeterministicFallback": False,
        }

    def _scene_chorus_keywords(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        speaker_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del profile
        keywords: list[dict[str, Any]] = []
        relationship_label = str(target.role or "").strip()
        if relationship_label:
            keywords.append(
                {
                    "keywordKey": (
                        f"relationship.{target.targetId}."
                        f"{hashlib.sha1(relationship_label.encode('utf-8')).hexdigest()[:10]}"
                    ),
                    "category": "relationship",
                    "label": relationship_label,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
        for label in self._speaker_persona_anchor_terms(speaker_context or {})[:2]:
            keywords.append(
                {
                    "keywordKey": (
                        f"speaker_persona.{target.targetId}."
                        f"{hashlib.sha1(label.encode('utf-8')).hexdigest()[:10]}"
                    ),
                    "category": "speaker_persona",
                    "label": label,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
        if main_target:
            keywords.append(
                {
                    "keywordKey": f"active_target.{main_target.targetId}",
                    "category": "target",
                    "label": main_target.label or main_target.role,
                    "sourceRefs": main_target.evidenceRefs[:4],
                }
            )
        scene_seeds = beats.sceneSeeds or {}
        for key, raw_value in [
            ("event", scene_seeds.get("event")),
            ("time", scene_seeds.get("time")),
            ("place", scene_seeds.get("place")),
            ("emotion", scene_seeds.get("emotion")),
        ]:
            label = str(raw_value or "").strip()
            if label:
                keywords.append(
                    {
                        "keywordKey": f"scene_seed.{key}.{hashlib.sha1(label.encode('utf-8')).hexdigest()[:10]}",
                        "category": "scene_seed",
                        "label": label,
                        "sourceRefs": beats.sourceRefs[:4],
                    }
                )
        for index, person in enumerate(scene_seeds.get("people") or []):
            label = str(person or "").strip()
            if label:
                keywords.append(
                    {
                        "keywordKey": f"scene_people.{index}.{hashlib.sha1(label.encode('utf-8')).hexdigest()[:10]}",
                        "category": "scene_people",
                        "label": label,
                        "sourceRefs": beats.sourceRefs[:4],
                    }
                )
        for index, obj in enumerate(scene_seeds.get("objects") or []):
            label = str(obj or "").strip()
            if label:
                keywords.append(
                    {
                        "keywordKey": f"scene_object.{index}.{hashlib.sha1(label.encode('utf-8')).hexdigest()[:10]}",
                        "category": "scene_object",
                        "label": label,
                        "sourceRefs": beats.sourceRefs[:4],
                    }
                )
        return keywords[:6]

    def _speaker_persona_context(
        self,
        target: NarrativeInteractionTarget,
        persona_card: PersonaCard | None,
    ) -> dict[str, Any]:
        roster_row = self._load_roster_index().get(target.targetId) or {}
        voice_style = list(persona_card.voiceStyle if persona_card else [])
        personality_traits = list(persona_card.personalityTraits if persona_card else [])
        lore_values = [
            roster_row.get("historicalAnecdote"),
            roster_row.get("bloodlineRumor"),
            roster_row.get("parentsSummary"),
            roster_row.get("description"),
            roster_row.get("story"),
            roster_row.get("personality"),
        ]
        lore_text = " ".join(str(value or "").strip() for value in lore_values if str(value or "").strip())
        role_text = " ".join(
            str(value or "")
            for value in [
                target.role,
                target.relationshipType,
                target.sourceType,
                target.gender,
                lore_text,
                " ".join(voice_style),
                " ".join(personality_traits),
            ]
        )
        return {
            "generalId": target.targetId,
            "displayName": persona_card.displayName if persona_card else target.label,
            "relationshipToMain": target.role,
            "relationshipType": target.relationshipType,
            "voiceStyle": voice_style,
            "personalityTraits": personality_traits,
            "safeFallbackLine": persona_card.safeFallbackLine if persona_card else "",
            "gender": target.gender,
            "femaleFocus": target.femaleFocus,
            "lore": self._compact_persona_lore(lore_text),
            "anchors": self._extract_persona_anchor_terms(role_text),
            "archetype": self._speaker_archetype(role_text, target),
            "personaSource": "persona-card" if persona_card else "relationship-only",
        }

    def _compact_persona_lore(self, lore_text: str, max_chars: int = 170) -> str:
        compact = " ".join(str(lore_text or "").split())
        if len(compact) <= max_chars:
            return compact
        sentence_ends = [index + 1 for index, char in enumerate(compact[:max_chars]) if char in "。！？"]
        if sentence_ends:
            return compact[: sentence_ends[-1]]
        return compact[: max_chars - 1] + "…"

    def _extract_persona_anchor_terms(self, text: str) -> list[str]:
        raw = str(text or "")
        candidates = [
            "江東",
            "主君",
            "形勢",
            "人心",
            "務實",
            "審勢",
            "婚盟",
            "宗室",
            "內母",
            "面子",
            "家線",
            "家室",
            "家眷",
            "阿斗",
            "孩子",
            "生路",
            "犧牲",
            "長坂坡",
            "自主",
            "鋒性",
            "帶兵",
            "騎射",
            "重義",
            "豪烈",
            "直率",
            "沉穩",
            "威嚴",
            "少言",
            "軍師",
            "謀定",
            "謹慎應對",
        ]
        mapped_traits = self._humanize_tag_list(re.split(r"[\s,、]+", raw))
        values: list[str] = []
        for candidate in candidates:
            if candidate and candidate in raw and candidate not in values:
                values.append(candidate)
        for item in re.split(r"[、,\s]+", mapped_traits):
            if item and item not in {"符合三國語境", "謹慎應對"} and item not in values:
                values.append(item)
        return values[:8]

    def _speaker_persona_anchor_terms(self, speaker_context: dict[str, Any]) -> list[str]:
        anchors = [str(item).strip() for item in (speaker_context.get("anchors") or []) if str(item).strip()]
        if anchors:
            return anchors
        fallback_values = [
            speaker_context.get("relationshipToMain"),
            speaker_context.get("relationshipType"),
            speaker_context.get("gender"),
        ]
        return [str(item).strip() for item in fallback_values if str(item).strip()][:3]

    def _speaker_archetype(self, text: str, target: NarrativeInteractionTarget) -> str:
        raw = str(text or "")
        if target.relationshipType in {"spouse", "lover"} or any(token in raw for token in ["姻親", "家室", "夫人"]):
            return "marriage_mediator"
        if target.relationshipType in {"parent_child", "sibling", "protects_family"}:
            return "family_line"
        if any(token in raw for token in ["婚盟", "宗室", "內母", "面子"]):
            return "marriage_mediator"
        if any(token in raw for token in ["長坂坡", "犧牲感", "犧牲", "生路讓給", "井底"]):
            return "family_sacrifice"
        if any(token in raw for token in ["家線", "不成額外的負擔", "顛沛", "阿斗"]):
            return "family_line"
        if any(token in raw for token in ["生路", "犧牲", "嬰孩", "孩子", "長坂坡"]):
            return "family_sacrifice"
        if any(token in raw for token in ["江東", "主君", "審勢", "務實", "形勢", "governance-minded", "strategic"]):
            return "jiangdong_ruler"
        if any(token in raw for token in ["豪烈", "直率", "戰場威壓", "direct_force", "martial"]):
            return "martial_direct"
        if any(token in raw for token in ["沉穩", "威嚴", "少言", "重義", "sworn_sibling", "loyal_oath"]):
            return "oath_guardian"
        if target.femaleFocus:
            return "family_witness"
        if target.relationshipType in {"enemy_rival", "battlefield_opponent"}:
            return "rival_observer"
        return "measured_observer"

    def _speaker_persona_summary(self, speaker_context: dict[str, Any]) -> str:
        anchors = "、".join(self._speaker_persona_anchor_terms(speaker_context)[:5])
        source = str(speaker_context.get("personaSource") or "")
        lore = str(speaker_context.get("lore") or "").strip()
        voice_style = "、".join(str(item).strip() for item in (speaker_context.get("voiceStyle") or []) if str(item).strip())
        personality = "、".join(str(item).strip() for item in (speaker_context.get("personalityTraits") or []) if str(item).strip())
        parts = [
            f"來源={source}" if source else "",
            f"人格錨點={anchors}" if anchors else "",
            f"語氣特徵={voice_style}" if voice_style else "",
            f"性格標籤={personality}" if personality else "",
            f"角色弧光={speaker_context.get('archetype') or ''}",
            f"人物小傳={lore}" if lore else "",
        ]
        return "；".join(part for part in parts if part)

    def _speaker_persona_guidance(
        self,
        speaker_context: dict[str, Any],
        target: NarrativeInteractionTarget,
    ) -> str:
        archetype = str(speaker_context.get("archetype") or "")
        guidance_map = {
            "marriage_mediator": "從情分、家室、去留與話裡分寸來說話",
            "family_sacrifice": "從代價、生路先後與誰該先被護住來說話",
            "family_line": "從宗支、血脈、家人安危與延續來說話",
            "jiangdong_ruler": "從形勢、名分、利害與節奏來說話",
            "martial_direct": "從戰機、氣勢、進退與誰來斷後來說話",
            "oath_guardian": "從義氣、補位、共擔與守住同伴來說話",
            "family_witness": "從情分、去留與眼前安危來說話",
            "rival_observer": "從破綻、代價與可趁之機來說話",
            "measured_observer": "先點出眼前一幕，再給克制而明確的判斷",
        }
        if archetype in guidance_map:
            return guidance_map[archetype]
        if target.relationshipType in {"sworn_sibling", "battle_ally", "loyal_oath"}:
            return "從義氣、補位與共同承擔來說話"
        if target.relationshipType in {"enemy_rival", "battlefield_opponent"}:
            return "從破綻、代價與勝負手來說話"
        if target.femaleFocus:
            return "從情分、去留與身邊人的安危來說話"
        return "先點出此刻看見了什麼，再給出屬於此人的判斷"

    def _scene_chorus_grounding_terms(
        self,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> list[str]:
        values: list[str] = []
        scene_seeds = beats.sceneSeeds or {}
        place = str(scene_seeds.get("place") or "").strip()
        if place and place not in values:
            values.append(place)
        for obj in scene_seeds.get("objects") or []:
            label = str(obj or "").strip()
            if label and label not in values:
                values.append(label)
        for raw_text in [
            scene_seeds.get("event"),
            beats.sceneText,
            beats.memoryText,
            story_text,
        ]:
            for phrase in self._scene_phrase_candidates(raw_text):
                if phrase and phrase not in values:
                    values.append(phrase)
        if main_target:
            for alias in self._target_aliases_for_interaction(main_target):
                alias = str(alias or "").strip()
                if alias and alias not in values:
                    values.append(alias)
        return values[:10]

    def _scene_phrase_candidates(self, text: Any, max_terms: int = 6) -> list[str]:
        raw = " ".join(str(text or "").split()).strip()
        if not raw:
            return []
        values: list[str] = []
        for chunk in re.split(r"[，。；：「」『』（）()\s]+", raw):
            phrase = str(chunk or "").strip("、,.!?！？：: ")
            if len(phrase) < 2 or len(phrase) > 14:
                continue
            if phrase.isdigit():
                continue
            if phrase not in values:
                values.append(phrase)
            if len(values) >= max_terms:
                break
        return values

    def _repair_chorus_generation(
        self,
        generation: DialogueGenerationResult,
        fallback_text: str,
        max_chars: int,
        target: NarrativeInteractionTarget,
        speaker_context: dict[str, Any],
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> DialogueGenerationResult:
        persona_card = self.get_persona_card(target.targetId)
        cleaned = self._strip_speaker_self_mentions(generation.text, target, persona_card)
        repaired = self._complete_generated_text(cleaned, fallback_text, max_chars)
        warnings = list(generation.qualityWarnings)
        repair_used = generation.repairUsed
        if repaired != generation.text:
            warnings.append("scene_chorus_self_name_or_sentence_repaired")
            repair_used = True
        if self._contains_internal_symbolic_token(repaired):
            warnings.append("scene_chorus_internal_token_rejected")
            return replace(
                generation,
                text=fallback_text,
                fallbackUsed=True,
                qualityWarnings=warnings,
                repairUsed=True,
            )
        if self._is_generic_chorus_line(repaired, speaker_context):
            warnings.append("scene_chorus_generic_rejected")
            return replace(
                generation,
                text=fallback_text,
                fallbackUsed=True,
                qualityWarnings=warnings,
                repairUsed=True,
            )
        if not self._line_has_scene_grounding(repaired, main_target, beats, story_text):
            warnings.append("scene_chorus_ungrounded_rejected")
            return replace(
                generation,
                text=fallback_text,
                fallbackUsed=True,
                qualityWarnings=warnings,
                repairUsed=True,
            )
        return replace(
            generation,
            text=repaired,
            qualityWarnings=warnings,
            repairUsed=repair_used,
        )

    def _strip_speaker_self_mentions(
        self,
        text: str,
        target: NarrativeInteractionTarget,
        persona_card: PersonaCard | None,
    ) -> str:
        cleaned = str(text or "").strip()
        names = [target.label]
        if persona_card:
            names.append(persona_card.displayName)
        names = [name for name in dict.fromkeys(name.strip() for name in names if name and len(name.strip()) >= 2)]
        for name in names:
            cleaned = re.sub(rf"^{re.escape(name)}[：:，,、\s]*(認為|覺得|看來|說|道)?[：:，,、\s]*", "", cleaned)
            cleaned = cleaned.replace(f"{name}認為", "")
            cleaned = cleaned.replace(f"{name}覺得", "")
            cleaned = cleaned.replace(f"{name}看來", "")
        return self._ensure_sentence(cleaned)

    def _is_generic_chorus_line(self, text: str, speaker_context: dict[str, Any]) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        generic_patterns = [
            r"先看證據",
            r"再說判斷",
            r"先說清楚",
            r"先聽完",
            r"別的判斷",
            r"安得住人",
            r"安住人心",
            r"這份憂色",
            r"這份憂慮",
            r"話還沒說透",
            r"局勢如何轉圜",
            r"局勢才不會",
            r"這一步不能只看",
            r"先看後路",
        ]
        has_generic_phrase = any(re.search(pattern, cleaned) for pattern in generic_patterns)
        anchors = self._speaker_persona_anchor_terms(speaker_context)
        has_persona_anchor = any(anchor and anchor in cleaned for anchor in anchors)
        return has_generic_phrase and not has_persona_anchor

    def _line_has_scene_grounding(
        self,
        text: str,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        if not grounding_terms:
            return True
        return any(term and term in cleaned for term in grounding_terms)

    def _contains_internal_symbolic_token(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        return bool(
            re.search(r"\b[a-z]+(?:[_-][a-z0-9]+)+\b", cleaned)
            or re.search(r"\b(?:personaSource|relationshipType|contextKey|sceneSeeds)\b", cleaned)
        )

    def _compose_scene_grounded_chorus_fallback(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
    ) -> str:
        lead = self._scene_chorus_lead_clause(main_target, beats, story_text)
        focus_terms = self._scene_chorus_focus_terms(speaker_context, target)
        judgment = self._scene_chorus_judgment_clause(
            target=target,
            main_target=main_target,
            beats=beats,
            story_text=story_text,
            speaker_context=speaker_context,
            focus_terms=focus_terms,
        )
        if lead and judgment:
            return self._ensure_sentence(f"{lead}，{judgment}")
        if judgment:
            return self._ensure_sentence(judgment)
        if lead:
            return self._ensure_sentence(lead)
        return self._ensure_sentence("眼前這一幕，不能只當作一時熱血。")

    def _scene_chorus_lead_clause(
        self,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> str:
        action_phrase = self._scene_chorus_action_phrase(main_target, beats, story_text)
        if action_phrase:
            return action_phrase
        place = str((beats.sceneSeeds or {}).get("place") or "").strip()
        if main_target and place:
            return f"看{main_target.label}在{place}這一帶撐住局面"
        if main_target:
            return f"看{main_target.label}這一下"
        if place:
            return f"{place}這一幕"
        return ""

    def _scene_chorus_judgment_clause(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
        focus_terms: list[str],
    ) -> str:
        archetype = str(speaker_context.get("archetype") or "")
        focus = focus_terms[0] if focus_terms else "分寸"
        scene_term = self._scene_chorus_scene_term(beats, story_text)
        target_name = str(main_target.label if main_target else "").strip()
        if archetype == "oath_guardian":
            return f"{focus}不是嘴上說說，得立刻把{scene_term or '後手'}接住，才不算負了{target_name or '身邊的人'}。"
        if archetype == "martial_direct":
            return f"{focus}要落在動作上；既有人頂在前面，後面的銜接就不能慢。"
        if archetype == "jiangdong_ruler":
            return f"{focus}都得收回局勢本身；有人替眾人換出一口氣，就要順勢把亂局壓住。"
        if archetype == "marriage_mediator":
            return f"情分一牽進來，越要把{scene_term or '去路'}安排穩，免得後頭的人心先亂。"
        if archetype == "family_sacrifice":
            return f"這一下換來的是{scene_term or '生路'}，不能白耗在遲疑裡。"
        if archetype == "family_line":
            return f"先把{scene_term or '後手'}接住，才護得住該護的人。"
        if archetype == "family_witness":
            return f"既有人替眾人撐住，後頭的人就更不能散。"
        if archetype == "rival_observer":
            return f"真換到對陣時，這股{scene_term or focus}最容易逼人露出破綻。"
        return f"先把{scene_term or '眼前這一口氣'}接住，再談{focus}才不會落空。"

    def _scene_chorus_scene_term(self, beats: SceneDirectorBeats, story_text: str) -> str:
        scene_seeds = beats.sceneSeeds or {}
        joined = " ".join(
            str(value or "")
            for value in [
                scene_seeds.get("event"),
                beats.sceneText,
                story_text,
            ]
        )
        marker_map = [
            ("斷後", "後手"),
            ("退走", "退路"),
            ("退兵", "退路"),
            ("追兵", "追兵"),
            ("長坂橋", "橋頭"),
            ("長阪橋", "橋頭"),
            ("長坂", "橋頭"),
            ("長阪", "橋頭"),
            ("大喝", "聲勢"),
            ("百姓", "眾人"),
            ("家眷", "同行的人"),
            ("阿斗", "幼主"),
            ("阿鬥", "幼主"),
        ]
        for marker, label in marker_map:
            if marker in joined:
                return label
        objects = [str(item or "").strip() for item in scene_seeds.get("objects") or [] if str(item or "").strip()]
        place = str(scene_seeds.get("place") or "").strip()
        for value in objects + ([place] if place else []):
            if value:
                return value
        return ""

    def _scene_chorus_action_phrase(
        self,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> str:
        target_aliases = self._target_aliases_for_interaction(main_target) if main_target else []
        candidates = self._scene_phrase_candidates((beats.sceneSeeds or {}).get("event"))
        candidates.extend(self._scene_phrase_candidates(beats.sceneText))
        candidates.extend(self._scene_phrase_candidates(beats.memoryText, max_terms=4))
        seen: list[str] = []
        for item in candidates:
            phrase = str(item or "").strip()
            if not phrase or phrase in seen:
                continue
            seen.append(phrase)
        for phrase in seen:
            if target_aliases and any(alias and alias in phrase for alias in target_aliases):
                return phrase if not phrase.startswith("在") else phrase[1:]
        for phrase in seen:
            if len(phrase) >= 3:
                return f"看著{phrase}" if not phrase.startswith(("在", "看著")) else phrase
        return ""

    def _scene_chorus_focus_terms(
        self,
        speaker_context: dict[str, Any],
        target: NarrativeInteractionTarget,
    ) -> list[str]:
        values: list[str] = []
        for raw in [
            *self._speaker_persona_anchor_terms(speaker_context),
            *(speaker_context.get("personalityTraits") or []),
            speaker_context.get("relationshipToMain"),
        ]:
            humanized = self._humanize_tag_list(str(raw or "").strip(), fallback=str(raw or "").strip())
            for piece in re.split(r"[、/,\s]+", str(humanized or "").strip()):
                token = piece.strip()
                if (
                    not token
                    or len(token) < 2
                    or token in values
                    or self._contains_internal_symbolic_token(token)
                ):
                    continue
                values.append(token)
        if values:
            return values[:4]
        if target.femaleFocus:
            return ["情分", "安危"]
        if target.relationshipType in {"sworn_sibling", "battle_ally", "loyal_oath"}:
            return ["義氣", "補位"]
        if target.relationshipType in {"enemy_rival", "battlefield_opponent"}:
            return ["破綻", "代價"]
        return ["分寸"]

    def _rewrite_scene_chorus_from_fallback(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
        selected_keywords: list[dict[str, Any]],
        evidence_refs: list[str],
        fallback_text: str,
    ) -> DialogueGenerationResult | None:
        persona_card = self.get_persona_card(target.targetId)
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        generation = self._generate_scene_director_text(
            general_id=target.targetId,
            persona_card=persona_card,
            memory_context={
                "saveId": f"demo-chorus-rewrite-{request.generalId}",
                "shortTerm": story_text or beats.sceneText,
                "longTerm": self._scene_seed_text(beats),
                "playerProfile": (
                    f"發話者人格資料：{self._speaker_persona_summary(speaker_context)}。"
                    f"發話時應著重：{self._speaker_persona_guidance(speaker_context, target)}。"
                    f"本幕場景錨點：{'、'.join(grounding_terms[:6]) or '互動對象與當下動作'}。"
                    f"請把這句 draft 改寫得更像此人親眼看完這一幕後的反應：{fallback_text}"
                ),
                "promises": (
                    "請只回一句自然短對白；語氣要像這個人真的看完本幕後脫口而出。"
                    "必須直接扣住互動對象或當下動作，不要解釋資料，不要照抄人格標籤。"
                ),
            },
            selected_context={
                "task": "chorus-line-rewrite",
                "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
                "speaker": {
                    "generalId": target.targetId,
                    "displayName": target.label,
                    "relationshipToMain": target.role,
                },
                "activeTarget": {
                    "targetId": main_target.targetId if main_target else None,
                    "label": main_target.label if main_target else None,
                    "role": main_target.role if main_target else None,
                },
                "sceneSeeds": beats.sceneSeeds,
                "sceneScript": story_text,
                "fallbackDraft": fallback_text,
            },
            evidence_refs=evidence_refs,
            deterministic_text="",
            max_chars=request.maxChorusChars,
            locale=request.locale,
            llm_model_preset=request.llmModelPreset,
            speech_context_mode="inner_monologue",
            tone_mode="in-character",
            selected_keywords=selected_keywords,
            include_resolved_evidence=False,
            provider_order=["gemini_flash_lite", "gemini_flash", "gemini"],
            model_overrides={"__timeoutMs": "1800", "__retryCount": "0"},
            allow_deterministic_fallback=False,
        )
        cleaned = self._strip_speaker_self_mentions(generation.text, target, persona_card)
        repaired = self._complete_generated_text(cleaned, fallback_text, request.maxChorusChars)
        if (
            not repaired
            or self._contains_internal_symbolic_token(repaired)
            or self._is_generic_chorus_line(repaired, speaker_context)
            or not self._line_has_scene_grounding(repaired, main_target, beats, story_text)
        ):
            return None
        warnings = list(generation.qualityWarnings)
        warnings.append("scene_chorus_rewrite_from_fallback")
        return replace(
            generation,
            text=repaired,
            fallbackUsed=False,
            repairUsed=True,
            qualityWarnings=warnings,
        )

    def _generate_scene_director_text(
        self,
        general_id: str,
        persona_card: PersonaCard | None,
        memory_context: dict[str, Any],
        selected_context: dict[str, Any] | None,
        evidence_refs: list[str],
        deterministic_text: str,
        max_chars: int,
        locale: str,
        llm_model_preset: str,
        speech_context_mode: str = "inner_monologue",
        tone_mode: str = "narrative_fusion",
        selected_keywords: list[dict[str, Any]] | None = None,
        include_resolved_evidence: bool = True,
        provider_order: list[str] | None = None,
        model_overrides: dict[str, str] | None = None,
        allow_deterministic_fallback: bool | None = None,
    ):
        evidence_pack = (
            self._resolve_evidence(general_id, None, [], evidence_refs)
            if include_resolved_evidence
            else ResolvedEvidencePack(resolutionTrace=["scene-director:seed-only-prompt"])
        )
        preset_config = LLM_MODEL_PRESETS.get(llm_model_preset, LLM_MODEL_PRESETS[DEFAULT_LLM_MODEL_PRESET])
        resolved_provider_order = provider_order if provider_order is not None else preset_config["providerOrder"]
        resolved_model_overrides = model_overrides if model_overrides is not None else preset_config["modelOverrides"]
        resolved_allow_fallback = (
            allow_deterministic_fallback
            if allow_deterministic_fallback is not None
            else preset_config["allowDeterministicFallback"]
        )
        return self.provider_router.generate(
            DialoguePromptPackage(
                generalId=general_id,
                personaCardSubset=self._persona_subset(persona_card),
                memoryContext=memory_context,
                selectedContext=selected_context,
                selectedKeywords=selected_keywords or [],
                resolvedEvidence=evidence_pack.resolvedEvidence,
                evidenceRefs=evidence_refs,
                deterministicText=deterministic_text[:max_chars],
                maxChars=max_chars,
                toneMode=tone_mode,
                locale=locale,
                speechContextMode=speech_context_mode,
            ),
            provider_order=resolved_provider_order,
            model_overrides=resolved_model_overrides,
            allow_deterministic_fallback=resolved_allow_fallback,
        )

    def _repair_complete_generation(
        self,
        generation: DialogueGenerationResult,
        fallback_text: str,
        max_chars: int,
        warning_code: str,
    ) -> DialogueGenerationResult:
        repaired = self._complete_generated_text(generation.text, fallback_text, max_chars)
        if repaired == generation.text:
            return generation
        return replace(
            generation,
            text=repaired,
            qualityWarnings=[*generation.qualityWarnings, warning_code],
            repairUsed=True,
        )

    def _complete_generated_text(self, text: str, fallback_text: str, max_chars: int) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        fallback = str(fallback_text or "").strip()
        if not cleaned:
            return fallback
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars]
        if self._has_balanced_quotes(cleaned) and cleaned.endswith(("。", "！", "？", "。」", "！」", "？」", "。』", "！』", "？』")):
            return cleaned
        candidates = [
            index + 1
            for index, char in enumerate(cleaned)
            if char in "。！？!?"
        ]
        minimum = max(16, int(len(cleaned) * 0.45))
        candidates = [index for index in candidates if index >= minimum]
        for index in reversed(candidates):
            candidate = cleaned[:index].strip()
            if self._has_balanced_quotes(candidate):
                return candidate
        return fallback

    def _has_balanced_quotes(self, text: str) -> bool:
        return str(text or "").count("「") == str(text or "").count("」") and str(text or "").count("『") == str(text or "").count("』")

    def _sentence_or_default(self, text: str, default: str, max_chars: int) -> str:
        normalized = " ".join(str(text or "").split()).strip() or default
        if len(normalized) > max_chars:
            normalized = normalized[: max_chars - 1] + "…"
        return self._ensure_sentence(normalized)

    def _ensure_sentence(self, text: str) -> str:
        stripped = str(text or "").strip()
        if not stripped:
            return ""
        return stripped if stripped.endswith(("。", "！", "？", "。」", "！」", "？」", "。』", "！』", "？』")) else f"{stripped}。"

    def _angle_focus(self, angle: str | None) -> str:
        return {
            "people": "先安人心與秩序",
            "bond": "先確保同伴與承諾",
            "battlefield": "先整隊與看退路",
            "rival": "先衡量人心代價",
            "resource": "先盤點糧草與恩義",
            "habit": "先巡營與問細事",
            "emotion": "先想家室與心事",
            "identity": "先對齊人物定位與責任",
            "relationship": "先判斷人際網與信任邊界",
            "location": "先抓地利與路線節奏",
            "activity": "先排可執行的小行動",
            "decision": "先選策略方向與代價",
            "personality": "先用性格校正決策語氣",
            "role": "先站回角色責任",
        }.get(str(angle or ""), "先讓局面站穩")

    def _resolve_memory_context(self, request: DialogueRequest) -> GeneralMemoryContext | None:
        if request.memoryContext is not None:
            return request.memoryContext if has_memory_context_content(request.memoryContext) else None
        if not request.saveId:
            return None
        memory = load_general_memory(self.repo_root, request.saveId, request.generalId)
        return memory_context_from_data(memory)

    def get_persona_card(self, general_id: str) -> PersonaCard | None:
        runtime_persona = self.store.read_runtime_persona(general_id)
        if runtime_persona:
            voice = runtime_persona.get("voiceAndPrompt") or {}
            profile = runtime_persona.get("profile") or {}
            safe_fallback_line = voice.get("safeFallbackLine") or f"{runtime_persona.get('displayName') or general_id}仍須有憑有據，不可妄言。"
            if general_id != "guan-yu" and "關某" in safe_fallback_line:
                safe_fallback_line = f"{runtime_persona.get('displayName') or general_id}仍須有憑有據，不可妄言。"
            return PersonaCard.model_validate({
                "generalId": general_id,
                "personaVersion": runtime_persona.get("personaVersion") or "general_runtime_persona_v1",
                "displayName": runtime_persona.get("displayName") or general_id,
                "voiceStyle": voice.get("voiceStyle") or [],
                "personalityTraits": profile.get("personalityTags") or [],
                "safeFallbackLine": safe_fallback_line,
                "taboos": voice.get("taboos") or [],
                "evidenceRefs": runtime_persona.get("evidenceRefs") or [],
            })
        payload = self.store.read_persona_card(general_id)
        if payload is None:
            return None
        return PersonaCard.model_validate(payload)

    def _read_json(self, filename: str):
        return self.store.read_api_fixture(filename)

    def _read_optional_json(self, filename: str):
        return self.store.read_optional_api_fixture(filename)

    def _read_runtime_persona(self, general_id: str):
        return self.store.read_runtime_persona(general_id)

    def _read_runtime_keywords(self, general_id: str):
        return self.store.read_runtime_keywords(general_id)

    def _read_runtime_relationships(self, general_id: str):
        return self.store.read_runtime_relationships(general_id)

    def _load_roster_index(self) -> dict[str, dict[str, Any]]:
        if self._roster_index_cache is not None:
            return self._roster_index_cache
        path = self.repo_root / "assets/resources/data/generals.json"
        if not path.exists():
            self._roster_index_cache = {}
            return self._roster_index_cache
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._roster_index_cache = {}
            return self._roster_index_cache
        if not isinstance(payload, list):
            self._roster_index_cache = {}
            return self._roster_index_cache
        self._roster_index_cache = {
            str(row.get("id")): row
            for row in payload
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        return self._roster_index_cache

    def _roster_name_for(self, general_id: str, roster_index: dict[str, dict[str, Any]]) -> str:
        row = roster_index.get(general_id) or {}
        name = str(row.get("name") or "").strip()
        if name:
            return name
        try:
            runtime_persona = self.store.read_runtime_persona(general_id) or {}
        except (OSError, json.JSONDecodeError):
            runtime_persona = {}
        name = str(runtime_persona.get("displayName") or "").strip()
        if name:
            return name
        try:
            persona_card = self.store.read_persona_card(general_id) or {}
        except (OSError, json.JSONDecodeError):
            persona_card = {}
        return str(persona_card.get("displayName") or "").strip()

    def _roster_gender_for(self, general_id: str, roster_index: dict[str, dict[str, Any]]) -> str | None:
        row = roster_index.get(general_id) or {}
        value = str(row.get("gender") or "").strip()
        if value:
            return value
        try:
            runtime_persona = self.store.read_runtime_persona(general_id) or {}
        except (OSError, json.JSONDecodeError):
            runtime_persona = {}
        value = str(runtime_persona.get("gender") or "").strip()
        return value or None

    def _is_female_gender(self, gender: str | None) -> bool:
        return str(gender or "").strip().lower() in {"female", "f", "woman", "女", "女性"}

    def _build_activity_seeds(
        self,
        runtime_persona: dict[str, Any],
        runtime_keywords: dict[str, Any],
    ) -> list[NarrativeActivitySeed]:
        seeds: list[NarrativeActivitySeed] = []
        seen: set[str] = set()
        for option in ((runtime_keywords.get("categories") or {}).get("activity") or []):
            keyword_key = str(option.get("keywordKey") or "").strip()
            if not keyword_key or keyword_key in seen:
                continue
            seen.add(keyword_key)
            seeds.append(
                NarrativeActivitySeed(
                    keywordKey=keyword_key,
                    label=str(option.get("label") or option.get("rawTag") or keyword_key),
                    confidence=self._coerce_float(option.get("confidence"), default=0.78),
                    sourceRefs=[str(ref) for ref in (option.get("sourceRefs") or []) if str(ref).strip()],
                    rawTag=str(option.get("rawTag") or "") or None,
                )
            )
        for raw_tag in ((runtime_persona.get("profile") or {}).get("activitySeedHints") or []):
            keyword_key = f"activity.{raw_tag}"
            if keyword_key in seen:
                continue
            seen.add(keyword_key)
            seeds.append(
                NarrativeActivitySeed(
                    keywordKey=keyword_key,
                    label=str(raw_tag),
                    confidence=0.72,
                    sourceRefs=[],
                    rawTag=str(raw_tag),
                )
            )
        return seeds[:12]

    def _relationship_display_priority(self, relationship_type: str | None, original_types: set[str]) -> int:
        if "spouse" in original_types or relationship_type == "spouse":
            return 95
        priority = {
            "sworn_sibling": 92,
            "protects_family": 88,
            "spouse": 95,
            "parent_child": 86,
            "mentor": 82,
            "battle_ally": 76,
            "loyal_oath": 74,
            "enemy_rival": 72,
            "battlefield_opponent": 68,
            "resource_support": 62,
            "ruler_subject": 50,
            "battlefield_contact": 42,
            "political_contact": 40,
        }
        return priority.get(str(relationship_type or ""), 35)

    def _runtime_sources_for_refs(self, runtime_persona: dict[str, Any] | None, refs: set[str]) -> list[str]:
        if not runtime_persona or not refs:
            return []
        matched: list[str] = []
        for source in [*(runtime_persona.get("storyBeats") or []), *(runtime_persona.get("sourceHighlights") or [])]:
            source_refs = {str(ref).strip() for ref in (source.get("sourceRefs") or []) if str(ref).strip()}
            source_ref = str(source.get("sourceRef") or "").strip()
            if source_ref:
                source_refs.add(source_ref)
            if not source_refs.intersection(refs):
                continue
            matched.extend(
                str(value)
                for value in [
                    source.get("summary"),
                    source.get("sourceQuote"),
                    source.get("quote"),
                    source.get("example"),
                ]
                if value
            )
        return matched

    def _runtime_edge_source_text(self, edge: dict[str, Any], runtime_persona: dict[str, Any] | None = None) -> str:
        refs = self._runtime_edge_refs(edge)
        return " ".join(
            str(value)
            for value in [
                *(edge.get("sourceQuotes") or []),
                edge.get("sourceQuote"),
                edge.get("evidenceText"),
                edge.get("summary"),
                *self._runtime_sources_for_refs(runtime_persona, refs),
            ]
            if value
        )

    def _runtime_edge_is_stable_relationship(self, edge: dict[str, Any]) -> bool:
        source_layer = str(edge.get("sourceLayer") or "").strip()
        source_layers = self.relationship_runtime_policy.get("stableRuntimeSourceLayers")
        grades = self.relationship_runtime_policy.get("aCanonGrades")
        stable_source_layers = (
            {str(item).strip() for item in source_layers if str(item).strip()}
            if isinstance(source_layers, list)
            else DEFAULT_STABLE_RELATIONSHIP_SOURCE_LAYERS
        )
        a_canon_grades = (
            {str(item).strip() for item in grades if str(item).strip()}
            if isinstance(grades, list)
            else DEFAULT_A_CANON_RELATIONSHIP_GRADES
        )
        return source_layer in stable_source_layers or str(edge.get("claimGrade") or "") in a_canon_grades

    def _relationship_edge_card_type(self, edge: dict[str, Any], resolved_type: str | None) -> str | None:
        raw_type = str(edge.get("type") or "").strip() or None
        if not raw_type:
            return resolved_type
        if raw_type == str(resolved_type or "").strip():
            return resolved_type
        if self._runtime_edge_is_stable_relationship(edge):
            return resolved_type
        return raw_type

    def _runtime_actor_aliases(self, runtime_persona: dict[str, Any]) -> list[str]:
        display_name = str(runtime_persona.get("displayName") or "").strip()
        aliases = [display_name, *(runtime_persona.get("aliases") or [])]
        if len(display_name) == 2:
            aliases.append(display_name[1:])
        return [alias for alias in aliases if alias]

    def _text_mentions_runtime_actor(self, runtime_persona: dict[str, Any], text: str) -> bool:
        source = str(text or "")
        return any(alias and alias in source for alias in self._runtime_actor_aliases(runtime_persona))

    def _runtime_target_aliases(self, target_id: str | None, target_name: str | None) -> list[str]:
        seeds: list[str] = []
        if target_name:
            seeds.append(str(target_name))
        target_key = str(target_id or "").strip()
        if target_key:
            try:
                target_persona = self.store.read_runtime_persona(target_key) or {}
            except (OSError, json.JSONDecodeError):
                target_persona = {}
            seeds.extend(
                str(value)
                for value in [
                    target_persona.get("displayName"),
                    *((target_persona.get("aliases") or []) if isinstance(target_persona.get("aliases"), list) else []),
                ]
                if value
            )
        aliases: list[str] = []
        is_female = self._is_female_gender(str((target_persona or {}).get("gender") or "").strip()) or any(
            token in str(target_name or "")
            for token in ("夫人", "氏", "太后", "太妃", "公主")
        )
        for seed in seeds:
            for alias in self._target_label_aliases(seed, allow_family_titles=is_female):
                if alias and alias not in aliases:
                    aliases.append(alias)
            if seed and len(seed) >= 2:
                family = seed[0]
                for suffix in ("軍", "兵", "營", "部"):
                    alias = f"{family}{suffix}"
                    if alias not in aliases:
                        aliases.append(alias)
        return aliases

    def _text_mentions_runtime_target(self, edge: dict[str, Any], text: str) -> bool:
        source = str(text or "")
        target_aliases = self._runtime_target_aliases(edge.get("targetId"), edge.get("targetName"))
        return any(alias and alias in source for alias in target_aliases)

    def _edge_has_direct_pair_signal(self, edge: dict[str, Any], runtime_persona: dict[str, Any]) -> bool:
        text = self._runtime_edge_source_text(edge, runtime_persona)
        if not text:
            return False
        return self._text_mentions_runtime_actor(runtime_persona, text) and self._text_mentions_runtime_target(edge, text)

    def _runtime_edge_refs(self, edge: dict[str, Any]) -> set[str]:
        return {str(ref).strip() for ref in (edge.get("evidenceRefs") or []) if str(ref).strip()}

    def _normalize_runtime_target_id(
        self,
        target_id: str | None,
        target_label: str | None = None,
        source_text: str | None = None,
    ) -> str:
        normalized_id = str(target_id or "").strip()
        if not normalized_id:
            return ""
        collision_map = TARGET_ID_NAME_COLLISIONS.get(normalized_id)
        if not collision_map:
            return normalized_id
        evidence = f"{target_label or ''} {source_text or ''}"
        for marker, replacement_id in collision_map.items():
            if marker and marker in evidence:
                return replacement_id
        return normalized_id

    def _edge_points_to_yellow_turban_enemy(self, edge: dict[str, Any]) -> bool:
        target_id = str(edge.get("targetId") or "").strip()
        if target_id in YELLOW_TURBAN_TARGET_IDS:
            return True
        text = self._runtime_edge_source_text(edge)
        label = str(edge.get("targetName") or "")
        return any(term in f"{label} {text}" for term in YELLOW_TURBAN_CONTEXT_TERMS)

    def _hard_relationship_override(
        self,
        edge: dict[str, Any],
        runtime_persona: dict[str, Any],
    ) -> str | None:
        source_id = str(runtime_persona.get("generalId") or "").strip()
        target_id = str(edge.get("targetId") or "").strip()
        if not source_id or not target_id:
            return None
        return HARD_RELATIONSHIP_PAIR_TYPES.get(frozenset({source_id, target_id}))

    def _relationship_type_label(self, relationship_type: str | None, fallback: str | None = None) -> str:
        labels = {
            "sworn_sibling": "結義兄弟",
            "protects_family": "護衛家室",
            "spouse": "姻親 / 家室",
            "parent_child": "親子",
            "mentor": "師友",
            "mentor_student": "師友",
            "battle_ally": "戰場同袍",
            "loyal_oath": "忠義相托",
            "alliance_oath": "盟友",
            "enemy_rival": "敵對競爭",
            "battlefield_opponent": "戰場對手",
            "betrayal_surrender": "背叛 / 降服",
            "resource_support": "資源支援",
            "ruler_subject": "君臣主從",
            "battlefield_contact": "戰場接觸",
            "political_contact": "政治接觸",
        }
        normalized = str(relationship_type or "").strip()
        return labels.get(normalized) or str(fallback or normalized or "互動關係")

    def _preferred_non_conflict_relationships(
        self,
        runtime_relationships: dict[str, Any],
        runtime_persona: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        conflict_types = {"enemy_rival", "battlefield_opponent", "betrayal_surrender"}
        preferred: dict[str, dict[str, Any]] = {}
        for edge in runtime_relationships.get("anchors") or []:
            target_id = str(edge.get("targetId") or "").strip()
            edge_type = str(edge.get("type") or "").strip()
            if not target_id or not edge_type or edge_type in conflict_types:
                continue
            if edge_type == "ruler_subject" and not self._edge_has_authority_signal(edge, runtime_persona):
                continue
            if edge_type == "patron_client" and not self._edge_has_patron_signal(edge, runtime_persona):
                continue
            priority = self._relationship_display_priority(edge_type, set(edge.get("originalTypes") or []))
            current = preferred.get(target_id)
            if current and int(current.get("priority") or 0) >= priority:
                current["refs"].update(self._runtime_edge_refs(edge))
                continue
            preferred[target_id] = {
                "type": edge_type,
                "label": self._relationship_type_label(edge_type, edge.get("typeLabel")),
                "priority": priority,
                "refs": set(self._runtime_edge_refs(edge)),
            }
        return preferred

    def _edge_has_command_or_ally_signal(self, edge: dict[str, Any], runtime_persona: dict[str, Any]) -> bool:
        text = self._runtime_edge_source_text(edge, runtime_persona)
        if not text:
            return False
        if not self._edge_has_direct_pair_signal(edge, runtime_persona):
            return False
        command_terms = ("令", "命", "遣", "撥", "使", "差", "引軍", "為先鋒", "爲先鋒")
        ally_terms = ("助戰", "救起", "同救", "來投", "投降", "歸順", "歸降", "保")
        return any(term in text for term in [*command_terms, *ally_terms])

    def _edge_has_authority_signal(self, edge: dict[str, Any], runtime_persona: dict[str, Any]) -> bool:
        text = self._runtime_edge_source_text(edge, runtime_persona)
        if self._runtime_edge_is_stable_relationship(edge):
            return True
        if not self._text_mentions_runtime_actor(runtime_persona, text):
            return False
        has_target = self._text_mentions_runtime_target(edge, text)
        strict_authority_terms = ("麾下", "效忠", "親信", "亲信", "侍衛", "侍卫", "部下", "部將", "部将", "主公", "臣", "君")
        return has_target and any(term in text for term in strict_authority_terms)
        authority_terms = ("令", "命", "遣", "撥", "使", "差", "引軍", "為先鋒", "爲先鋒", "部將", "主公")
        return has_target and any(term in text for term in authority_terms)

    def _edge_has_patron_signal(self, edge: dict[str, Any], runtime_persona: dict[str, Any]) -> bool:
        text = self._runtime_edge_source_text(edge, runtime_persona)
        if not self._text_mentions_runtime_actor(runtime_persona, text):
            return False
        has_target = self._text_mentions_runtime_target(edge, text)
        patron_terms = ("來投", "投降", "歸順", "厚待", "收留", "薦", "請", "降", "授", "拜")
        return has_target and any(term in text for term in patron_terms)

    def _edge_has_direct_conflict_signal(self, edge: dict[str, Any], runtime_persona: dict[str, Any]) -> bool:
        text = self._runtime_edge_source_text(edge, runtime_persona)
        if not text:
            return False
        has_actor = self._text_mentions_runtime_actor(runtime_persona, text)
        has_target = self._text_mentions_runtime_target(edge, text)
        conflict_terms = ("戰", "攻", "討", "殺", "追", "圍", "敗", "斬", "拒", "敵", "廝殺", "搦戰")
        return has_actor and has_target and any(term in text for term in conflict_terms)

    def _resolve_runtime_relationship_type(
        self,
        edge: dict[str, Any],
        runtime_persona: dict[str, Any],
        preferred_non_conflict: dict[str, dict[str, Any]],
    ) -> str | None:
        edge_type = str(edge.get("type") or "").strip() or None
        hard_override = self._hard_relationship_override(edge, runtime_persona)
        if hard_override:
            return hard_override
        if self._edge_points_to_yellow_turban_enemy(edge):
            if edge_type in {"ruler_subject", "patron_client", "mentor_student", "political_contact", "battlefield_contact"}:
                return "enemy_rival"
        edge_text = self._runtime_edge_source_text(edge, runtime_persona)
        direct_pair = self._edge_has_direct_pair_signal(edge, runtime_persona)
        if edge_type not in {"enemy_rival", "battlefield_opponent", "betrayal_surrender"}:
            if edge_type == "ruler_subject" and not self._edge_has_authority_signal(edge, runtime_persona):
                return "battlefield_contact" if direct_pair and self._text_has_battle_signal(edge_text) else "political_contact"
            if edge_type == "patron_client" and not self._edge_has_patron_signal(edge, runtime_persona):
                if self._edge_has_direct_conflict_signal(edge, runtime_persona):
                    return "enemy_rival"
                return "battlefield_contact" if direct_pair and self._text_has_battle_signal(edge_text) else "political_contact"
            return edge_type

        target_id = str(edge.get("targetId") or "").strip()
        refs = self._runtime_edge_refs(edge)
        preferred = preferred_non_conflict.get(target_id)
        if not direct_pair:
            if preferred:
                return str(preferred.get("type") or edge_type)
            return "battlefield_contact" if self._text_has_battle_signal(edge_text) else "political_contact"
        if preferred and refs and refs.intersection(preferred.get("refs") or set()):
            return str(preferred.get("type") or edge_type)

        if self._edge_has_command_or_ally_signal(edge, runtime_persona):
            return "ruler_subject"

        target_name = str(edge.get("targetName") or "")
        looks_like_indirect_family_or_woman = any(term in target_name for term in ("氏", "夫人", "后", "妃"))
        if looks_like_indirect_family_or_woman and not self._edge_has_direct_conflict_signal(edge, runtime_persona):
            return "political_contact"

        return edge_type

    def _build_interaction_targets(
        self,
        general_id: str,
        runtime_persona: dict[str, Any],
        runtime_keywords: dict[str, Any],
        runtime_relationships: dict[str, Any],
        roster_index: dict[str, dict[str, Any]],
    ) -> list[NarrativeInteractionTarget]:
        buckets: dict[str, dict[str, Any]] = {}
        category_weight = {
            "person": 2.4,
            "event": 1.4,
            "relationship": 2.0,
            "location": 0.8,
            "affect": 0.6,
        }
        preferred_non_conflict = self._preferred_non_conflict_relationships(runtime_relationships, runtime_persona)

        def ensure_bucket(target_id: str) -> dict[str, Any]:
            bucket = buckets.get(target_id)
            if bucket is not None:
                return bucket
            gender = self._roster_gender_for(target_id, roster_index)
            bucket = {
                "targetId": target_id,
                "label": self._roster_name_for(target_id, roster_index),
                "role": "人物線索",
                "gender": gender,
                "sourceType": "keyword-cooccurrence",
                "relationshipType": None,
                "relationshipPriority": 0,
                "confidence": 0.58,
                "score": 0.0,
                "evidenceRefs": [],
                "femaleFocus": self._is_female_gender(gender),
            }
            buckets[target_id] = bucket
            return bucket

        for edge in (runtime_relationships.get("anchors") or []):
            target_id = self._normalize_runtime_target_id(
                edge.get("targetId"),
                edge.get("targetName"),
                self._runtime_edge_source_text(edge),
            )
            if not target_id or target_id == general_id:
                continue
            if target_id != str(edge.get("targetId") or "").strip():
                edge = {**edge, "targetId": target_id}
            bucket = ensure_bucket(target_id)
            original_types = {str(item).strip() for item in (edge.get("originalTypes") or []) if str(item).strip()}
            edge_type = self._resolve_runtime_relationship_type(edge, runtime_persona, preferred_non_conflict)
            edge_label = self._relationship_type_label(edge_type, edge.get("typeLabel") or bucket["role"])
            if "spouse" in original_types:
                edge_type = "spouse"
                edge_label = "姻親 / 家室"
            edge_priority = self._relationship_display_priority(edge_type, original_types)
            bucket["label"] = self._roster_name_for(target_id, roster_index) or str(edge.get("targetName") or bucket["label"] or target_id)
            if edge_priority >= int(bucket.get("relationshipPriority") or 0):
                bucket["role"] = edge_label
                bucket["relationshipType"] = edge_type
                bucket["sourceType"] = "relationship-edge"
                bucket["relationshipPriority"] = edge_priority
            bucket["confidence"] = max(bucket["confidence"], self._coerce_float(edge.get("edgeConfidence"), default=0.72))
            bucket["score"] += 4.0 + bucket["confidence"]
            bucket["evidenceRefs"].extend(str(ref) for ref in (edge.get("evidenceRefs") or []) if str(ref).strip())

        for category, options in (runtime_keywords.get("categories") or {}).items():
            for option in options or []:
                refs = [str(ref) for ref in (option.get("sourceRefs") or []) if str(ref).strip()]
                for target_id in (option.get("generalIds") or []):
                    target_key = self._normalize_runtime_target_id(
                        str(target_id or "").strip(),
                        option.get("label") or option.get("fullLabel"),
                        " ".join(str(ref) for ref in refs),
                    )
                    if not target_key or target_key == general_id:
                        continue
                    bucket = ensure_bucket(target_key)
                    bucket["score"] += category_weight.get(category, 0.45)
                    bucket["confidence"] = max(bucket["confidence"], self._coerce_float(option.get("confidence"), default=0.62))
                    if bucket["sourceType"] != "relationship-edge":
                        bucket["role"] = (
                            "人物線索"
                            if category == "person"
                            else "事件共振"
                            if category == "event"
                            else "場景牽動"
                            if category == "location"
                            else "情緒關聯"
                            if category == "affect"
                            else "互動線索"
                        )
                    bucket["evidenceRefs"].extend(refs)

        for source in [*(runtime_persona.get("storyBeats") or []), *(runtime_persona.get("sourceHighlights") or [])]:
            refs = [str(ref) for ref in (source.get("sourceRefs") or []) if str(ref).strip()]
            if source.get("sourceRef"):
                refs.append(str(source.get("sourceRef")))
            families = {str(family).strip() for family in (source.get("angleFamilies") or []) if str(family).strip()}
            for target_id in source.get("relatedGeneralIds") or []:
                target_key = self._normalize_runtime_target_id(
                    str(target_id or "").strip(),
                    None,
                    " ".join(str(value) for value in [source.get("summary"), source.get("sourceQuote"), source.get("quote")] if value),
                )
                if not target_key or target_key == general_id:
                    continue
                bucket = ensure_bucket(target_key)
                is_female = bucket["femaleFocus"] or self._is_female_gender(self._roster_gender_for(target_key, roster_index))
                bucket["femaleFocus"] = bool(is_female)
                if bucket["sourceType"] != "relationship-edge":
                    bucket["sourceType"] = "pipeline-angle-target-link"
                    if bucket["role"] == "人物線索":
                        bucket["role"] = "女性互動線索" if is_female and "female_interaction" in families else "原文關聯"
                bucket["confidence"] = max(bucket["confidence"], 0.74 if families else 0.68)
                bucket["score"] += 2.0 if families else 1.2
                bucket["evidenceRefs"].extend(refs)

        for text, refs, has_emotion_angle in self._iter_runtime_target_mention_sources(runtime_persona):
            if not text:
                continue
            for target_id, row in roster_index.items():
                target_key = str(target_id or "").strip()
                if not target_key or target_key == general_id:
                    continue
                label = str(row.get("name") or target_key).strip()
                if not label:
                    continue
                allow_family_titles = self._is_female_gender(self._roster_gender_for(target_key, roster_index))
                if not any(alias and alias in text for alias in self._target_label_aliases(label, allow_family_titles=allow_family_titles)):
                    continue
                bucket = ensure_bucket(target_key)
                is_female = bucket["femaleFocus"] or self._is_female_gender(self._roster_gender_for(target_key, roster_index))
                bucket["femaleFocus"] = bool(is_female)
                if bucket["sourceType"] != "relationship-edge":
                    bucket["sourceType"] = "source-text-mention"
                    if bucket["role"] == "人物線索":
                        bucket["role"] = "女性互動線索" if is_female else "原文提及"
                bucket["confidence"] = max(bucket["confidence"], 0.7 if is_female and has_emotion_angle else 0.64)
                bucket["score"] += 1.8 if is_female and has_emotion_angle else 1.0
                bucket["evidenceRefs"].extend(refs)

        def sort_key(item: dict[str, Any]) -> tuple[int, int, float, float, str]:
            return (
                0 if item["sourceType"] == "relationship-edge" else 1,
                0 if item["femaleFocus"] else 1,
                -float(item["confidence"]),
                -float(item["score"]),
                str(item["label"]),
            )

        targets = [
            NarrativeInteractionTarget(
                targetId=item["targetId"],
                label=str(item["label"] or item["targetId"]),
                role=str(item["role"] or "人物線索"),
                gender=item["gender"],
                sourceType=str(item["sourceType"]),
                relationshipType=item["relationshipType"],
                confidence=self._coerce_float(item["confidence"], default=0.68),
                evidenceRefs=sorted(set(item["evidenceRefs"]))[:12],
                femaleFocus=bool(item["femaleFocus"]),
            )
            for item in sorted(buckets.values(), key=sort_key)[:12]
        ]
        return targets

    def _iter_runtime_target_mention_sources(self, runtime_persona: dict[str, Any]) -> list[tuple[str, list[str], bool]]:
        sources: list[tuple[str, list[str], bool]] = []
        for beat in (runtime_persona.get("storyBeats") or [])[:18]:
            refs = [str(ref) for ref in (beat.get("sourceRefs") or []) if str(ref).strip()]
            text = " ".join(
                str(value)
                for value in [
                    beat.get("summary"),
                    beat.get("sourceQuote"),
                    beat.get("location"),
                ]
                if value
            )
            if text.strip():
                sources.append((text, refs, False))
        for highlight in (runtime_persona.get("sourceHighlights") or [])[:24]:
            source_ref = str(highlight.get("sourceRef") or "").strip()
            families = {str(family).strip() for family in (highlight.get("angleFamilies") or []) if str(family).strip()}
            text = " ".join(
                str(value)
                for value in [
                    highlight.get("example"),
                    highlight.get("summary"),
                    source_ref,
                ]
                if value
            )
            if text.strip():
                sources.append((text, [source_ref] if source_ref else [], "female_interaction" in families))
        return sources

    def _build_narrative_evidence_cards(
        self,
        runtime_persona: dict[str, Any],
        runtime_relationships: dict[str, Any],
        interaction_targets: list[NarrativeInteractionTarget],
    ) -> list[NarrativeEvidenceCard]:
        cards: list[NarrativeEvidenceCard] = []
        seen: set[str] = set()
        target_labels = {target.targetId: target.label for target in interaction_targets}
        target_by_id = {target.targetId: target for target in interaction_targets}
        female_target_ids = [target.targetId for target in interaction_targets if target.femaleFocus]
        preferred_non_conflict = self._preferred_non_conflict_relationships(runtime_relationships, runtime_persona)

        def normalize_related_ids(raw_ids: list[Any] | None, source_text: str = "") -> list[str]:
            related_ids: list[str] = []
            for raw_id in raw_ids or []:
                target_id = self._normalize_runtime_target_id(str(raw_id), None, source_text)
                if target_id in target_labels and target_id not in related_ids:
                    related_ids.append(target_id)
            return related_ids

        def normalize_relationship_edge(edge: dict[str, Any]) -> dict[str, Any]:
            target_id = self._normalize_runtime_target_id(
                edge.get("targetId"),
                edge.get("targetName"),
                self._runtime_edge_source_text(edge),
            )
            if target_id and target_id != str(edge.get("targetId") or "").strip():
                return {**edge, "targetId": target_id}
            return edge

        highlight_by_ref = {
            str(item.get("sourceRef") or "").strip(): item
            for item in (runtime_persona.get("sourceHighlights") or [])
            if str(item.get("sourceRef") or "").strip()
        }

        for beat in (runtime_persona.get("storyBeats") or [])[:14]:
            refs = [str(ref) for ref in (beat.get("sourceRefs") or []) if str(ref).strip()]
            primary_ref = refs[0] if refs else ""
            families = list((highlight_by_ref.get(primary_ref) or {}).get("angleFamilies") or [])
            beat_text = " ".join(
                str(value)
                for value in [
                    beat.get("summary"),
                    beat.get("sourceQuote"),
                    beat.get("location"),
                ]
                if value
            )
            related_target_ids = normalize_related_ids(beat.get("relatedGeneralIds"), beat_text)
            if not related_target_ids:
                related_target_ids = self._detect_related_target_ids(
                    beat_text,
                    target_labels,
                    female_target_ids=female_target_ids,
                )
            evidence_id = str(beat.get("eventId") or beat.get("eventKey") or primary_ref or f"story-beat-{len(cards)}").strip()
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            chapter_no = beat.get("chapterNo")
            classification_text = beat_text
            card_angle = self._classify_narrative_angle(
                families=families,
                relationship_type=None,
                related_target_ids=related_target_ids,
                source_text=classification_text,
            )
            related_target_ids = self._filter_related_target_ids_for_angle(
                card_angle,
                related_target_ids,
                target_by_id,
                source_text=classification_text,
                runtime_persona=runtime_persona,
            )
            if card_angle == "emotion" and not related_target_ids:
                continue
            cards.append(
                NarrativeEvidenceCard(
                    evidenceId=evidence_id,
                    contextKey=str(beat.get("eventKey") or "").strip() or None,
                    angle=card_angle,
                    title=str(beat.get("location") or beat.get("eventKey") or "舊事浮現"),
                    summary=str(beat.get("summary") or beat.get("sourceQuote") or "一段舊事正在浮上心頭。"),
                    quote=str(beat.get("sourceQuote") or "") or None,
                    location=str(beat.get("location") or "") or None,
                    chapterNo=int(chapter_no) if isinstance(chapter_no, int) else None,
                    sourceType="runtime-story-beat",
                    sourceRefs=refs,
                    relatedTargetIds=related_target_ids,
                    confidence=self._coerce_float(beat.get("confidence"), default=0.72),
                )
            )

        for highlight in (runtime_persona.get("sourceHighlights") or []):
            if len(cards) >= 30:
                break
            evidence_id = f"highlight:{highlight.get('sourceRef') or len(cards)}"
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            families = list(highlight.get("angleFamilies") or [])
            example = str(highlight.get("example") or "").strip()
            source_ref = str(highlight.get("sourceRef") or "").strip()
            related_target_ids = normalize_related_ids(highlight.get("relatedGeneralIds"), example)
            if not related_target_ids:
                related_target_ids = self._detect_related_target_ids(
                    example,
                    target_labels,
                    female_target_ids=female_target_ids,
                )
            card_angle = self._classify_narrative_angle(
                families=families,
                relationship_type=None,
                related_target_ids=related_target_ids,
                source_text=example,
            )
            related_target_ids = self._filter_related_target_ids_for_angle(
                card_angle,
                related_target_ids,
                target_by_id,
                source_text=example,
                runtime_persona=runtime_persona,
            )
            if card_angle == "emotion" and not related_target_ids:
                continue
            cards.append(
                NarrativeEvidenceCard(
                    evidenceId=evidence_id,
                    contextKey=None,
                    angle=card_angle,
                    title=source_ref or "線索片段",
                    summary=example or "一條來自來源封包的片段線索。",
                    quote=example or None,
                    location=None,
                    chapterNo=None,
                    sourceType="runtime-source-highlight",
                    sourceRefs=[source_ref] if source_ref else [],
                    relatedTargetIds=related_target_ids,
                    confidence=0.7 if families else 0.64,
                )
            )

        target_order = {target.targetId: index for index, target in enumerate(interaction_targets)}

        def relationship_edge_sort_key(edge: dict[str, Any]) -> tuple[int, int, int, str]:
            edge = normalize_relationship_edge(edge)
            target_id = str(edge.get("targetId") or "").strip()
            target = target_by_id.get(target_id)
            resolved_type = self._resolve_runtime_relationship_type(edge, runtime_persona, preferred_non_conflict)
            card_type = self._relationship_edge_card_type(edge, resolved_type)
            return (
                0 if target else 1,
                target_order.get(target_id, 999),
                0 if target and target.relationshipType == card_type else 1,
                0 if self._runtime_edge_is_stable_relationship(edge) else 1,
                -self._coerce_float(edge.get("edgeConfidence"), default=0.7),
                str(card_type or ""),
                str(edge.get("type") or ""),
            )

        for edge in sorted(runtime_relationships.get("anchors") or [], key=relationship_edge_sort_key):
            if len(cards) >= 36:
                break
            edge = normalize_relationship_edge(edge)
            target_id = str(edge.get("targetId") or "").strip()
            if not target_id or target_id not in target_by_id:
                continue
            resolved_type = self._resolve_runtime_relationship_type(edge, runtime_persona, preferred_non_conflict)
            relationship_type = self._relationship_edge_card_type(edge, resolved_type)
            if not relationship_type:
                continue
            target = target_by_id.get(target_id)
            evidence_id = f"relationship:{target_id}:{relationship_type or edge.get('type') or len(cards)}"
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            quote = next((str(line) for line in (edge.get("sourceQuotes") or []) if str(line).strip()), None)
            target_name = str(edge.get("targetName") or target_labels.get(target_id) or target_id)
            relationship_label = self._relationship_type_label(relationship_type, edge.get("typeLabel"))
            cards.append(
                NarrativeEvidenceCard(
                    evidenceId=evidence_id,
                    contextKey=None,
                    angle=self._classify_narrative_angle(
                        families=[],
                        relationship_type=relationship_type,
                        related_target_ids=[target_id],
                        source_text=" ".join(value for value in [quote or "", target_name] if value),
                    ),
                    title=f"想到{target_name}",
                    summary=quote or f"{runtime_persona.get('displayName') or '此人'}與{target_name}之間有{edge.get('typeLabel') or relationship_type or '一段互動'}。",
                    quote=quote,
                    location=None,
                    chapterNo=None,
                    sourceType="runtime-relationship-edge",
                    sourceRefs=[str(ref) for ref in (edge.get("evidenceRefs") or []) if str(ref).strip()],
                    relatedTargetIds=[target_id],
                    confidence=self._coerce_float(edge.get("edgeConfidence"), default=0.7),
                )
            )

        return cards

    def _filter_related_target_ids_for_angle(
        self,
        angle: str,
        related_target_ids: list[str],
        target_by_id: dict[str, NarrativeInteractionTarget],
        source_text: str = "",
        runtime_persona: dict[str, Any] | None = None,
    ) -> list[str]:
        if angle == "emotion":
            filtered = [
                target_id
                for target_id in related_target_ids
                if self._is_valid_emotion_target(
                    target_by_id.get(target_id),
                    source_text=source_text,
                    runtime_persona=runtime_persona or {},
                )
            ]
            return self._filter_related_target_ids_with_source_mentions(filtered, target_by_id, source_text)
        if angle == "bond":
            bond_types = {
                "sworn_sibling",
                "battle_ally",
                "loyal_oath",
                "protects_family",
                "spouse",
                "parent_child",
                "sibling",
                "alliance_oath",
            }
            filtered = [
                target_id
                for target_id in related_target_ids
                if str((target_by_id.get(target_id).relationshipType if target_by_id.get(target_id) else "") or "") in bond_types
            ]
            return self._filter_related_target_ids_with_source_mentions(filtered, target_by_id, source_text)
        if angle not in {"people", "resource", "bond"}:
            return self._filter_related_target_ids_with_source_mentions(related_target_ids, target_by_id, source_text)
        filtered: list[str] = []
        for target_id in related_target_ids:
            target = target_by_id.get(target_id)
            relationship_type = str(target.relationshipType or "") if target else ""
            role = str(target.role or "") if target else ""
            if relationship_type in {"enemy_rival", "battlefield_opponent", "betrayal_surrender"}:
                continue
            if "敵對" in role or "戰場對手" in role:
                continue
            filtered.append(target_id)
        return self._filter_related_target_ids_with_source_mentions(filtered, target_by_id, source_text)

    def _filter_related_target_ids_with_source_mentions(
        self,
        related_target_ids: list[str],
        target_by_id: dict[str, NarrativeInteractionTarget],
        source_text: str,
    ) -> list[str]:
        source = str(source_text or "")
        if not source:
            return related_target_ids
        filtered: list[str] = []
        for target_id in related_target_ids:
            target = target_by_id.get(target_id)
            if not target:
                continue
            if any(alias and alias in source for alias in self._target_aliases_for_interaction(target)):
                filtered.append(target_id)
        return filtered

    def _is_valid_emotion_target(
        self,
        target: NarrativeInteractionTarget | None,
        source_text: str,
        runtime_persona: dict[str, Any],
    ) -> bool:
        if not target or not target.femaleFocus:
            return False
        relationship_type = str(target.relationshipType or "")
        if relationship_type in {"spouse", "parent_child", "sibling", "protects_family", "lover"}:
            return True
        text = str(source_text or "")
        if not self._text_mentions_runtime_actor(runtime_persona, text):
            return False
        if not any(alias and alias in text for alias in self._target_label_aliases(target.label, allow_family_titles=target.femaleFocus or self._is_female_gender(target.gender))):
            return False
        emotion_terms = ("夫人", "家眷", "妻", "母", "女", "嫁", "婚", "去留", "煩惱", "垂淚", "思親", "情")
        return any(term in text for term in emotion_terms)

    def _text_has_resource_signal(self, text: str) -> bool:
        return bool(
            re.search(
                r"糧草|糧食|錢糧|軍資|輜重|補給|俸祿|金銀|金帛|府庫|倉廩|"
                r"資財|家業|牌印|印綬|器皿|輜車|軍械|兵器",
                str(text or ""),
            )
        )

    def _text_has_battle_signal(self, text: str) -> bool:
        return any(term in str(text or "") for term in ("戰", "兵", "軍", "殺", "攻", "追", "退", "圍", "敗", "斬", "陣", "馬"))

    def _classify_narrative_angle(
        self,
        families: list[str],
        relationship_type: str | None,
        related_target_ids: list[str],
        source_text: str = "",
    ) -> str:
        relation = relationship_type or ""
        family_set = {str(family).strip() for family in families if str(family).strip()}
        text = str(source_text or "")
        bond_markers = r"stableKnowledgeBootstrap:sworn_sibling|結義|盟誓|桃園|兄弟|付託|託付|二夫人|嫂|三罪|故人舊日之情"
        if relation in {"sworn_sibling", "alliance_oath", "protects_family"}:
            return "bond"
        if relation == "battle_ally":
            return "bond" if re.search(bond_markers, text) else "people"
        if relation in {"enemy_rival", "battlefield_opponent", "betrayal_surrender"}:
            return "rival"
        if relation in {"battlefield_contact", "political_contact"}:
            return "people"
        if relation in {"ruler_subject", "patron_client", "mentor_student"}:
            return "relationship"
        if "female_interaction" in family_set:
            return "emotion"
        if "relationship" in family_set:
            if re.search(bond_markers, text):
                return "bond"
            return "relationship"
        if re.search(bond_markers, text):
            return "bond"
        if "decision_weight" in family_set or "faction_timeline" in family_set:
            return "decision"
        if "battle" in family_set or "location_context" in family_set:
            return "battlefield"
        if "item_equipment" in family_set and self._text_has_resource_signal(text):
            return "resource"
        if "activity_seed" in family_set or "work_role" in family_set:
            return "habit"
        if "item_equipment" in family_set:
            return "battlefield"
        if related_target_ids:
            return "people"
        return "people"

    def _target_label_aliases(self, label: str, allow_family_titles: bool = False) -> list[str]:
        normalized = str(label or "").strip()
        if not normalized:
            return []
        aliases: list[str] = [normalized]
        compact = normalized.replace(" ", "")
        if compact and compact not in aliases:
            aliases.append(compact)
        if allow_family_titles or compact.endswith(("氏", "夫人")):
            surname = compact[:1]
            if surname:
                spouse_form = f"{surname}夫人"
                if spouse_form not in aliases:
                    aliases.append(spouse_form)
                clan_form = f"{surname}氏"
                if clan_form not in aliases:
                    aliases.append(clan_form)
            if compact.endswith("氏"):
                bare = compact[:-1]
                if bare:
                    bare_spouse = f"{bare}夫人"
                    if bare_spouse not in aliases:
                        aliases.append(bare_spouse)
        return aliases

    def _target_aliases_for_interaction(self, target: NarrativeInteractionTarget) -> list[str]:
        seeds = [target.label]
        try:
            runtime_persona = self.store.read_runtime_persona(target.targetId)
        except Exception:
            runtime_persona = None
        if isinstance(runtime_persona, dict):
            for key in ("aliases", "nameAliases"):
                value = runtime_persona.get(key)
                if isinstance(value, list):
                    seeds.extend(str(item) for item in value if str(item).strip())
                elif value:
                    seeds.append(str(value))
        if "益德" in seeds and "翼德" not in seeds:
            seeds.append("翼德")
        if "翼德" in seeds and "益德" not in seeds:
            seeds.append("益德")
        if "阿斗" in seeds and "阿鬥" not in seeds:
            seeds.append("阿鬥")
        if "阿鬥" in seeds and "阿斗" not in seeds:
            seeds.append("阿斗")
        if "後主" in seeds:
            for alias in ("幼主", "小主人"):
                if alias not in seeds:
                    seeds.append(alias)
        aliases: list[str] = []
        allow_family_titles = bool(target.femaleFocus or self._is_female_gender(target.gender))
        for seed in seeds:
            for alias in self._target_label_aliases(seed, allow_family_titles=allow_family_titles):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return sorted(aliases, key=len, reverse=True)

    def _detect_related_target_ids(
        self,
        text: str,
        target_labels: dict[str, str],
        female_target_ids: list[str] | None = None,
    ) -> list[str]:
        if not text:
            return []
        female_ids = list(dict.fromkeys(female_target_ids or []))
        hits: list[str] = []
        for target_id, label in target_labels.items():
            aliases = self._target_label_aliases(label, allow_family_titles=target_id in female_ids)
            if any(alias and alias in text for alias in aliases):
                hits.append(target_id)
        if female_ids and any(token in text for token in ("夫人", "二夫人", "二嫂嫂", "家眷", "嫂嫂", "妻")):
            for target_id in female_ids:
                if target_id not in hits:
                    hits.append(target_id)
        return hits[:4]

    def _build_illustration_prompt(
        self,
        display_name: str,
        runtime_persona: dict[str, Any],
        interaction_targets: list[NarrativeInteractionTarget],
    ) -> str:
        voice_style = ", ".join((runtime_persona.get("voiceAndPrompt") or {}).get("voiceStyle") or [])
        personality_tags = self._humanize_tag_list((runtime_persona.get("profile") or {}).get("personalityTags") or [])
        affect_tags = self._humanize_tag_list((runtime_persona.get("profile") or {}).get("affectTags") or [])
        key_targets = "、".join(target.label for target in interaction_targets[:3])
        return (
            f"以清爽、輕鬆、易讀的劇情角色插畫描繪{display_name}，"
            "乾淨線條、柔和色彩、自然表情、溫暖電影光感，不要水墨山水、卷軸感或厚重筆刷；"
            f"保留角色氣質：{voice_style or personality_tags or '仁德與沉著'}；"
            f"情緒核心：{affect_tags or '情義與憂民'}；"
            f"可加入互動對象：{key_targets or '張飛、關羽、百姓'}；"
            "讓人物像剛從回憶中走出來，正準備做出下一個決定。畫面中不要出現任何文字、中文字、字幕或招牌字。"
        )

    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, numeric))

    def _humanize_tag_list(self, values: Any, fallback: str = "") -> str:
        if not values:
            return fallback
        if isinstance(values, str):
            raw_items = [part.strip() for part in re.split(r"[、,，·\s]+", values) if part.strip()]
        elif isinstance(values, (list, tuple, set)):
            raw_items = [str(part).strip() for part in values if str(part).strip()]
        else:
            raw_items = [str(values).strip()] if str(values).strip() else []
        tag_map = {
            "governance-minded": "重治理",
            "literary": "文雅",
            "family-bound": "重家室與結義",
            "martial": "武勇",
            "strategic": "審勢",
            "君臣主從": "護主",
            "親子": "護住家人",
            "姻親": "去留",
            "家室": "身邊的人",
            "結義兄弟": "義氣",
            "敵對競爭": "對陣",
            "sworn_sibling": "義氣",
            "battle_ally": "並肩",
            "loyal_oath": "守義",
            "enemy_rival": "對陣",
            "battlefield_opponent": "對陣",
            "ruler_subject": "護主",
            "patron_client": "知遇",
            "mentor_student": "授受",
            "spouse": "情分",
            "sibling": "手足",
            "parent_child": "護住家人",
            "protects_family": "護住家人",
            "political_contact": "衡勢",
            "battlefield_contact": "臨陣",
            "direct_force": "行動果斷",
            "commanding": "統率威嚴",
            "analytical": "審勢而行",
            "administrative": "善理政務",
            "persuasive": "善於勸說",
            "opportunistic": "臨機應變",
            "family_affection": "家族牽掛",
            "mercy_compassion": "仁心與憐憫",
            "grief_regret": "悲痛與遺憾",
            "ambition_pride": "志氣與自尊",
            "host_banquet": "設宴安撫",
            "family_duty": "家室安置",
            "suppress_unrest": "平亂整軍",
        }
        humanized: list[str] = []
        for item in raw_items:
            mapped = tag_map.get(item, item)
            if re.fullmatch(r"[a-z0-9_-]+", str(mapped or "").strip()):
                continue
            if mapped and mapped not in humanized:
                humanized.append(mapped)
        return "、".join(humanized) or fallback

    def _resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else self.repo_root / path

    def _select_context(self, response: ContextOptionsResponse, context_key: str | None) -> ContextOption | None:
        if not response.options:
            return None
        if context_key:
            for option in response.options:
                if option.contextKey == context_key:
                    return option
            return None
        return response.options[0]

    def _index_keywords(self, response: KeywordOptionsResponse) -> dict[str, tuple[str, KeywordOption]]:
        return {
            option.keywordKey: (category, option)
            for category, options in response.categories.items()
            for option in options
        }

    def _render_deterministic_dialogue(
        self,
        general_id: str,
        context: ContextOption | None,
        keywords: list[UsedKeyword],
        persona: PersonaCard | None,
        max_chars: int,
        locale: str,
        speech_context_mode: str,
    ) -> str:
        context_label = context.label if context else "舊事"
        keyword_labels = [keyword.label for keyword in keywords[:2]]
        display_name = persona.displayName if persona else "張飛"
        keyword_text = "、".join(keyword_labels) if keyword_labels else context_label
        if locale == "en":
            text = f"About {keyword_text}, I am {display_name}; I will guard my lord first and settle victory after."
        elif locale == "ja":
            text = f"{keyword_text}のことなら、俺はまず主君を守り、それから勝負を語る。"
        elif keyword_labels:
            prefix = {
                "life_chat": "閒聊說起",
                "encounter_speech": "當面撞見",
                "inner_monologue": "心裡想起",
                "meeting_statement": "帳中議到",
            }.get(speech_context_mode, "說起")
            self_name = {
                "cao-cao": "孤",
                "guan-yu": "關某",
                "liu-bei": "備",
                "lu-bu": "奉先",
                "sun-quan": "權",
                "wei-yan": "魏延",
                "yuan-shao": "本初",
                "zhang-fei": "俺",
                "zhao-yun": "雲",
                "zhuge-liang": "亮",
            }.get(general_id, display_name)
            text = f"{prefix}{context_label}與{keyword_text}，{self_name}只按眼前證據作答，不敢亂編史事。"
        elif persona:
            text = persona.safeFallbackLine
        else:
            text = f"說起{context_label}，俺{display_name}只認一個理：臨陣不可退，先護住主公。"
        return text[:max_chars]

    def _record_llm_dialogue_history(
        self,
        request: DialogueRequest,
        selected_context: ContextOption | None,
        used_keywords: list[UsedKeyword],
        evidence_refs: list[str],
        generation,
    ) -> None:
        if generation.provider not in LLM_HISTORY_PROVIDERS:
            return
        text = str(generation.text or "").strip()
        if not text:
            return
        entry = {
            "createdAt": datetime.now(UTC).isoformat(),
            "generalId": request.generalId,
            "contextKey": selected_context.contextKey if selected_context else request.contextKey,
            "locale": request.locale,
            "speechContextMode": request.speechContextMode,
            "llmModelPreset": request.llmModelPreset,
            "keywordKeys": [keyword.keywordKey for keyword in used_keywords],
            "keywordLabels": [keyword.label for keyword in used_keywords],
            "evidenceRefs": evidence_refs,
            "usedEvidenceRefs": generation.usedEvidenceRefs,
            "provider": generation.provider,
            "model": generation.model,
            "generationMode": generation.generationMode,
            "qualityWarnings": generation.qualityWarnings,
            "repairUsed": generation.repairUsed,
            "text": text,
        }
        self._append_history_entry(entry)

    def _record_scene_story_history(
        self,
        request: SceneDirectorRequest,
        context_key: str | None,
        selected_keywords: list[dict[str, Any]],
        evidence_refs: list[str],
        generation,
    ) -> None:
        if generation.provider not in LLM_HISTORY_PROVIDERS:
            return
        text = str(generation.text or "").strip()
        if not text:
            return
        entry = {
            "createdAt": datetime.now(UTC).isoformat(),
            "generalId": request.generalId,
            "contextKey": context_key,
            "locale": request.locale,
            "speechContextMode": "inner_monologue",
            "llmModelPreset": request.llmModelPreset,
            "keywordKeys": [str(keyword.get("keywordKey") or "") for keyword in selected_keywords if str(keyword.get("keywordKey") or "").strip()],
            "keywordLabels": [str(keyword.get("label") or "") for keyword in selected_keywords if str(keyword.get("label") or "").strip()],
            "evidenceRefs": evidence_refs,
            "usedEvidenceRefs": generation.usedEvidenceRefs,
            "provider": generation.provider,
            "model": generation.model,
            "generationMode": generation.generationMode,
            "qualityWarnings": generation.qualityWarnings,
            "repairUsed": generation.repairUsed,
            "text": text,
        }
        self._append_history_entry(entry)

    def _record_scene_chorus_history(
        self,
        request: SceneDirectorRequest,
        target: NarrativeInteractionTarget,
        context_key: str | None,
        selected_keywords: list[dict[str, Any]],
        evidence_refs: list[str],
        generation,
    ) -> None:
        if generation.provider not in LLM_HISTORY_PROVIDERS:
            return
        text = str(generation.text or "").strip()
        if not text:
            return
        entry = {
            "createdAt": datetime.now(UTC).isoformat(),
            "generalId": target.targetId,
            "contextKey": context_key,
            "locale": request.locale,
            "speechContextMode": "inner_monologue",
            "llmModelPreset": request.llmModelPreset,
            "keywordKeys": [str(keyword.get("keywordKey") or "") for keyword in selected_keywords if str(keyword.get("keywordKey") or "").strip()],
            "keywordLabels": [str(keyword.get("label") or "") for keyword in selected_keywords if str(keyword.get("label") or "").strip()],
            "evidenceRefs": evidence_refs,
            "usedEvidenceRefs": generation.usedEvidenceRefs,
            "provider": generation.provider,
            "model": generation.model,
            "generationMode": generation.generationMode,
            "qualityWarnings": generation.qualityWarnings,
            "repairUsed": generation.repairUsed,
            "text": text,
        }
        self._append_history_entry(entry)

    def _append_history_entry(self, entry: dict[str, Any]) -> None:
        try:
            self.history_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log_debug_event("dialogue.history.write-failed", path=str(self.history_cache_path), error=str(exc))

    def _persona_subset(self, persona: PersonaCard | None) -> dict:
        if persona is None:
            return {}
        return {
            "generalId": persona.generalId,
            "personaVersion": persona.personaVersion,
            "displayName": persona.displayName,
            "voiceStyle": persona.voiceStyle,
            "personalityTraits": persona.personalityTraits,
            "taboos": persona.taboos,
            "safeFallbackLine": persona.safeFallbackLine,
            "evidenceRefs": persona.evidenceRefs,
        }

    def _context_subset(self, context: ContextOption | None) -> dict | None:
        if context is None:
            return None
        return {
            "contextKey": context.contextKey,
            "label": context.label,
            "sourceType": context.sourceType,
            "confidence": context.confidence,
            "evidenceRefs": context.evidenceRefs,
        }

    def _resolve_evidence(
        self,
        general_id: str,
        context: ContextOption | None,
        keywords: list[UsedKeyword],
        evidence_refs: list[str],
    ) -> ResolvedEvidencePack:
        return self.evidence_resolver.resolve(general_id, context, keywords, evidence_refs)


def find_repo_root(start: Path) -> Path:
    override = (os.environ.get("NPC_REPO_ROOT") or "").strip()
    if override:
        return Path(override).resolve()

    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "app").exists() and (candidate / "pipelines/sanguo-rag").exists():
            return candidate
    raise FileNotFoundError("Could not locate standalone npc-brain repo root from current working directory.")
