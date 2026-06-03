from __future__ import annotations

import json
import os
import re
import hashlib
import subprocess
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
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
DEFAULT_HEALTH_ARTIFACT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted")
DEFAULT_HEALTH_ARTIFACT_CACHE_TTL_SECONDS = 120
DEFAULT_HEALTH_ARTIFACT_BASIS = "json-marker-path:v1-sorted"
HEALTH_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
HEALTH_VERSION_MARKER_KEYS = (
    "schemaVersion",
    "dataVersion",
    "datasetVersion",
    "snapshotVersion",
    "cacheVersion",
    "generatedAt",
    "version",
    "promptVersion",
    "cacheSchemaVersion",
)
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
SCENE_RENDER_MODE_DATA_FIRST = "data_first"
SCENE_RENDER_MODE_LLM_POLISH = "llm_polish"
SCENE_RENDER_MODE_LLM_SCRIPT_V2 = "llm_script_v2"
SUPPORTED_SCENE_RENDER_MODES = {
    SCENE_RENDER_MODE_DATA_FIRST,
    SCENE_RENDER_MODE_LLM_POLISH,
    SCENE_RENDER_MODE_LLM_SCRIPT_V2,
}
DEPRECATED_SCENE_RENDER_MODES = {
    SCENE_RENDER_MODE_DATA_FIRST: "deprecated:data_first_is_legacy_deterministic_fallback",
}
DEPRECATED_SCENE_RENDER_MODE_LABEL = "[已過時] legacy deterministic emergency fallback"
SCENE_SCRIPT_V2_STORY_TARGET_CJK_CHARS = 200
SCENE_SCRIPT_V2_STORY_MIN_CJK_CHARS = 180
SCENE_SCRIPT_V2_STORY_MAX_CJK_CHARS = 220
SCENE_SCRIPT_V2_STORY_RAW_OVERHEAD_CHARS = 60
SCENE_CHORUS_BOND_RELATIONSHIP_TYPES = {
    "sworn_sibling",
    "loyal_oath",
    "battle_ally",
    "protects_family",
    "sibling",
    "alliance_oath",
}
SCENE_CHORUS_HARD_EXCLUDED_RELATIONSHIP_TYPES = {
    "enemy_rival",
    "battlefield_opponent",
    "betrayal_surrender",
}
SCENE_CHORUS_EMOTION_FAMILY_RELATIONSHIP_TYPES = {
    "parent_child",
    "protects_family",
}
HARD_RELATIONSHIP_PAIR_TYPES: dict[frozenset[str], str] = {}
TARGET_ID_NAME_COLLISIONS: dict[str, dict[str, str]] = {
    "zhang-bao": {
        "張寶": "zhang-bao-enemy",
        "张宝": "zhang-bao-enemy",
        "地公將軍": "zhang-bao-enemy",
        "地公将军": "zhang-bao-enemy",
    },
}
YELLOW_TURBAN_TARGET_IDS = {"zhang-bao-enemy", "zhang-jiao", "zhang-liang"}
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
        "providerOrder": ["gemini_flash", "gemini_flash_lite"],
        "modelOverrides": {"__timeoutMs": "9000", "__retryCount": "2"},
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
    global YELLOW_TURBAN_TARGET_IDS

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
    sceneEligible: bool = True
    linkAuthority: str | None = None
    sourceDataStatus: str | None = None
    upstreamFeedbackRequired: bool = False


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
    sceneEligible: bool = True
    linkAuthority: str | None = None
    sourceDataStatus: str | None = None


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
    # [已過時] Default kept for API compatibility only; production callers should
    # send llm_script_v2 explicitly.
    renderMode: str = "data_first"
    chorusTargetIds: list[str] = Field(default_factory=list)
    locale: str = DEFAULT_LOCALE
    llmModelPreset: str = DEFAULT_LLM_MODEL_PRESET
    maxStoryChars: int = Field(default=560, ge=80, le=650)
    maxChorusChars: int = Field(default=110, ge=24, le=180)

    @model_validator(mode="after")
    def normalize_scene_director_fields(self):
        self.chorusTargetIds = list(dict.fromkeys(target_id for target_id in self.chorusTargetIds if target_id))
        if self.renderMode not in SUPPORTED_SCENE_RENDER_MODES:
            self.renderMode = SCENE_RENDER_MODE_DATA_FIRST
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
    generationMode: str | None = None
    fallbackUsed: bool = False
    cacheHit: bool = False
    providerTrace: list[str] = Field(default_factory=list)
    qualityWarnings: list[str] = Field(default_factory=list)
    evidenceRefs: list[str] = Field(default_factory=list)


class SceneEvidenceResolution(BaseModel):
    usedEvidenceRefs: list[str] = Field(default_factory=list)
    unresolvedEvidenceRefs: list[str] = Field(default_factory=list)
    resolutionTrace: list[str] = Field(default_factory=list)


class SceneSeedQuality(BaseModel):
    status: str = "empty"
    presentBeatFields: list[str] = Field(default_factory=list)
    missingBeatFields: list[str] = Field(default_factory=list)
    beatFieldCount: int = 0
    seedKeys: list[str] = Field(default_factory=list)
    seedKeyCount: int = 0


class SceneSourceCoverage(BaseModel):
    sourceRefCount: int = 0
    usedEvidenceRefCount: int = 0
    unresolvedEvidenceRefCount: int = 0
    storyUsedEvidenceRefCount: int = 0
    chorusEvidenceRefCount: int = 0


class SceneSelectionDebug(BaseModel):
    requestedTargetId: str | None = None
    selectedTargetId: str | None = None
    requestedEvidenceId: str | None = None
    selectedEvidenceId: str | None = None
    selectedContextKey: str | None = None
    selectedKeywordKeys: list[str] = Field(default_factory=list)
    selectedKeywordLabels: list[str] = Field(default_factory=list)
    requestedChorusTargetIds: list[str] = Field(default_factory=list)
    selectedChorusTargetIds: list[str] = Field(default_factory=list)


class SceneProviderDebug(BaseModel):
    provider: str | None = None
    model: str | None = None
    generationMode: str | None = None
    fallbackUsed: bool = False
    repairUsed: bool = False
    cacheHit: bool = False
    providerTrace: list[str] = Field(default_factory=list)
    qualityWarnings: list[str] = Field(default_factory=list)


class SceneDirectorDebugMetadata(BaseModel):
    hasSceneData: bool = False
    selectedEvidenceRefs: list[str] = Field(default_factory=list)
    rejectedReasons: list[str] = Field(default_factory=list)
    diagnosticIssues: list[str] = Field(default_factory=list)
    seedQuality: SceneSeedQuality = Field(default_factory=SceneSeedQuality)
    sourceCoverage: SceneSourceCoverage = Field(default_factory=SceneSourceCoverage)
    selection: SceneSelectionDebug = Field(default_factory=SceneSelectionDebug)
    story: SceneProviderDebug = Field(default_factory=SceneProviderDebug)
    chorus: list[SceneProviderDebug] = Field(default_factory=list)


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
    diagnostics: list[str] = Field(default_factory=list)
    evidenceResolution: SceneEvidenceResolution = Field(default_factory=SceneEvidenceResolution)
    beats: SceneDirectorBeats
    storyText: str
    storyProvider: str | None = None
    storyModel: str | None = None
    storyGenerationMode: str | None = None
    storyFallbackUsed: bool = False
    storyRepairUsed: bool = False
    storyCacheHit: bool = False
    storyQualityWarnings: list[str] = Field(default_factory=list)
    chorusLines: list[SceneChorusLine] = Field(default_factory=list)
    providerTrace: list[str] = Field(default_factory=list)
    debug: SceneDirectorDebugMetadata = Field(default_factory=SceneDirectorDebugMetadata)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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
        self.history_cache_enabled = _env_flag("NPC_LLM_HISTORY_CACHE_ENABLED", default=True)
        self.provider_router = provider_router or DialogueProviderRouter()
        self.evidence_resolver = EvidenceResolver(self.store)
        self.scene_image_renderer = GeminiSceneImageRenderer(cache_root=self.repo_root / "local/npc-scene-image-cache")
        self.scene_cache_enabled = _env_flag("NPC_SCENE_CACHE_ENABLED", default=False)
        self._roster_index_cache: dict[str, dict[str, Any]] | None = None
        self._source_event_packet_cache: list[dict[str, Any]] | None = None
        self._baihua_passage_cache: list[dict[str, Any]] | None = None
        self._chapter_paragraph_cache: dict[str, list[str]] = {}
        self._health_artifact_cache: dict[str, Any] | None = None
        self._health_artifact_cache_at = 0.0
        self._health_artifact_cache_ttl_seconds = DEFAULT_HEALTH_ARTIFACT_CACHE_TTL_SECONDS

    def _load_relationship_runtime_canon_policy(self) -> dict[str, Any]:
        candidates = [self.repo_root / DEFAULT_RELATIONSHIP_RUNTIME_CANON_POLICY_LOCAL_PATH]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"Relationship runtime canon policy must be an object: {path}")
        if payload.get("id") != "Policy_RelationshipRuntimeCanon_P1":
            raise ValueError(f"Unexpected relationship runtime canon policy id: {path}")
        return payload

    def _describe_path_snapshot(self, path: Path) -> dict[str, Any]:
        exists = path.exists()
        snapshot: dict[str, Any] = {
            "path": str(path),
            "exists": exists,
        }
        if not exists:
            return snapshot
        stat = path.stat()
        snapshot.update(
            {
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "sizeBytes": stat.st_size,
            }
        )
        return snapshot

    def _is_semver(self, value: str) -> bool:
        return bool(HEALTH_SEMVER_PATTERN.match(value))

    def _read_repo_git_sha(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
        return completed.stdout.strip()

    def _resolve_health_data_version(self) -> tuple[str, str]:
        for env_name in ("RENDER_GIT_COMMIT", "GITHUB_SHA", "BUILD_SHA"):
            configured = str(os.environ.get(env_name) or "").strip()
            if configured and re.fullmatch(r"[0-9a-fA-F]{7,40}", configured):
                return configured.lower(), f"env:{env_name}"

        repo_sha = self._read_repo_git_sha()
        if repo_sha and re.fullmatch(r"[0-9a-fA-F]{7,40}", repo_sha):
            return repo_sha.lower(), "git:HEAD"

        return "unknown", "fallback:unknown"

    def _extract_health_markers(self, payload: object) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        markers: dict[str, str] = {}
        for key in HEALTH_VERSION_MARKER_KEYS:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                markers[key] = text
        return markers

    def _compute_health_artifact_identity(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._health_artifact_cache and now - self._health_artifact_cache_at < self._health_artifact_cache_ttl_seconds:
            return dict(self._health_artifact_cache)

        root = self.repo_root / DEFAULT_HEALTH_ARTIFACT_ROOT
        digester = hashlib.sha256()
        total_files = 0
        marker_files = 0
        for path in sorted(root.rglob("*.json"), key=lambda item: str(item).replace("\\", "/")):
            total_files += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            markers = self._extract_health_markers(payload)
            if not markers:
                continue
            marker_files += 1
            rel_path = str(path.relative_to(self.repo_root)).replace("\\", "/")
            ordered_markers = {key: markers[key] for key in sorted(markers)}
            row = json.dumps(
                {"path": rel_path, "markers": ordered_markers},
                ensure_ascii=False,
                sort_keys=True,
            )
            digester.update(row.encode("utf-8"))

        digest = digester.hexdigest()[:24] if marker_files > 0 else "no-markers"
        result = {
            "artifactVersion": digest,
            "artifactVersionKind": "sha256",
            "artifactVersionBasis": DEFAULT_HEALTH_ARTIFACT_BASIS,
            "artifactVersionSource": "sha256:path+markers",
            "artifactVersionFileCount": marker_files,
            "artifactVersionScannedFiles": total_files,
            "artifactVersionScannedRoot": str(root),
        }
        self._health_artifact_cache = dict(result)
        self._health_artifact_cache_at = now
        return result

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
        data_version, data_version_source = self._resolve_health_data_version()
        artifact_identity = self._compute_health_artifact_identity()
        deployment_health = {
            "runtime": "render",
            "renderGitCommit": os.environ.get("RENDER_GIT_COMMIT"),
            "renderServiceId": os.environ.get("RENDER_SERVICE_ID"),
            "renderServiceName": os.environ.get("RENDER_SERVICE_NAME"),
            "renderExternalUrl": os.environ.get("RENDER_EXTERNAL_URL"),
            "githubSha": os.environ.get("GITHUB_SHA"),
            "buildSha": os.environ.get("BUILD_SHA"),
            "buildVersion": os.environ.get("BUILD_VERSION"),
            "deployedAt": os.environ.get("RENDER_DEPLOY_TIMESTAMP") or os.environ.get("BUILD_TIMESTAMP"),
            "dataVersion": data_version,
            "dataVersionSource": data_version_source,
            "artifactVersion": artifact_identity["artifactVersion"],
            "artifactVersionKind": artifact_identity["artifactVersionKind"],
            "artifactVersionBasis": artifact_identity["artifactVersionBasis"],
            "artifactVersionSource": artifact_identity["artifactVersionSource"],
        }
        return {
            "ok": True,
            "service": "npc-brain",
            "schemaVersion": "healthz.v2",
            "dataVersion": data_version,
            "dataVersionSource": data_version_source,
            "artifactVersion": artifact_identity["artifactVersion"],
            "artifactVersionKind": artifact_identity["artifactVersionKind"],
            "artifactVersionBasis": artifact_identity["artifactVersionBasis"],
            "artifactVersionSource": artifact_identity["artifactVersionSource"],
            "artifactVersionFileCount": artifact_identity["artifactVersionFileCount"],
            "artifactVersionScannedFiles": artifact_identity["artifactVersionScannedFiles"],
            "deployment": deployment_health,
            "runtimeSnapshots": {
                "personaRoot": self._describe_path_snapshot(self.persona_root),
                "runtimeProfileRoot": self._describe_path_snapshot(self.runtime_profile_root),
                "eventRoot": self._describe_path_snapshot(self.event_root),
                "readyEventsFile": self._describe_path_snapshot(self.event_root / "events.jsonl"),
                "sourceEventPacketsFile": self._describe_path_snapshot(
                    self.repo_root / "artifacts" / "data-pipeline" / "sanguo-rag" / "extracted" / "source-event-packets" / "source-event-packets.jsonl"
                ),
            },
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
                "sceneCacheEnabled": self.scene_cache_enabled,
                "historyCacheEnabled": self.scene_cache_enabled,
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
        actor_aliases = self._actor_label_aliases(profile)
        target = self._select_narrative_target(profile.interactionTargets, request.targetId)
        target_invalid = bool(request.targetId and target is None)
        card = self._select_narrative_card(
            profile.evidenceCards,
            request.evidenceId,
            request.angle,
            target,
            actor_aliases=actor_aliases,
        )
        evidence_invalid = bool(request.evidenceId and not any(card_item.evidenceId == request.evidenceId for card_item in profile.evidenceCards))
        semantic_empty_reason = self._scene_pair_empty_reason(request.angle, card, target)
        requested_evidence_refs = self._scene_requested_evidence_refs(request, card, target)
        evidence_pack = self._resolve_evidence(request.generalId, None, [], requested_evidence_refs)
        resolved_evidence_refs = [item.evidenceRef for item in evidence_pack.resolvedEvidence]
        unresolved_evidence_refs = [ref for ref in evidence_pack.unresolvedEvidenceRefs if ref]
        selection_issues = self._scene_selection_issues(
            request=request,
            profile=profile,
            target=target,
            card=card,
            actor_aliases=actor_aliases,
            requested_evidence_refs=requested_evidence_refs,
            resolved_evidence_refs=resolved_evidence_refs,
            unresolved_evidence_refs=unresolved_evidence_refs,
        )
        has_scene_data = (
            card is not None
            and not target_invalid
            and not evidence_invalid
            and not semantic_empty_reason
            and not unresolved_evidence_refs
        )
        if has_scene_data and any(issue in {"invalid_ref", "pair_mismatch", "angle_mismatch"} for issue in selection_issues):
            has_scene_data = False
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
        if unresolved_evidence_refs and data_status in {"direct", "angle_empty_filled", "target_empty_filled"}:
            data_status = "empty"
            fallback_reason = f"unresolvedEvidenceRefs={','.join(unresolved_evidence_refs[:3])}"
        if not has_scene_data and data_status != "invalid_request":
            data_status = "empty"
            if not fallback_reason:
                fallback_reason = f"selectionIssues={','.join(selection_issues[:3])}" if selection_issues else "data_missing"
        beats = self._build_scene_director_beats(profile, card, target, request.angle) if has_scene_data else self._build_empty_scene_director_beats()
        evidence_refs = requested_evidence_refs or sorted(set(beats.sourceRefs))
        evidence_resolution = SceneEvidenceResolution(
            usedEvidenceRefs=resolved_evidence_refs[:12],
            unresolvedEvidenceRefs=unresolved_evidence_refs[:12],
            resolutionTrace=[
                f"scene-director:{request.renderMode}",
                f"renderMode:{request.renderMode}",
                f"dataStatus:{data_status}",
                *evidence_pack.resolutionTrace,
                f"requestedRefs:{len(requested_evidence_refs)}",
                f"resolvedRefs:{len(resolved_evidence_refs)}",
                f"unresolvedRefs:{len(unresolved_evidence_refs)}",
            ],
        )
        story_context_key: str | None = None
        story_keywords: list[dict[str, Any]] = []
        chorus_targets: list[NarrativeInteractionTarget] = []
        if has_scene_data:
            story_context = self._build_scene_director_selected_context(profile, target, card, beats, request.renderMode)
            story_context_key = str(story_context.get("contextKey") or "").strip() or None
            story_keywords = self._scene_story_keywords(profile, target, card, beats)
            if request.renderMode == SCENE_RENDER_MODE_DATA_FIRST:
                # [已過時] legacy deterministic route. Keep this only for explicit
                # data_first requests and emergency fallback visibility.
                story_generation = DialogueGenerationResult(
                    text=self._scene_director_data_first_story_text(profile, target, card, beats, request.maxStoryChars),
                    provider="deterministic",
                    model=None,
                    generationMode="data_first-deterministic-deprecated",
                    fallbackUsed=not bool(resolved_evidence_refs),
                    providerTrace=[*evidence_resolution.resolutionTrace, "story:data_first_deterministic"],
                    usedEvidenceRefs=resolved_evidence_refs[:12],
                    qualityWarnings=self._deprecated_render_mode_warnings(request.renderMode),
                    repairUsed=False,
                )
            elif request.renderMode == SCENE_RENDER_MODE_LLM_SCRIPT_V2:
                try:
                    story_generation, beats = self._generate_scene_script_pack_v2(
                        request=request,
                        profile=profile,
                        target=target,
                        card=card,
                        beats=beats,
                        story_context=story_context,
                        story_keywords=story_keywords,
                        evidence_refs=evidence_refs,
                    )
                except Exception as exc:
                    log_debug_event(
                        "scene_director.script_pack_v2.error",
                        generalId=request.generalId,
                        targetId=target.targetId if target else None,
                        error=str(exc)[:240],
                    )
                    fallback_text = self._scene_director_data_first_story_text(profile, target, card, beats, request.maxStoryChars)
                    story_generation = DialogueGenerationResult(
                        text=fallback_text,
                        provider="unavailable",
                        model=None,
                        generationMode="llm_script_v2-fallback-data_first-deprecated",
                        fallbackUsed=True,
                        providerTrace=[*evidence_resolution.resolutionTrace, f"script-pack-v2-error:{str(exc)[:120]}"],
                        usedEvidenceRefs=resolved_evidence_refs[:12],
                        qualityWarnings=["scene_script_pack_v2_failed", *self._deprecated_render_mode_warnings(SCENE_RENDER_MODE_DATA_FIRST)],
                        repairUsed=False,
                    )
            else:
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
            story_generation = self._scene_script_v2_guard_story_generation(
                request=request,
                profile=profile,
                target=target,
                beats=beats,
                generation=story_generation,
            )
            self._record_scene_story_history(
                request=request,
                context_key=str(story_context.get("contextKey") or "").strip() or None,
                selected_keywords=story_keywords,
                evidence_refs=evidence_refs,
                generation=story_generation,
            )
            beats = self._enrich_scene_director_beats_from_story(profile, target, beats, story_generation.text)
            chorus_targets = self._select_chorus_targets(
                profile.interactionTargets,
                request.chorusTargetIds,
                target.targetId if target else None,
                request.angle or (card.angle if card else None),
                main_target=target,
                card=card,
                beats=beats,
                runtime_persona=profile.persona,
            )
            chorus_story_text = (
                str(story_generation.text or "").strip()
                or self._scene_seed_text(beats)
                or str(beats.sceneText or "").strip()
                or str(beats.memoryText or "").strip()
            )
            if request.renderMode == SCENE_RENDER_MODE_LLM_SCRIPT_V2:
                try:
                    chorus_lines = self._build_scene_chorus_batch_v2(
                        request=request,
                        profile=profile,
                        targets=chorus_targets,
                        main_target=target,
                        card=card,
                        beats=beats,
                        story_text=chorus_story_text,
                        timeout_seconds=self._scene_director_remaining_seconds(started_at),
                    )
                except Exception as exc:
                    log_debug_event(
                        "scene_director.chorus_batch_v2.error",
                        generalId=request.generalId,
                        targetCount=len(chorus_targets),
                        error=str(exc)[:240],
                    )
                    fallback_request = request.model_copy(update={"renderMode": SCENE_RENDER_MODE_DATA_FIRST})
                    # [已過時] emergency fallback: do not treat this as the normal
                    # chorus path after llm_script_v2 became the primary route.
                    chorus_lines = self._build_scene_chorus_lines(
                        request=fallback_request,
                        profile=profile,
                        targets=chorus_targets,
                        main_target=target,
                        card=card,
                        beats=beats,
                        story_text=chorus_story_text,
                        timeout_seconds=self._scene_director_remaining_seconds(started_at),
                    )
                    chorus_lines = [
                        line.model_copy(update={"qualityWarnings": [*line.qualityWarnings, "scene_chorus_batch_v2_failed", *self._deprecated_render_mode_warnings(SCENE_RENDER_MODE_DATA_FIRST)]})
                        for line in chorus_lines
                    ]
            else:
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
            empty_reason = fallback_reason or ("invalid_request" if data_status == "invalid_request" else "data_missing")
            story_generation = DialogueGenerationResult(
                text="",
                provider="data_first",
                model=None,
                generationMode="data_first-empty",
                fallbackUsed=False,
                providerTrace=[*evidence_resolution.resolutionTrace, f"empty:{empty_reason}"],
                qualityWarnings=self._deprecated_render_mode_warnings(request.renderMode),
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
        debug_metadata = self._build_scene_director_debug_metadata(
            request=request,
            data_status=data_status,
            target=target,
            card=card,
            beats=beats,
            has_scene_data=has_scene_data,
            evidence_resolution=evidence_resolution,
            story_generation=story_generation,
            story_context_key=story_context_key,
            story_keywords=story_keywords,
            chorus_targets=chorus_targets,
            chorus_lines=chorus_lines,
            selection_issues=selection_issues,
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
            emptyReason=empty_reason if is_empty else None,
            diagnostics=list(debug_metadata.diagnosticIssues),
            evidenceResolution=evidence_resolution,
            beats=beats,
            storyText=story_generation.text,
            storyProvider=story_generation.provider,
            storyModel=story_generation.model,
            storyGenerationMode=story_generation.generationMode,
            storyFallbackUsed=story_generation.fallbackUsed,
            storyRepairUsed=story_generation.repairUsed,
            storyCacheHit=self._generation_cache_hit(story_generation),
            storyQualityWarnings=list(story_generation.qualityWarnings),
            chorusLines=chorus_lines,
            providerTrace=story_generation.providerTrace,
            debug=debug_metadata,
        )

    def _generation_cache_hit(self, generation: DialogueGenerationResult) -> bool:
        provider = str(generation.provider or "").strip()
        if provider == "history_cache":
            return True
        trace = [str(item or "").strip() for item in generation.providerTrace]
        return any(item == "history_cache:ok" or item.startswith("history_cache:ok") for item in trace)

    def _scene_seed_quality(self, beats: SceneDirectorBeats) -> SceneSeedQuality:
        beat_values = {
            "sceneText": str(beats.sceneText or "").strip(),
            "memoryText": str(beats.memoryText or "").strip(),
            "emotionText": str(beats.emotionText or "").strip(),
            "dialogueText": str(beats.dialogueText or "").strip(),
            "intentText": str(beats.intentText or "").strip(),
        }
        present = [key for key, value in beat_values.items() if value]
        missing = [key for key, value in beat_values.items() if not value]
        seed_keys = sorted(str(key) for key, value in (beats.sceneSeeds or {}).items() if value)
        if present and not missing:
            status = "complete"
        elif present:
            status = "partial"
        else:
            status = "empty"
        return SceneSeedQuality(
            status=status,
            presentBeatFields=present,
            missingBeatFields=missing,
            beatFieldCount=len(present),
            seedKeys=seed_keys,
            seedKeyCount=len(seed_keys),
        )

    def _scene_source_coverage(
        self,
        evidence_resolution: SceneEvidenceResolution,
        beats: SceneDirectorBeats,
        story_generation: DialogueGenerationResult,
        chorus_lines: list[SceneChorusLine],
    ) -> SceneSourceCoverage:
        chorus_refs = sorted({ref for line in chorus_lines for ref in line.evidenceRefs})
        return SceneSourceCoverage(
            sourceRefCount=len(sorted(set(beats.sourceRefs))),
            usedEvidenceRefCount=len(evidence_resolution.usedEvidenceRefs),
            unresolvedEvidenceRefCount=len(evidence_resolution.unresolvedEvidenceRefs),
            storyUsedEvidenceRefCount=len(sorted(set(story_generation.usedEvidenceRefs))),
            chorusEvidenceRefCount=len(chorus_refs),
        )

    def _scene_rejected_reasons(
        self,
        story_generation: DialogueGenerationResult,
        chorus_lines: list[SceneChorusLine],
    ) -> list[str]:
        reasons: list[str] = []
        for warning in story_generation.qualityWarnings:
            if warning and warning not in reasons:
                reasons.append(str(warning))
        for line in chorus_lines:
            for warning in line.qualityWarnings:
                if warning and warning not in reasons:
                    reasons.append(str(warning))
        return reasons

    def _scene_diagnostic_issues(
        self,
        *,
        data_status: str,
        source_coverage: SceneSourceCoverage,
        story_generation: DialogueGenerationResult,
        chorus_target_count: int,
        chorus_lines: list[SceneChorusLine],
        selection_issues: list[str] | None = None,
    ) -> list[str]:
        issues: list[str] = []
        if data_status == "empty" or source_coverage.unresolvedEvidenceRefCount > 0:
            issues.append("data_missing")
        for issue in selection_issues or []:
            if issue and issue not in issues:
                issues.append(str(issue))
        story_provider = str(story_generation.provider or "").strip()
        if self.history_cache_enabled and (
            story_provider == "history_cache"
            or source_coverage.unresolvedEvidenceRefCount > 0
            or (source_coverage.usedEvidenceRefCount > 0 and source_coverage.storyUsedEvidenceRefCount == 0)
        ):
            issues.append("cache_pollution_risk")
        chorus_total = len(chorus_lines)
        chorus_fallback_count = sum(1 for line in chorus_lines if bool(line.fallbackUsed))
        if chorus_target_count > 0 and chorus_total == 0:
            issues.append("chorus_fallback")
        elif chorus_total > 0 and chorus_fallback_count >= chorus_total:
            issues.append("chorus_fallback")
        elif chorus_fallback_count > 0:
            issues.append("chorus_partial_fallback")
        return issues

    def _build_scene_director_debug_metadata(
        self,
        *,
        request: SceneDirectorRequest,
        data_status: str,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        has_scene_data: bool,
        evidence_resolution: SceneEvidenceResolution,
        story_generation: DialogueGenerationResult,
        story_context_key: str | None,
        story_keywords: list[dict[str, Any]],
        chorus_targets: list[NarrativeInteractionTarget],
        chorus_lines: list[SceneChorusLine],
        selection_issues: list[str] | None = None,
    ) -> SceneDirectorDebugMetadata:
        source_coverage = self._scene_source_coverage(evidence_resolution, beats, story_generation, chorus_lines)
        return SceneDirectorDebugMetadata(
            hasSceneData=has_scene_data,
            selectedEvidenceRefs=list(evidence_resolution.usedEvidenceRefs),
            rejectedReasons=self._scene_rejected_reasons(story_generation, chorus_lines),
            diagnosticIssues=self._scene_diagnostic_issues(
                data_status=data_status,
                source_coverage=source_coverage,
                story_generation=story_generation,
                chorus_target_count=len(chorus_targets),
                chorus_lines=chorus_lines,
                selection_issues=selection_issues,
            ),
            seedQuality=self._scene_seed_quality(beats),
            sourceCoverage=source_coverage,
            selection=SceneSelectionDebug(
                requestedTargetId=request.targetId,
                selectedTargetId=target.targetId if target else request.targetId,
                requestedEvidenceId=request.evidenceId,
                selectedEvidenceId=card.evidenceId if card else beats.evidenceId,
                selectedContextKey=story_context_key,
                selectedKeywordKeys=[str(keyword.get("keywordKey") or "") for keyword in story_keywords if str(keyword.get("keywordKey") or "").strip()],
                selectedKeywordLabels=[str(keyword.get("label") or "") for keyword in story_keywords if str(keyword.get("label") or "").strip()],
                requestedChorusTargetIds=list(request.chorusTargetIds),
                selectedChorusTargetIds=[target_item.targetId for target_item in chorus_targets if str(target_item.targetId or "").strip()],
            ),
            story=SceneProviderDebug(
                provider=story_generation.provider,
                model=story_generation.model,
                generationMode=story_generation.generationMode,
                fallbackUsed=story_generation.fallbackUsed,
                repairUsed=story_generation.repairUsed,
                cacheHit=self._generation_cache_hit(story_generation),
                providerTrace=list(story_generation.providerTrace),
                qualityWarnings=list(story_generation.qualityWarnings),
            ),
            chorus=[
                SceneProviderDebug(
                    provider=line.provider,
                    model=line.model,
                    generationMode=line.generationMode,
                    fallbackUsed=line.fallbackUsed,
                    repairUsed=False,
                    cacheHit=line.cacheHit,
                    providerTrace=list(line.providerTrace),
                    qualityWarnings=list(line.qualityWarnings),
                )
                for line in chorus_lines
            ],
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
            reason = "targetId_invalid" if target_invalid else "evidenceId_invalid"
            return "invalid_request", reason
        if semantic_empty_reason:
            return "empty", "semantic_empty"
        if card is None:
            return "empty", "data_missing"
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

    def _scene_selection_issues(
        self,
        *,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        actor_aliases: list[str],
        requested_evidence_refs: list[str],
        resolved_evidence_refs: list[str],
        unresolved_evidence_refs: list[str],
    ) -> list[str]:
        issues: list[str] = []
        if request.evidenceId and not any(card_item.evidenceId == request.evidenceId for card_item in profile.evidenceCards):
            issues.append("invalid_ref")
        if unresolved_evidence_refs and "invalid_ref" not in issues:
            issues.append("invalid_ref")
        grounded_target_cards = [
            candidate
            for candidate in profile.evidenceCards
            if target is not None and self._card_matches_target(candidate, target) and self._card_source_matches_scene(candidate, target, actor_aliases)
        ] if target is not None else []
        if target is not None and card is None and target.sourceType == "relationship-edge":
            issues.append("pair_mismatch")
        if target is not None and card is not None and not self._card_source_matches_scene(card, target, actor_aliases):
            issues.append("pair_mismatch")
        if request.angle and card is not None and card.angle != request.angle:
            issues.append("angle_mismatch")
        if request.angle and card is None and grounded_target_cards:
            issues.append("angle_mismatch")
        if card is not None and len([ref for ref in card.sourceRefs if ref]) < 2:
            issues.append("evidence_context_short")
        if not requested_evidence_refs and not resolved_evidence_refs and card is not None:
            issues.append("evidence_context_short")
        return list(dict.fromkeys(issues))

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
        target_cards = [
            card
            for card in cards
            if self._card_matches_target(card, target) and self._card_source_matches_scene(card, target, actor_aliases)
        ] if target is not None else []
        if target is not None:
            if angle:
                matching_angle_cards = [card for card in target_cards if card.angle == angle]
                if matching_angle_cards:
                    return sorted(
                        matching_angle_cards,
                        key=lambda card: (
                            0 if card.sourceType == "runtime-relationship-edge" else 1,
                            -self._coerce_float(card.confidence, default=0.0),
                            -len(card.sourceRefs or []),
                            str(card.evidenceId or ""),
                        ),
                    )[0]
                return None
            if target_cards:
                return sorted(
                    target_cards,
                    key=lambda card: (
                        0 if card.sourceType == "runtime-relationship-edge" else 1,
                        -self._coerce_float(card.confidence, default=0.0),
                        -len(card.sourceRefs or []),
                        str(card.evidenceId or ""),
                    ),
                )[0]
            return None
        if angle:
            for card in cards:
                if card.angle == angle and self._card_source_matches_scene(card, target, actor_aliases):
                    return card
            return None
        return cards[0] if cards else None

    def _card_matches_target(
        self,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> bool:
        if card is None or target is None:
            return card is not None
        related_target_ids = set(card.relatedTargetIds or [])
        if target.targetId in related_target_ids:
            return True
        # Some Render payloads omit relatedTargetIds on otherwise grounded cards.
        # Fall back to the source text itself so directly named pairs can still surface.
        return self._card_source_mentions_target(card, target)

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
            and target is not None
            and target.targetId in set(card.relatedTargetIds or [])
        ):
            return True
        units = self._card_match_text_units(card)
        if target_aliases and actor_aliases:
            if self._card_pair_grounded_in_source(card, target_aliases, actor_aliases):
                return True
            if (
                card.sourceType == "runtime-relationship-edge"
                and target is not None
                and target.targetId in set(card.relatedTargetIds or [])
                and (
                    self._card_source_mentions_any(card, target_aliases)
                    or self._card_source_mentions_any(card, actor_aliases)
                )
            ):
                return True
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
                return self._source_window_has_interaction_signal(sentence)
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

    def _scene_requested_evidence_refs(
        self,
        request: SceneDirectorRequest,
        card: NarrativeEvidenceCard | None,
        target: NarrativeInteractionTarget | None,
    ) -> list[str]:
        refs: list[str] = []
        target_refs = list(target.evidenceRefs) if target else []
        card_source_refs = list(card.sourceRefs) if card else []
        supplemental_target_refs = target_refs if not card_source_refs else []
        for raw_ref in [
            request.evidenceId,
            card.evidenceId if card and self._is_resolvable_scene_evidence_ref(card.evidenceId) else None,
            *card_source_refs,
            *supplemental_target_refs,
        ]:
            ref = str(raw_ref or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        return refs[:12]

    def _is_resolvable_scene_evidence_ref(self, ref: str | None) -> bool:
        value = str(ref or "").strip()
        if not value:
            return False
        if value.startswith("ext-card:"):
            return True
        return bool(re.match(r"^\d{3}#.+$", value))

    def _select_chorus_targets(
        self,
        targets: list[NarrativeInteractionTarget],
        requested_ids: list[str],
        active_target_id: str | None,
        angle: str | None = None,
        main_target: NarrativeInteractionTarget | None = None,
        card: NarrativeEvidenceCard | None = None,
        beats: SceneDirectorBeats | None = None,
        runtime_persona: dict[str, Any] | None = None,
    ) -> list[NarrativeInteractionTarget]:
        candidate_targets = self._scene_chorus_candidate_targets_for_angle(
            targets,
            angle,
            main_target=main_target,
            card=card,
            beats=beats,
            runtime_persona=runtime_persona or {},
        )
        by_id = {target.targetId: target for target in candidate_targets}
        selected = [by_id[target_id] for target_id in requested_ids if target_id in by_id and target_id != active_target_id]
        selected_ids = {target.targetId for target in selected}
        for target in candidate_targets:
            if target.targetId == active_target_id or target.targetId in selected_ids:
                continue
            selected.append(target)
            selected_ids.add(target.targetId)
            if len(selected) >= 4:
                break
        return selected[:4]

    def _scene_chorus_candidate_targets_for_angle(
        self,
        targets: list[NarrativeInteractionTarget],
        angle: str | None,
        main_target: NarrativeInteractionTarget | None = None,
        card: NarrativeEvidenceCard | None = None,
        beats: SceneDirectorBeats | None = None,
        runtime_persona: dict[str, Any] | None = None,
    ) -> list[NarrativeInteractionTarget]:
        normalized_angle = str(angle or "").strip()
        eligible_targets = [target for target in targets if target.sceneEligible]
        if normalized_angle == "bond":
            bond_targets = [target for target in eligible_targets if self._is_bond_chorus_target(target)]
            if bond_targets:
                return bond_targets
            return [
                target
                for target in eligible_targets
                if not target.femaleFocus
                and str(target.relationshipType or "") not in {
                    *SCENE_CHORUS_HARD_EXCLUDED_RELATIONSHIP_TYPES,
                    "spouse",
                    "lover",
                    "political_contact",
                    "resource_support",
                }
            ]
        if normalized_angle == "emotion":
            source_text = self._scene_chorus_emotion_source_text(card, beats)
            emotional_targets = [
                target
                for target in eligible_targets
                if self._is_emotion_chorus_target(
                    target,
                    main_target=main_target,
                    source_text=source_text,
                    runtime_persona=runtime_persona or {},
                )
            ]
            if emotional_targets:
                return emotional_targets
            family_targets = [
                target
                for target in eligible_targets
                if self._is_emotion_family_chorus_target(target, main_target=main_target)
            ]
            if family_targets:
                return family_targets
            return []
        return [
            target
            for target in eligible_targets
            if str(target.relationshipType or "") not in SCENE_CHORUS_HARD_EXCLUDED_RELATIONSHIP_TYPES
        ]

    def _is_bond_chorus_target(self, target: NarrativeInteractionTarget) -> bool:
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type not in SCENE_CHORUS_BOND_RELATIONSHIP_TYPES:
            return False
        if target.femaleFocus or relationship_type in {"spouse", "lover"}:
            return False
        return True

    def _scene_chorus_emotion_source_text(
        self,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats | None,
    ) -> str:
        scene_seeds = beats.sceneSeeds if beats else {}
        return " ".join(
            str(part or "").strip()
            for part in [
                card.summary if card else "",
                card.quote if card else "",
                beats.sceneText if beats else "",
                beats.memoryText if beats else "",
                beats.emotionText if beats else "",
                scene_seeds.get("event") if isinstance(scene_seeds, dict) else "",
                scene_seeds.get("emotion") if isinstance(scene_seeds, dict) else "",
                scene_seeds.get("place") if isinstance(scene_seeds, dict) else "",
            ]
            if str(part or "").strip()
        )

    def _is_emotion_chorus_target(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        source_text: str,
        runtime_persona: dict[str, Any],
    ) -> bool:
        if main_target and target.targetId == main_target.targetId:
            return False
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type in SCENE_CHORUS_HARD_EXCLUDED_RELATIONSHIP_TYPES:
            return False
        if relationship_type in {"spouse", "lover"}:
            return False
        if target.femaleFocus:
            return self._is_valid_emotion_target(target, source_text=source_text, runtime_persona=runtime_persona)
        if relationship_type in SCENE_CHORUS_EMOTION_FAMILY_RELATIONSHIP_TYPES:
            return True
        if relationship_type == "sibling":
            return self._scene_chorus_target_matches_source_text(target, source_text)
        return False

    def _is_emotion_family_chorus_target(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
    ) -> bool:
        if main_target and target.targetId == main_target.targetId:
            return False
        return str(target.relationshipType or "").strip() in SCENE_CHORUS_EMOTION_FAMILY_RELATIONSHIP_TYPES

    def _scene_chorus_target_matches_source_text(self, target: NarrativeInteractionTarget, source_text: str) -> bool:
        text = str(source_text or "")
        if not text:
            return False
        aliases = self._target_label_aliases(
            target.label,
            allow_family_titles=target.femaleFocus or self._is_female_gender(target.gender),
        )
        return any(alias and alias in text for alias in aliases)

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
        actor_aliases = self._actor_label_aliases(profile)
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
            strict_scene_match = self._card_source_matches_scene(candidate, target, actor_aliases)
            if (
                primary_refs
                and candidate.evidenceId != (card.evidenceId if card else None)
                and not (candidate_refs & primary_refs)
            ):
                if not (
                    allow_target_context_expansion
                    and target.targetId in candidate.relatedTargetIds
                    and bool(candidate_refs & primary_refs)
                    and strict_scene_match
                    and candidate.sourceType != "runtime-relationship-edge"
                ):
                    continue
            is_related = (
                candidate.evidenceId == (card.evidenceId if card else None)
                or (target.targetId in candidate.relatedTargetIds and strict_scene_match)
                or (bool(candidate_refs & primary_refs) and strict_scene_match)
                or (bool(candidate_refs & target_refs) and strict_scene_match and not primary_refs)
                or (mentions_target and strict_scene_match)
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
        options: list[tuple[float, int, str]] = []
        primary_options: list[tuple[float, int, str]] = []
        target_aliases = self._target_aliases_for_interaction(target) if target else []
        order = 0
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
                    option = (score, order, cleaned)
                    if index == 0:
                        primary_options.append(option)
                    options.append(option)
                    order += 1
        if primary_options:
            return sorted(primary_options, key=lambda item: (-item[0], item[1]))[0][2]
        if not options:
            return ""
        return sorted(options, key=lambda item: (-item[0], item[1]))[0][2]

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
            self._seed_display_sentence(profile, scene_text, 120, preserve_actor_aliases=True),
            self._seed_display_sentence(profile, memory_text, 120, preserve_actor_aliases=True),
            self._seed_display_sentence(profile, emotion_text, 96),
            self._seed_display_sentence(profile, dialogue_text, 80),
            self._seed_display_sentence(profile, intent_text, 88),
        )

    def _enrich_scene_director_beats_from_story(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> SceneDirectorBeats:
        story = str(story_text or "").strip()
        if not story:
            return beats
        emotion_text = str(beats.emotionText or "").strip() or self._relationship_derived_emotion_text(target)
        if not emotion_text:
            emotion_text = self._story_derived_emotion_text(story)
        dialogue_text = str(beats.dialogueText or "").strip() or self._story_derived_dialogue_text(story)
        intent_text = str(beats.intentText or "").strip() or self._story_derived_intent_text(story)
        scene_seeds = dict(beats.sceneSeeds or {})
        if emotion_text and not str(scene_seeds.get("emotion") or "").strip():
            scene_seeds["emotion"] = self._clean_seed_text(emotion_text, max_chars=90)
        updates = {
            "emotionText": self._seed_display_sentence(profile, emotion_text, 96) if emotion_text else str(beats.emotionText or ""),
            "dialogueText": self._seed_display_sentence(profile, dialogue_text, 80) if dialogue_text else str(beats.dialogueText or ""),
            "intentText": self._seed_display_sentence(profile, intent_text, 88, preserve_actor_aliases=True) if intent_text else str(beats.intentText or ""),
            "sceneSeeds": scene_seeds,
        }
        return beats.model_copy(update=updates)

    def _relationship_derived_emotion_text(self, target: NarrativeInteractionTarget | None) -> str:
        if not target:
            return ""
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type in {"spouse", "lover"}:
            return "家室牽掛"
        if relationship_type in {"parent_child", "sibling", "protects_family"}:
            return "護念"
        if relationship_type in {"sworn_sibling", "battle_ally", "loyal_oath"}:
            return "義氣"
        if relationship_type in {"enemy_rival", "battlefield_opponent"}:
            return "戒備"
        if relationship_type in {"ruler_subject", "political_contact"}:
            return "克制"
        if relationship_type in {"battlefield_contact", "mentor_student", "mentor"}:
            return "審勢"
        if target.femaleFocus:
            return "牽掛"
        return ""

    def _seed_display_sentence(
        self,
        profile: NarrativeProfileResponse,
        text: str,
        max_chars: int,
        preserve_actor_aliases: bool = False,
    ) -> str:
        value = str(text or "").strip() if preserve_actor_aliases else self._replace_actor_aliases_in_seed(profile, text)
        if self._is_weak_scene_seed_text(value):
            return ""
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
            if re.search(r"[「『」』]", clause):
                score -= 18.0
            if re.search(r"(?:曰|說|道|云|問|答|泣告|告)", clause):
                score -= 12.0
            for term in emotion_terms:
                if term in clause:
                    score += 12.0
            if re.search(r"垂淚|流淚|落淚|含淚|暗暗|悄悄|煩惱|擔憂|牽掛|不安|悲|憂|慌", clause):
                score += 8.0
            if "心" in clause:
                score += 4.0
            if len(clause) <= 28:
                score += 2.0
            if score > best_score:
                best = clause
                best_score = score
        if best_score <= 0:
            return ""
        if re.search(r"[「『」』]", best):
            return ""
        if re.search(r"(?:曰|說|道|云|問|答|泣告|告)", best):
            return ""
        return best

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
        if re.search(r"[（(]\s*(?:此人|主角)\s*[）)]", value):
            return True
        if re.fullmatch(r"[（(]?\s*(?:此人|主角)\s*[）)]?", value):
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
        candidates: list[tuple[int, int, str]] = []
        order = 0
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
            candidates.append((score, order, normalized))
            order += 1
        if not candidates:
            return ""
        return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][2]

    def _extract_quoted_dialogue(self, quote: str) -> str:
        text = str(quote or "").strip()
        if not text:
            return ""
        matches = re.findall(r"[「『“\"]([^」』”\"]{2,80})[」』”\"]", text)
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
        excerpt = self._extract_target_source_excerpt(
            source_text=text,
            target=target,
            max_chars=120,
            include_previous=False,
            prefer_target_only=True,
        )
        if self._is_weak_scene_seed_text(excerpt) or len(str(excerpt or "").strip()) < 20:
            expanded = self._extract_target_source_excerpt(
                source_text=text,
                target=target,
                max_chars=160,
                include_previous=True,
                prefer_target_only=False,
            )
            if expanded and (not excerpt or len(expanded) >= len(excerpt)):
                excerpt = expanded
        if self._is_weak_scene_seed_text(excerpt):
            return ""
        return excerpt

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
        text = str(source_text or "").strip()
        if not text:
            return ""
        if target and target.femaleFocus and re.search(r"家眷|家室|夫人|妻|孫尚香|孫夫人|二嫂|嫂嫂", text):
            return "先把家眷安頓好"
        if re.search(r"家眷|家室|夫人|妻|孫尚香|孫夫人|二嫂|嫂嫂", text):
            return "先把家眷安頓好"
        if re.search(r"兵馬|人馬|軍|追兵", text):
            return "先把人馬安排穩"
        if re.search(r"江邊|渡口|船|水路|河岸|江上", text):
            return "先把去路安排穩"
        if re.search(r"垂淚|煩惱|牽掛|悲|憂|不安|愁", text):
            return "先把心裡這口氣安住"
        best = ""
        best_score = -1.0
        for clause in self._story_seed_clauses(text, max_chars=88):
            score = 0.0
            for term in ("先", "要", "必須", "安排", "安頓", "收攏", "護住", "接住", "守住", "留住", "看住", "撤", "退"):
                if term in clause:
                    score += 8.0
            if re.search(r"[「『」』]", clause):
                score -= 8.0
            if re.search(r"(此人|主角)", clause):
                score -= 30.0
            if len(clause) <= 26:
                score += 2.0
            if score > best_score:
                best = clause
                best_score = score
        if best_score > 0 and not self._is_weak_scene_seed_text(best):
            if re.search(r"先|要|安排|安頓|收攏|護住|接住|守住|留住|看住|撤|退", best):
                return best
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
        def candidate_score(index: int) -> float:
            sentence = next(sentence for sentence_index, sentence in readable if sentence_index == index)
            score = 0.0
            if target_aliases and any(alias and alias in sentence for alias in target_aliases):
                score += 18.0
            if re.search(r"[「『」』]|(?:曰|說|道|云|問|答|泣告|告)", sentence):
                score += 22.0
            if re.search(r"入見|暗暗|垂淚|欲言又止|煩惱|相見|起身|上前|回頭", sentence):
                score += 6.0
            if re.search(r"先是|既而|當時|次日|正旦|年終|長坂|古城|江邊|官道|南徐|莊外|重圍|追兵", sentence):
                score -= 8.0
            if len(sentence) <= 24:
                score += 3.0
            return score

        candidate_indexes = target_indexes if target_indexes else ([] if prefer_target_only else [index for index, _ in readable])
        if candidate_indexes:
            candidate_indexes = sorted(candidate_indexes, key=candidate_score, reverse=True)
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
            "version": 4,
            "generalId": profile.generalId,
            "targetId": target.targetId if target else None,
            "targetRole": target.role if target else None,
            "angle": card.angle if card else None,
            "renderMode": render_mode,
            "sceneSeeds": self._scene_chorus_sanitized_seeds(beats),
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
        if render_mode == SCENE_RENDER_MODE_DATA_FIRST:
            model_overrides = dict(preset_config.get("modelOverrides") or {})
            model_overrides["__timeoutMs"] = "0"
            model_overrides["__retryCount"] = "0"
            return {
                "providerOrder": ["deterministic"],
                "modelOverrides": model_overrides,
                "allowDeterministicFallback": True,
            }
        base_order = list(preset_config.get("providerOrder") or self.provider_router.provider_order or [])
        provider_order: list[str] = []
        if self.history_cache_enabled and render_mode != SCENE_RENDER_MODE_LLM_SCRIPT_V2:
            provider_order.append("history_cache")
        preferred = ["gemini_flash_lite", "gemini_flash", "gemini"]
        for provider_name in [*base_order, *preferred]:
            if provider_name in {"history_cache", "deterministic"}:
                continue
            if provider_name not in provider_order:
                provider_order.append(provider_name)
        model_overrides = dict(preset_config.get("modelOverrides") or {})
        model_overrides.setdefault("__timeoutMs", "6000")
        model_overrides.setdefault("__retryCount", "1")
        return {
            "providerOrder": provider_order,
            "modelOverrides": model_overrides,
            "allowDeterministicFallback": render_mode == SCENE_RENDER_MODE_LLM_SCRIPT_V2,
        }

    def _deprecated_render_mode_warnings(self, render_mode: str) -> list[str]:
        warning = DEPRECATED_SCENE_RENDER_MODES.get(render_mode)
        return [warning] if warning else []

    def _scene_script_v2_story_target_cjk_chars(self, request: SceneDirectorRequest) -> int:
        requested = int(request.maxStoryChars or SCENE_SCRIPT_V2_STORY_TARGET_CJK_CHARS)
        if SCENE_SCRIPT_V2_STORY_MIN_CJK_CHARS <= requested <= SCENE_SCRIPT_V2_STORY_MAX_CJK_CHARS:
            return requested
        return SCENE_SCRIPT_V2_STORY_TARGET_CJK_CHARS

    def _scene_script_v2_story_min_cjk_chars(self, request: SceneDirectorRequest) -> int:
        return max(
            SCENE_SCRIPT_V2_STORY_MIN_CJK_CHARS,
            self._scene_script_v2_story_target_cjk_chars(request) - 20,
        )

    def _scene_script_v2_story_max_cjk_chars(self, request: SceneDirectorRequest) -> int:
        return min(
            SCENE_SCRIPT_V2_STORY_MAX_CJK_CHARS,
            self._scene_script_v2_story_target_cjk_chars(request) + 20,
        )

    def _scene_script_v2_story_max_chars(self, request: SceneDirectorRequest) -> int:
        return self._scene_script_v2_story_target_cjk_chars(request) + SCENE_SCRIPT_V2_STORY_RAW_OVERHEAD_CHARS

    def _scene_script_v2_prompt_budget(self, request: SceneDirectorRequest) -> int:
        return max(620, self._scene_script_v2_story_max_chars(request) + 360)

    def _scene_script_v2_fit_story_text(self, text: str, request: SceneDirectorRequest) -> tuple[str, list[str]]:
        max_chars = self._scene_script_v2_story_max_cjk_chars(request)
        fitted = self._sentence_or_default(text, "", max_chars=max_chars)
        warnings: list[str] = []
        if len(fitted) > max_chars:
            completed = self._complete_generated_text(fitted, "", max_chars)
            if completed:
                fitted = completed
            else:
                trimmed = fitted[: max_chars - 1].rstrip("，。！？；、：\"' 」』）)]")
                fitted = self._ensure_sentence(trimmed)
            warnings.append("scene_script_v2_story_clamped_to_raw_limit")
        compact = self._scene_script_v2_compact_story_text(fitted)
        if len(compact) > self._scene_script_v2_story_max_cjk_chars(request):
            fitted = self._trim_scene_script_v2_story_to_cjk_limit(
                fitted,
                self._scene_script_v2_story_max_cjk_chars(request),
            )
            warnings.append("scene_script_v2_story_trimmed_to_target")
        return fitted, warnings

    def _scene_script_v2_story_compare_text(self, text: str) -> str:
        return re.sub(r"[\s,，。．.!?！？；;：:/／\\｜|、（）()《》〈〉【】\[\]{}\"'“”‘’·…—-]+", "", str(text or "").strip())

    def _scene_script_v2_story_seed_candidates(self, value: Any, max_terms: int = 6) -> list[str]:
        candidates: list[str] = []
        cleaned = self._clean_seed_text(str(value or ""), max_chars=120).strip()
        if cleaned:
            candidates.append(cleaned)
        for phrase in self._scene_phrase_candidates(cleaned, max_terms=max_terms):
            if phrase and phrase not in candidates:
                candidates.append(phrase)
        return candidates[:max_terms]

    def _scene_script_v2_story_seed_options(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, list[str]]:
        options: dict[str, list[str]] = {key: [] for key in ["people", "event", "time", "place", "objects", "emotion"]}
        scene_seeds = beats.sceneSeeds or {}

        def add(category: str, value: Any, max_terms: int = 6) -> None:
            for candidate in self._scene_script_v2_story_seed_candidates(value, max_terms=max_terms):
                if candidate and candidate not in options[category]:
                    options[category].append(candidate)

        add("people", profile.displayName, 4)
        for alias in self._actor_label_aliases(profile)[:4]:
            add("people", alias, 4)
        if target:
            add("people", target.label, 4)
            for alias in self._target_aliases_for_interaction(target)[:4]:
                add("people", alias, 4)
        for person in scene_seeds.get("people") or []:
            if isinstance(person, dict):
                add("people", person.get("label") or person.get("name") or "", 4)
            else:
                add("people", person, 4)
        add("event", scene_seeds.get("event") or beats.sceneText or beats.memoryText, 6)
        add("time", scene_seeds.get("time"), 4)
        add("place", scene_seeds.get("place"), 4)
        for obj in scene_seeds.get("objects") or []:
            add("objects", obj, 4)
        add("emotion", scene_seeds.get("emotion") or beats.emotionText, 4)
        return options

    def _scene_script_v2_story_coverage(
        self,
        story_text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, list[str]]:
        story_compare = self._scene_script_v2_story_compare_text(story_text)
        coverage: dict[str, list[str]] = {}
        for category, candidates in self._scene_script_v2_story_seed_options(profile, target, beats).items():
            matched: list[str] = []
            for candidate in candidates:
                candidate_compare = self._scene_script_v2_story_compare_text(candidate)
                if not candidate_compare:
                    continue
                if candidate_compare in story_compare or candidate in str(story_text or ""):
                    if candidate not in matched:
                        matched.append(candidate)
            if matched:
                coverage[category] = matched[:4]
        return coverage

    def _scene_script_v2_story_is_seed_dump(self, story_text: str) -> bool:
        cleaned = str(story_text or "").strip()
        if not cleaned:
            return False
        if any(symbol in cleaned for symbol in [";", "；", "/", "／"]):
            return True
        if re.search(r"(?:people|event|time|place|objects|emotion)\s*[:：]", cleaned, re.I):
            return True
        if re.search(r"(?:人|事|時|地|物|感情)\s*[:：]", cleaned):
            return True
        if re.search(r"(?:people|event|time|place|objects|emotion)(?:[、,，]\s*(?:people|event|time|place|objects|emotion)){2,}", cleaned, re.I):
            return True
        if re.search(r"(?:人|事|時|地|物|感情)(?:[、,，]\s*(?:人|事|時|地|物|感情)){2,}", cleaned):
            return True
        if re.search(r"\b(?:sceneSeeds|usedSeedKeys|memoryText|emotionText|dialogueText|intentText)\b", cleaned):
            return True
        return False

    def _scene_script_v2_story_narration_text(self, story_text: str) -> str:
        narration = str(story_text or "")
        if not narration:
            return ""
        narration = re.sub(r"「[^」]{0,120}」", "", narration)
        narration = re.sub(r"『[^』]{0,120}』", "", narration)
        narration = re.sub(r"“[^”]{0,120}”", "", narration)
        narration = re.sub(r"\"[^\"]{0,120}\"", "", narration)
        narration = re.sub(r"[\u4e00-\u9fff]{1,8}(?:說|問|答|回|道|曰|呼|叫|喊)[:：][^。！？!?]{0,120}", "", narration)
        return " ".join(narration.split()).strip()

    def _scene_script_v2_story_has_first_person_narration(self, story_text: str) -> bool:
        narration = self._scene_script_v2_story_narration_text(story_text)
        if not narration:
            return False
        return any(marker in narration for marker in ("我", "吾", "俺", "咱", "孤", "朕"))

    def _scene_script_v2_story_sentences(self, story_text: str) -> list[str]:
        normalized = " ".join(str(story_text or "").split()).strip()
        if not normalized:
            return []
        return [sentence.strip() for sentence in re.findall(r"[^。！？!?]+[。！？!?]?", normalized) if sentence.strip()]

    def _scene_script_v2_story_apply_outside_quotes(self, story_text: str, transform) -> str:
        text = str(story_text or "")
        if not text:
            return ""
        quote_pattern = r"(「[^」]{0,120}」|『[^』]{0,120}』|“[^”]{0,120}”|\"[^\"]{0,120}\")"
        pieces = re.split(quote_pattern, text)
        rebuilt: list[str] = []
        for piece in pieces:
            if not piece:
                continue
            if re.fullmatch(quote_pattern, piece):
                rebuilt.append(piece)
            else:
                rebuilt.append(str(transform(piece)))
        return "".join(rebuilt)

    def _scene_script_v2_story_target_name(self, target: NarrativeInteractionTarget | None) -> str:
        return self._clean_seed_text(target.label if target else "", max_chars=20).strip()

    def _scene_script_v2_story_target_aliases(self, target: NarrativeInteractionTarget | None) -> list[str]:
        if not target:
            return []
        aliases: list[str] = []
        for alias in [target.label, *self._target_aliases_for_interaction(target)[:8]]:
            cleaned = self._clean_seed_text(alias, max_chars=20).strip()
            if cleaned and cleaned not in aliases:
                aliases.append(cleaned)
            if cleaned.endswith("夫人") and "夫人" not in aliases:
                aliases.append("夫人")
        return aliases

    def _scene_script_v2_story_canonicalize_target_naming(
        self,
        story_text: str,
        target: NarrativeInteractionTarget | None,
    ) -> str:
        canonical_name = self._scene_script_v2_story_target_name(target)
        aliases = [alias for alias in self._scene_script_v2_story_target_aliases(target) if alias and alias != canonical_name]
        if not canonical_name or not aliases:
            return str(story_text or "")

        def replace_aliases(segment: str) -> str:
            updated = str(segment or "")
            for alias in aliases:
                updated = updated.replace(alias, canonical_name)
            if canonical_name:
                updated = updated.replace(f"{canonical_name[0]}{canonical_name}", canonical_name)
            return updated

        return self._scene_script_v2_story_apply_outside_quotes(story_text, replace_aliases)

    def _scene_script_v2_story_canonicalize_actor_clause(
        self,
        story_text: str,
        profile: NarrativeProfileResponse,
    ) -> str:
        main_name = self._clean_seed_text(profile.displayName or "", max_chars=20).strip()
        if not main_name:
            return str(story_text or "")
        updated = str(story_text or "")
        for alias in self._actor_label_aliases(profile):
            if alias and alias != main_name:
                updated = updated.replace(alias, main_name)
        updated = updated.replace(f"{main_name[0]}{main_name}", main_name)
        short_name = main_name[-1] if len(main_name) >= 2 else ""
        if short_name:
            updated = re.sub(
                rf"(^|[，。！？；、]){re.escape(short_name)}(?=(?:便|只得|知道|深知|打算|決意|決定|希望|感到|深感|越發|仍|想|欲|心中|心頭))",
                rf"\1{main_name}",
                updated,
            )
        updated = re.sub(
            rf"(^|[，。！？；、])(?:他|她)(?=(?:只得|知道|深知|打算|決意|決定|希望|感到|深感|越發|仍|想|欲|心中|心頭))",
            rf"\1{main_name}",
            updated,
        )
        updated = re.sub(rf"([令讓使教])(?:他|她)", rf"\1{main_name}", updated)
        return updated

    def _scene_script_v2_story_has_actor_pronoun_subject(self, story_text: str) -> bool:
        narration = self._scene_script_v2_story_narration_text(story_text)
        if not narration:
            return False
        return bool(
            re.search(
                r"(?:^|[。！？；，、\s])(?:他|她)(?:只得|知道|深知|明白|打算|希望|仍得|決定|心|便|就|要|想|先|在|望|低聲|抬頭|深吸|深感|感到|只能|當下|越發|不敢)",
                narration,
            )
        )

    def _scene_script_v2_story_has_actor_anchor_mixing(
        self,
        story_text: str,
        profile: NarrativeProfileResponse,
    ) -> bool:
        narration = self._scene_script_v2_story_narration_text(story_text)
        if not narration:
            return False
        actor_aliases = [alias for alias in self._actor_label_aliases(profile)[:4] if alias]
        has_actor_name = any(alias and alias in narration for alias in actor_aliases)
        return has_actor_name and self._scene_script_v2_story_has_actor_pronoun_subject(narration)

    def _scene_script_v2_story_has_duplicate_adjacent_clause(
        self,
        story_text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
    ) -> bool:
        sentences = self._scene_script_v2_story_sentences(self._scene_script_v2_story_narration_text(story_text))
        if len(sentences) < 2:
            return False
        actor_aliases = [alias for alias in self._actor_label_aliases(profile)[:3] if alias]
        target_aliases = self._scene_script_v2_story_target_aliases(target)
        repeated_event_markers = ("相見", "入見", "見到", "望著", "垂淚", "問候")
        repeated_time_markers = ("剛", "方才", "剛才", "一見")
        for left, right in zip(sentences, sentences[1:]):
            left_compare = self._scene_script_v2_story_compare_text(left)
            right_compare = self._scene_script_v2_story_compare_text(right)
            if not left_compare or not right_compare:
                continue
            if len(left_compare) >= 12 and (left_compare in right_compare or right_compare in left_compare):
                return True
            same_people = any(alias and alias in left and alias in right for alias in [*actor_aliases, *target_aliases])
            repeated_event = any(marker in left and marker in right for marker in repeated_event_markers)
            repeated_time = any(marker in left for marker in repeated_time_markers) and any(marker in right for marker in repeated_time_markers)
            if same_people and repeated_event and repeated_time:
                return True
        return False

    def _scene_script_v2_story_has_target_naming_issue(self, story_text: str, target: NarrativeInteractionTarget | None) -> bool:
        narration = self._scene_script_v2_story_narration_text(story_text)
        if not narration:
            return False
        seen_aliases = [
            alias
            for alias in self._scene_script_v2_story_target_aliases(target)
            if alias and alias in narration
        ]
        return len(seen_aliases) > 1

    def _scene_script_v2_story_has_actionable_ending(
        self,
        story_text: str,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> bool:
        sentences = self._scene_script_v2_story_sentences(story_text)
        if not sentences:
            return False
        ending = sentences[-1]
        action_markers = (
            "告訴",
            "坦陳",
            "商議",
            "商量",
            "請",
            "安頓",
            "護住",
            "穩住",
            "脫身",
            "離開",
            "返回",
            "趕回",
            "設法",
            "決定",
            "打算",
            "只能",
            "先把",
            "挑明",
            "理順",
            "說開",
            "收手",
            "接住",
            "先理",
            "先求",
            "先停",
            "先收",
            "先放",
        )
        concrete_terms = [
            self._scene_script_v2_story_target_name(target),
            self._clean_seed_text((beats.sceneSeeds or {}).get("place"), max_chars=20).strip(),
            self._scene_script_v2_pick_object_phrase((beats.sceneSeeds or {}).get("objects")),
            self._clean_seed_text((beats.sceneSeeds or {}).get("event"), max_chars=24).strip(),
        ]
        has_action = any(marker in ending for marker in action_markers)
        has_concrete = any(term and term in ending for term in concrete_terms)
        return has_action and has_concrete

    def _scene_script_v2_story_has_narrative_flow_issue(
        self,
        story_text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> bool:
        sentences = self._scene_script_v2_story_sentences(story_text)
        if len(sentences) < 3:
            return True
        grounded_sentences = 0
        for sentence in sentences:
            sentence_coverage = self._scene_script_v2_story_coverage(sentence, profile, target, beats)
            coverage_keys = [key for key in ["people", "event", "time", "place", "objects", "emotion"] if key in sentence_coverage]
            if coverage_keys:
                grounded_sentences += 1
        return grounded_sentences < max(2, len(sentences) - 1)

    def _scene_script_v2_story_slot_order(self, angle: str | None) -> list[str]:
        normalized = str(angle or "").strip().lower()
        mapping = {
            "emotion": ["memory", "event", "dialogue", "intent", "emotion", "people", "place", "objects", "time"],
            "bond": ["people", "memory", "event", "dialogue", "intent", "objects", "place", "time", "emotion"],
            "rival": ["event", "people", "dialogue", "memory", "objects", "place", "intent", "time", "emotion"],
            "people": ["people", "event", "memory", "dialogue", "intent", "place", "objects", "time", "emotion"],
        }
        return mapping.get(normalized, ["event", "memory", "people", "dialogue", "intent", "place", "objects", "time", "emotion"])

    def _scene_script_v2_story_strip_meta_prefixes(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"(?:[\u4e00-\u9fff]{1,6}-[\u4e00-\u9fff]{1,12})", "", cleaned)
        cleaned = re.sub(r"([^\W\d_]{2,6})\1{1,}", r"\1", cleaned)
        cleaned = re.sub(r"^(?:一看[，,：:]?|內容大致是[：:]?|只見|忽聽|回想起|想到)", "", cleaned).strip("，。！？；： ")
        cleaned = re.sub(r"^(?:我乃[\u4e00-\u9fff]{1,6})", "", cleaned).strip("，。！？；： ")
        cleaned = cleaned.replace("只見", "").replace("忽聽", "")
        return cleaned

    def _scene_script_v2_story_has_repetition_noise(self, text: str) -> bool:
        compact = self._scene_script_v2_story_compare_text(text)
        if not compact:
            return True
        if re.search(r"([^\W\d_]{2,6})\1{2,}", compact):
            return True
        if compact.count("-") >= 1:
            return True
        return False

    def _scene_script_v2_story_has_action_motion(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        return bool(re.search(r"(入見|相見|垂淚|現身|追|退|回|走|奔|圍|守|護|救|殺|阻|逼|領|告|說|問|商議|安頓|脫身|設法|返回|起疑|寫|相聚)", cleaned))

    def _scene_script_v2_story_human_like_term(self, text: str) -> bool:
        cleaned = self._clean_seed_text(text, max_chars=20).strip()
        if not cleaned:
            return False
        return bool(re.search(r"(夫人|嫂|姬|氏|公|君|兄|弟|姊|妹|子|王|眷)$", cleaned))

    def _scene_script_v2_story_clause_units(self, text: str, max_clauses: int = 8) -> list[str]:
        raw = " ".join(str(text or "").split()).strip("，。！？；：、 ")
        if not raw:
            return []
        values: list[str] = []
        for sentence in re.split(r"[。！？!?；;]+", raw):
            for clause in re.split(r"[，、]+", sentence):
                cleaned = str(clause or "").strip("，。！？；：、 ")
                if not cleaned:
                    continue
                values.append(cleaned)
                if len(values) >= max_clauses:
                    return values
        return values

    def _scene_script_v2_story_clause_keywords(self, text: str, max_terms: int = 6) -> list[str]:
        values: list[str] = []
        for phrase in self._scene_phrase_candidates(text, max_terms=max_terms * 2):
            cleaned = str(phrase or "").strip()
            compare = self._scene_script_v2_story_compare_text(cleaned)
            if not compare or len(compare) < 3:
                continue
            if cleaned not in values:
                values.append(cleaned)
            if len(values) >= max_terms:
                break
        return values

    def _scene_script_v2_story_clause_repetition_score(self, left: str, right: str) -> int:
        left_keywords = self._scene_script_v2_story_clause_keywords(left)
        right_keywords = self._scene_script_v2_story_clause_keywords(right)
        if not left_keywords or not right_keywords:
            return 0
        left_set = {self._scene_script_v2_story_compare_text(item) for item in left_keywords if self._scene_script_v2_story_compare_text(item)}
        right_set = {self._scene_script_v2_story_compare_text(item) for item in right_keywords if self._scene_script_v2_story_compare_text(item)}
        return len(left_set & right_set)

    def _scene_script_v2_story_sentence_quote(self, text: str) -> str:
        quote = self._extract_quoted_dialogue(text)
        return str(quote or "").strip().rstrip("。！？；")

    def _scene_script_v2_story_action_scene_mode(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return "default"
        if re.search(r"(殺|劍|阻|攔|逼|喝|怒|衝)", cleaned):
            return "conflict"
        if re.search(r"(追|退|回|返|圍|趕|奔|跑|殺出|現身|折返)", cleaned):
            return "movement"
        if re.search(r"(見|問|答|說|入見|相見|垂淚|請|告)", cleaned):
            return "encounter"
        if re.search(r"(商議|安頓|脫身|設法|返回|護住|穩住|決定|打算)", cleaned):
            return "resolve"
        return "default"

    def _scene_script_v2_story_action_hint(
        self,
        action_source: str,
        actor: str = "",
    ) -> str:
        clauses = self._scene_script_v2_story_clause_units(action_source, max_clauses=4)
        if not clauses:
            clauses = [str(action_source or "").strip()]
        ranked: list[tuple[int, str]] = []
        for clause in clauses:
            cleaned = str(clause or "").strip("，。！？；： ")
            if not cleaned:
                continue
            score = 0
            if self._scene_script_v2_story_has_action_motion(cleaned):
                score += 12
            if "「" in cleaned or "」" in cleaned:
                score -= 8
            if self._scene_script_v2_story_has_event_blob_density_issue(cleaned):
                score -= 10
            score -= max(0, len(cleaned) - 16)
            ranked.append((score, cleaned))
        if not ranked:
            return ""
        hint = sorted(ranked, key=lambda item: (-item[0], len(item[1])))[0][1]
        if actor and hint.startswith(actor):
            hint = hint[len(actor) :].lstrip("，、 ")
        hint = re.sub(r"之際$", "", hint).strip("，。！？；： ")
        return hint

    def _scene_script_v2_story_variant_index(self, modulo: int, *parts: str) -> int:
        if modulo <= 1:
            return 0
        basis = "|".join(str(part or "").strip() for part in parts if str(part or "").strip())
        if not basis:
            return 0
        return int(hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8], 16) % modulo

    def _scene_script_v2_story_sentence_frame_signature(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        mode = self._scene_script_v2_story_action_scene_mode(cleaned)
        cleaned = self._scene_script_v2_story_narration_text(cleaned)
        cleaned = re.sub(r"[「『“\"][^」』”\"]{0,120}[」』”\"]", "Q", cleaned)
        clauses: list[str] = []
        for clause in re.split(r"[。！？!?；;]+", cleaned):
            piece = clause.strip("，、：: ")
            if not piece:
                continue
            piece = re.sub(r"[一-鿿]{2,}", "N", piece)
            piece = re.sub(r"\d+", "D", piece)
            clauses.append(piece)
            if len(clauses) >= 4:
                break
        return "|".join([mode, *clauses])

    def _scene_script_v2_story_sentence_frame_similarity(self, left: str, right: str) -> float:
        left_signature = self._scene_script_v2_story_sentence_frame_signature(left)
        right_signature = self._scene_script_v2_story_sentence_frame_signature(right)
        if not left_signature or not right_signature:
            return 0.0
        if left_signature == right_signature:
            return 1.0
        left_parts = {item for item in left_signature.split("|") if item}
        right_parts = {item for item in right_signature.split("|") if item}
        if not left_parts or not right_parts:
            return 0.0
        return len(left_parts & right_parts) / max(len(left_parts), len(right_parts))

    def _scene_script_v2_story_sentence_frame_similarity_check(self, left: str, right: str) -> bool:
        return self._scene_script_v2_story_sentence_frame_similarity(left, right) >= 0.72

    def _scene_script_v2_story_quote_dominance_check(self, story_text: str) -> bool:
        cleaned = " ".join(str(story_text or "").split()).strip()
        if not cleaned:
            return False
        quote_spans = re.findall(r"「[^」]{0,120}」|『[^』]{0,120}』|“[^”]{0,120}”|\"[^\"]{0,120}\"", cleaned)
        if len(quote_spans) >= 2:
            return True
        quote_chars = sum(len(span) for span in quote_spans)
        compact_len = max(1, len(self._scene_script_v2_compact_story_text(cleaned)))
        return quote_chars >= 20 and quote_chars / compact_len >= 0.22

    def _scene_script_v2_story_repeated_support_pressure_tension_shape_check(self, story_text: str) -> bool:
        sentences = self._scene_script_v2_story_sentences(story_text)
        if len(sentences) < 3:
            return False
        tail = [sentence for sentence in sentences[-4:] if sentence and "「" not in sentence and "」" not in sentence]
        if len(tail) < 3:
            return False
        for left, right in zip(tail, tail[1:]):
            if self._scene_script_v2_story_sentence_frame_similarity_check(left, right):
                return True
        return False

    def _scene_script_v2_story_ending_template_similarity_check(self, story_text: str) -> bool:
        sentences = self._scene_script_v2_story_sentences(story_text)
        if len(sentences) < 2:
            return False
        ending = sentences[-1]
        previous = sentences[-2]
        if not ending or not previous:
            return False
        if self._scene_script_v2_story_sentence_frame_similarity_check(previous, ending):
            return True
        ending_signature = self._scene_script_v2_story_sentence_frame_signature(ending)
        previous_signature = self._scene_script_v2_story_sentence_frame_signature(previous)
        if not ending_signature or not previous_signature:
            return False
        return ending_signature == previous_signature and len(ending_signature) <= len(previous_signature)

    def _scene_script_v2_story_pick_diverse_variant(
        self,
        slot_name: str,
        candidates: list[str],
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
        *,
        prefer_action: bool = False,
        prefer_quote: bool = False,
    ) -> str:
        ranked: list[tuple[float, str]] = []
        for candidate in candidates:
            text = self._ensure_sentence(str(candidate or "").strip())
            if not text:
                continue
            if any(self._scene_script_v2_story_sentences_overlap(text, used) for used in used_sentences):
                continue
            if self._scene_script_v2_story_has_fragment_readability_issue(text):
                text = self._scene_script_v2_story_normalize_action_fragment(text, profile, target)
                text = self._ensure_sentence(text)
            if not text:
                continue
            score = 0.0
            hits = self._scene_script_v2_story_sentence_grounding_slots(text, slot_data)
            score += len(hits) * 10
            if slot_name in hits:
                score += 4
            if self._scene_script_v2_story_has_action_motion(text):
                score += 6
            if prefer_action and self._scene_script_v2_story_has_action_motion(text):
                score += 4
            if prefer_quote and self._extract_quoted_dialogue(text):
                score += 4
            if self._scene_script_v2_story_has_event_blob_density_issue(text):
                score -= 14
            if self._scene_script_v2_story_has_fragment_readability_issue(text):
                score -= 18
            if self._scene_script_v2_story_quote_dominance_check(text):
                score -= 10 if slot_name == "dialogue" else 14
            frame_penalty = max((self._scene_script_v2_story_sentence_frame_similarity(text, used) for used in used_sentences), default=0.0)
            score -= frame_penalty * 40
            score -= max(0, len(text) - 72) * 0.35
            ranked.append((score, text))
        if not ranked:
            return ""
        return sorted(ranked, key=lambda item: (-item[0], len(item[1])))[0][1]

    def _scene_script_v2_story_has_duplicate_quote_span(self, story_text: str) -> bool:
        sentences = self._scene_script_v2_story_sentences(story_text)
        if len(sentences) < 2:
            return False
        for left, right in zip(sentences, sentences[1:]):
            left_quote = self._scene_script_v2_story_compare_text(self._scene_script_v2_story_sentence_quote(left))
            right_quote = self._scene_script_v2_story_compare_text(self._scene_script_v2_story_sentence_quote(right))
            if left_quote and right_quote and (left_quote in right_quote or right_quote in left_quote):
                return True
            right_narration = self._scene_script_v2_story_compare_text(self._scene_script_v2_story_narration_text(right))
            if left_quote and right_narration and left_quote in right_narration:
                return True
        return False

    def _scene_script_v2_story_has_event_blob_density_issue(self, story_text: str) -> bool:
        for sentence in self._scene_script_v2_story_sentences(story_text) or [str(story_text or "").strip()]:
            cleaned = str(sentence or "").strip()
            if not cleaned or "「" in cleaned or "」" in cleaned:
                continue
            clauses = self._scene_script_v2_story_clause_units(cleaned, max_clauses=8)
            if len(clauses) >= 4 and len(cleaned) >= 30:
                return True
            if cleaned.count("，") >= 3 and len(self._scene_script_v2_story_clause_keywords(cleaned, max_terms=8)) >= 5:
                return True
        return False

    def _scene_script_v2_story_has_fragment_readability_issue(self, story_text: str) -> bool:
        dangling_pattern = r"(?:只見|忽聽|想到|想起|於是|便|就|說|問|道|曰|阻住|攔住)$"
        broken_action_pattern = r"(?:阻住|攔住)(?:想到|想起)"
        for sentence in self._scene_script_v2_story_sentences(story_text) or [str(story_text or "").strip()]:
            cleaned = str(sentence or "").strip("，。！？；： ")
            if not cleaned:
                continue
            if re.search(dangling_pattern, cleaned):
                return True
            if re.search(broken_action_pattern, cleaned):
                return True
            if self._scene_script_v2_story_has_repetition_noise(cleaned):
                return True
        return False

    def _scene_script_v2_story_has_clause_repetition_issue(self, story_text: str) -> bool:
        sentences = self._scene_script_v2_story_sentences(self._scene_script_v2_story_narration_text(story_text))
        if len(sentences) < 2:
            return False
        for left, right in zip(sentences, sentences[1:]):
            if self._scene_script_v2_story_clause_repetition_score(left, right) >= 3:
                return True
        return False

    def _scene_script_v2_story_cleanup_sentences(
        self,
        sentences: list[str],
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> list[str]:
        cleaned_sentences: list[str] = []
        for sentence in sentences:
            text = self._scene_script_v2_story_canonicalize_target_naming(sentence, target).strip()
            text = self._scene_script_v2_story_canonicalize_actor_clause(text, profile).strip()
            if self._scene_script_v2_story_has_fragment_readability_issue(text):
                text = self._scene_script_v2_story_normalize_action_fragment(text, profile, target)
            text = self._ensure_sentence(text)
            if not text:
                continue
            if not cleaned_sentences:
                cleaned_sentences.append(text)
                continue
            previous = cleaned_sentences[-1]
            previous_mode = self._scene_script_v2_story_action_scene_mode(previous)
            current_mode = self._scene_script_v2_story_action_scene_mode(text)
            previous_quote = self._scene_script_v2_story_compare_text(self._scene_script_v2_story_sentence_quote(previous))
            current_quote = self._scene_script_v2_story_compare_text(self._scene_script_v2_story_sentence_quote(text))
            current_frame_similarity = self._scene_script_v2_story_sentence_frame_similarity(previous, text)
            if previous_quote and current_quote and (previous_quote in current_quote or current_quote in previous_quote):
                continue
            repetition_score = self._scene_script_v2_story_clause_repetition_score(previous, text)
            if self._scene_script_v2_story_sentences_overlap(previous, text) or repetition_score >= 4 or current_frame_similarity >= 0.8:
                previous_grounding = len(self._scene_script_v2_story_sentence_grounding_slots(previous, slot_data))
                current_grounding = len(self._scene_script_v2_story_sentence_grounding_slots(text, slot_data))
                previous_is_ending = self._scene_script_v2_story_has_actionable_ending(previous, target, beats)
                current_is_ending = self._scene_script_v2_story_has_actionable_ending(text, target, beats)
                if current_is_ending and not previous_is_ending:
                    cleaned_sentences[-1] = text
                    continue
                if previous_mode != current_mode:
                    cleaned_sentences.append(text)
                    continue
                if current_grounding > previous_grounding:
                    cleaned_sentences[-1] = text
                    continue
                if (
                    current_grounding == previous_grounding
                    and len(text) > len(previous)
                    and not self._scene_script_v2_story_quote_dominance_check(text)
                    and current_frame_similarity >= 0.84
                ):
                    cleaned_sentences[-1] = text
                    continue
                continue
            cleaned_sentences.append(text)
        return cleaned_sentences

    def _scene_script_v2_story_normalize_action_fragment(
        self,
        text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
    ) -> str:
        cleaned = self._scene_script_v2_story_strip_meta_prefixes(text)
        cleaned = self._scene_script_v2_story_canonicalize_target_naming(cleaned, target).strip()
        cleaned = self._scene_script_v2_story_canonicalize_actor_clause(cleaned, profile).strip()
        cleaned = re.sub(r"([\u4e00-\u9fff])\1(?=[\u4e00-\u9fff]{1,2})", r"\1", cleaned)
        cleaned = re.sub(r"(?:[\u4e00-\u9fff]{2,6})-(?:[\u4e00-\u9fff]{2,12})", "", cleaned)
        cleaned = cleaned.replace("只見", "").replace("忽聽", "")
        cleaned = re.sub(
            r"(?P<actor>[\u4e00-\u9fff]{2,4})扯劍上廳(?:，|,)?要殺(?P<enemy>[\u4e00-\u9fff]{2,4})，?(?P<blocker>[\u4e00-\u9fff]{2,4})(?:慌忙)?(?:阻住|攔住)",
            r"\g<actor>扯劍上廳欲殺\g<enemy>，\g<blocker>連忙攔住",
            cleaned,
        )
        cleaned = re.sub(
            r"(?P<blocker>[\u4e00-\u9fff]{2,4})(?:慌忙)?(?:阻住|攔住)(?P<actor>[\u4e00-\u9fff]{2,4})扯劍上廳(?:，|,)?要殺(?P<enemy>[\u4e00-\u9fff]{2,4})",
            r"\g<actor>扯劍上廳欲殺\g<enemy>，\g<blocker>連忙攔住",
            cleaned,
        )
        cleaned = re.sub(
            r"(?P<blocker>[\u4e00-\u9fff]{2,4})(?:慌忙)?(?:阻住|攔住)(?:想到|想起)?(?P<target>[\u4e00-\u9fff]{2,4})?",
            lambda match: f"{str(match.group('blocker') or '').strip()}連忙攔住",
            cleaned,
        )
        cleaned = re.sub(r"入見(?P<person>[\u4e00-\u9fff]{2,6})，?暗暗垂淚", r"入見\g<person>時，不禁暗自垂淚", cleaned)

        def replace_pursuit(match: re.Match[str]) -> str:
            actor_name = str(match.group("actor") or "").strip()
            distance = str(match.group("distance") or "").strip()
            risk = str(match.group("risk") or "").strip()
            prefix = f"{actor_name}" if actor_name else ""
            return f"{prefix}一路追出{distance}，惟恐{risk}，只得掉頭折返"

        cleaned = re.sub(
            r"(?:(?P<actor>[\u4e00-\u9fff]{2,4}))?一直追去，追了(?P<distance>[^，。]{2,10})，怕(?P<risk>[^，。]{2,14})，於是掉頭往回跑",
            replace_pursuit,
            cleaned,
        )
        cleaned = re.sub(r"閃出一彪人馬", "一隊人馬忽然殺出", cleaned)
        cleaned = re.sub(r"(?P<turn>[^，。]{2,12})喊聲大起", r"\g<turn>忽然喊聲大起", cleaned)
        cleaned = re.sub(
            r"為首[^，。]{0,8}，?乃是(?P<names>[\u4e00-\u9fff、]{4,24})",
            lambda match: f"{str(match.group('names') or '').strip('、')}一齊現身",
            cleaned,
        )
        cleaned = re.sub(r"(?:慌忙)?(?:連忙){2,}", "連忙", cleaned)
        cleaned = re.sub(r"慌忙連忙", "連忙", cleaned)
        cleaned = re.sub(r"連忙連忙", "連忙", cleaned)
        cleaned = re.sub(r"(?:想到|想起)(?=[，。！？；：]|$)", "", cleaned)
        cleaned = re.sub(r"(?:只見|忽聽)(?=[，。！？；：]|$)", "", cleaned)
        cleaned = re.sub(r"(?:[\u4e00-\u9fff]{2,6})?(?:說|問|道|曰)[:：]?$", "", cleaned).strip("，。！？；： ")
        cleaned = re.sub(r"(?:想到|想起)$", "", cleaned).strip("，。！？；： ")
        cleaned = re.sub(r"([，。！？；：、]){2,}", r"\1", cleaned)
        return cleaned.strip("，。！？；： ")

    def _scene_script_v2_story_compact_source_clause(
        self,
        slot_name: str,
        text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        *,
        max_chars: int = 72,
    ) -> str:
        cleaned = self._scene_script_v2_story_normalize_action_fragment(text, profile, target)
        if not cleaned:
            return ""
        if slot_name == "memory" and "信裡還寫著" in cleaned:
            return self._clean_seed_text(cleaned, max_chars=max_chars).strip()
        clauses = self._scene_script_v2_story_clause_units(cleaned, max_clauses=8)
        if not clauses:
            return self._clean_seed_text(cleaned, max_chars=max_chars).strip()
        selected: list[str] = []
        for clause in clauses:
            normalized_clause = self._scene_script_v2_story_normalize_action_fragment(clause, profile, target)
            normalized_clause = self._clean_seed_text(normalized_clause, max_chars=max_chars).strip("，。！？；： ")
            if not normalized_clause or self._is_weak_scene_seed_text(normalized_clause):
                continue
            if any(
                self._scene_script_v2_story_sentences_overlap(normalized_clause, kept)
                or self._scene_script_v2_story_clause_repetition_score(normalized_clause, kept) >= 2
                for kept in selected
            ):
                continue
            selected.append(normalized_clause)
        if not selected:
            return ""

        actor_name = self._clean_seed_text(profile.displayName or "", max_chars=20).strip()
        target_name = self._scene_script_v2_story_target_name(target)

        def clause_score(clause: str) -> tuple[int, int]:
            score = 0
            if self._scene_script_v2_story_has_action_motion(clause):
                score += 12
            if actor_name and actor_name in clause:
                score += 6
            if target_name and target_name in clause:
                score += 8
            if re.search(r"(忽然|連忙|只得|現身|殺出|垂淚|折返|追趕|追出|欲殺|攔住|問得|說出口)", clause):
                score += 5
            if "「" in clause and "」" in clause:
                score += 4 if slot_name == "memory" else -2
            if len(clause) > 24:
                score += 2
            return score, -len(clause)

        dense = len(cleaned) > max(34, max_chars - 20) or cleaned.count("，") >= 3 or len(selected) >= 3
        if dense:
            limit = 2 if slot_name == "memory" else 3
            ranked_indexes = sorted(range(len(selected)), key=lambda index: clause_score(selected[index]), reverse=True)[:limit]
            selected = [selected[index] for index in sorted(ranked_indexes)]

        compacted = "，".join(selected[: (2 if slot_name == "memory" else 3)])
        if slot_name == "event" and len(selected) >= 2:
            action_clause = next((clause for clause in selected if self._scene_script_v2_story_has_action_motion(clause)), selected[0])
            reveal_clause = next(
                (
                    clause
                    for clause in selected
                    if clause != action_clause and (actor_name and actor_name in clause or target_name and target_name in clause or "現身" in clause)
                ),
                "",
            )
            if reveal_clause:
                compacted = "，".join([action_clause.rstrip("，。！？；："), reveal_clause.rstrip("，。！？；：")])
            elif self._scene_script_v2_story_has_action_motion(action_clause):
                rest_clauses = [clause for clause in selected if clause != action_clause]
                if rest_clauses:
                    compacted = "，".join([action_clause.rstrip("，。！？；："), rest_clauses[0].rstrip("，。！？；：")])
        compacted = self._clean_seed_text(compacted, max_chars=max_chars).strip("，。！？；： ")
        return compacted

    def _scene_script_v2_story_normalize_source_clause(
        self,
        slot_name: str,
        text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        *,
        max_chars: int = 72,
        allow_quotes: bool = False,
    ) -> str:
        cleaned = self._scene_script_v2_story_compact_source_clause(
            slot_name,
            text,
            profile,
            target,
            max_chars=max_chars,
        )
        cleaned = re.sub(r"[“”\"]", "", cleaned).strip("，。！？；： ")
        if not allow_quotes:
            cleaned = re.sub(r"[「『][^」』]{2,80}[」』]", "", cleaned).strip("，。！？；： ")
        cleaned = re.sub(r"(?:說|問|道|曰|云)[:：]?$", "", cleaned).strip("，。！？；： ")
        if not cleaned or self._contains_internal_symbolic_token(cleaned):
            return ""
        if self._scene_script_v2_story_has_repetition_noise(cleaned):
            return ""
        if self._is_weak_scene_seed_text(cleaned):
            return ""
        return cleaned

    def _scene_script_v2_story_source_candidates(
        self,
        slot_name: str,
        raw_text: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        *,
        max_chars: int = 72,
    ) -> list[str]:
        raw = " ".join(str(raw_text or "").split()).strip()
        if not raw:
            return []
        values: list[str] = []
        if slot_name == "memory" and "信" in raw:
            letter_quote = self._extract_quoted_dialogue(raw)
            if not letter_quote:
                letter_tail = re.split(r"[：:]", raw, maxsplit=1)
                if len(letter_tail) == 2:
                    letter_quote = letter_tail[1].strip().strip("“”\"' ")
            if letter_quote:
                letter_clause = self._scene_script_v2_story_normalize_source_clause(
                    slot_name,
                    f"信裡還寫著「{letter_quote}」",
                    profile,
                    target,
                    max_chars=max_chars,
                    allow_quotes=True,
                )
                if letter_clause:
                    values.append(letter_clause)
        sources = [
            *self._split_source_sentences(raw),
            *self._story_seed_clauses(raw, max_chars=max_chars),
            *self._scene_phrase_candidates(raw, max_terms=10),
        ]
        for source in sources:
            normalized = self._scene_script_v2_story_normalize_source_clause(
                slot_name,
                source,
                profile,
                target,
                max_chars=max_chars,
                allow_quotes=slot_name == "dialogue",
            )
            if not normalized:
                continue
            if slot_name == "dialogue" and "「" not in source and "」" not in source and len(normalized) < 6:
                continue
            if normalized not in values:
                values.append(normalized)
        return values[:8]

    def _scene_script_v2_story_build_slots(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, Any]:
        scene_seeds = beats.sceneSeeds or {}
        actor_name = self._clean_seed_text(profile.displayName or "", max_chars=20).strip()
        target_name = self._scene_script_v2_story_target_name(target)
        raw_bundle = " ".join(
            str(value or "").strip()
            for value in [
                scene_seeds.get("event"),
                beats.memoryText,
                beats.emotionText,
                beats.dialogueText,
                beats.intentText,
            ]
            if str(value or "").strip()
        )
        people_terms: list[str] = []
        for item in scene_seeds.get("people") or []:
            label = self._clean_seed_text(item.get("label") if isinstance(item, dict) else item, max_chars=20).strip()
            if not label:
                continue
            if label == actor_name:
                continue
            if label == target_name:
                continue
            if label not in people_terms:
                people_terms.append(label)
        place_value = self._scene_script_v2_story_strip_meta_prefixes(scene_seeds.get("place") or "")
        place_value = self._clean_seed_text(place_value, max_chars=16).strip()
        if (
            not place_value
            or len(place_value) < 2
            or re.search(r"[，。！？；：「」『』\-\"]", place_value)
            or place_value.startswith("我")
            or place_value.startswith("想到")
        ):
            place_value = ""
        time_value = self._scene_script_v2_story_strip_meta_prefixes(scene_seeds.get("time") or "")
        time_value = self._clean_seed_text(time_value, max_chars=12).strip()
        if not time_value or re.search(r"[，。！？；：「」『』\-\"]", time_value):
            time_value = ""
        object_terms: list[str] = []
        raw_objects = [self._clean_seed_text(item, max_chars=12).strip() for item in (scene_seeds.get("objects") or [])]
        raw_objects = [item for item in raw_objects if item]
        grounded_object_terms = [
            item
            for item in raw_objects
            if item in raw_bundle and not self._scene_script_v2_story_human_like_term(item)
        ]
        fallback_object_terms = [
            item
            for item in raw_objects
            if not self._scene_script_v2_story_human_like_term(item)
        ]
        chosen_objects = grounded_object_terms or fallback_object_terms[:2] or raw_objects[:1]
        for item in chosen_objects:
            if item and item not in object_terms:
                object_terms.append(item)
        return {
            "angle": request.angle,
            "actor": actor_name,
            "target": target_name,
            "people": people_terms,
            "time": time_value,
            "place": place_value,
            "objects": object_terms[:2],
            "event": self._scene_script_v2_story_source_candidates("event", scene_seeds.get("event") or beats.sceneText, profile, target),
            "memory": self._scene_script_v2_story_source_candidates("memory", beats.memoryText, profile, target),
            "emotion": self._scene_script_v2_story_source_candidates("emotion", beats.emotionText or scene_seeds.get("emotion"), profile, target),
            "dialogue": self._scene_script_v2_story_source_candidates("dialogue", beats.dialogueText, profile, target),
            "intent": self._scene_script_v2_story_source_candidates("intent", beats.intentText, profile, target),
            "eventQuote": self._extract_quoted_dialogue(
                (beats.sceneFacts or {}).get("rawSceneText")
                or (beats.sceneFacts or {}).get("rawSceneEventSeed")
                or scene_seeds.get("event")
                or beats.sceneText
            ),
            "dialogueQuote": self._extract_quoted_dialogue(beats.dialogueText),
        }

    def _scene_script_v2_story_sentence_grounding_slots(
        self,
        sentence: str,
        slot_data: dict[str, Any],
    ) -> set[str]:
        hits: set[str] = set()
        compare_text = self._scene_script_v2_story_compare_text(sentence)
        if not compare_text:
            return hits
        for slot_name in ["event", "memory", "emotion", "dialogue", "intent"]:
            for candidate in slot_data.get(slot_name) or []:
                candidate_compare = self._scene_script_v2_story_compare_text(candidate)
                if candidate_compare and (candidate_compare in compare_text or compare_text in candidate_compare):
                    hits.add(slot_name)
                    break
        for slot_name in ["people", "time", "place", "objects"]:
            values = slot_data.get(slot_name) or []
            if not isinstance(values, list):
                values = [values]
            for value in values:
                candidate_compare = self._scene_script_v2_story_compare_text(value)
                if candidate_compare and candidate_compare in compare_text:
                    hits.add(slot_name)
                    break
        return hits

    def _scene_script_v2_story_has_dialogue_grounding_issue(
        self,
        story_text: str,
        slot_data: dict[str, Any],
    ) -> bool:
        story_quote = self._extract_quoted_dialogue(story_text)
        if not story_quote:
            return False
        expected = [
            *[str(item or "").strip() for item in (slot_data.get("dialogue") or []) if str(item or "").strip()],
            str(slot_data.get("eventQuote") or "").strip(),
            str(slot_data.get("dialogueQuote") or "").strip(),
        ]
        expected = [item for item in expected if item]
        if not expected:
            return False
        story_compare = self._scene_script_v2_story_compare_text(story_quote)
        return not any(
            self._scene_script_v2_story_compare_text(candidate) and self._scene_script_v2_story_compare_text(candidate) in story_compare
            for candidate in expected
        )

    def _scene_script_v2_story_has_angle_role_scene_coherence_issue(
        self,
        story_text: str,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> bool:
        slot_data = self._scene_script_v2_story_build_slots(request, profile, target, beats)
        slot_order = self._scene_script_v2_story_slot_order(request.angle)
        active_slots = [slot for slot in slot_order if slot_data.get(slot)]
        top_slots = active_slots[:4]
        sentences = self._scene_script_v2_story_sentences(story_text)
        if not sentences:
            return True
        grounded_sentences = 0
        hit_slots: set[str] = set()
        for sentence in sentences:
            hits = self._scene_script_v2_story_sentence_grounding_slots(sentence, slot_data)
            if hits:
                grounded_sentences += 1
                hit_slots.update(hits)
        if target and slot_data.get("target") and slot_data["target"] not in self._scene_script_v2_story_narration_text(story_text):
            return True
        if top_slots and len(hit_slots & set(top_slots)) < min(3, len(top_slots)):
            return True
        if grounded_sentences < max(2, len(sentences) - 1):
            return True
        if self._scene_script_v2_story_has_dialogue_grounding_issue(story_text, slot_data):
            return True
        return False

    def _scene_script_v2_story_validation(
        self,
        story_text: str,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> dict[str, Any]:
        normalized = " ".join(str(story_text or "").split()).strip()
        normalized = self._scene_script_v2_story_canonicalize_target_naming(normalized, target).strip()
        coverage = self._scene_script_v2_story_coverage(normalized, profile, target, beats)
        coverage_keys = [key for key in ["people", "event", "time", "place", "objects", "emotion"] if key in coverage]
        warnings: list[str] = []
        if not normalized:
            warnings.append("scene_script_v2_story_empty")
        if self._scene_script_v2_story_is_seed_dump(normalized):
            warnings.append("scene_script_v2_story_seed_dump")
        if self._scene_script_v2_story_has_first_person_narration(normalized):
            warnings.append("scene_script_v2_story_pov_inconsistent")
        if self._scene_script_v2_story_has_actor_anchor_mixing(normalized, profile):
            warnings.append("scene_script_v2_story_actor_anchor_mixed")
        if self._scene_script_v2_story_has_target_naming_issue(normalized, target):
            warnings.append("scene_script_v2_story_target_name_mixed")
        if self._scene_script_v2_story_has_duplicate_quote_span(normalized):
            warnings.append("scene_script_v2_story_duplicate_quote_span")
        if self._scene_script_v2_story_has_duplicate_adjacent_clause(normalized, profile, target):
            warnings.append("scene_script_v2_story_duplicate_clause")
        if self._scene_script_v2_story_has_clause_repetition_issue(normalized):
            warnings.append("scene_script_v2_story_clause_repetition")
        if any(
            self._scene_script_v2_story_sentence_frame_similarity_check(left, right)
            for left, right in zip(self._scene_script_v2_story_sentences(normalized), self._scene_script_v2_story_sentences(normalized)[1:])
        ):
            warnings.append("scene_script_v2_story_sentence_frame_similarity")
        if self._scene_script_v2_story_quote_dominance_check(normalized):
            warnings.append("scene_script_v2_story_quote_dominance")
        if self._scene_script_v2_story_repeated_support_pressure_tension_shape_check(normalized):
            warnings.append("scene_script_v2_story_repeated_support_pressure_tension_shape")
        if self._scene_script_v2_story_has_event_blob_density_issue(normalized):
            warnings.append("scene_script_v2_story_event_blob_dense")
        if self._scene_script_v2_story_has_fragment_readability_issue(normalized):
            warnings.append("scene_script_v2_story_fragment_readability_weak")
        if not self._scene_script_v2_story_has_actionable_ending(normalized, target, beats):
            warnings.append("scene_script_v2_story_ending_not_actionable")
        if self._scene_script_v2_story_ending_template_similarity_check(normalized):
            warnings.append("scene_script_v2_story_ending_template_similarity")
        if self._scene_script_v2_story_has_narrative_flow_issue(normalized, profile, target, beats):
            warnings.append("scene_script_v2_story_narrative_flow_weak")
        if self._scene_script_v2_story_has_angle_role_scene_coherence_issue(normalized, request, profile, target, beats):
            warnings.append("scene_script_v2_story_angle_scene_coherence_weak")
        if len(coverage_keys) < 4:
            warnings.append("scene_script_v2_story_seed_coverage_low")
        compact_len = len(self._scene_script_v2_compact_story_text(normalized))
        if compact_len < self._scene_script_v2_story_min_cjk_chars(request):
            warnings.append("scene_script_v2_story_short")
        if compact_len > self._scene_script_v2_story_max_cjk_chars(request):
            warnings.append("scene_script_v2_story_long")
        return {
            "valid": not warnings,
            "warnings": warnings,
            "coverageKeys": coverage_keys,
            "coverage": coverage,
            "normalizedText": normalized,
            "compactLen": compact_len,
        }

    def _scene_script_v2_story_sentence_clause(
        self,
        text: str,
        fallback: str,
        max_chars: int,
        *,
        reject_first_person: bool = False,
        reject_actor_pronoun: bool = False,
    ) -> str:
        cleaned = self._clean_seed_text(text, max_chars=max_chars).strip()
        if not cleaned or self._is_weak_scene_seed_text(cleaned) or self._scene_script_v2_story_is_seed_dump(cleaned):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        if reject_first_person and self._scene_script_v2_story_has_first_person_narration(cleaned):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        if reject_actor_pronoun and self._scene_script_v2_story_has_actor_pronoun_subject(cleaned):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        if not cleaned:
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        return self._sentence_or_default(cleaned, self._clean_seed_text(fallback, max_chars=max_chars), max_chars=max_chars)

    def _scene_script_v2_story_dialogue_clause(self, text: str, fallback: str, max_chars: int) -> str:
        cleaned = self._clean_seed_text(text, max_chars=max_chars).strip()
        if not cleaned or self._is_weak_scene_seed_text(cleaned) or self._scene_script_v2_story_is_seed_dump(cleaned):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        if any(mark in cleaned for mark in ("「", "」", "『", "』", "\"")):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        if cleaned.count("，") >= 2 and any(marker in cleaned for marker in ("說道", "回道", "問道", "低聲", "輕聲")):
            cleaned = self._clean_seed_text(fallback, max_chars=max_chars).strip()
        cleaned = cleaned.strip("「」\"'").rstrip("。！？")
        return cleaned

    def _scene_script_v2_pick_object_phrase(self, objects: Any) -> str:
        terms: list[str] = []
        for item in objects or []:
            label = self._clean_seed_text(item, max_chars=16).strip()
            if label and label not in terms and not self._scene_script_v2_story_is_seed_dump(label):
                terms.append(label)
            if len(terms) >= 2:
                break
        if not terms:
            return ""
        return "和".join(terms[:2]) if len(terms) > 1 else terms[0]

    def _scene_script_v2_pick_people_phrase(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        scene_seeds: dict[str, Any],
    ) -> str:
        terms: list[str] = []
        for value in [profile.displayName, target.label if target else ""]:
            label = self._clean_seed_text(value, max_chars=20).strip()
            if label and label not in terms:
                terms.append(label)
        for person in scene_seeds.get("people") or []:
            if isinstance(person, dict):
                label = self._clean_seed_text(person.get("label") or person.get("name") or "", max_chars=20).strip()
            else:
                label = self._clean_seed_text(person, max_chars=20).strip()
            if label and label not in terms:
                terms.append(label)
            if len(terms) >= 3:
                break
        if not terms:
            return ""
        return "和".join(terms[:2]) if len(terms) > 1 else terms[0]

    def _scene_script_v2_story_sentences_overlap(self, left: str, right: str) -> bool:
        left_compare = self._scene_script_v2_story_compare_text(left)
        right_compare = self._scene_script_v2_story_compare_text(right)
        if not left_compare or not right_compare:
            return False
        return left_compare in right_compare or right_compare in left_compare

    def _scene_script_v2_story_candidate_allowed(
        self,
        slot_name: str,
        candidate: str,
        slot_data: dict[str, Any],
    ) -> bool:
        text = str(candidate or "").strip()
        compare = self._scene_script_v2_story_compare_text(text)
        if not compare:
            return False
        if self._scene_script_v2_story_has_repetition_noise(text):
            return False
        if self._scene_script_v2_story_has_fragment_readability_issue(text):
            return False
        phrase_candidates = [
            fragment
            for fragment in self._scene_phrase_candidates(text, max_terms=6)
            if fragment and len(self._scene_script_v2_story_compare_text(fragment)) >= 4
        ]
        if len(text) > 48 and any(text.count(fragment) >= 2 for fragment in phrase_candidates):
            return False
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        strong_groups = 0
        for values in [
            [actor] if actor else [],
            [target_name] if target_name else [],
            [str(slot_data.get("place") or "").strip()] if str(slot_data.get("place") or "").strip() else [],
            [str(slot_data.get("time") or "").strip()] if str(slot_data.get("time") or "").strip() else [],
            [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()],
            [str(item or "").strip() for item in (slot_data.get("people") or []) if str(item or "").strip()],
        ]:
            if any(self._scene_script_v2_story_compare_text(value) and self._scene_script_v2_story_compare_text(value) in compare for value in values):
                strong_groups += 1
        event_overlap = any(
            phrase_compare and (phrase_compare in compare or compare in phrase_compare)
            for phrase_compare in [
                self._scene_script_v2_story_compare_text(item)
                for item in (slot_data.get("event") or [])
                if str(item or "").strip() and str(item or "").strip() != text
            ]
        )
        quote_compare = self._scene_script_v2_story_compare_text(slot_data.get("eventQuote") or slot_data.get("dialogueQuote") or "")
        if quote_compare and quote_compare in compare:
            event_overlap = True
        has_quote = "「" in text and "」" in text
        has_action = self._scene_script_v2_story_has_action_motion(text)
        is_dense = self._scene_script_v2_story_has_event_blob_density_issue(text)
        if slot_name == "dialogue":
            return False
        if slot_name == "intent":
            return False
        if slot_name == "emotion":
            return False
        if quote_compare and quote_compare in compare and slot_name == "event":
            return False
        if quote_compare and quote_compare in compare and slot_name == "memory" and "信裡還寫著" not in text:
            return False
        if slot_name == "memory":
            return (has_quote or has_action or (strong_groups >= 1 and event_overlap)) and not is_dense
        if slot_name == "event":
            return (strong_groups >= 1 or has_action or event_overlap) and not is_dense
        return True

    def _scene_script_v2_story_best_slot_candidate(
        self,
        slot_name: str,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        used_sentences: list[str],
        *,
        prefer_action: bool = False,
    ) -> str:
        candidates = [str(item or "").strip() for item in (slot_data.get(slot_name) or []) if str(item or "").strip()]
        if not candidates:
            return ""
        ranked: list[tuple[int, str]] = []
        for candidate in candidates:
            if any(self._scene_script_v2_story_sentences_overlap(candidate, used) for used in used_sentences):
                continue
            if any(self._scene_script_v2_story_clause_repetition_score(candidate, used) >= 3 for used in used_sentences):
                continue
            if not self._scene_script_v2_story_candidate_allowed(slot_name, candidate, slot_data):
                continue
            score = 0
            hits = self._scene_script_v2_story_sentence_grounding_slots(candidate, slot_data)
            score += len(hits) * 12
            if slot_name in hits:
                score += 8
            if slot_data.get("actor") and slot_data["actor"] in candidate:
                score += 4
            if slot_data.get("target") and slot_data["target"] in candidate:
                score += 6
            if self._scene_script_v2_story_has_action_motion(candidate):
                score += 10 if slot_name in {"event", "memory", "intent"} else 4
            if prefer_action and self._scene_script_v2_story_has_action_motion(candidate):
                score += 12
            if prefer_action and not self._scene_script_v2_story_has_action_motion(candidate):
                score -= 12
            if "「" in candidate or "」" in candidate:
                score += 10 if slot_name in {"dialogue", "memory"} else -2
            if self._scene_script_v2_story_has_event_blob_density_issue(candidate):
                score -= 24
            if self._scene_script_v2_story_has_fragment_readability_issue(candidate):
                score -= 28
            event_quote_compare = self._scene_script_v2_story_compare_text(slot_data.get("eventQuote") or "")
            if event_quote_compare and event_quote_compare in self._scene_script_v2_story_compare_text(candidate):
                score -= 18 if slot_name != "memory" else 6
            if slot_name == "memory" and "信裡還寫著" in candidate:
                score += 8
            if slot_name == "event" and len(candidate) > 48:
                score -= 16
            if slot_name == "memory" and len(candidate) > 60 and "「" not in candidate:
                score -= 12
            score -= max(0, candidate.count("，") - 1) * 5
            if len(candidate) > 56:
                score -= len(candidate) - 56
            ranked.append((score, candidate))
        if not ranked:
            return ""
        return sorted(ranked, key=lambda item: (-item[0], len(item[1])))[0][1]

    def _scene_script_v2_story_pick_stage_sentence(self, slot_data: dict[str, Any]) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        place = str(slot_data.get("place") or "").strip()
        time_value = str(slot_data.get("time") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        if not actor:
            return ""
        prefix = actor
        if time_value and place:
            prefix = f"{time_value}，{actor}在{place}"
        elif place:
            prefix = f"{actor}在{place}"
        elif time_value:
            prefix = f"{time_value}，{actor}還在原處"
        if objects:
            lead = "身邊還有" if any(self._scene_script_v2_story_human_like_term(item) for item in objects) else "眼前還有"
            return self._ensure_sentence(f"{prefix}，{lead}{'和'.join(objects[:2])}")
        if time_value or place:
            return self._ensure_sentence(prefix)
        return ""

    def _scene_script_v2_story_pick_dialogue_sentence(
        self,
        slot_data: dict[str, Any],
        dialogue_clause: str,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        reply = dialogue_clause.strip("「」『』\"' ").rstrip("。！？；")
        if not reply:
            return ""
        if target_name and actor:
            candidates = [
                f"{target_name}問過之後，{actor}只回：「{reply}」",
                f"{target_name}把話拋出來，{actor}低聲答：「{reply}」",
                f"{target_name}問得直接，{actor}也只答：「{reply}」",
                f"{actor}沒有多說，只回了一句：「{reply}」",
            ]
        elif actor:
            candidates = [
                f"{actor}只回：「{reply}」",
                f"{actor}低聲答：「{reply}」",
                f"{actor}沒有多說，只答：「{reply}」",
            ]
        else:
            candidates = [f"只回了一句：「{reply}」"]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "dialogue",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
            prefer_quote=True,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))


    def _scene_script_v2_story_slot_fragments(
        self,
        slot_name: str,
        slot_data: dict[str, Any],
        primary: str,
    ) -> list[str]:
        first = str(primary or "").strip()
        if not first:
            return []
        fragments = [first]
        if slot_name not in {"event", "memory", "intent"}:
            return fragments
        total_chars = len(first)
        wants_more = len(self._scene_script_v2_story_compare_text(first)) < 14 or not self._scene_script_v2_story_has_action_motion(first)
        if slot_name == "memory" and first.startswith(("一直", "怕", "於是", "是")):
            wants_more = True
        if not wants_more and slot_name != "event":
            return fragments
        for candidate in slot_data.get(slot_name) or []:
            text = str(candidate or "").strip()
            if not text or text == first:
                continue
            if slot_name == "memory" and "信裡還寫著" in first:
                break
            if slot_name == "event":
                quote_texts = [
                    str(slot_data.get("eventQuote") or "").strip().rstrip("。！？；"),
                    str(slot_data.get("dialogueQuote") or "").strip().rstrip("。！？；"),
                ]
                if any(quote and text.rstrip("。！？；") == quote for quote in quote_texts):
                    continue
            if len(self._scene_script_v2_story_compare_text(text)) < 4:
                continue
            if self._scene_script_v2_story_has_repetition_noise(text):
                continue
            if any(self._scene_script_v2_story_sentences_overlap(text, existing) for existing in fragments):
                continue
            projected = total_chars + len(text) + 1
            if projected > 72:
                continue
            fragments.append(text)
            total_chars = projected
            if len(fragments) >= (3 if slot_name == "event" else 2):
                break
        return fragments

    def _scene_script_v2_story_compose_slot_sentence(
        self,
        slot_name: str,
        fragments: list[str],
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str] | None = None,
    ) -> str:
        parts = [str(item or "").strip().rstrip("，。！？；：") for item in fragments if str(item or "").strip()]
        if not parts:
            return ""
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        if slot_name == "dialogue":
            dialogue_text = "，".join(parts)
            dialogue_clause = self._scene_script_v2_story_dialogue_clause(dialogue_text, dialogue_text, max_chars=42)
            return self._scene_script_v2_story_pick_dialogue_sentence(
                slot_data,
                dialogue_clause,
                profile,
                target,
                used_sentences or [],
            )
        text = "，".join(parts)
        if slot_name == "memory" and "信裡還寫著" in text:
            sentence = text
        elif slot_name == "memory" and actor and actor not in text:
            if text.startswith(("一直", "仍", "還", "先", "再", "便", "就", "只", "怕", "於是", "要")):
                sentence = f"{actor}{text}"
            elif self._scene_script_v2_story_has_action_motion(text):
                sentence = f"{actor}{text}"
            else:
                sentence = f"{actor}還記得{text}"
        elif slot_name == "event" and actor and actor not in text:
            if text.startswith(("一直", "仍", "還", "先", "再", "便", "就", "只", "怕", "於是", "要")):
                sentence = f"{actor}{text}"
            elif self._scene_script_v2_story_has_action_motion(text):
                sentence = text
            else:
                sentence = f"{actor}眼前正逢{text}"
        elif slot_name == "intent" and actor and actor not in text:
            if text.startswith(("要", "先", "再", "回", "退", "守", "護", "請", "告", "說", "商", "設", "追", "返")):
                sentence = f"{actor}{text}"
            else:
                sentence = f"{actor}當下想的仍是{text}"
        elif slot_name == "emotion" and actor and actor not in text and target_name and target_name in text:
            sentence = f"{actor}{text}"
        else:
            sentence = text
        sentence = self._scene_script_v2_story_canonicalize_target_naming(sentence, target).strip()
        sentence = self._scene_script_v2_story_canonicalize_actor_clause(sentence, profile).strip()
        if slot_name == "dialogue":
            return self._scene_script_v2_story_pick_dialogue_sentence(
                slot_data,
                sentence,
                profile,
                target,
                used_sentences or [],
            )
        return self._ensure_sentence(sentence)

    def _scene_script_v2_story_pick_question_sentence(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        question = str(slot_data.get("eventQuote") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        actor = str(slot_data.get("actor") or "").strip()
        if not question:
            return ""
        question_text = self._scene_script_v2_story_dialogue_clause(question, question, max_chars=28).strip()
        if not question_text:
            return ""
        question_text = question_text.rstrip("？?。！!")
        if not question_text.endswith(("？", "?")):
            question_text = f"{question_text}？"
        if target_name:
            candidates = [
                f"{target_name}低聲問道：「{question_text}」",
                f"{target_name}抬眼追問：「{question_text}」",
                f"{target_name}看著{actor}，問道：「{question_text}」" if actor else f"{target_name}問道：「{question_text}」",
                f"{target_name}把話放低，問道：「{question_text}」",
            ]
        elif actor:
            candidates = [
                f"{actor}低聲問道：「{question_text}」",
                f"{actor}抬眼追問：「{question_text}」",
                f"{actor}看著眼前這一幕，問道：「{question_text}」",
            ]
        else:
            candidates = [f"問道：「{question_text}」"]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "dialogue",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
            prefer_quote=True,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_question_aftermath(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        place = str(slot_data.get("place") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        action_source = " ".join(
            str(item or "").strip()
            for item in [*(slot_data.get("event") or []), *(slot_data.get("memory") or [])]
            if str(item or "").strip()
        )
        mode = self._scene_script_v2_story_action_scene_mode(action_source)
        focus_term = target_name or (objects[0] if objects else place) or actor
        if not actor:
            return ""
        if mode == "movement":
            candidates = [
                f"那句話一落下，{actor}知道後面還得接著走。",
                f"那句話一落下，{actor}把回身的念頭先按住。",
                f"那句話一落下，{actor}便不敢再拖。",
            ]
        elif mode == "conflict":
            candidates = [
                f"那句話一落下，{actor}只得先收住手。",
                f"那句話一落下，{actor}先把火氣壓低。",
                f"那句話一落下，{actor}便知道不能再往前逼。",
            ]
        elif mode == "resolve":
            candidates = [
                f"那句話一落下，{actor}便知道還得把話說完。",
                f"那句話一落下，{actor}先把回應想清楚。",
                f"那句話一落下，{actor}只好把局面再看一遍。",
            ]
        else:
            candidates = [
                f"那句話一落下，{actor}心裡先沉了一截。",
                f"那句話一落下，{actor}知道這口氣不能再拖。",
                f"那句話一落下，{actor}只得先把顧慮壓住。",
            ]
        if focus_term and focus_term != actor:
            candidates.insert(0, f"那句話一落下，{actor}和{focus_term}之間的分寸就更明白了。")
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "quote_aftermath",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_support_sentence(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        place = str(slot_data.get("place") or "").strip()
        time_value = str(slot_data.get("time") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        people = [str(item or "").strip() for item in (slot_data.get("people") or []) if str(item or "").strip()]
        angle = str(slot_data.get("angle") or "").strip().lower()
        action_source = ""
        for slot_name in self._scene_script_v2_story_slot_order(angle):
            for candidate in slot_data.get(slot_name) or []:
                text = str(candidate or "").strip()
                if text and self._scene_script_v2_story_has_action_motion(text):
                    action_source = text
                    break
            if action_source:
                break
        mode = self._scene_script_v2_story_action_scene_mode(action_source)
        focus_terms: list[str] = []
        for value in [target_name, *(people[:1]), *(objects[:1]), place, time_value]:
            term = str(value or "").strip()
            if term and term not in focus_terms:
                focus_terms.append(term)
        focus_main = focus_terms[0] if focus_terms else (target_name or place or time_value)
        if not actor:
            return ""
        if angle == "emotion" or mode == "resolve":
            candidates = [
                f"{actor}先把心事穩住，只把眼前這一層看清，免得下一句又失了分寸。",
                f"{actor}沒有立刻開口，只把心事收短了一拍，好讓自己先站穩再說。",
                f"{actor}把心事壓住，先不讓話說得太滿，免得把局面推得更緊。",
            ]
        elif angle == "bond":
            candidates = [
                f"{actor}先把{focus_main}穩住，好讓後面那句接得上，不至於把關係立刻割開。",
                f"{actor}不急著把{focus_main}說開，只先把氣緩住，讓話還能慢慢接上去。",
                f"{actor}把{focus_main}收在心口，讓兩邊都有回頭路，不至於對話一開口就斷掉。",
            ]
        elif angle == "rival":
            candidates = [
                f"{actor}先把火氣壓住，沒讓{focus_main}當場炸開，場面才還能留一口氣。",
                f"{actor}盯著{focus_main}，先忍住沒有往前逼，免得場面立刻翻腳。",
                f"{actor}把{focus_main}穩在手邊，沒讓局面立刻臷臉，只先把進勒收一收。",
            ]
        else:
            candidates = [
                f"{actor}先把{focus_main}穩住，好讓局面先慢一拍，免得話一口氣全衝出去。",
                f"{actor}沒有立刻接話，只把{focus_main}放在眼前，讓後頭那句先慢下來。",
                f"{actor}把{focus_main}收短，讓後頭那句先不要出口，免得把局面推得更緊。",
            ]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "support",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_pressure_extension(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        place = str(slot_data.get("place") or "").strip()
        time_value = str(slot_data.get("time") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        angle = str(slot_data.get("angle") or "").strip().lower()
        action_source = ""
        for slot_name in ["event", "memory"]:
            for candidate in slot_data.get(slot_name) or []:
                text = str(candidate or "").strip()
                if text and self._scene_script_v2_story_has_action_motion(text):
                    action_source = text
                    break
            if action_source:
                break
        if not actor or not action_source:
            return ""
        mode = self._scene_script_v2_story_action_scene_mode(action_source)
        focus_a = objects[0] if objects else (target_name or place or time_value)
        focus_b = objects[1] if len(objects) > 1 else (place if place and place != focus_a else (target_name or time_value))
        if not focus_a:
            return ""
        if angle == "emotion" or mode == "resolve":
            candidates = [
                f"{focus_a}一緊，{actor}便知道再拖只會更難回身，連後路也一起卡住。",
                f"{actor}若再把這局拖下去，{focus_b}就要一起吃緊，回頭也更難。",
                f"{actor}把眼前這條路壓住，後面那一段也跟著發緊，不能再慢慢拖。",
            ]
        elif angle == "bond":
            candidates = [
                f"{focus_a}一牽動，{actor}就不好只顧眼前，還得把另一邊也看住。",
                f"{actor}若想把{focus_a}顧全，{focus_b}也得一併看住，免得一頭往下銷掉。",
                f"{focus_b}跟著吃緊，{actor}也只能先穩住局面，不能任由它纏下去。",
            ]
        elif angle == "rival":
            candidates = [
                f"{focus_a}一逼近，{actor}就知道不能再往前衝，免得場面立刻翻開。",
                f"{actor}若再硬頂，{focus_b}也會跟著翻起來，連周邊都要被卷進去。",
                f"{actor}把眼前這口氣壓住，場面才不會立刻炸開，也才能留一口氣。",
            ]
        else:
            candidates = [
                f"{focus_a}一緊，{actor}就知道這裡不能再拖，再拖就会得更緊。",
                f"{actor}再往前一步，{focus_b}就要卡死，回頭也不好轉。",
                f"{actor}把這一層局面壓住，後頭的路也會發緊，讓人沒法慢慢走。",
            ]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "pressure",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
            prefer_action=True,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_tension_sentence(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        place = str(slot_data.get("place") or "").strip()
        time_value = str(slot_data.get("time") or "").strip()
        action_source = " ".join(str(item or "").strip() for item in (slot_data.get("event") or slot_data.get("memory") or []) if str(item or "").strip())
        if not actor or not target_name:
            return ""
        side_term = objects[0] if objects else (place or time_value)
        angle = str(slot_data.get("angle") or "").strip().lower()
        mode = self._scene_script_v2_story_action_scene_mode(action_source)
        if angle == "emotion" or mode == "resolve":
            candidates = [
                f"{actor}和{target_name}一對上眼，話就卡在喇間，連呼吸都跟著慢下來。",
                f"{target_name}再一問，{actor}便把後話收住，不讓場面得太直白。",
                f"{actor}沒再把話說滿，只讓這一拍先停住，不讓局面立刻被拉開。",
            ]
        elif angle == "bond":
            candidates = [
                f"{actor}和{target_name}一碰上，彼此都先沉默了一拍，話也不敢動得太快。",
                f"{target_name}沒急著接話，{actor}也就把後話收住，讓這一拍先穩下來。",
                f"{actor}看著{target_name}，只覚得下一句更難落地，連心口都跟著緊。",
            ]
        elif angle == "rival":
            candidates = [
                f"{actor}一和{target_name}對上，場面便緊得發硬，連進一步都有描轉不開的味道。",
                f"{target_name}若再往前逼一步，{actor}便只能先把話收住，不讓腳步立刻走碍。",
                f"{actor}盯著{target_name}，不敢讓下一句先衝出去，連手都跟著收緊。",
            ]
        else:
            candidates = [
                f"{actor}和{target_name}一對上眼，話就卡住了，讓這一拍先停下來。",
                f"{actor}沒再往下說，只讓這一拍先停住，不讓話一口氣全走掉。",
                f"{target_name}一沉默，{actor}便把後話收住，讓場面先留一点空白。",
            ]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "tension",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
            prefer_action=True,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_fallback_ending(
        self,
        slot_data: dict[str, Any],
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        used_sentences: list[str],
    ) -> str:
        actor = str(slot_data.get("actor") or "").strip()
        target_name = str(slot_data.get("target") or "").strip()
        place = str(slot_data.get("place") or "").strip()
        objects = [str(item or "").strip() for item in (slot_data.get("objects") or []) if str(item or "").strip()]
        angle = str(slot_data.get("angle") or "").strip().lower()
        action_source = " ".join(
            str(item or "").strip()
            for item in [*(slot_data.get("intent") or []), *(slot_data.get("event") or []), *(slot_data.get("memory") or [])]
            if str(item or "").strip()
        )
        mode = self._scene_script_v2_story_action_scene_mode(action_source)
        focus_main = target_name or (objects[0] if objects else place) or actor
        if not actor:
            return ""
        if angle == "emotion" or mode == "resolve":
            candidates = [
                f"{actor}只好先把{focus_main}說開，再看{target_name or focus_main}怎麼回應，免得這一局再拖更緊。",
                f"{actor}決意先把話挑明，好讓{focus_main}有個落點，不至於擔著。",
                f"{actor}先把{place or focus_main}穩住，接著才好把心事說明，讓後面也有地方落下。",
            ]
        elif angle == "bond":
            candidates = [
                f"{actor}先把{focus_main}安住，再回頭和{target_name or focus_main}商量下一步，免得關係一頻雲乱。",
                f"{actor}只好先把後路顧住，免得這一層牵連散開，也好留住轉彈。",
                f"{actor}決意先把這局理順，再看{target_name or focus_main}怎麼回應，不讓多話一口氣陷死。",
            ]
        elif angle == "rival":
            candidates = [
                f"{actor}只得先收住手，免得{focus_main}真被掰翻，場面也才能留下來。",
                f"{actor}先把火氣壓下，再看{target_name or focus_main}要怎麼接，免得場面立刻翻開。",
                f"{actor}決定先停一步，好讓{focus_main}不至於當場翻開，也好留住回頭。",
            ]
        else:
            candidates = [
                f"{actor}只好先把{focus_main}說開，再看{target_name or focus_main}怎麼回應，免得後面話一直緊著。",
                f"{actor}先把局面穩住，好讓後面的話有地方落下，不至於滿盤追緊。",
                f"{actor}決意先理順這一層，再往下走，讓人心裡也能慢一拍。",
            ]
        chosen = self._scene_script_v2_story_pick_diverse_variant(
            "ending",
            candidates,
            slot_data,
            profile,
            target,
            used_sentences,
        )
        return self._ensure_sentence(chosen or (candidates[0] if candidates else ""))

    def _scene_script_v2_story_pick_actionable_ending(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        slot_data: dict[str, Any],
        used_sentences: list[str],
    ) -> str:
        preferred_slots = [slot for slot in self._scene_script_v2_story_slot_order(request.angle) if slot in {"event", "memory"}]
        seen_slots: list[str] = []
        for slot_name in preferred_slots:
            if slot_name in seen_slots or slot_name not in {"intent", "event", "memory", "dialogue", "emotion"}:
                continue
            seen_slots.append(slot_name)
            primary = self._scene_script_v2_story_best_slot_candidate(
                slot_name,
                slot_data,
                profile,
                target,
                beats,
                used_sentences,
                prefer_action=True,
            )
            if not primary:
                continue
            sentence = self._scene_script_v2_story_compose_slot_sentence(
                slot_name,
                self._scene_script_v2_story_slot_fragments(slot_name, slot_data, primary),
                slot_data,
                profile,
                target,
                used_sentences,
            )
            if not sentence:
                continue
            if self._scene_script_v2_story_quote_dominance_check(sentence) or self._extract_quoted_dialogue(sentence):
                continue
            if any(self._scene_script_v2_story_sentences_overlap(sentence, used) for used in used_sentences):
                continue
            if self._scene_script_v2_story_has_actionable_ending("".join([*used_sentences, sentence]), target, beats):
                return sentence
        fallback = self._scene_script_v2_story_pick_fallback_ending(slot_data, profile, target, used_sentences)
        if fallback and not any(self._scene_script_v2_story_sentences_overlap(fallback, used) for used in used_sentences):
            return fallback
        return ""


    def _scene_script_v2_source_repair_beats(
        self,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> SceneDirectorBeats:
        card = self._select_narrative_card(
            profile.evidenceCards,
            request.evidenceId,
            request.angle,
            target,
            actor_aliases=self._actor_label_aliases(profile),
        )
        if not card:
            return beats
        try:
            rebuilt = self._build_scene_director_beats(profile, card, target, request.angle)
        except Exception:
            return beats
        scene_facts = dict(rebuilt.sceneFacts or {})
        scene_seeds = dict(rebuilt.sceneSeeds or {})
        raw_scene_text = str(rebuilt.sceneText or "").strip()
        raw_scene_event = str(scene_seeds.get("event") or "").strip()
        raw_memory_text = str(rebuilt.memoryText or "").strip()
        if raw_scene_text:
            scene_facts["rawSceneText"] = raw_scene_text
        if raw_scene_event:
            scene_facts["rawSceneEventSeed"] = raw_scene_event
        compacted_event = self._scene_script_v2_story_compact_source_clause(
            "event",
            raw_scene_event or raw_scene_text,
            profile,
            target,
            max_chars=72,
        )
        compacted_memory = self._scene_script_v2_story_compact_source_clause(
            "memory",
            raw_memory_text,
            profile,
            target,
            max_chars=72,
        )
        if compacted_event:
            scene_seeds["event"] = compacted_event
        return rebuilt.model_copy(
            update={
                "sceneText": compacted_event or rebuilt.sceneText,
                "memoryText": compacted_memory or rebuilt.memoryText,
                "sceneSeeds": scene_seeds,
                "sceneFacts": scene_facts,
            }
        )

    def _scene_script_v2_repair_story_text(
        self,
        *,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
    ) -> str:
        source_beats = self._scene_script_v2_source_repair_beats(request, profile, target, beats)
        slot_data = self._scene_script_v2_story_build_slots(request, profile, target, source_beats)
        slot_order = self._scene_script_v2_story_slot_order(request.angle)
        sentences: list[str] = []
        used_slots: list[str] = []

        def append_sentence(sentence: str, slot_name: str | None = None) -> bool:
            text = str(sentence or "").strip()
            if not text:
                return False
            text = self._scene_script_v2_story_canonicalize_target_naming(text, target).strip()
            text = self._scene_script_v2_story_canonicalize_actor_clause(text, profile).strip()
            if self._scene_script_v2_story_has_fragment_readability_issue(text):
                text = self._scene_script_v2_story_normalize_action_fragment(text, profile, target)
            text = self._ensure_sentence(text)
            if not text:
                return False
            if sentences:
                previous = sentences[-1]
                if self._scene_script_v2_story_has_duplicate_quote_span("".join([previous, text])):
                    return False
                repetition_score = self._scene_script_v2_story_clause_repetition_score(previous, text)
                if self._scene_script_v2_story_sentences_overlap(text, previous) or repetition_score >= 3:
                    previous_is_ending = self._scene_script_v2_story_has_actionable_ending(previous, target, source_beats)
                    current_is_ending = self._scene_script_v2_story_has_actionable_ending(text, target, source_beats)
                    if current_is_ending and not previous_is_ending:
                        sentences[-1] = text
                        if slot_name and slot_name not in used_slots:
                            used_slots.append(slot_name)
                        return True
                    if slot_name not in {"support", "pressure", "tension"}:
                        return False
            sentences.append(text)
            if slot_name and slot_name not in used_slots:
                used_slots.append(slot_name)
            return True

        append_sentence(self._scene_script_v2_story_pick_stage_sentence(slot_data), "stage")

        primary_slots = [slot for slot in slot_order if slot in {"event", "memory"} and slot_data.get(slot)]
        for slot_name in primary_slots:
            primary = self._scene_script_v2_story_best_slot_candidate(
                slot_name,
                slot_data,
                profile,
                target,
                source_beats,
                sentences,
                prefer_action=slot_name == "event",
            )
            if not primary:
                continue
            if slot_name == "memory":
                primary_phrases = {
                    fragment
                    for fragment in self._scene_phrase_candidates(primary, max_terms=6)
                    if fragment and len(self._scene_script_v2_story_compare_text(fragment)) >= 4
                }
                if any(
                    self._scene_script_v2_story_sentences_overlap(primary, event_candidate)
                    or any(phrase in str(event_candidate or "") for phrase in primary_phrases)
                    for event_candidate in (slot_data.get("event") or [])
                ):
                    continue
            sentence = self._scene_script_v2_story_compose_slot_sentence(
                slot_name,
                self._scene_script_v2_story_slot_fragments(slot_name, slot_data, primary),
                slot_data,
                profile,
                target,
            )
            if append_sentence(sentence, slot_name) and len(sentences) >= 3:
                break

        append_sentence(
            self._scene_script_v2_story_pick_question_sentence(slot_data, profile, target, sentences),
            "quote",
        )
        if len(self._scene_script_v2_compact_story_text("".join(sentences))) < max(120, self._scene_script_v2_story_min_cjk_chars(request) - 60):
            append_sentence(
                self._scene_script_v2_story_pick_question_aftermath(slot_data, profile, target, sentences),
                "quote_aftermath",
            )

        if len(self._scene_script_v2_compact_story_text("".join(sentences))) < max(140, self._scene_script_v2_story_min_cjk_chars(request) - 30):
            for slot_name in slot_order:
                if slot_name in {"people", "time", "place", "objects", "dialogue"} or slot_name in used_slots:
                    continue
                primary = self._scene_script_v2_story_best_slot_candidate(
                    slot_name,
                    slot_data,
                    profile,
                    target,
                    source_beats,
                    sentences,
                )
                if not primary:
                    continue
                sentence = self._scene_script_v2_story_compose_slot_sentence(
                    slot_name,
                    self._scene_script_v2_story_slot_fragments(slot_name, slot_data, primary),
                    slot_data,
                    profile,
                    target,
                    sentences,
                )
                if append_sentence(sentence, slot_name):
                    break

        ending_sentence = self._scene_script_v2_story_pick_actionable_ending(
            request,
            profile,
            target,
            source_beats,
            slot_data,
            sentences,
        )

        if len(self._scene_script_v2_compact_story_text("".join(sentences))) < self._scene_script_v2_story_min_cjk_chars(request):
            append_sentence(self._scene_script_v2_story_pick_support_sentence(slot_data, profile, target, sentences), "support")
        if len(self._scene_script_v2_compact_story_text("".join(sentences))) < self._scene_script_v2_story_min_cjk_chars(request):
            append_sentence(self._scene_script_v2_story_pick_pressure_extension(slot_data, profile, target, sentences), "pressure")
        if len(self._scene_script_v2_compact_story_text("".join(sentences))) < self._scene_script_v2_story_min_cjk_chars(request):
            append_sentence(self._scene_script_v2_story_pick_tension_sentence(slot_data, profile, target, sentences), "tension")

        append_sentence(ending_sentence, "ending")
        sentences = self._scene_script_v2_story_cleanup_sentences(sentences, slot_data, profile, target, source_beats)

        story = "".join(sentences)
        if len(self._scene_script_v2_compact_story_text(story)) < self._scene_script_v2_story_min_cjk_chars(request):
            actor = str(slot_data.get("actor") or "").strip()
            target_name = str(slot_data.get("target") or "").strip()
            place = str(slot_data.get("place") or "").strip()
            if actor:
                if request.angle == "emotion":
                    tail_sentence = f"{actor}知道這一局不能再拖，只能先把話說明，再看{target_name or place or '眼前這一局'}怎麼接，免得後頭更難落地。"
                elif request.angle == "bond":
                    tail_sentence = f"{actor}知道這一局不能再拖，只能先把關係理順，再看{target_name or place or '眼前這一局'}怎麼接，也好讓後面還能往下走。"
                elif request.angle == "rival":
                    tail_sentence = f"{actor}知道這一局不能再拖，只能先收住手，免得{target_name or place or '場面'}當場炸開，也免得周邊的人被卷進去。"
                else:
                    tail_sentence = f"{actor}知道這一局不能再拖，只能先把話說明，再往下走，免得後面一路發緊。"
                tail_sentence = self._ensure_sentence(tail_sentence)
                if tail_sentence:
                    sentences.append(tail_sentence)
                    story = "".join(sentences)
        fitted, _ = self._scene_script_v2_fit_story_text(story, request)
        return fitted

    def _scene_script_v2_guard_story_generation(
        self,
        *,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        generation: DialogueGenerationResult,
    ) -> DialogueGenerationResult:
        if request.renderMode != SCENE_RENDER_MODE_LLM_SCRIPT_V2:
            return generation
        raw_story_text = str(generation.text or "").strip()
        canonical_story_text = self._scene_script_v2_story_canonicalize_target_naming(raw_story_text, target).strip()
        validation = self._scene_script_v2_story_validation(canonical_story_text, request, profile, target, beats)
        story_text = str(validation["normalizedText"] or "").strip()
        warnings = list(generation.qualityWarnings)
        if canonical_story_text and canonical_story_text != raw_story_text:
            warnings.append("scene_script_v2_story_target_canonicalized")
        repair_used = bool(generation.repairUsed)
        fallback_used = bool(generation.fallbackUsed)
        used_seed_keys = list(validation["coverageKeys"] or [])
        if not validation["valid"]:
            warnings = [warning for warning in warnings if not str(warning).startswith("scene_script_v2_story_")]
            repair_beats = self._scene_script_v2_source_repair_beats(request, profile, target, beats)
            repair_slot_data = self._scene_script_v2_story_build_slots(request, profile, target, repair_beats)
            story_text = self._scene_script_v2_repair_story_text(
                request=request,
                profile=profile,
                target=target,
                beats=repair_beats,
            )
            repaired_sentences = self._scene_script_v2_story_cleanup_sentences(
                self._scene_script_v2_story_sentences(story_text),
                repair_slot_data,
                profile,
                target,
                repair_beats,
            )
            if repaired_sentences:
                story_text = "".join(repaired_sentences)
            story_text, fit_warnings = self._scene_script_v2_fit_story_text(story_text, request)
            warnings.extend(fit_warnings)
            warnings.append("scene_script_v2_story_repaired")
            repair_used = True
            validation = self._scene_script_v2_story_validation(story_text, request, profile, target, repair_beats)
            used_seed_keys = list(validation["coverageKeys"] or used_seed_keys)
        elif story_text != generation.text:
            warnings.append("scene_script_v2_story_normalized")
            story_text, fit_warnings = self._scene_script_v2_fit_story_text(story_text, request)
            warnings.extend(fit_warnings)
        else:
            story_text, fit_warnings = self._scene_script_v2_fit_story_text(story_text, request)
            warnings.extend(fit_warnings)
        if not used_seed_keys:
            used_seed_keys = [key for key in ["people", "event", "time", "place", "objects", "emotion"] if key in validation["coverage"]]
        return replace(
            generation,
            text=story_text,
            fallbackUsed=fallback_used,
            repairUsed=repair_used or not validation["valid"],
            qualityWarnings=list(dict.fromkeys([
                *warnings,
                *([reason for reason in validation["warnings"] if reason not in warnings] if validation["warnings"] else []),
            ])),
        )

    def _scene_script_v2_compact_story_text(self, text: str) -> str:
        return re.sub(r"[\s，。！？；：「」『』、（）()《》〈〉,.!?;:'\"“”‘’\-—…]", "", str(text or ""))

    def _trim_scene_script_v2_story_to_cjk_limit(self, text: str, max_cjk_chars: int) -> str:
        kept: list[str] = []
        compact_count = 0
        for char in str(text or ""):
            if not re.match(r"[\s，。！？；：「」『』、（）()《》〈〉,.!?;:'\"“”‘’\-—…]", char):
                compact_count += 1
            if compact_count > max_cjk_chars:
                break
            kept.append(char)
        trimmed = "".join(kept).rstrip("，、；：「『（( ")
        return self._ensure_sentence(trimmed)

    def _scene_script_seed_summary(self, beats: SceneDirectorBeats) -> str:
        scene_seeds = beats.sceneSeeds or {}
        people = "、".join(
            str(item.get("label") or "").strip()
            for item in (scene_seeds.get("people") or [])
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        )
        objects = "、".join(str(item or "").strip() for item in (scene_seeds.get("objects") or []) if str(item or "").strip())
        lines = [
            f"人={people}" if people else "",
            f"事={str(scene_seeds.get('event') or '').strip()}" if str(scene_seeds.get("event") or "").strip() else "",
            f"時={str(scene_seeds.get('time') or '').strip()}" if str(scene_seeds.get("time") or "").strip() else "",
            f"地={str(scene_seeds.get('place') or '').strip()}" if str(scene_seeds.get("place") or "").strip() else "",
            f"物={objects}" if objects else "",
            f"情={str(scene_seeds.get('emotion') or '').strip()}" if str(scene_seeds.get("emotion") or "").strip() else "",
        ]
        return "；".join(line for line in lines if line)

    def _scene_script_text_has_placeholder(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        if self._contains_internal_symbolic_token(cleaned):
            return True
        return bool(re.search(r"(?:此人|主角|某人|[（(]\s*主角\s*[)）])", cleaned))

    def _parse_scene_script_pack_v2_payload(self, text: str) -> dict[str, Any]:
        try:
            payload = json.loads(str(text or ""))
        except json.JSONDecodeError as exc:
            raise ValueError(f"scene_script_pack_v2:json_parse:{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("scene_script_pack_v2:not-object")
        required_fields = ["memoryText", "emotionText", "dialogueText", "intentText", "storyText"]
        parsed = {field: " ".join(str(payload.get(field) or "").split()).strip() for field in required_fields}
        if not all(parsed.values()):
            raise ValueError("scene_script_pack_v2:missing_required_field")
        parsed["usedSeedKeys"] = [str(item).strip() for item in (payload.get("usedSeedKeys") or []) if str(item).strip()]
        parsed["usedPersonaAnchors"] = [str(item).strip() for item in (payload.get("usedPersonaAnchors") or []) if str(item).strip()]
        parsed["violations"] = [str(item).strip() for item in (payload.get("violations") or []) if str(item).strip()]
        return parsed

    def _scene_script_pack_v2_warnings(
        self,
        payload: dict[str, Any],
        story_text: str,
        request: SceneDirectorRequest,
    ) -> list[str]:
        warnings: list[str] = []
        compact_story = self._scene_script_v2_compact_story_text(story_text)
        if len(compact_story) < self._scene_script_v2_story_min_cjk_chars(request):
            warnings.append("scene_script_v2_story_short")
        if len(compact_story) > self._scene_script_v2_story_max_cjk_chars(request):
            warnings.append("scene_script_v2_story_long")
        if not payload.get("usedSeedKeys"):
            warnings.append("scene_script_v2_used_seed_keys_missing")
        if not payload.get("usedPersonaAnchors"):
            warnings.append("scene_script_v2_used_persona_anchors_missing")
        warnings.extend(str(item).strip() for item in (payload.get("violations") or []) if str(item).strip())
        return list(dict.fromkeys(warnings))

    def _generate_scene_script_pack_v2(
        self,
        *,
        request: SceneDirectorRequest,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        story_context: dict[str, Any],
        story_keywords: list[dict[str, Any]],
        evidence_refs: list[str],
    ) -> tuple[DialogueGenerationResult, SceneDirectorBeats]:
        persona_card = self.get_persona_card(request.generalId)
        selected_context = {
            **story_context,
            "task": "scene-script-pack-v2",
            "label": target.label if target else profile.displayName,
            "deprecatedModes": list(DEPRECATED_SCENE_RENDER_MODES.keys()),
        }
        memory_context = {
            "saveId": f"demo-scene-script-v2-{profile.generalId}",
            "shortTerm": beats.sceneText,
            "longTerm": self._scene_script_seed_summary(beats),
            "playerProfile": (
                f"主角={profile.displayName}；"
                f"互動對象={target.label if target else '未指定'}；"
                f"關係角度={card.angle if card else request.angle or '未指定'}；"
                f"對象關係={target.role if target else '未指定'}"
            ),
            "promises": (
                "請一次輸出主角五段文字：想起什麼、心裡怎麼變、對此人的一句話、接下來想做什麼、"
                "以及一段約 200 個繁體中文字、可演出的小劇本。"
            ),
        }
        provider_config = self._scene_story_provider_config(request.llmModelPreset, request.renderMode)
        generation = self._generate_scene_director_text(
            general_id=request.generalId,
            persona_card=persona_card,
            memory_context=memory_context,
            selected_context=selected_context,
            evidence_refs=evidence_refs,
            deterministic_text="",
            max_chars=self._scene_script_v2_prompt_budget(request),
            locale=request.locale,
            llm_model_preset=request.llmModelPreset,
            tone_mode="narrative_fusion",
            selected_keywords=story_keywords,
            include_resolved_evidence=True,
            provider_order=provider_config["providerOrder"],
            model_overrides=provider_config["modelOverrides"],
            allow_deterministic_fallback=provider_config["allowDeterministicFallback"],
        )
        raw_generation_text = str(generation.text or "").strip()
        payload_parse_failed = False
        try:
            payload = self._parse_scene_script_pack_v2_payload(raw_generation_text)
        except ValueError:
            payload_parse_failed = True
            scene_seeds = beats.sceneSeeds or {}
            used_seed_keys = [
                key
                for key, value in {
                    "people": self._scene_script_seed_summary(beats),
                    "event": str(scene_seeds.get("event") or "").strip(),
                    "time": str(scene_seeds.get("time") or "").strip(),
                    "place": str(scene_seeds.get("place") or "").strip(),
                    "objects": "、".join(str(item).strip() for item in (scene_seeds.get("objects") or []) if str(item).strip()),
                    "emotion": str(scene_seeds.get("emotion") or "").strip(),
                }.items()
                if value
            ]
            fallback_story = self._sentence_or_default(
                raw_generation_text if raw_generation_text and not raw_generation_text.startswith("{") else "",
                self._scene_director_data_first_story_text(profile, target, card, beats, request.maxStoryChars),
                max_chars=self._scene_script_v2_story_max_chars(request),
            )
            payload = {
                "memoryText": self._seed_display_sentence(profile, beats.memoryText or beats.sceneText, 120, preserve_actor_aliases=True),
                "emotionText": self._seed_display_sentence(profile, beats.emotionText, 96, preserve_actor_aliases=True),
                "dialogueText": self._seed_display_sentence(profile, beats.dialogueText, 80, preserve_actor_aliases=True)
                or self._sentence_or_default(
                    f"{persona_card.displayName if persona_card else profile.displayName}對{target.label if target else '對方'}說：先把眼前這件事守住。",
                    "",
                    max_chars=80,
                ),
                "intentText": self._seed_display_sentence(profile, beats.intentText, 88, preserve_actor_aliases=True),
                "storyText": fallback_story,
                "usedSeedKeys": used_seed_keys,
                "usedPersonaAnchors": [
                    anchor
                    for anchor in [
                        str(persona_card.displayName if persona_card else profile.displayName).strip(),
                        str(target.label if target else "").strip(),
                        str(card.angle if card and card.angle else request.angle or "").strip(),
                    ]
                    if anchor
                ],
                "violations": ["scene_script_pack_v2_json_repaired_from_plain_text"],
            }
            generation = replace(
                generation,
                qualityWarnings=[*generation.qualityWarnings, "scene_script_pack_v2_json_repaired_from_plain_text"],
                repairUsed=True,
            )
        story_text, story_fit_warnings = self._scene_script_v2_fit_story_text(
            " ".join(str(payload.get("storyText") or "").split()).strip() or raw_generation_text,
            request,
        )
        story_validation = self._scene_script_v2_story_validation(story_text, request, profile, target, beats)
        payload["usedSeedKeys"] = list(story_validation["coverageKeys"] or [])
        story_fit_warnings.extend(story_validation["warnings"])
        if self._scene_script_text_has_placeholder(story_text):
            raise ValueError("scene_script_pack_v2:story_placeholder_or_internal_token")
        memory_text = self._seed_display_sentence(profile, payload["memoryText"], 120, preserve_actor_aliases=True) or self._sentence_or_default(
            f"{persona_card.displayName if persona_card else profile.displayName}先想起這一幕的牽連。",
            "",
            max_chars=120,
        )
        emotion_text = self._seed_display_sentence(profile, payload["emotionText"], 96, preserve_actor_aliases=True) or self._sentence_or_default(
            "心裡先沉住，再把牽掛與責任收攏。",
            "",
            max_chars=96,
        )
        dialogue_text = self._seed_display_sentence(profile, payload["dialogueText"], 80, preserve_actor_aliases=True) or self._sentence_or_default(
            f"{persona_card.displayName if persona_card else profile.displayName}對{target.label if target else '對方'}說：先把眼前這件事守住。",
            "",
            max_chars=80,
        )
        intent_text = self._seed_display_sentence(profile, payload["intentText"], 88, preserve_actor_aliases=True) or self._sentence_or_default(
            "接下來先把局面安排穩，再往前推進。",
            "",
            max_chars=88,
        )
        if any(not value for value in [memory_text, emotion_text, dialogue_text, intent_text]):
            raise ValueError("scene_script_pack_v2:weak_beat_field")
        updated_beats = beats.model_copy(
            update={
                "memoryText": memory_text,
                "emotionText": emotion_text,
                "dialogueText": dialogue_text,
                "intentText": intent_text,
            }
        )
        updated_generation = replace(
            generation,
            text=story_text,
            generationMode="scene-script-pack-v2",
            qualityWarnings=[
                *generation.qualityWarnings,
                *story_fit_warnings,
                *self._scene_script_pack_v2_warnings(payload, story_text, request),
                *(["scene_script_pack_v2_json_repaired_from_plain_text"] if payload_parse_failed else []),
            ],
        )
        return updated_generation, updated_beats

    def _parse_scene_chorus_batch_v2_payload(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(str(text or ""))
        except json.JSONDecodeError as exc:
            raise ValueError(f"scene_chorus_batch_v2:json_parse:{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("scene_chorus_batch_v2:not-object")
        raw_lines = payload.get("chorusLines")
        if not isinstance(raw_lines, list):
            raise ValueError("scene_chorus_batch_v2:missing_chorus_lines")
        lines: list[dict[str, Any]] = []
        for item in raw_lines:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("targetId") or "").strip()
            text_value = " ".join(str(item.get("text") or "").split()).strip()
            if not target_id or not text_value:
                continue
            lines.append(
                {
                    "targetId": target_id,
                    "label": str(item.get("label") or "").strip(),
                    "role": str(item.get("role") or "").strip(),
                    "text": text_value,
                    "usedSeedKeys": [str(seed).strip() for seed in (item.get("usedSeedKeys") or []) if str(seed).strip()],
                    "usedPersonaAnchors": [str(anchor).strip() for anchor in (item.get("usedPersonaAnchors") or []) if str(anchor).strip()],
                    "voiceTag": str(item.get("voiceTag") or "").strip(),
                    "violations": [str(code).strip() for code in (item.get("violations") or []) if str(code).strip()],
                }
            )
        if not lines:
            raise ValueError("scene_chorus_batch_v2:no_valid_lines")
        return lines

    def _build_scene_chorus_batch_v2(
        self,
        *,
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
            speaker_context = self._scene_chorus_personalized_context(
                self._speaker_persona_context(target, persona_card),
                target,
                main_target,
                beats,
                story_text,
            )
            return SceneChorusLine(
                targetId=target.targetId,
                label=target.label,
                role=target.role,
                text=self._compose_scene_grounded_chorus_fallback_v3(
                    target=target,
                    main_target=main_target,
                    beats=beats,
                    story_text=story_text,
                    speaker_context=speaker_context,
                ),
                provider="deterministic",
                model=None,
                generationMode="data_first-deterministic-deprecated",
                fallbackUsed=True,
                providerTrace=[f"scene_chorus_batch_v2_fallback:{reason}"],
                qualityWarnings=["scene_chorus_batch_v2_failed", *self._deprecated_render_mode_warnings(SCENE_RENDER_MODE_DATA_FIRST)],
                evidenceRefs=evidence_refs[:12],
            )

        if timeout_seconds is not None and timeout_seconds <= 0.05:
            return [fallback_line(target, "deadline-before-submit") for target in targets]

        speakers_payload: list[dict[str, Any]] = []
        speaker_contexts: dict[str, dict[str, Any]] = {}
        evidence_refs = list(card.sourceRefs if card else [])
        for target in targets:
            persona_card = self.get_persona_card(target.targetId)
            speaker_context = self._scene_chorus_personalized_context(
                self._speaker_persona_context(target, persona_card),
                target,
                main_target,
                beats,
                story_text,
            )
            speaker_contexts[target.targetId] = speaker_context
            evidence_refs.extend(target.evidenceRefs)
            speakers_payload.append(
                {
                    "targetId": target.targetId,
                    "label": target.label,
                    "role": target.role,
                    "relationshipType": target.relationshipType,
                    "guidance": self._speaker_persona_guidance(speaker_context, target),
                    "focusHint": str(speaker_context.get("focusHint") or ""),
                    "seedHint": str(speaker_context.get("seedHint") or ""),
                    "voiceHook": str(speaker_context.get("voiceHook") or ""),
                    "anchors": self._speaker_persona_anchor_terms(speaker_context),
                    "voiceStyle": [str(item).strip() for item in (speaker_context.get("voiceStyle") or []) if str(item).strip()],
                    "personalityTraits": [str(item).strip() for item in (speaker_context.get("personalityTraits") or []) if str(item).strip()],
                    "femaleFocus": bool(target.femaleFocus),
                }
            )

        def selected_keywords_for(target: NarrativeInteractionTarget, speaker_context: dict[str, Any]) -> list[dict[str, Any]]:
            selected_keywords = self._scene_chorus_keywords(profile, target, main_target, beats, speaker_context)
            for prefix, term in [
                ("speaker_focus", str(speaker_context.get("focusHint") or "").strip()),
                ("speaker_seed", str(speaker_context.get("seedHint") or "").strip()),
            ]:
                if not term:
                    continue
                selected_keywords.append(
                    {
                        "keywordKey": (
                            f"{prefix}.{target.targetId}."
                            f"{hashlib.sha1(term.encode('utf-8')).hexdigest()[:10]}"
                        ),
                        "category": prefix,
                        "label": term,
                        "sourceRefs": target.evidenceRefs[:4],
                    }
                )
            return selected_keywords

        def try_repair_line(
            *,
            target: NarrativeInteractionTarget,
            line_generation: DialogueGenerationResult,
            speaker_context: dict[str, Any],
            fallback_text: str,
            draft_text: str,
            evidence_refs_for_line: list[str],
            quality_issues: list[str],
        ) -> DialogueGenerationResult | None:
            try:
                regenerated = self._rewrite_scene_chorus_from_fallback(
                    request=request,
                    profile=profile,
                    target=target,
                    main_target=main_target,
                    beats=beats,
                    story_text=story_text,
                    speaker_context=speaker_context,
                    selected_keywords=selected_keywords_for(target, speaker_context),
                    evidence_refs=evidence_refs_for_line,
                    fallback_text=fallback_text,
                    draft_text=draft_text,
                    quality_issues=quality_issues,
                )
            except Exception as exc:  # pragma: no cover - rewrite is best-effort
                log_debug_event(
                    "scene_director.chorus_batch_v2.repair_error",
                    targetId=target.targetId,
                    error=str(exc)[:240],
                )
                return None
            if regenerated is None:
                fallback_candidate = self._complete_generated_text(fallback_text, fallback_text, request.maxChorusChars)
                if (
                    fallback_candidate
                    and not self._contains_internal_symbolic_token(fallback_candidate)
                    and not self._is_narrative_exposition_chorus_line(fallback_candidate, beats)
                ):
                    warnings = [warning for warning in line_generation.qualityWarnings if warning != "scene_chorus_ungrounded_rejected"]
                    warnings.append("scene_chorus_fallback_text_accepted")
                    return replace(
                        line_generation,
                        text=fallback_candidate,
                        fallbackUsed=False,
                        repairUsed=True,
                        providerTrace=[*line_generation.providerTrace, f"scene-chorus-batch-v2-fallback:{target.targetId}"],
                        qualityWarnings=list(dict.fromkeys(warnings)),
                    )
                return None
            return replace(
                regenerated,
                providerTrace=[*regenerated.providerTrace, f"scene-chorus-batch-v2-repair:{target.targetId}"],
            )

        def accept_structured_line_by_metadata(
            *,
            raw_line: dict[str, Any],
            line_generation: DialogueGenerationResult,
            raw_text: str,
            fallback_text: str,
            target: NarrativeInteractionTarget,
            speaker_context: dict[str, Any],
        ) -> DialogueGenerationResult | None:
            if line_generation.provider == "deterministic":
                return None
            warnings = list(line_generation.qualityWarnings)
            if "scene_chorus_persona_thin_rejected" not in warnings:
                return None
            metadata_anchors = [
                str(item or "").strip()
                for item in [
                    *(raw_line.get("usedPersonaAnchors") or []),
                    raw_line.get("voiceTag"),
                ]
                if str(item or "").strip()
            ]
            if not metadata_anchors:
                return None
            candidate = self._complete_generated_text(raw_text, fallback_text, request.maxChorusChars)
            if (
                not candidate
                or self._contains_internal_symbolic_token(candidate)
                or self._is_narrative_exposition_chorus_line(candidate, beats)
                or self._is_generic_chorus_line(candidate, speaker_context)
                or not self._line_has_scene_grounding(candidate, main_target, beats, story_text)
            ):
                return None
            warnings = [warning for warning in warnings if warning != "scene_chorus_persona_thin_rejected"]
            warnings.append("scene_chorus_persona_anchor_metadata_used")
            return replace(
                line_generation,
                text=candidate,
                fallbackUsed=False,
                repairUsed=True,
                qualityWarnings=list(dict.fromkeys(warnings)),
            )

        provider_config = self._scene_chorus_provider_config(request.llmModelPreset, request.renderMode)
        generation = self._generate_scene_director_text(
            general_id=request.generalId,
            persona_card=self.get_persona_card(request.generalId),
            memory_context={
                "saveId": f"demo-scene-chorus-batch-v2-{request.generalId}",
                "shortTerm": f"本幕主劇本：{story_text}",
                "longTerm": self._scene_script_seed_summary(beats),
                "playerProfile": (
                    f"主角={profile.displayName}；互動對象={main_target.label if main_target else '未指定'}；"
                    f"共 {len(targets)} 位路人各說一句。"
                ),
                "promises": "請一次回傳所有指定 speaker 的一句台詞，每句都要有獨立個性、立場與場景接地。",
            },
            selected_context={
                "task": "scene-chorus-batch-v2",
                "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
                "activeTarget": {
                    "targetId": main_target.targetId if main_target else None,
                    "label": main_target.label if main_target else None,
                    "role": main_target.role if main_target else None,
                },
                "speakers": speakers_payload,
                "sceneSeeds": self._scene_chorus_sanitized_seeds(beats),
                "sceneFacts": beats.sceneFacts,
                "sceneGrounding": self._scene_chorus_grounding_terms(main_target, beats, story_text)[:8],
                "sceneScript": story_text,
            },
            evidence_refs=sorted(set(ref for ref in evidence_refs if ref)),
            deterministic_text="",
            max_chars=max(240, request.maxChorusChars * max(2, len(targets))),
            locale=request.locale,
            llm_model_preset=request.llmModelPreset,
            speech_context_mode="inner_monologue",
            tone_mode="in-character",
            selected_keywords=[],
            include_resolved_evidence=True,
            provider_order=provider_config["providerOrder"],
            model_overrides=provider_config["modelOverrides"],
            allow_deterministic_fallback=provider_config["allowDeterministicFallback"],
        )
        parsed_lines = self._parse_scene_chorus_batch_v2_payload(generation.text)
        parsed_map = {str(item.get("targetId") or "").strip(): item for item in parsed_lines if str(item.get("targetId") or "").strip()}
        results: list[SceneChorusLine] = []
        for target in targets:
            raw_line = parsed_map.get(target.targetId)
            evidence_refs_for_line = sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))
            speaker_context = speaker_contexts[target.targetId]
            fallback_text = self._compose_scene_grounded_chorus_fallback_v3(
                target=target,
                main_target=main_target,
                beats=beats,
                story_text=story_text,
                speaker_context=speaker_context,
            )
            if raw_line is None:
                repaired_missing = try_repair_line(
                    target=target,
                    speaker_context=speaker_context,
                    fallback_text=fallback_text,
                    draft_text="",
                    evidence_refs_for_line=evidence_refs_for_line,
                    quality_issues=["scene_chorus_batch_v2_missing_target_line"],
                )
                if repaired_missing is None:
                    results.append(fallback_line(target, "missing_target_line"))
                    continue
                raw_line = {"label": target.label, "role": target.role, "text": repaired_missing.text, "violations": []}
                line_generation = repaired_missing
            else:
                line_generation = replace(
                    generation,
                    text=str(raw_line.get("text") or "").strip(),
                    generationMode="scene-chorus-batch-v2",
                    providerTrace=[*generation.providerTrace, f"scene-chorus-batch-v2:{target.targetId}"],
                    qualityWarnings=[
                        *generation.qualityWarnings,
                        *[str(code).strip() for code in (raw_line.get("violations") or []) if str(code).strip()],
                    ],
                )
                raw_generation_text = str(line_generation.text or "").strip()
                line_generation = self._repair_complete_generation(
                    line_generation,
                    fallback_text=fallback_text,
                    max_chars=request.maxChorusChars,
                    warning_code="scene_chorus_trimmed_to_complete_sentence",
                )
                line_generation = self._repair_chorus_generation(
                    generation=line_generation,
                    fallback_text=fallback_text,
                    max_chars=request.maxChorusChars,
                    target=target,
                    speaker_context=speaker_context,
                    main_target=main_target,
                    beats=beats,
                    story_text=story_text,
                )
                if line_generation.fallbackUsed:
                    accepted_line = accept_structured_line_by_metadata(
                        raw_line=raw_line,
                        line_generation=line_generation,
                        raw_text=raw_generation_text,
                        fallback_text=fallback_text,
                        target=target,
                        speaker_context=speaker_context,
                    )
                    if accepted_line is not None:
                        line_generation = accepted_line
                    else:
                        repaired_line = try_repair_line(
                            target=target,
                            line_generation=line_generation,
                            speaker_context=speaker_context,
                            fallback_text=fallback_text,
                            draft_text=raw_generation_text,
                            evidence_refs_for_line=evidence_refs_for_line,
                            quality_issues=list(line_generation.qualityWarnings),
                        )
                        if repaired_line is not None:
                            line_generation = repaired_line
                    if line_generation.fallbackUsed:
                        fallback_candidate = self._complete_generated_text(fallback_text, fallback_text, request.maxChorusChars)
                        if (
                            fallback_candidate
                            and not self._contains_internal_symbolic_token(fallback_candidate)
                            and not self._is_narrative_exposition_chorus_line(fallback_candidate, beats)
                            and not self._is_generic_chorus_line(fallback_candidate, speaker_context)
                            and self._line_has_scene_grounding(fallback_candidate, main_target, beats, story_text)
                        ):
                            fallback_warnings = [
                                warning
                                for warning in line_generation.qualityWarnings
                                if warning != "scene_chorus_ungrounded_rejected"
                            ]
                            fallback_warnings.append("scene_chorus_fallback_text_accepted")
                            line_generation = replace(
                                line_generation,
                                text=fallback_candidate,
                                fallbackUsed=False,
                                repairUsed=True,
                                qualityWarnings=list(dict.fromkeys(fallback_warnings)),
                            )
            if raw_line is None:
                continue
            line_warnings = list(line_generation.qualityWarnings)
            if line_generation.fallbackUsed:
                line_warnings.extend(self._deprecated_render_mode_warnings(SCENE_RENDER_MODE_DATA_FIRST))
            line = SceneChorusLine(
                targetId=target.targetId,
                label=str(raw_line.get("label") or "").strip() or target.label,
                role=str(raw_line.get("role") or "").strip() or target.role,
                text=line_generation.text,
                provider=line_generation.provider,
                model=line_generation.model,
                generationMode=line_generation.generationMode,
                fallbackUsed=line_generation.fallbackUsed,
                cacheHit=self._generation_cache_hit(line_generation),
                providerTrace=list(line_generation.providerTrace),
                qualityWarnings=list(dict.fromkeys(line_warnings)),
                evidenceRefs=evidence_refs_for_line[:12],
            )
            if not line_generation.fallbackUsed:
                self._record_scene_chorus_history(
                    request=request,
                    target=target,
                    context_key=None,
                    selected_keywords=[],
                    evidence_refs=evidence_refs_for_line,
                    generation=line_generation,
                )
            results.append(line)
        return results

    # [已過時] Deprecated legacy deterministic fallback. Keep only as an explicit
    # data_first / emergency fallback marker while llm_script_v2 is the main path.
    def _scene_director_data_first_story_text(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget | None,
        card: NarrativeEvidenceCard | None,
        beats: SceneDirectorBeats,
        max_chars: int,
    ) -> str:
        _ = card
        scene_facts = beats.sceneFacts or {}
        scene_seeds = beats.sceneSeeds or {}
        scene_clause = self._clean_seed_text(beats.sceneText or beats.memoryText, max_chars=140).strip()
        emotion_clause = self._clean_seed_text(
            beats.emotionText or self._relationship_derived_emotion_text(target),
            max_chars=40,
        ).strip()
        dialogue_clause = self._clean_seed_text(beats.dialogueText, max_chars=80).strip()
        intent_clause = self._clean_seed_text(beats.intentText, max_chars=80).strip()
        time_clause = self._clean_seed_text(scene_seeds.get("time") or self._scene_seed_time(scene_facts), max_chars=24).strip()
        place_clause = self._clean_seed_text(scene_seeds.get("place") or self._scene_seed_place(scene_facts, []), max_chars=28).strip()
        people_terms: list[str] = []
        for item in scene_seeds.get("people") or []:
            if len(people_terms) >= 3:
                break
            label = ""
            role = ""
            if isinstance(item, dict):
                label = self._clean_seed_text(item.get("label"), max_chars=18).strip()
                role = self._clean_seed_text(item.get("role"), max_chars=12).strip()
            else:
                label = self._clean_seed_text(item, max_chars=18).strip()
            if not label:
                continue
            phrase = f"{label}（{role}）" if role and role != label else label
            if phrase not in people_terms:
                people_terms.append(phrase)
        object_terms = [
            self._clean_seed_text(item, max_chars=14).strip()
            for item in (scene_seeds.get("objects") or [])
            if self._clean_seed_text(item, max_chars=14).strip()
        ]
        object_terms = [item for item in dict.fromkeys(object_terms)][:3]
        event_clause = self._clean_seed_text(
            scene_seeds.get("event") or beats.sceneText or beats.memoryText,
            max_chars=96,
        ).strip()
        main_name = self._clean_seed_text(profile.displayName or "", max_chars=20)
        target_name = self._clean_seed_text((target.label if target else "") or "", max_chars=20)
        opening_bits: list[str] = []
        if time_clause:
            opening_bits.append(time_clause)
        if place_clause:
            opening_bits.append(f"在{place_clause}")
        if people_terms:
            opening_bits.append("、".join(people_terms))
        if object_terms:
            opening_bits.append(f"眼前還有{'、'.join(object_terms)}")
        if main_name and target_name and target_name != main_name and not any(main_name in bit and target_name in bit for bit in opening_bits):
            opening_bits.append(f"{main_name}與{target_name}")
        if not opening_bits:
            return ""
        sentences: list[str] = []
        opening_sentence = "，".join(opening_bits).rstrip("。！？!?")
        if opening_sentence:
            sentences.append(f"{opening_sentence}。")
        secondary_clause = ""
        for candidate in [scene_clause, event_clause]:
            cleaned_candidate = self._clean_seed_text(candidate, max_chars=160).strip()
            if cleaned_candidate and not self._is_weak_scene_seed_text(cleaned_candidate):
                normalized_candidate = cleaned_candidate.rstrip("。！？!?")
                if normalized_candidate and normalized_candidate not in opening_sentence:
                    secondary_clause = normalized_candidate
                    break
        if secondary_clause:
            sentences.append(f"{secondary_clause}。")
        if dialogue_clause:
            quoted_dialogue = dialogue_clause.rstrip("。！？!?")
            if not re.search(r"[「『].+[」』]", quoted_dialogue):
                speaker_name = "孫夫人" if target and target.femaleFocus else (target_name or "對方")
                quoted_dialogue = f"{speaker_name}說：「{quoted_dialogue}」"
            sentences.append(f"{quoted_dialogue}。")
        closing_bits: list[str] = []
        if emotion_clause:
            closing_bits.append(f"心裡浮著{emotion_clause}")
        if intent_clause and not self._is_weak_scene_seed_text(intent_clause):
            closing_bits.append(f"接著只想{intent_clause.rstrip('。！？!?')}")
        if closing_bits:
            sentences.append(f"{'，'.join(closing_bits)}。")
        if not sentences:
            return ""
        return self._sentence_or_default("".join(sentences), "", max_chars=max_chars)

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
        speaker_context = self._scene_chorus_personalized_context(
            self._speaker_persona_context(target, persona_card),
            target,
            main_target,
            beats,
            story_text,
        )
        speaker_focus_term = str(speaker_context.get("focusHint") or "").strip()
        speaker_seed_term = str(speaker_context.get("seedHint") or "").strip()
        if request.renderMode == SCENE_RENDER_MODE_DATA_FIRST:
            text = self._compose_scene_grounded_chorus_fallback_v3(
                target=target,
                main_target=main_target,
                beats=beats,
                story_text=story_text,
                speaker_context=speaker_context,
            )
            return SceneChorusLine(
                targetId=target.targetId,
                label=target.label,
                role=target.role,
                text=text,
                provider="deterministic",
                model=None,
                generationMode="data_first-deterministic-deprecated",
                fallbackUsed=True,
                evidenceRefs=sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))[:12],
                providerTrace=["scene_director.chorus.data_first_deterministic"],
                qualityWarnings=self._deprecated_render_mode_warnings(request.renderMode),
            )
        evidence_refs = sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))
        selected_keywords = self._scene_chorus_keywords(profile, target, main_target, beats, speaker_context)
        if speaker_focus_term:
            selected_keywords.append(
                {
                    "keywordKey": (
                        f"speaker_focus.{target.targetId}."
                        f"{hashlib.sha1(speaker_focus_term.encode('utf-8')).hexdigest()[:10]}"
                    ),
                    "category": "speaker_focus",
                    "label": speaker_focus_term,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
        if speaker_seed_term:
            selected_keywords.append(
                {
                    "keywordKey": (
                        f"speaker_seed.{target.targetId}."
                        f"{hashlib.sha1(speaker_seed_term.encode('utf-8')).hexdigest()[:10]}"
                    ),
                    "category": "speaker_seed",
                    "label": speaker_seed_term,
                    "sourceRefs": target.evidenceRefs[:4],
                }
            )
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        fallback_text = self._compose_scene_grounded_chorus_fallback_v3(
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
        provider_config = self._scene_chorus_provider_config(request.llmModelPreset, request.renderMode)
        if not draft_line and fallback_text:
            prompt_player_profile = prompt_player_profile.replace(fallback_text, "請直接依本幕重寫一句反應")
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
                    f"本人專屬著力點：{speaker_focus_term or '依關係自行判斷'}。"
                    f"本幕專屬種子：{speaker_seed_term or '依場景自行判斷'}。"
                    f"本幕必須對準的場景錨點：{'、'.join(grounding_terms[:6]) or '互動對象與當下動作'}。"
                ),
                "promises": (
                    "請以發話者視角說一句自然短對白；要讓 persona、關係與本幕短劇本共同決定語氣。"
                    "這句話必須直接對準互動對象，或本幕裡一個具體動作、地點、物件。"
                    "每位路人都要抓不同的關係、性格與六種子，彼此口氣不能一樣。"
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
        raw_generation = generation
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
                    draft_text=str(raw_generation.text or "").strip(),
                    quality_issues=list(generation.qualityWarnings),
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
            generationMode=generation.generationMode,
            fallbackUsed=generation.fallbackUsed,
            cacheHit=self._generation_cache_hit(generation),
            providerTrace=list(generation.providerTrace),
            qualityWarnings=list(generation.qualityWarnings),
            evidenceRefs=evidence_refs[:12],
        )
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
        # [已過時] Legacy per-line chorus path for data_first / emergency fallback.
        # The normal theatre route is _build_scene_chorus_batch_v2().
        if not targets:
            return []
        def fallback_line(target: NarrativeInteractionTarget, reason: str) -> SceneChorusLine:
            evidence_refs = sorted(set((card.sourceRefs if card else []) + target.evidenceRefs))
            persona_card = self.get_persona_card(target.targetId)
            speaker_context = self._scene_chorus_personalized_context(
                self._speaker_persona_context(target, persona_card),
                target,
                main_target,
                beats,
                story_text,
            )
            log_debug_event(
                "scene_director.chorus.fallback",
                targetId=target.targetId,
                reason=reason,
            )
            return SceneChorusLine(
                targetId=target.targetId,
                label=target.label,
                role=target.role,
                text=self._compose_scene_grounded_chorus_fallback_v3(
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
        max_workers = min(2, len(targets))
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
        return [line for line in results if line is not None and str(line.text or "").strip()]

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
            "version": 8,
            "generalId": request.generalId,
            "speakerId": target.targetId,
            "speakerRole": target.role,
            "activeTargetId": main_target.targetId if main_target else None,
            "activeTargetRole": main_target.role if main_target else None,
            "evidenceId": card.evidenceId if card else None,
            "angle": card.angle if card else request.angle,
            "locale": request.locale,
            "maxChars": request.maxChorusChars,
            "profileSnapshot": self._scene_profile_cache_snapshot(profile),
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
        context_key = self._scene_chorus_context_key(profile, target, main_target, beats, story_text, speaker_context)
        sanitized_seeds = self._scene_chorus_sanitized_seeds(beats)
        return {
            "contextKey": context_key,
            "speaker": {
                "generalId": target.targetId,
                "displayName": target.label,
                "relationshipToMain": target.role,
                "persona": speaker_context,
                "guidance": self._speaker_persona_guidance(speaker_context, target),
                "focusHint": str(speaker_context.get("focusHint") or ""),
                "seedHint": str(speaker_context.get("seedHint") or ""),
                "voiceHook": str(speaker_context.get("voiceHook") or ""),
            },
            "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
            "activeTarget": {
                "targetId": main_target.targetId if main_target else None,
                "label": main_target.label if main_target else None,
                "role": main_target.role if main_target else None,
            },
            "sceneSeeds": sanitized_seeds,
            "sceneGrounding": grounding_terms[:8],
            "sceneScript": story_text,
            "speakerFocus": str(speaker_context.get("focusHint") or ""),
            "speakerSeed": str(speaker_context.get("seedHint") or ""),
        }

    def _scene_chorus_context_key(
        self,
        profile: NarrativeProfileResponse,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "version": 9,
            "mainActor": profile.generalId,
            "speaker": target.targetId,
            "relationship": target.role,
            "activeTarget": main_target.targetId if main_target else None,
            "activeTargetRole": main_target.role if main_target else None,
            "activeTargetAliases": self._target_aliases_for_interaction(main_target)[:6] if main_target else [],
            "sceneSeeds": beats.sceneSeeds or {},
            "sceneGrounding": self._scene_chorus_grounding_terms(main_target, beats, story_text)[:8],
            "speakerFocus": str((speaker_context or {}).get("focusHint") or ""),
            "speakerSeed": str((speaker_context or {}).get("seedHint") or ""),
            "storyText": self._clean_seed_text(story_text, max_chars=320),
            "sourceRefs": beats.sourceRefs,
            "profileSnapshot": self._scene_profile_cache_snapshot(profile),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"scene-chorus:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"

    def _scene_chorus_provider_config(self, llm_model_preset: str, render_mode: str | None = None) -> dict[str, Any]:
        preset_config = LLM_MODEL_PRESETS.get(llm_model_preset, LLM_MODEL_PRESETS[DEFAULT_LLM_MODEL_PRESET])
        base_order = list(preset_config.get("providerOrder") or self.provider_router.provider_order or [])
        provider_order: list[str] = []
        if self.history_cache_enabled and render_mode != SCENE_RENDER_MODE_LLM_SCRIPT_V2:
            provider_order.append("history_cache")
        preferred = ["gemini_flash_lite", "gemini_flash", "gemini"]
        for provider_name in [*base_order, *preferred]:
            if provider_name in {"history_cache", "deterministic"}:
                continue
            if provider_name not in provider_order:
                provider_order.append(provider_name)
        model_overrides = dict(preset_config.get("modelOverrides") or {})
        model_overrides.setdefault("__timeoutMs", "4500")
        model_overrides.setdefault("__retryCount", "1")
        return {
            "providerOrder": provider_order,
            "modelOverrides": model_overrides,
            "allowDeterministicFallback": render_mode == SCENE_RENDER_MODE_LLM_SCRIPT_V2,
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
            ("event", self._sanitize_chorus_event_text(scene_seeds.get("event"))),
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
        values: list[str] = []
        for source_text in [raw, self._humanize_tag_list(re.split(r"[\s,、]+", raw))]:
            for item in re.split(r"[、,\s]+", str(source_text or "")):
                candidate = item.strip()
                if not candidate or candidate in {"符合三國語境", "謹慎應對"}:
                    continue
                if len(candidate) < 2:
                    continue
                if candidate not in values:
                    values.append(candidate)
                if len(values) >= 8:
                    return values
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

    def _scene_profile_cache_snapshot(self, profile: NarrativeProfileResponse) -> dict[str, Any]:
        persona = profile.persona if isinstance(profile.persona, dict) else {}
        relationship_summary = persona.get("relationshipSummary") if isinstance(persona.get("relationshipSummary"), dict) else {}
        return {
            "generalId": profile.generalId,
            "personaVersion": str(persona.get("personaVersion") or ""),
            "personaGeneratedAt": str(persona.get("generatedAt") or ""),
            "relationshipVersion": str(relationship_summary.get("relationshipVersion") or ""),
            "relationshipCount": len(profile.relationshipEdges or []),
            "activitySeedCount": len(profile.activitySeeds or []),
            "interactionTargetCount": len(profile.interactionTargets or []),
            "keywordCategoryCount": len(profile.keywords or {}),
        }

    def _speaker_archetype(self, text: str, target: NarrativeInteractionTarget) -> str:
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type in {"spouse", "lover"}:
            return "marriage_mediator"
        if relationship_type in {"parent_child", "sibling", "protects_family"}:
            return "family_line"
        if relationship_type in {"battle_ally", "loyal_oath", "sworn_sibling"}:
            return "oath_guardian"
        if relationship_type in {"enemy_rival", "battlefield_opponent"}:
            return "rival_observer"
        if relationship_type in {"ruler_subject", "political_contact"}:
            return "jiangdong_ruler"
        if target.femaleFocus:
            return "family_witness"
        if relationship_type in {"battlefield_contact", "mentor_student", "mentor"}:
            return "martial_direct"
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
            "marriage_mediator": "從關係、去留與分寸來說話",
            "family_sacrifice": "從代價、先後與誰先被護住來說話",
            "family_line": "從家人、承接與延續來說話",
            "jiangdong_ruler": "從形勢、名分與節奏來說話",
            "martial_direct": "從戰機、氣勢與進退來說話",
            "oath_guardian": "從義氣、補位與共同承擔來說話",
            "family_witness": "從情分、去留與安危來說話",
            "rival_observer": "從破綻、代價與勝負手來說話",
            "measured_observer": "先點出眼前一幕，再給出克制判斷",
        }
        if archetype in guidance_map:
            return guidance_map[archetype]
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type in {"sworn_sibling", "battle_ally", "loyal_oath"}:
            return "從義氣、補位與共同承擔來說話"
        if relationship_type in {"enemy_rival", "battlefield_opponent"}:
            return "從破綻、代價與勝負手來說話"
        if target.femaleFocus:
            return "從情分、去留與身邊人的安危來說話"
        anchors = "、".join(self._speaker_persona_anchor_terms(speaker_context)[:3])
        return anchors or str(target.targetId or "")

    def _scene_chorus_personalized_context(
        self,
        speaker_context: dict[str, Any],
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> dict[str, Any]:
        context = dict(speaker_context)
        focus_term, seed_term = self._scene_chorus_focus_and_seed_terms(target, main_target, beats, story_text, context)
        anchors = [str(item).strip() for item in (context.get("anchors") or []) if str(item).strip()]
        for term in [focus_term, seed_term]:
            if term and term not in anchors:
                anchors.insert(0, term)
        context["anchors"] = anchors[:8]
        if focus_term:
            context["focusHint"] = focus_term
        if seed_term:
            context["seedHint"] = seed_term
        if focus_term or seed_term:
            context["voiceHook"] = "、".join(term for term in [focus_term, seed_term] if term)
        return context

    def _scene_chorus_focus_and_seed_terms(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
    ) -> tuple[str, str]:
        focus_terms = self._scene_chorus_focus_terms(speaker_context, target)
        if not focus_terms:
            focus_terms = [str(target.role or "").strip(), str(target.targetId or "").strip()]
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        hash_basis = "|".join(
            [
                str(target.targetId or ""),
                str(target.role or ""),
                str(main_target.targetId if main_target else ""),
                self._clean_seed_text(story_text, max_chars=120),
                self._clean_seed_text((beats.sceneSeeds or {}).get("event"), max_chars=80),
                self._clean_seed_text((beats.sceneSeeds or {}).get("place"), max_chars=48),
            ]
        )
        index = int(hashlib.sha1(hash_basis.encode("utf-8")).hexdigest()[:8], 16)
        focus_term = focus_terms[index % len(focus_terms)] if focus_terms else ""
        if not focus_term:
            focus_term = str(target.role or target.targetId or "").strip()
        seed_term = ""
        if grounding_terms:
            for offset in range(len(grounding_terms)):
                candidate = str(grounding_terms[(index + offset) % len(grounding_terms)] or "").strip()
                if candidate and candidate != focus_term and not self._is_weak_chorus_grounding_term(candidate):
                    seed_term = candidate
                    break
        return focus_term, seed_term

    def _scene_chorus_grounding_terms(
        self,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> list[str]:
        values: list[str] = []
        scene_seeds = beats.sceneSeeds or {}
        place = str(scene_seeds.get("place") or "").strip()
        if place and not self._is_weak_chorus_grounding_term(place) and place not in values:
            values.append(place)
        for obj in scene_seeds.get("objects") or []:
            label = str(obj or "").strip()
            if label and not self._is_weak_chorus_grounding_term(label) and label not in values:
                values.append(label)
        for raw_text in [
            self._sanitize_chorus_event_text(scene_seeds.get("event")),
            beats.sceneText,
            beats.memoryText,
            story_text,
        ]:
            for phrase in self._scene_phrase_candidates_for_chorus(raw_text):
                if phrase and not self._is_weak_chorus_grounding_term(phrase) and phrase not in values:
                    values.append(phrase)
        if main_target:
            for alias in self._target_aliases_for_interaction(main_target):
                alias = str(alias or "").strip()
                if alias and alias not in values:
                    values.append(alias)
        return values[:10]

    def _scene_chorus_sanitized_seeds(self, beats: SceneDirectorBeats) -> dict[str, Any]:
        scene_seeds = dict(beats.sceneSeeds or {})
        scene_seeds["event"] = self._sanitize_chorus_event_text(scene_seeds.get("event"))
        return scene_seeds

    def _sanitize_chorus_event_text(self, text: Any, max_chars: int = 80) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"^(卻說|且說|只見|忽見|此時|這時|正行間|忽然|原來|再看)", "", cleaned).strip()
        cleaned = cleaned.lstrip("，。；：、")
        cleaned = re.split(r"[「『\"]", cleaned, maxsplit=1)[0].strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rstrip("，。；：、 ")
        return cleaned

    def _scene_phrase_candidates_for_chorus(self, text: Any, max_terms: int = 6) -> list[str]:
        normalized = self._sanitize_chorus_event_text(text, max_chars=160)
        values: list[str] = []
        for phrase in self._scene_phrase_candidates(normalized, max_terms=max_terms * 2):
            cleaned = str(phrase or "").strip()
            if not cleaned:
                continue
            if re.match(r"^(卻說|且說|只見|忽見|此時|這時|正行間|忽然|原來|再看)", cleaned):
                continue
            if cleaned not in values:
                values.append(cleaned)
            if len(values) >= max_terms:
                break
        return values

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
        if self._is_narrative_exposition_chorus_line(repaired, beats):
            warnings.append("scene_chorus_narrative_exposition_rejected")
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
        if self._lacks_persona_specificity_in_chorus(
            repaired,
            target=target,
            speaker_context=speaker_context,
            main_target=main_target,
            beats=beats,
            story_text=story_text,
        ):
            strengthened = self._strengthen_chorus_persona_line(
                repaired,
                max_chars=max_chars,
                target=target,
                speaker_context=speaker_context,
                main_target=main_target,
                beats=beats,
                story_text=story_text,
            )
            if strengthened:
                warnings.append("scene_chorus_persona_cue_repaired")
                return replace(
                    generation,
                    text=strengthened,
                    fallbackUsed=False,
                    qualityWarnings=warnings,
                    repairUsed=True,
                )
            warnings.append("scene_chorus_persona_thin_rejected")
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

    def _strengthen_chorus_persona_line(
        self,
        text: str,
        max_chars: int,
        target: NarrativeInteractionTarget,
        speaker_context: dict[str, Any],
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        archetype = str(speaker_context.get("archetype") or self._speaker_archetype("", target)).strip()
        stance_prefixes = {
            "oath_guardian": ["這份義氣不能只靠一句話，", "既是同袍，"],
            "martial_direct": ["這一刻先看進退，", "戰機不能慢，"],
            "jiangdong_ruler": ["形勢要先收住，", "名分與節奏都不能亂，"],
            "marriage_mediator": ["情分越重越要留分寸，", "這層關係不能只憑一時心氣，"],
            "family_line": ["家人這一線要先護住，", "承接的人不能亂，"],
            "family_witness": ["身邊人的安危要先顧到，", "情分牽到此處，"],
            "rival_observer": ["真到對陣時，", "破綻往往就在這一口氣裡，"],
            "measured_observer": ["先把眼前這幕看定，", "此刻最要緊的是，"],
        }.get(archetype, ["先把眼前這幕看定，"])
        for prefix in stance_prefixes:
            candidate = self._complete_generated_text(f"{prefix}{cleaned}", cleaned, max_chars)
            if (
                candidate
                and not self._contains_internal_symbolic_token(candidate)
                and not self._is_narrative_exposition_chorus_line(candidate, beats)
                and not self._is_generic_chorus_line(candidate, speaker_context)
                and self._line_has_scene_grounding(candidate, main_target, beats, story_text)
            ):
                return candidate
        return ""

    def _is_narrative_exposition_chorus_line(self, text: str, beats: SceneDirectorBeats) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        if re.match(r"^(卻說|且說|只見|忽見|此時|這時|正行間|忽然|原來|再看)", cleaned):
            return True
        prefix_candidates: list[str] = []
        scene_seeds = beats.sceneSeeds or {}
        for raw in [
            scene_seeds.get("event"),
            beats.sceneText,
            beats.memoryText,
        ]:
            normalized = re.sub(r"\s+", " ", str(raw or "").strip()).strip("「」『』\"'")
            if not normalized:
                continue
            head = re.split(r"[，。！？；]", normalized, maxsplit=1)[0].strip()
            if len(head) >= 6:
                prefix_candidates.append(head[:12])
        return any(prefix and cleaned.startswith(prefix) for prefix in prefix_candidates)

    def _lacks_persona_specificity_in_chorus(
        self,
        text: str,
        target: NarrativeInteractionTarget,
        speaker_context: dict[str, Any],
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        generic_patterns = [
            r"該護的人",
            r"人心先亂",
            r"情分一牽進來",
            r"先把[^，。！？]{0,12}(接住|安排穩)",
            r"免得[^，。！？]{0,10}先亂",
            r"越要把[^，。！？]{0,10}安排穩",
        ]
        if any(re.search(pattern, cleaned) for pattern in generic_patterns):
            return True
        if self._matches_banned_chorus_phrasing(cleaned):
            return True
        cues = self._scene_chorus_voice_cues(target, speaker_context, main_target, beats, story_text)
        return not any(cue and cue in cleaned for cue in cues)

    def _matches_banned_chorus_phrasing(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        banned_patterns = [
            r"重義要落在動作上",
            r"後面的銜接就不能慢",
            r"這份情義，亂軍之中也難得",
            r"不知(?:他們|去向)何方",
            r"護住家人，方為根本",
        ]
        return any(re.search(pattern, cleaned) for pattern in banned_patterns)

    def _scene_chorus_voice_cues(
        self,
        target: NarrativeInteractionTarget,
        speaker_context: dict[str, Any],
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> list[str]:
        values: list[str] = []
        for raw in self._speaker_persona_anchor_terms(speaker_context):
            cue = str(raw or "").strip()
            if cue and cue not in values and len(cue) >= 2:
                values.append(cue)
        humanized_role = self._humanize_tag_list(
            [target.role, target.relationshipType, target.gender],
            fallback="",
        )
        for raw in re.split(r"[、／/，,\s]+", humanized_role):
            cue = str(raw or "").strip()
            if cue and cue not in values and len(cue) >= 2:
                values.append(cue)
        relationship_titles = {
            "parent_child": ["父親", "母親", "父王", "家人"],
            "spouse": ["夫人", "夫妻", "家人"],
            "lover": ["夫人", "情分", "家人"],
            "sibling": ["兄長", "手足", "家人"],
            "protects_family": ["家人", "家眷", "安危"],
        }.get(str(target.relationshipType or "").strip(), [])
        for cue in relationship_titles:
            if cue and cue not in values:
                values.append(cue)
        title_like_terms = ["主公", "兄長", "二嫂", "叔父", "夫人", "家眷", "幼主", "父親", "母親"]
        cleaned_scene = " ".join(
            str(item or "").strip()
            for item in [
                story_text,
                beats.sceneText,
                beats.memoryText,
                (beats.sceneSeeds or {}).get("event"),
            ]
            if str(item or "").strip()
        )
        for cue in title_like_terms:
            if cue in cleaned_scene:
                if cue not in values:
                    values.append(cue)
        if main_target:
            for alias in self._target_aliases_for_interaction(main_target):
                cue = str(alias or "").strip()
                if cue and cue not in values and len(cue) >= 2:
                    values.append(cue)
        return values[:12]

    def _strip_speaker_self_mentions(
        self,
        text: str,
        target: NarrativeInteractionTarget,
        persona_card: PersonaCard | None,
    ) -> str:
        cleaned = str(text or "").strip()
        names = [target.label, *self._target_aliases_for_interaction(target)]
        if persona_card:
            names.append(persona_card.displayName)
        names = [name for name in dict.fromkeys(name.strip() for name in names if name and len(name.strip()) >= 2)]
        for name in names:
            cleaned = re.sub(rf"(?:我|俺|吾|某|咱)\s*{re.escape(name)}", "我", cleaned)
            surname = name[0]
            cleaned = re.sub(rf"(?:我|俺|吾|某|咱)\s*老?{re.escape(surname)}", "我", cleaned)
            cleaned = re.sub(rf"燕人\s*{re.escape(name)}", "我", cleaned)
            cleaned = re.sub(rf"{re.escape(name)}\s*在此", "我在此", cleaned)
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

    def _is_weak_chorus_grounding_term(self, term: str) -> bool:
        cleaned = self._clean_seed_text(term, max_chars=40).strip()
        if not cleaned:
            return True
        if cleaned in {"二嫂", "家眷", "車駕", "對方", "想到關"}:
            return True
        return bool(re.match(r"^(乃是|為首|內容大致|一看|想到)", cleaned))

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
        normalized_text = self._normalize_grounding_match_text(cleaned)
        for term in grounding_terms:
            raw_term = str(term or "").strip()
            if not raw_term:
                continue
            if raw_term in cleaned:
                return True
            normalized_term = self._normalize_grounding_match_text(raw_term)
            if normalized_term and normalized_term in normalized_text:
                return True
        joined_grounding = self._normalize_grounding_match_text(
            " ".join(
                [
                    *grounding_terms,
                    str((beats.sceneSeeds or {}).get("event") or ""),
                    str(beats.sceneText or ""),
                    str(beats.memoryText or ""),
                    str(story_text or ""),
                ]
            )
        )
        canonical_markers = [
            "桃園結義",
            "結義",
            "同心協力",
            "誓同生死",
            "重圍",
            "公孫瓚",
            "袁紹",
        ]
        if main_target:
            canonical_markers.extend(self._target_aliases_for_interaction(main_target))
        for marker in canonical_markers:
            normalized_marker = self._normalize_grounding_match_text(marker)
            if normalized_marker and normalized_marker in normalized_text and normalized_marker in joined_grounding:
                return True
        return False

    def _normalize_grounding_match_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", "", str(text or "").strip())
        cleaned = re.sub(r"[「」『』、，。！？；：]", "", cleaned)
        cleaned = cleaned.replace("之", "")
        return cleaned

    def _contains_internal_symbolic_token(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        return bool(
            re.search(r"\b[a-z]+(?:[_-][a-z0-9]+)+\b", cleaned)
            or re.search(r"\b(?:personaSource|relationshipType|contextKey|sceneSeeds)\b", cleaned)
        )

    def _compose_scene_grounded_chorus_fallback_v3(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any],
    ) -> str:
        anchor = self._scene_chorus_best_grounding_anchor(target, main_target, beats, story_text)
        if not anchor:
            return ""
        anchor_clause = anchor if anchor.endswith(("，", "；", "。")) else f"{anchor}，"
        main_label = self._clean_seed_text(main_target.label if main_target else "", max_chars=12).strip() or "前頭的人"
        archetype = str(speaker_context.get("archetype") or self._speaker_archetype("", target)).strip()
        variant = int(hashlib.sha1(str(target.targetId or "").encode("utf-8")).hexdigest()[:8], 16)
        choose = lambda options: options[variant % len(options)]
        if archetype == "oath_guardian":
            text = choose([
                f"{anchor_clause}這份義氣不能只讓{main_label}一人撐著，我得補上後手。",
                f"{anchor_clause}既說同生死，{main_label}那頭一緊，我這邊就不能慢。",
                f"{anchor_clause}兄弟情分不是口號，該有人替{main_label}把後路守住。",
            ])
        elif archetype == "martial_direct":
            text = choose([
                f"{anchor_clause}這一動最怕銜接慢，我先替{main_label}看住退路。",
                f"{anchor_clause}戰機已起，前頭有人頂住，後手就得立刻跟上。",
            ])
        elif archetype == "jiangdong_ruler":
            text = choose([
                f"{anchor_clause}形勢已推緊，先收住節奏，別讓人心散開。",
                f"{anchor_clause}名分和進退都要看清，不能讓一時氣勢牽著走。",
            ])
        elif archetype in {"marriage_mediator", "family_line", "family_witness", "family_sacrifice"}:
            text = choose([
                f"{anchor_clause}牽著身邊人的安危，先把{main_label}那頭接穩。",
                f"{anchor_clause}越是有人被牽連，越要先穩住{main_label}那頭與去路。",
            ])
        elif archetype == "rival_observer":
            text = f"{anchor_clause}這股氣若逼到陣前，最容易露出勝負破綻。"
        else:
            text = choose([
                f"{anchor_clause}先看定，話不必急，後手要留穩。",
                f"{anchor_clause}眼前不能只看熱鬧，誰補位、誰收勢都要分清。",
            ])
        return self._sentence_or_default(text, "", max_chars=72)

    def _scene_chorus_best_grounding_anchor(
        self,
        target: NarrativeInteractionTarget,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
    ) -> str:
        terms = [
            self._clean_seed_text(term, max_chars=18).strip("，。；：、 ")
            for term in self._scene_chorus_grounding_terms(main_target, beats, story_text)
            if not self._is_weak_chorus_grounding_term(str(term or ""))
        ]
        terms = [term for term in dict.fromkeys(terms) if term]
        relationship_type = str((main_target.relationshipType if main_target else "") or target.relationshipType or "").strip()
        if relationship_type in SCENE_CHORUS_BOND_RELATIONSHIP_TYPES:
            for term in terms:
                if any(marker in term for marker in ["桃園", "結義", "誓", "同生死", "義氣", "兄弟"]):
                    return term
        for term in terms:
            if any(marker in term for marker in ["重圍", "追趕", "橋頭", "退路", "聲勢", "幼主"]):
                return term
        return terms[0] if terms else ""

    def _scene_chorus_lead_clause(
        self,
        main_target: NarrativeInteractionTarget | None,
        beats: SceneDirectorBeats,
        story_text: str,
        speaker_context: dict[str, Any] | None = None,
    ) -> str:
        action_phrase = self._scene_chorus_action_phrase(main_target, beats, story_text)
        seed_hint = str((speaker_context or {}).get("seedHint") or "").strip()
        focus_hint = str((speaker_context or {}).get("focusHint") or "").strip()
        scene_term = self._scene_chorus_scene_term(beats, story_text)
        if action_phrase:
            if seed_hint and seed_hint not in action_phrase:
                return f"{action_phrase}，我先把{seed_hint}記住"
            return action_phrase
        place = str((beats.sceneSeeds or {}).get("place") or "").strip()
        if main_target and seed_hint:
            return f"我先看{main_target.label}這一幕落在{seed_hint}"
        if main_target and scene_term:
            return f"我先看{main_target.label}這一下"
        if main_target and place:
            return f"我先看{main_target.label}在{place}這一帶撐住局面"
        if main_target:
            return f"看{main_target.label}這一下"
        if scene_term:
            return f"我先把{scene_term}記住"
        if place:
            return f"{place}這一幕"
        if focus_hint:
            return f"我先記住{focus_hint}"
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
        seed_hint = str(speaker_context.get("seedHint") or "").strip()
        scene_term = self._scene_chorus_scene_term(beats, story_text)
        target_name = str(main_target.label if main_target else "").strip()
        scene_focus = seed_hint or scene_term or focus
        if archetype == "oath_guardian":
            return f"{focus}不是嘴上說說，我得先把{scene_focus}接住，才不算負了{target_name or '身邊的人'}。"
        if archetype == "martial_direct":
            return f"{focus}要落在動作上，{scene_focus}這一頭既然有人頂著，後面的銜接就不能慢。"
        if archetype == "jiangdong_ruler":
            return f"{focus}都得收回局勢本身；{scene_focus}這一口氣有人替眾人換出來，就要順勢把亂局壓住。"
        if archetype == "marriage_mediator":
            return f"情分一牽進來，越要把{scene_focus}和去路安排穩，免得後頭的人心先亂。"
        if archetype == "family_sacrifice":
            return f"這一下換來的是{scene_focus or '生路'}，不能白耗在遲疑裡。"
        if archetype == "family_line":
            return f"先把{scene_focus or '後手'}接住，才護得住該護的人。"
        if archetype == "family_witness":
            return f"既有人替眾人撐住{scene_focus or '這一幕'}，後頭的人就更不能散。"
        if archetype == "rival_observer":
            return f"真換到對陣時，這股{scene_focus or focus}最容易逼人露出破綻。"
        return f"先把{scene_focus or '眼前這一口氣'}接住，再談{focus}才不會落空。"

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
        objects = [
            str(item or "").strip()
            for item in scene_seeds.get("objects") or []
            if str(item or "").strip() and not self._is_weak_chorus_grounding_term(str(item or "").strip())
        ]
        place = str(scene_seeds.get("place") or "").strip()
        place_values = [place] if place and not self._is_weak_chorus_grounding_term(place) else []
        for value in objects + place_values:
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
        candidates = self._scene_phrase_candidates_for_chorus((beats.sceneSeeds or {}).get("event"))
        candidates.extend(self._scene_phrase_candidates_for_chorus(beats.sceneText))
        candidates.extend(self._scene_phrase_candidates_for_chorus(beats.memoryText, max_terms=4))
        seen: list[str] = []
        for item in candidates:
            phrase = str(item or "").strip()
            if not phrase or phrase in seen:
                continue
            seen.append(phrase)
        for phrase in seen:
            if self._is_weak_chorus_grounding_term(phrase):
                continue
            if target_aliases and any(alias and alias in phrase for alias in target_aliases):
                return phrase if not phrase.startswith("在") else phrase[1:]
        for phrase in seen:
            if len(phrase) >= 3 and not self._is_weak_chorus_grounding_term(phrase):
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
                token = str(piece or "").strip()
                if (
                    not token
                    or len(token) < 2
                    or token in values
                    or self._contains_internal_symbolic_token(token)
                    or token in {"符合三國語境", "謹慎應對"}
                ):
                    continue
                values.append(token)
        if values:
            return values[:4]
        relationship_type = str(target.relationshipType or "").strip()
        if relationship_type in {"spouse", "lover", "parent_child", "sibling", "protects_family"}:
            return ["情分", "安危"]
        if target.femaleFocus:
            return ["情分", "安危"]
        if relationship_type in {"sworn_sibling", "battle_ally", "loyal_oath"}:
            return ["義氣", "補位"]
        if relationship_type in {"enemy_rival", "battlefield_opponent"}:
            return ["破綻", "代價"]
        fallback_terms = [
            token
            for token in [
                relationship_type,
                speaker_context.get("archetype"),
                *(self._speaker_persona_anchor_terms(speaker_context)[:2]),
            ]
            if str(token or "").strip()
        ]
        return fallback_terms[:4] if fallback_terms else [str(target.targetId or "")]

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
        draft_text: str = "",
        quality_issues: list[str] | None = None,
    ) -> DialogueGenerationResult | None:
        persona_card = self.get_persona_card(target.targetId)
        grounding_terms = self._scene_chorus_grounding_terms(main_target, beats, story_text)
        draft_line = str(draft_text or "").strip()
        if (
            self._is_narrative_exposition_chorus_line(draft_line, beats)
            or self._is_generic_chorus_line(draft_line, speaker_context)
            or self._lacks_persona_specificity_in_chorus(
                draft_line,
                target=target,
                speaker_context=speaker_context,
                main_target=main_target,
                beats=beats,
                story_text=story_text,
            )
        ):
            draft_line = ""
        issue_labels = [str(item or "").strip() for item in (quality_issues or []) if str(item or "").strip()]
        prompt_player_profile = (
            f"發話者人格資料：{self._speaker_persona_summary(speaker_context)}。"
            f"發話時應著重：{self._speaker_persona_guidance(speaker_context, target)}。"
            f"本幕場景錨點：{'、'.join(grounding_terms[:6]) or '互動對象與當下動作'}。"
            f"請把這句 draft 改寫得更像此人親眼看完這一幕後的反應：{draft_line or fallback_text}。"
        )
        if issue_labels:
            prompt_player_profile += f"目前問題：{'、'.join(issue_labels)}。"
        generation = self._generate_scene_director_text(
            general_id=target.targetId,
            persona_card=persona_card,
            memory_context={
                "saveId": f"demo-chorus-rewrite-{request.generalId}",
                "shortTerm": draft_line or story_text or beats.sceneText,
                "longTerm": self._scene_seed_text(beats),
                "playerProfile": prompt_player_profile,
                "promises": (
                    "請只回一句自然短對白；語氣要像這個人真的看完本幕後脫口而出。"
                    "必須直接扣住互動對象或當下動作，不要解釋資料，不要照抄人格標籤。"
                    "不要用『卻說』『且說』『只見』『此時』『正行間』『忽然』『原來』這種說書旁白起手。"
                    "不要直接照抄 scene seed 的句首；請改成帶有說話者立場的反應。"
                ),
            },
            selected_context={
                "task": "chorus-line-rewrite",
                "mainActor": {"generalId": profile.generalId, "displayName": profile.displayName},
                "speaker": {
                    "generalId": target.targetId,
                    "displayName": target.label,
                    "relationshipToMain": target.role,
                    "focusHint": speaker_focus_term,
                    "seedHint": speaker_seed_term,
                },
                "activeTarget": {
                    "targetId": main_target.targetId if main_target else None,
                    "label": main_target.label if main_target else None,
                    "role": main_target.role if main_target else None,
                },
                "sceneSeeds": self._scene_chorus_sanitized_seeds(beats),
                "sceneScript": story_text,
                "fallbackDraft": draft_line or "",
                "draftLine": draft_line,
                "qualityIssues": issue_labels,
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
            provider_order=["gemini_flash", "gemini_flash_lite", "gemini"],
            model_overrides={"__timeoutMs": "4500", "__retryCount": "1"},
            allow_deterministic_fallback=False,
        )
        cleaned = self._strip_speaker_self_mentions(generation.text, target, persona_card)
        repaired = self._complete_generated_text(cleaned, fallback_text, request.maxChorusChars)
        if (
            not repaired
            or self._contains_internal_symbolic_token(repaired)
            or self._is_narrative_exposition_chorus_line(repaired, beats)
            or self._is_generic_chorus_line(repaired, speaker_context)
            or self._lacks_persona_specificity_in_chorus(
                repaired,
                target=target,
                speaker_context=speaker_context,
                main_target=main_target,
                beats=beats,
                story_text=story_text,
            )
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
        seen_text: set[str] = set()
        for _, source in self._iter_runtime_profile_sources(runtime_persona):
            source_refs = self._runtime_source_refs(source)
            if not source_refs.intersection(refs):
                continue
            for value in [source.get("summary"), source.get("sourceQuote"), source.get("quote"), source.get("example")]:
                text = str(value or "").strip()
                if text and text not in seen_text:
                    matched.append(text)
                    seen_text.add(text)
        return matched

    def _runtime_source_refs(self, source: dict[str, Any]) -> set[str]:
        refs = {str(ref).strip() for ref in (source.get("sourceRefs") or []) if str(ref).strip()}
        source_ref = str(source.get("sourceRef") or "").strip()
        if source_ref:
            refs.add(source_ref)
        return refs

    def _iter_runtime_profile_sources(
        self,
        runtime_persona: dict[str, Any] | None,
        *,
        story_limit: int | None = None,
        highlight_limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        if not runtime_persona:
            return []
        sources: list[tuple[str, dict[str, Any]]] = []
        seen_story_refs: set[str] = set()
        story_beats = list(runtime_persona.get("storyBeats") or [])
        if story_limit is not None:
            story_beats = story_beats[:story_limit]
        for beat in story_beats:
            refs = self._runtime_source_refs(beat)
            seen_story_refs.update(refs)
            sources.append(("storyBeat", beat))
        highlights = list(runtime_persona.get("sourceHighlights") or [])
        if highlight_limit is not None:
            highlights = highlights[:highlight_limit]
        for highlight in highlights:
            refs = self._runtime_source_refs(highlight)
            if refs and refs.intersection(seen_story_refs):
                continue
            sources.append(("sourceHighlight", highlight))
        return sources

    def _runtime_target_link_sources(self, source: dict[str, Any], target_id: str) -> set[str]:
        cleaned_target_id = str(target_id or "").strip()
        sources: set[str] = set()
        for trace in source.get("targetLinkTrace") or []:
            if str(trace.get("targetId") or "").strip() != cleaned_target_id:
                continue
            sources.update(str(item).strip() for item in (trace.get("sources") or []) if str(item).strip())
        return sources

    def _runtime_target_projection(self, source: dict[str, Any], target_id: str) -> dict[str, Any] | None:
        cleaned_target_id = str(target_id or "").strip()
        first_match: dict[str, Any] | None = None
        for projection in source.get("targetProjections") or []:
            if not isinstance(projection, dict):
                continue
            if str(projection.get("targetId") or "").strip() == cleaned_target_id:
                if first_match is None:
                    first_match = projection
                if bool(projection.get("sceneEligible")):
                    return projection
        return first_match

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
        return target_id in YELLOW_TURBAN_TARGET_IDS

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
        projection_gate_active = bool((runtime_persona.get("targetLinking") or {}).get("focusProjectionVersion"))

        def ensure_bucket(target_id: str) -> dict[str, Any]:
            bucket = buckets.get(target_id)
            if bucket is not None:
                return bucket
            gender = self._roster_gender_for(target_id, roster_index)
            bucket = {
                "targetId": target_id,
                "label": self._prefer_human_target_label(target_id, self._roster_name_for(target_id, roster_index), target_id),
                "role": "人物線索",
                "gender": gender,
                "sourceType": "keyword-cooccurrence",
                "relationshipType": None,
                "relationshipPriority": 0,
                "confidence": 0.58,
                "score": 0.0,
                "evidenceRefs": [],
                "femaleFocus": self._is_female_gender(gender),
                "sceneEligible": True,
                "linkAuthority": None,
                "sourceDataStatus": None,
                "upstreamFeedbackRequired": False,
            }
            buckets[target_id] = bucket
            return bucket

        def apply_projection_metadata(bucket: dict[str, Any], projection: dict[str, Any]) -> None:
            projection_scene_eligible = bool(projection.get("sceneEligible"))
            bucket_is_canonical = str(bucket.get("sourceType") or "") == "relationship-edge"
            bucket["sceneEligible"] = bool(bucket.get("sceneEligible")) or projection_scene_eligible
            if not bucket_is_canonical and (projection_scene_eligible or not bucket.get("linkAuthority")):
                bucket["linkAuthority"] = projection.get("linkAuthority") or bucket.get("linkAuthority")
            if not bucket_is_canonical and (projection_scene_eligible or not bucket.get("sourceDataStatus")):
                bucket["sourceDataStatus"] = projection.get("sourceDataStatus") or bucket.get("sourceDataStatus")
            bucket["upstreamFeedbackRequired"] = bool(bucket.get("upstreamFeedbackRequired")) or bool(projection.get("upstreamFeedback"))

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
            bucket["label"] = self._prefer_human_target_label(
                target_id,
                edge.get("targetName"),
                self._roster_name_for(target_id, roster_index),
                bucket["label"],
                target_id,
            )
            if edge_priority >= int(bucket.get("relationshipPriority") or 0):
                bucket["role"] = edge_label
                bucket["relationshipType"] = edge_type
                bucket["sourceType"] = "relationship-edge"
                bucket["relationshipPriority"] = edge_priority
                bucket["sceneEligible"] = True
                bucket["linkAuthority"] = "relationship-edge"
                bucket["sourceDataStatus"] = "ready"
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

        for source_type, source in self._iter_runtime_profile_sources(runtime_persona):
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
                trace_sources = self._runtime_target_link_sources(source, target_key)
                projection = self._runtime_target_projection(source, target_key)
                projection_scene_eligible = bool(projection and projection.get("sceneEligible"))
                if projection is not None and not projection_scene_eligible:
                    continue
                if source_type == "sourceHighlight" and trace_sources and trace_sources <= {"aliasMatch"} and target_key not in buckets and not projection_scene_eligible:
                    continue
                if projection_gate_active and target_key not in buckets and not projection_scene_eligible:
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
                if projection is not None:
                    apply_projection_metadata(bucket, projection)
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
                if projection_gate_active and target_key not in buckets:
                    continue
                if target_key not in buckets and not (allow_family_titles and has_emotion_angle):
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

        selected_items = sorted(buckets.values(), key=sort_key)
        primary_items = selected_items[:12]
        primary_target_ids = {item["targetId"] for item in primary_items}
        overflow_projection_items = [
            item
            for item in selected_items[12:]
            if str(item.get("sourceType") or "") == "pipeline-angle-target-link"
            and bool(item.get("sceneEligible"))
            and item["targetId"] not in primary_target_ids
        ]

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
                sceneEligible=bool(item.get("sceneEligible", True)),
                linkAuthority=item.get("linkAuthority"),
                sourceDataStatus=item.get("sourceDataStatus"),
                upstreamFeedbackRequired=bool(item.get("upstreamFeedbackRequired")),
            )
            for item in [*primary_items, *overflow_projection_items]
        ]
        return targets

    def _iter_runtime_target_mention_sources(self, runtime_persona: dict[str, Any]) -> list[tuple[str, list[str], bool]]:
        sources: list[tuple[str, list[str], bool]] = []
        for source_type, source in self._iter_runtime_profile_sources(runtime_persona, story_limit=18, highlight_limit=24):
            refs = sorted(self._runtime_source_refs(source))
            families = {str(family).strip() for family in (source.get("angleFamilies") or []) if str(family).strip()}
            if source_type == "storyBeat":
                text_values = [source.get("summary"), source.get("sourceQuote"), source.get("location")]
                has_emotion_angle = False
            else:
                text_values = [source.get("example"), source.get("summary"), source.get("sourceRef")]
                has_emotion_angle = "female_interaction" in families
            text = " ".join(str(value) for value in text_values if value)
            if text.strip():
                sources.append((text, refs, has_emotion_angle))
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

        def scene_eligible_related_ids(source: dict[str, Any], raw_ids: list[Any] | None, source_text: str = "") -> list[str]:
            projections = [item for item in (source.get("targetProjections") or []) if isinstance(item, dict)]
            if projections:
                related_ids: list[str] = []
                for projection in projections:
                    if not projection.get("sceneEligible"):
                        continue
                    target_id = self._normalize_runtime_target_id(projection.get("targetId"), None, source_text)
                    if target_id in target_labels and target_id not in related_ids:
                        related_ids.append(target_id)
                return related_ids
            return normalize_related_ids(raw_ids, source_text)

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

        story_refs_seen: set[str] = set()
        for beat in (runtime_persona.get("storyBeats") or [])[:14]:
            refs = [str(ref) for ref in (beat.get("sourceRefs") or []) if str(ref).strip()]
            story_refs_seen.update(refs)
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
            related_target_ids = scene_eligible_related_ids(beat, beat.get("relatedGeneralIds"), beat_text)
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
            source_ref = str(highlight.get("sourceRef") or "").strip()
            if source_ref and source_ref in story_refs_seen:
                continue
            evidence_id = f"highlight:{highlight.get('sourceRef') or len(cards)}"
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            families = list(highlight.get("angleFamilies") or [])
            example = str(highlight.get("example") or "").strip()
            related_target_ids = scene_eligible_related_ids(highlight, highlight.get("relatedGeneralIds"), example)
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
            source_refs = [str(ref) for ref in (edge.get("evidenceRefs") or []) if str(ref).strip()]
            if not quote and not source_refs:
                continue
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
                    summary=quote or "",
                    quote=quote,
                    location=None,
                    chapterNo=None,
                    sourceType="runtime-relationship-edge",
                    sourceRefs=source_refs,
                    relatedTargetIds=[target_id],
                    confidence=self._coerce_float(edge.get("edgeConfidence"), default=0.7),
                    sceneEligible=True,
                    linkAuthority="relationship-edge",
                    sourceDataStatus="ready",
                )
            )

        def pair_sort_key(candidate: NarrativeEvidenceCard) -> tuple[int, float, int, int, str]:
            chapter_no = candidate.chapterNo if isinstance(candidate.chapterNo, int) else 10**9
            return (
                0 if candidate.sourceType == "runtime-relationship-edge" else 1,
                -self._coerce_float(candidate.confidence, default=0.0),
                -len(candidate.sourceRefs or []),
                chapter_no,
                str(candidate.evidenceId or ""),
            )

        canonical_cards: list[NarrativeEvidenceCard] = []
        pair_groups: dict[tuple[str, str], list[NarrativeEvidenceCard]] = {}
        for candidate in cards:
            related_target_ids = [
                target_id
                for target_id in dict.fromkeys(
                    str(target_id).strip()
                    for target_id in (candidate.relatedTargetIds or [])
                    if str(target_id).strip()
                )
                if target_id in target_labels
            ]
            if not related_target_ids:
                canonical_cards.append(candidate)
                continue
            for target_id in related_target_ids:
                pair_groups.setdefault((candidate.angle, target_id), []).append(candidate)

        for (angle, target_id), pair_candidates in sorted(pair_groups.items(), key=lambda item: (item[0][0], item[0][1])):
            best_candidate = sorted(pair_candidates, key=pair_sort_key)[0]
            source_refs = sorted({ref for candidate in pair_candidates for ref in candidate.sourceRefs if ref})
            canonical_cards.append(
                NarrativeEvidenceCard(
                    evidenceId=f"{best_candidate.evidenceId}@@{target_id}",
                    contextKey=best_candidate.contextKey,
                    angle=angle,
                    title=best_candidate.title,
                    summary=best_candidate.summary,
                    quote=best_candidate.quote,
                    location=best_candidate.location,
                    chapterNo=best_candidate.chapterNo,
                    sourceType=best_candidate.sourceType,
                    sourceRefs=source_refs,
                    relatedTargetIds=[target_id],
                    confidence=best_candidate.confidence,
                    sceneEligible=best_candidate.sceneEligible,
                    linkAuthority=best_candidate.linkAuthority,
                    sourceDataStatus=best_candidate.sourceDataStatus,
                )
            )

        return canonical_cards

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

    def _prefer_human_target_label(self, target_id: str, *candidates: Any) -> str:
        normalized_target_id = str(target_id or "").strip()
        normalized_candidates = [str(candidate or "").strip() for candidate in candidates if str(candidate or "").strip()]
        for candidate in normalized_candidates:
            if candidate == normalized_target_id:
                continue
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", candidate):
                return candidate
        for candidate in normalized_candidates:
            if candidate:
                return candidate
        return normalized_target_id

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
        # llm_script_v2 currently returns a full JSON pack from the provider but stores
        # only storyText in the response. Do not cache that story-only text as a
        # structured pack; it would be a cache pollution source on later requests.
        if request.renderMode == SCENE_RENDER_MODE_LLM_SCRIPT_V2:
            return
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
        if generation.fallbackUsed:
            return
        if any(str(code or "").endswith("_rejected") for code in generation.qualityWarnings):
            return
        text = str(generation.text or "").strip()
        if not text:
            return
        entry = {
            "createdAt": datetime.now(UTC).isoformat(),
            "generalId": target.targetId,
            "task": "chorus-line",
            "cacheSchemaVersion": 2,
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
            "fallbackUsed": generation.fallbackUsed,
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
