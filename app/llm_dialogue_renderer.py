from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_FLASH_LITE_MODEL = "gemini-2.5-flash-lite"
DEFAULT_TIMEOUT_MS = 2500
DEFAULT_GEMINI_THINKING_BUDGET = 128
DEFAULT_GEMINI_FLASH_LITE_THINKING_BUDGET = 512
DEFAULT_GEMINI_MAX_OUTPUT_TOKENS = 512
DEFAULT_GEMINI_RETRY_COUNT = 2
DEFAULT_GEMINI_REPAIR_RETRY_COUNT = 1
DEFAULT_GEMINI_REPAIR_TIMEOUT_MS = 2500
DEFAULT_LOCAL_LLAMA_MODEL = "qwen2.5:7b"
DEFAULT_DEEPSEEK_REASONER_MODEL = "deepseek-r1:7b"
DEFAULT_LOCAL_LLAMA_TIMEOUT_MS = 6000
DEFAULT_LOCAL_LLAMA_API_BASE = "http://127.0.0.1:11434"
DEFAULT_LOCAL_LLAMA_API_PATH = "/api/chat"
DEFAULT_LOCAL_LLAMA_TEMPERATURE = 0.45
DEFAULT_LOCAL_LLAMA_TOP_P = 0.85
DEFAULT_LOCAL_LLAMA_REPEAT_PENALTY = 1.12
DEFAULT_LOCAL_LLAMA_NUM_CTX = 4096
DEFAULT_LOCAL_LLAMA_REPAIR_RETRY_COUNT = 1
DEFAULT_HISTORY_CACHE_PATH = "local/npc-dialogue-history.jsonl"
DEFAULT_LOCALE = "zh-TW"
DEFAULT_SPEECH_CONTEXT_MODE = "life_chat"
_DEBUG_TRUE_VALUES = {"1", "true", "yes", "on"}
LOGGER = logging.getLogger("uvicorn.error")


LOCALE_INSTRUCTIONS = {
    "zh-TW": {
        "label": "Traditional Chinese",
        "instruction": "Write the dialogue in Traditional Chinese using Taiwan-style punctuation and wording.",
    },
    "en": {
        "label": "English",
        "instruction": "Write the dialogue in natural English, while keeping historical names recognizable.",
    },
    "ja": {
        "label": "Japanese",
        "instruction": "Write the dialogue in natural Japanese, while keeping historical names recognizable.",
    },
}


SPEECH_CONTEXT_INSTRUCTIONS = {
    "life_chat": {
        "label": "生活聊天",
        "instruction": "The NPC is casually chatting with the player because the selected keyword came up as a topic in everyday conversation. Speak to the player, not to the keyword target.",
        "keywordAngle": "The selected keyword is a conversation topic mentioned by the player or recalled during casual talk.",
        "must": ["address the player or speak generally", "sound relaxed and personal", "treat the keyword as the subject of conversation"],
        "avoid": ["battle challenge phrasing", "formal council language", "speaking directly to the keyword target as if they are present"],
    },
    "encounter_speech": {
        "label": "遭遇發言",
        "instruction": "The NPC is directly facing, challenging, warning, greeting, or addressing the selected keyword target in the current scene.",
        "keywordAngle": "The selected keyword is the addressee or immediate target standing before the NPC.",
        "must": ["speak outward toward the keyword target", "make the line immediate and scene-facing", "allow direct second-person challenge or warning when appropriate"],
        "avoid": ["private reflection", "detached historical explanation", "council report wording"],
    },
    "inner_monologue": {
        "label": "想法獨白",
        "instruction": "The NPC is not speaking to anyone directly. Render an inner thought, memory, judgment, doubt, or association triggered by the selected keyword.",
        "keywordAngle": "The selected keyword is an internal association in the NPC's mind, not a listener.",
        "must": ["make it inward-facing", "avoid directly addressing the player or keyword target", "show the NPC's private feeling or judgment"],
        "avoid": ["calling out to the target", "public meeting phrasing", "instructional explanation"],
    },
    "meeting_statement": {
        "label": "會議發言",
        "instruction": "The NPC is formally presenting an opinion in a council or military meeting to allies, officers, or the lord. The selected keyword is the agenda item.",
        "keywordAngle": "The selected keyword is the meeting topic or agenda subject being discussed in front of others.",
        "must": ["sound public and deliberate", "speak to the group rather than one private listener", "state an opinion or recommendation about the keyword"],
        "avoid": ["casual banter", "private inner thought", "single-target battlefield taunt"],
    },
}


GENERAL_IDENTITY_GUARDS = {
    "zhang-fei": {
        "allowedSelfNames": ["張飛", "張翼德", "翼德", "俺"],
        "forbiddenSelfNamePatterns": [
            r"(^|[，。！？；：\s「『])亮(?=以為|以|觀|請|曰|言|豈|敢|認)",
            r"(^|[，。！？；：\s「『])孔明(?=以為|以|觀|請|曰|言|認)",
            r"(^|[，。！？；：\s「『])雲(?=以為|以|觀|請|曰|言|認)",
            r"(^|[，。！？；：\s「『])子龍(?=以為|以|觀|請|曰|言|認)",
            r"(^|[，。！？；：\s「『])關某(?=以為|以|觀|請|曰|言|認)",
            r"(^|[，。！？；：\s「『])孟德(?=以為|以|觀|請|曰|言|認)",
        ],
    },
}

ZH_TW_SIMPLIFIED_MARKERS = set("这为国马见关刘备张飞赵云诸葛说与对会战将军汉长东风无发过气众门当问处后")
ALLOWED_ASCII_WORDS = {"json", "id", "npc", "api"}


def _is_debug_enabled() -> bool:
    return str(os.environ.get("NPC_LLM_DEBUG") or "").strip().lower() in _DEBUG_TRUE_VALUES


def _preview_text(text: str | None, limit: int = 220) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}..."


def log_debug_event(event: str, **payload) -> None:
    if not _is_debug_enabled():
        return
    LOGGER.info("[npc-brain-debug] %s %s", event, json.dumps(payload, ensure_ascii=False, default=str))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _DEBUG_TRUE_VALUES


def _open_url(request: urllib.request.Request, timeout_seconds: float, *, disable_proxy: bool) -> object:
    if disable_proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout_seconds)
    return urllib.request.urlopen(request, timeout=timeout_seconds)


@dataclass(frozen=True)
class ResolvedEvidence:
    evidenceRef: str
    sourceType: str = "romance"
    sourceQuote: str | None = None
    factSummary: str | None = None
    generalIds: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(frozen=True)
class DialoguePromptPackage:
    generalId: str
    personaCardSubset: dict
    memoryContext: dict | None
    selectedContext: dict | None
    selectedKeywords: list[dict]
    resolvedEvidence: list[ResolvedEvidence]
    evidenceRefs: list[str]
    deterministicText: str
    maxChars: int
    toneMode: str
    locale: str = DEFAULT_LOCALE
    speechContextMode: str = DEFAULT_SPEECH_CONTEXT_MODE


@dataclass(frozen=True)
class DialogueGenerationResult:
    text: str
    provider: str
    model: str | None
    generationMode: str
    fallbackUsed: bool
    providerTrace: list[str] = field(default_factory=list)
    usedEvidenceRefs: list[str] = field(default_factory=list)
    qualityWarnings: list[str] = field(default_factory=list)
    repairUsed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class DialogueProvider(Protocol):
    name: str

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        ...


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderOutputError(RuntimeError):
    pass


class DeterministicTemplateProvider:
    name = "deterministic"

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        resolved_refs = [evidence.evidenceRef for evidence in package.resolvedEvidence]
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        if selected_task == "scene-script-pack-v2":
            # [已過時] Deterministic structured scene output is an emergency
            # fallback only. The normal scene-director path is live llm_script_v2.
            text = self._generate_scene_script_pack_v2_text(package)
            return DialogueGenerationResult(
                text=text,
                provider=self.name,
                model=None,
                generationMode="deterministic-template-v1+scene-script-pack-v2",
                fallbackUsed=True,
                usedEvidenceRefs=resolved_refs,
                qualityWarnings=["scene_script_pack_v2_deterministic_fallback"],
            )
        if selected_task == "scene-chorus-batch-v2":
            # [已過時] Deterministic chorus output is only for provider outages.
            text = self._generate_scene_chorus_batch_v2_text(package)
            return DialogueGenerationResult(
                text=text,
                provider=self.name,
                model=None,
                generationMode="deterministic-template-v1+scene-chorus-batch-v2",
                fallbackUsed=True,
                usedEvidenceRefs=resolved_refs,
                qualityWarnings=["scene_chorus_batch_v2_deterministic_fallback"],
            )
        return DialogueGenerationResult(
            text=package.deterministicText[: package.maxChars],
            provider=self.name,
            model=None,
            generationMode="deterministic-template-v1+persona-card" if package.personaCardSubset else "deterministic-template-v1",
            fallbackUsed=not bool(resolved_refs),
            usedEvidenceRefs=resolved_refs,
        )

    def _first_text(self, *values: object) -> str:
        for value in values:
            text = " ".join(str(value or "").split()).strip()
            if text:
                return text
        return ""

    def _scene_seed_text(self, selected_context: dict | None) -> str:
        scene_seeds = (selected_context or {}).get("sceneSeeds") if isinstance(selected_context, dict) else {}
        if not isinstance(scene_seeds, dict):
            scene_seeds = {}
        people: list[str] = []
        for item in scene_seeds.get("people") or []:
            if not isinstance(item, dict):
                continue
            label = self._first_text(item.get("label"), item.get("id"))
            if label:
                people.append(label)
        objects = [self._first_text(item) for item in (scene_seeds.get("objects") or [])]
        pieces = [
            self._first_text(scene_seeds.get("event")),
            self._first_text(scene_seeds.get("time")),
            self._first_text(scene_seeds.get("place")),
            self._first_text(scene_seeds.get("emotion")),
            "、".join(people),
            "、".join(item for item in objects if item),
        ]
        return "；".join(piece for piece in pieces if piece)

    def _fit_sentence(self, text: str, minimum: int = 16, maximum: int = 110) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return ""
        if len(cleaned) > maximum:
            cleaned = cleaned[:maximum]
        if len(cleaned) < minimum:
            return cleaned
        for punctuation in "。！？!?；;":
            index = cleaned.rfind(punctuation)
            if minimum <= index + 1 <= maximum:
                return cleaned[: index + 1]
        return cleaned

    def _structured_persona_anchor_text(self, package: DialoguePromptPackage) -> str:
        voice_style = [str(item).strip() for item in (package.personaCardSubset.get("voiceStyle") or []) if str(item).strip()]
        traits = [str(item).strip() for item in (package.personaCardSubset.get("personalityTraits") or []) if str(item).strip()]
        return "、".join([*voice_style[:2], *traits[:2]])

    def _generate_scene_script_pack_v2_text(self, package: DialoguePromptPackage) -> str:
        selected_context = package.selectedContext or {}
        persona_name = self._first_text(package.personaCardSubset.get("displayName"), package.personaCardSubset.get("name"), package.generalId)
        target = selected_context.get("activeTarget") if isinstance(selected_context, dict) else {}
        target_name = self._first_text(target.get("label") if isinstance(target, dict) else None, target.get("targetId") if isinstance(target, dict) else None, "對方")
        short_term = self._first_text((package.memoryContext or {}).get("shortTerm"), (package.memoryContext or {}).get("playerProfile"))
        long_term = self._first_text((package.memoryContext or {}).get("longTerm"), (package.memoryContext or {}).get("directorInstructions"))
        seed_text = self._scene_seed_text(selected_context)
        seed_focus = self._first_text(seed_text, short_term, long_term)
        memory_text = self._fit_sentence(f"{persona_name}想起{target_name}與這一幕的牽連，{seed_focus}", minimum=18, maximum=54)
        emotion_text = self._fit_sentence(f"他心裡先沉下來，再把牽掛與責任一併收住。", minimum=14, maximum=44)
        dialogue_text = self._fit_sentence(f"{target_name}，這一路我不會讓你再獨自承擔。", minimum=14, maximum=40)
        intent_text = self._fit_sentence(f"接下來他要把眼前這件事安排穩，再往前推進。", minimum=14, maximum=44)
        story_text = self._fit_sentence(
            f"{persona_name}站在{self._first_text(selected_context.get('sceneFacts', {}).get('place'), selected_context.get('sceneSeeds', {}).get('place'), '眼前的場面')}，"
            f"看著{target_name}與眾人，想起{self._first_text(seed_text, short_term)}，"
            f"心裡的牽掛與責任慢慢攏在一起。他先壓住急切，分辨誰在身旁、事從何起、此刻天時地利又落在哪裡；"
            f"再把那件牽動眾人的物事收進眼底，讓悲喜不外露，只化成一句能安人心的承諾。"
            f"於是他向{target_name}點頭，決定先把眼前這樁事安穩下來，再帶眾人往下一步走。",
            minimum=180,
            maximum=240,
        )
        payload = {
            "memoryText": memory_text or persona_name,
            "emotionText": emotion_text or "心裡先穩住，再慢慢想下一步。",
            "dialogueText": dialogue_text or f"{target_name}，先把眼前這件事守住。",
            "intentText": intent_text or "接下來先把局面收穩，再往前走。",
            "storyText": story_text or f"{persona_name}與{target_name}對望，先把局面穩住。",
            "usedSceneSeeds": [
                key
                for key, value in {
                    "people": self._first_text(seed_text),
                    "event": self._first_text((package.selectedContext or {}).get("sceneSeeds", {}).get("event") if isinstance(package.selectedContext, dict) else ""),
                    "time": self._first_text((package.selectedContext or {}).get("sceneSeeds", {}).get("time") if isinstance(package.selectedContext, dict) else ""),
                    "place": self._first_text((package.selectedContext or {}).get("sceneSeeds", {}).get("place") if isinstance(package.selectedContext, dict) else ""),
                    "objects": self._first_text((package.selectedContext or {}).get("sceneSeeds", {}).get("objects") if isinstance(package.selectedContext, dict) else ""),
                    "emotion": self._first_text((package.selectedContext or {}).get("sceneSeeds", {}).get("emotion") if isinstance(package.selectedContext, dict) else ""),
                }.items()
                if value
            ],
            "usedPersonaAnchors": [anchor for anchor in [persona_name, target_name, self._structured_persona_anchor_text(package)] if anchor],
            "usedBeatFields": [field for field, value in {"memory": memory_text, "emotion": emotion_text, "dialogue": dialogue_text, "intent": intent_text}.items() if value],
            "violations": [],
        }
        payload["usedSeedKeys"] = list(payload["usedSceneSeeds"])
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _speaker_seed_focus(self, speaker: dict, selected_context: dict | None) -> str:
        scene_grounding = selected_context.get("sceneGrounding") if isinstance(selected_context, dict) else []
        if isinstance(scene_grounding, list):
            for item in scene_grounding:
                text = self._first_text(item)
                if text:
                    return text
        focus_hint = self._first_text(speaker.get("focusHint"), speaker.get("seedHint"), speaker.get("voiceHook"))
        if focus_hint:
            return focus_hint
        seed_text = self._scene_seed_text(selected_context)
        return seed_text or "這一幕"

    def _generate_scene_chorus_batch_v2_text(self, package: DialoguePromptPackage) -> str:
        selected_context = package.selectedContext or {}
        speakers = selected_context.get("speakers") if isinstance(selected_context, dict) else []
        if not isinstance(speakers, list):
            speakers = []
        scene_script = self._first_text(selected_context.get("sceneScript"), (package.memoryContext or {}).get("shortTerm"))
        scene_seed_text = self._scene_seed_text(selected_context)
        chorus_lines: list[dict[str, object]] = []
        templates = [
            "先把局面看清，再決定要不要出手。",
            "這件事不能只看表面，得先把牽連理順。",
            "若讓這股氣先亂，後面就更難收拾。",
            "話要說重一點，但動作得先穩住。",
        ]
        for index, speaker in enumerate(speakers[:4]):
            if not isinstance(speaker, dict):
                continue
            target_id = self._first_text(speaker.get("targetId"), f"speaker-{index + 1}")
            label = self._first_text(speaker.get("label"), target_id)
            role = self._first_text(speaker.get("role"), "路人")
            seed_focus = self._speaker_seed_focus(speaker, selected_context)
            guidance = self._first_text(speaker.get("guidance"))
            voice_hook = self._first_text(speaker.get("voiceHook"), speaker.get("voiceTag"))
            traits = [self._first_text(item) for item in (speaker.get("personalityTraits") or []) if self._first_text(item)]
            voice_style = [self._first_text(item) for item in (speaker.get("voiceStyle") or []) if self._first_text(item)]
            template = templates[index % len(templates)]
            if index == 0 and guidance:
                template = f"{seed_focus}要先穩住，別急著下定論。"
            elif index == 1 and voice_hook:
                template = f"{voice_hook}裡藏著提醒，得先把{self._fit_sentence(seed_focus, 2, 18)}看明白。"
            elif index == 2 and traits:
                template = f"{traits[0]}的人看這幕，最怕{self._fit_sentence(seed_focus, 2, 18)}先散掉。"
            elif index == 3 and voice_style:
                template = f"{voice_style[0]}一出手，最好先替{self._fit_sentence(seed_focus, 2, 18)}留條路。"
            line_text = self._fit_sentence(f"{template}", minimum=12, maximum=72)
            if not line_text:
                line_text = templates[index % len(templates)]
            chorus_lines.append(
                {
                    "targetId": target_id,
                    "label": label,
                    "role": role,
                    "text": line_text,
                    "usedSeedKeys": [key for key, value in {
                        "sceneScript": scene_script,
                        "sceneSeed": seed_focus,
                        "voiceHook": voice_hook,
                        "guidance": guidance,
                    }.items() if value],
                    "usedPersonaAnchors": [anchor for anchor in [voice_hook, *traits[:2], *voice_style[:2]] if anchor],
                    "voiceTag": self._first_text(voice_hook, *traits, *voice_style, role),
                    "violations": [],
                }
            )
        if not chorus_lines:
            chorus_lines = [
                {
                    "targetId": f"speaker-{index + 1}",
                    "label": f"speaker-{index + 1}",
                    "role": "路人",
                    "text": template,
                    "usedSeedKeys": [key for key, value in {"sceneScript": scene_script, "sceneSeed": scene_seed_text}.items() if value],
                    "usedPersonaAnchors": [],
                    "voiceTag": "",
                    "violations": [],
                }
                for index, template in enumerate(templates[:4])
            ]
        payload = {"chorusLines": chorus_lines[:4]}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class MockDialogueProvider:
    name = "mock"

    def __init__(self, text: str | None = None) -> None:
        self.text = text or os.environ.get("NPC_LLM_MOCK_TEXT") or "曹操兵再多又如何？俺張飛守在橋上，先護住主公，誰敢近前！"

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        if not package.resolvedEvidence:
            raise ProviderUnavailableError("mock:no-resolved-evidence")
        return DialogueGenerationResult(
            text=self.text[: package.maxChars],
            provider=self.name,
            model="mock-dialogue-v1",
            generationMode="mock-llm-v1+persona-card",
            fallbackUsed=False,
            usedEvidenceRefs=[package.resolvedEvidence[0].evidenceRef],
        )


class GeminiDialogueProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
        retry_count: int | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("NPC_LLM_MODEL_GEMINI") or os.environ.get("NPC_LLM_MODEL") or DEFAULT_GEMINI_MODEL
        self.timeout_ms = timeout_ms or int(os.environ.get("NPC_LLM_TIMEOUT_MS") or DEFAULT_TIMEOUT_MS)
        self.endpoint_base = os.environ.get("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com/v1beta"
        self.thinking_budget = int(os.environ.get("NPC_LLM_GEMINI_THINKING_BUDGET") or DEFAULT_GEMINI_THINKING_BUDGET)
        self.max_output_tokens = int(os.environ.get("NPC_LLM_GEMINI_MAX_OUTPUT_TOKENS") or DEFAULT_GEMINI_MAX_OUTPUT_TOKENS)
        self.retry_count = max(1, retry_count or int(os.environ.get("NPC_LLM_GEMINI_RETRY_COUNT") or DEFAULT_GEMINI_RETRY_COUNT))
        self.repair_retry_count = max(0, int(os.environ.get("NPC_LLM_GEMINI_REPAIR_RETRY_COUNT") or DEFAULT_GEMINI_REPAIR_RETRY_COUNT))
        self.repair_timeout_ms = max(
            500,
            min(
                self.timeout_ms,
                int(os.environ.get("NPC_LLM_GEMINI_REPAIR_TIMEOUT_MS") or DEFAULT_GEMINI_REPAIR_TIMEOUT_MS),
            ),
        )
        self.disable_proxy = _env_flag("NPC_LLM_DISABLE_PROXY", default=True)

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        if not self.api_key:
            raise ProviderUnavailableError("gemini:no-api-key")
        tone_mode = str(package.toneMode or "").strip().lower()
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        allow_memory_only = tone_mode == "narrative_fusion" or selected_task in {
            "chorus-line",
            "chorus-line-rewrite",
            "scene-script-pack-v2",
            "scene-chorus-batch-v2",
        }
        if not package.resolvedEvidence and not allow_memory_only:
            raise ProviderUnavailableError("gemini:no-resolved-evidence")

        prompt = self._build_prompt(package)
        request_body = self._build_gemini_request_body(package, prompt)
        payload = self._request_gemini_payload(request_body, package, prompt, repair=False)
        text = self._extract_text(payload)
        original_warnings: list[str] = []
        repair_used = False
        try:
            parsed, dialogue_text, used_keyword_keys, used_refs = self._parse_and_validate_gemini_response(text, package)
        except ProviderOutputError as exc:
            original_warnings = [str(exc)]
            if self.repair_retry_count <= 0:
                raise
            repair_used = True
            repair_prompt = self._build_gemini_repair_prompt(package, text, original_warnings)
            repair_body = self._build_gemini_request_body(package, repair_prompt, temperature=0.2)
            repair_payload = self._request_gemini_payload(repair_body, package, repair_prompt, repair=True)
            repair_text = self._extract_text(repair_payload)
            try:
                parsed, dialogue_text, used_keyword_keys, used_refs = self._parse_and_validate_gemini_response(repair_text, package)
            except ProviderOutputError as repair_exc:
                raise ProviderOutputError(f"{self.name}:repair-failed:{repair_exc}") from repair_exc

        log_debug_event(
            "provider.response.parsed",
            provider=self.name,
            model=self.model,
            usedEvidenceRefs=used_refs,
            usedKeywordKeys=used_keyword_keys,
            repairUsed=repair_used,
            textPreview=_preview_text(dialogue_text),
        )
        quality_warnings = [f"repaired:{warning}" for warning in original_warnings[:4]] if repair_used else []

        final_text = dialogue_text if self._is_structured_scene_task(package) else self._compact_dialogue_text(dialogue_text, package.maxChars)
        return DialogueGenerationResult(
            text=final_text,
            provider=self.name,
            model=self.model,
            generationMode="gemini-json-v2+persona-card+repair-guard",
            fallbackUsed=False,
            usedEvidenceRefs=used_refs,
            qualityWarnings=quality_warnings,
            repairUsed=repair_used,
        )

    def _build_gemini_request_body(self, package: DialoguePromptPackage, prompt: str, temperature: float = 0.75) -> dict:
        max_output_tokens = max(self.max_output_tokens, min(1200, package.maxChars * 2 + 220))
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
                "thinkingConfig": {
                    "thinkingBudget": self.thinking_budget,
                },
            },
        }

    def _request_gemini_payload(self, request_body: dict, package: DialoguePromptPackage, prompt: str, repair: bool) -> dict:
        log_debug_event(
            "provider.request",
            provider=self.name,
            model=self.model,
            repair=repair,
            generalId=package.generalId,
            selectedKeywordKeys=[str(keyword.get("keywordKey") or "") for keyword in package.selectedKeywords],
            selectedKeywordLabels=[str(keyword.get("label") or "") for keyword in package.selectedKeywords],
            evidenceRefs=package.evidenceRefs,
            resolvedEvidenceRefs=[evidence.evidenceRef for evidence in package.resolvedEvidence],
            generationConfig=request_body["generationConfig"],
            promptPreview=_preview_text(prompt, 420),
        )
        url = f"{self.endpoint_base}/models/{self.model}:generateContent?key={self.api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        payload: dict | None = None
        last_error: ProviderUnavailableError | None = None
        attempt_limit = 1 if repair else self.retry_count
        timeout_seconds = (self.repair_timeout_ms if repair else self.timeout_ms) / 1000
        for attempt_index in range(attempt_limit):
            try:
                with _open_url(request, timeout_seconds, disable_proxy=self.disable_proxy) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_error = ProviderUnavailableError(f"{self.name}:http-{exc.code}:{detail}")
                log_debug_event(
                    "provider.retry",
                    provider=self.name,
                    model=self.model,
                    repair=repair,
                    attempt=attempt_index + 1,
                    error=str(last_error),
                )
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = ProviderUnavailableError(f"{self.name}:network:{exc}")
                log_debug_event(
                    "provider.retry",
                    provider=self.name,
                    model=self.model,
                    repair=repair,
                    attempt=attempt_index + 1,
                    error=str(last_error),
                )
        if payload is None:
            raise last_error or ProviderUnavailableError(f"{self.name}:no-response")

        log_debug_event(
            "provider.response.raw",
            provider=self.name,
            model=self.model,
            repair=repair,
            payloadSummary=self._summarize_gemini_payload(payload),
        )
        return payload

    def _parse_and_validate_gemini_response(
        self,
        response_text: str,
        package: DialoguePromptPackage,
    ) -> tuple[dict, str, list[str], list[str]]:
        parsed = self._parse_json_text(response_text)
        dialogue_text = self._extract_structured_scene_text(parsed, package) if self._is_structured_scene_task(package) else self._extract_dialogue_text(parsed)
        if not dialogue_text:
            raise ProviderOutputError(f"{self.name}:empty-text")
        used_keyword_keys = self._extract_used_keyword_keys(parsed, package)
        if not self._is_structured_scene_task(package) and not self._matches_selected_keyword_focus(dialogue_text, package, used_keyword_keys):
            raise ProviderOutputError(f"{self.name}:missing-keyword-focus")
        if not self._is_structured_scene_task(package) and self._violates_taboos(dialogue_text, package):
            raise ProviderOutputError(f"{self.name}:taboo-violation")

        allowed_refs = {evidence.evidenceRef for evidence in package.resolvedEvidence}
        used_refs = [ref for ref in parsed.get("usedEvidenceRefs", []) if ref in allowed_refs]
        if not used_refs and package.resolvedEvidence:
            used_refs = [package.resolvedEvidence[0].evidenceRef]
        return parsed, dialogue_text, used_keyword_keys, used_refs

    def _build_gemini_repair_prompt(self, package: DialoguePromptPackage, raw_text: str, warnings: list[str]) -> str:
        try:
            original_prompt: dict | str = json.loads(self._build_prompt(package))
        except json.JSONDecodeError:
            original_prompt = self._build_prompt(package)[:6000]
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        structured_task = self._is_structured_scene_task(package)
        chorus_task = selected_task == "scene-chorus-batch-v2"
        payload = {
            "task": (
                "Repair or regenerate the previous Gemini output into the requested chorus JSON object."
                if chorus_task
                else "Repair or regenerate the previous Gemini output into the requested scene JSON object."
                if structured_task
                else "Repair or regenerate the previous Gemini output into a valid NPC dialogue JSON object."
            ),
            "blockingIssues": warnings,
            "previousOutput": raw_text[:1600],
            "repairRules": [
                "Return exactly one JSON object and no markdown.",
                "Follow originalPrompt.outputContract exactly." if structured_task else "The JSON object must contain top-level text, usedKeywordKeys, usedEvidenceRefs, usedPersonaAnchors, safetyFallbackUsed, and violations.",
                "If previousOutput is partial or invalid, regenerate from originalPrompt instead of copying the broken format.",
                "Keep the same speakerGeneralId, locale, speechContextMode, selectedKeywords, and resolvedEvidence intent.",
                "Do not add unsupported historical facts.",
            ],
            "originalPrompt": original_prompt,
        }
        if structured_task and chorus_task:
            payload["repairRules"].extend(
                [
                    "Preserve exactly one chorusLines array and keep each selected speaker entry aligned to its targetId.",
                    "Keep each chorus line a direct spoken reaction from that speaker's persona, not narration, summary, or seed dump.",
                    "Use speaker focusHint, seedHint, voiceTag, and selectedContext.sceneGrounding as the repair anchors when available.",
                    "Keep each repaired line within the chorus output shape and do not import scene-script-pack four-beat rules.",
                    "Do not replace a chorus line with a generic scene summary or fallback-style template if a speakable reaction can be recovered.",
                ]
            )
        if structured_task and not chorus_task:
            payload["repairRules"].extend(
                [
                    "Preserve the four-beat fusion so remembered fact, emotional shift, spoken line, and next action stay discernible in the repaired pack, but keep the prose natural rather than checklist-like.",
                    "Do not collapse the scene into a one-line summary, seed dump, or fallback-style template with only longer sentences.",
                    "Keep usedSceneSeeds, usedPersonaAnchors, and usedBeatFields aligned with what is actually visible in the repaired storyText.",
                ]
            )
        return json.dumps(payload, ensure_ascii=False)

    def _extract_dialogue_text(self, parsed: dict) -> str:
        direct_text = str(parsed.get("text") or "").strip()
        if direct_text:
            return direct_text
        nested_format = parsed.get("format")
        if isinstance(nested_format, dict):
            return str(nested_format.get("text") or "").strip()
        return ""

    def _is_structured_scene_task(self, package: DialoguePromptPackage) -> bool:
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        return selected_task in {
            "scene-script-pack-v2",
            "scene-script-pack-v3",
            "scene-chorus-batch-v2",
            "scene-chorus-batch-v3",
        }

    def _extract_structured_scene_text(self, parsed: dict, package: DialoguePromptPackage) -> str:
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        if selected_task in {"scene-script-pack-v2", "scene-script-pack-v3"}:
            nested_format = parsed.get("format") if isinstance(parsed.get("format"), dict) else {}
            used_scene_seeds = parsed.get("usedSceneSeeds") if isinstance(parsed.get("usedSceneSeeds"), list) else []
            if not used_scene_seeds and isinstance(nested_format.get("usedSceneSeeds"), list):
                used_scene_seeds = nested_format.get("usedSceneSeeds")
            if not used_scene_seeds and isinstance(parsed.get("usedSeedKeys"), list):
                used_scene_seeds = parsed.get("usedSeedKeys")
            payload = {
                "memoryText": str(parsed.get("memoryText") or nested_format.get("memoryText") or "").strip(),
                "emotionText": str(parsed.get("emotionText") or nested_format.get("emotionText") or "").strip(),
                "dialogueText": str(parsed.get("dialogueText") or nested_format.get("dialogueText") or "").strip(),
                "intentText": str(parsed.get("intentText") or nested_format.get("intentText") or "").strip(),
                "storyText": str(parsed.get("storyText") or nested_format.get("storyText") or "").strip(),
                "usedSceneSeeds": used_scene_seeds,
                "usedPersonaAnchors": (
                    parsed.get("usedPersonaAnchors")
                    if isinstance(parsed.get("usedPersonaAnchors"), list)
                    else nested_format.get("usedPersonaAnchors")
                    if isinstance(nested_format.get("usedPersonaAnchors"), list)
                    else []
                ),
                "usedBeatFields": (
                    parsed.get("usedBeatFields")
                    if isinstance(parsed.get("usedBeatFields"), list)
                    else nested_format.get("usedBeatFields")
                    if isinstance(nested_format.get("usedBeatFields"), list)
                    else []
                ),
                "violations": (
                    parsed.get("violations")
                    if isinstance(parsed.get("violations"), list)
                    else nested_format.get("violations")
                    if isinstance(nested_format.get("violations"), list)
                    else []
                ),
            }
            if not any(payload[key] for key in ["memoryText", "emotionText", "dialogueText", "intentText", "storyText"]):
                return ""
            payload["usedSeedKeys"] = list(payload["usedSceneSeeds"])
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if selected_task in {"scene-chorus-batch-v2", "scene-chorus-batch-v3"}:
            raw_lines = parsed.get("chorusLines")
            if not isinstance(raw_lines, list):
                raw_lines = []
            lines: list[dict] = []
            for raw_line in raw_lines:
                if not isinstance(raw_line, dict):
                    continue
                lines.append(
                    {
                        "targetId": str(raw_line.get("targetId") or "").strip(),
                        "label": str(raw_line.get("label") or "").strip(),
                        "role": str(raw_line.get("role") or "").strip(),
                        "text": str(raw_line.get("text") or "").strip(),
                        "status": str(raw_line.get("status") or "ok").strip() or "ok",
                        "usedSeedKeys": raw_line.get("usedSeedKeys") if isinstance(raw_line.get("usedSeedKeys"), list) else [],
                        "usedPersonaAnchors": raw_line.get("usedPersonaAnchors") if isinstance(raw_line.get("usedPersonaAnchors"), list) else [],
                        "voiceTag": str(raw_line.get("voiceTag") or "").strip(),
                        "violations": raw_line.get("violations") if isinstance(raw_line.get("violations"), list) else [],
                    }
                )
            if not lines:
                return ""
            return json.dumps({"chorusLines": lines}, ensure_ascii=False, separators=(",", ":"))
        return ""

    def _build_prompt(self, package: DialoguePromptPackage) -> str:
        selected_keyword_labels = [str(keyword.get("label") or "") for keyword in package.selectedKeywords if keyword.get("label")]
        locale_instruction = LOCALE_INSTRUCTIONS.get(package.locale, LOCALE_INSTRUCTIONS[DEFAULT_LOCALE])
        speech_instruction = SPEECH_CONTEXT_INSTRUCTIONS.get(package.speechContextMode, SPEECH_CONTEXT_INSTRUCTIONS[DEFAULT_SPEECH_CONTEXT_MODE])
        memory_block = self._memory_prompt_block(package.memoryContext)
        tone_mode = str(package.toneMode or "").strip().lower()
        evidence_payload = [
            {
                "evidenceRef": evidence.evidenceRef,
                "sourceType": evidence.sourceType,
                "sourceQuote": evidence.sourceQuote,
                "factSummary": evidence.factSummary,
                "generalIds": evidence.generalIds,
                "confidence": evidence.confidence,
            }
            for evidence in package.resolvedEvidence[:5]
        ]
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        if selected_task == "scene-script-pack-v3":
            payload = {
                "task": "Generate a strict protagonist scene script for a Three Kingdoms memory theatre.",
                "hardRules": [
                    "Return JSON only and no markdown.",
                    "Use only personaCardSubset, selectedContext, selectedKeywords, resolvedEvidence, and draftFragments.",
                    "The protagonist is selectedContext.mainActor; the counterpart is selectedContext.activeTarget. Do not swap their actions, emotions, or dialogue ownership.",
                    "Use sceneSeeds, persona, memoryText, emotionText, dialogueText, intentText, activeTarget, and resolvedEvidence as source material, but rewrite them into clean prose instead of copying noisy fragments.",
                    "Return four display-card sentences plus storyText. The display-card sentences are memoryText, emotionText, dialogueText, and intentText; they must be newly written from the same interpretation as storyText, not copied from draftFragments.",
                    "memoryText must answer what the protagonist recalls now; emotionText must answer how the protagonist's heart changes; dialogueText must be one speakable line to activeTarget; intentText must answer what the protagonist will do next.",
                    "Each display-card sentence should be concise but meaningful, roughly 18-55 Traditional Chinese characters, and must not be a source fragment, isolated quote tail, metadata label, or fallback summary.",
                    "If storyText is non-empty, all four display-card sentences must also be non-empty. If any display-card sentence cannot be supported, return empty storyText and a violation instead.",
                    "Write one natural zh-TW paragraph, roughly 180-260 Traditional Chinese characters, that reads like a formal director passage rather than a field summary.",
                    "Let personaCardSubset voiceStyle and personalityTraits shape the attitude, cadence, and decision weight of the paragraph.",
                    "Fuse memory, emotion, dialogue, and intent into one advancing scene. Do not output a beat sheet, labels, bullets, metadata, sourceRef, or analysis phrasing.",
                    "Use the six scene seeds as grounding. Prefer 4-5 of people, event, time, place, object, emotion when they genuinely help the scene move.",
                    "Do not echo duplicated names, role labels, runtime markers, source fragments, targetId, evidenceId, contextKey, angle labels, or mojibake.",
                    "Do not imitate fallback bundle wording, deterministic mini-story tone, or data_first summary cadence.",
                    "Outside quoted speech, keep stable naming for main actor and active target.",
                    "End on one concrete move, decision, or emotional commitment in the current scene.",
                    "If the material is too weak to support a clean paragraph, return storyText as an empty string and add a violation code instead of fabricating prose.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "personaCardSubset": package.personaCardSubset,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "draftFragments": {
                    "sceneDraft": str((package.memoryContext or {}).get("shortTerm") or "").strip(),
                    "seedSummary": str((package.memoryContext or {}).get("longTerm") or "").strip(),
                    "relationshipDraft": str((package.memoryContext or {}).get("playerProfile") or "").strip(),
                    "directorInstructions": str((package.memoryContext or {}).get("promises") or "").strip(),
                },
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "format": {
                        "memoryText": "string, required when storyText is non-empty; one rewritten display-card sentence answering what the protagonist recalls now",
                        "emotionText": "string, required when storyText is non-empty; one rewritten display-card sentence answering how the protagonist's heart changes",
                        "dialogueText": "string, required when storyText is non-empty; one quoted or speakable line from the protagonist to activeTarget",
                        "intentText": "string, required when storyText is non-empty; one rewritten display-card sentence answering what the protagonist will do next",
                        "storyText": "string, clean formal director prose in zh-TW; empty string if no clean story can be formed",
                        "usedSceneSeeds": ["only the seed categories actually visible or clearly implied in storyText"],
                        "usedPersonaAnchors": ["only persona anchors actually visible in storyText"],
                        "usedBeatFields": ["only beat fields actually visible in storyText; choose from memory, emotion, dialogue, intent"],
                        "violations": [],
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False)
        if selected_task == "scene-script-pack-v2":
            payload = {
                "task": "Generate a structured protagonist scene script pack for a Three Kingdoms memory theatre.",
                "hardRules": [
                    "Return JSON only and no markdown.",
                    "Use only personaCardSubset, selectedContext, selectedKeywords, resolvedEvidence, and draftFragments.",
                    "The protagonist is selectedContext.mainActor; the counterpart is selectedContext.activeTarget. Do not swap their actions or emotions.",
                    "Let the paragraph naturally carry four beat functions: remembered fact, inner emotional shift, one line to the counterpart, and next intended action. Keep them fused rather than splitting into a checklist.",
                    "Keep those beat functions fused into one advancing scene arc, not four unrelated summary cards.",
                    "Use the six scene seeds as structural ground. People, event, time, place, object, and emotion should shape the scene, but do not turn them into a label parade.",
                    "Let personaCardSubset voiceStyle and personalityTraits control the narration's attitude, cadence, and decision-making. Do not write a neutral summary voice.",
                    "Then write one natural zh-TW paragraph, roughly 220-280 Traditional Chinese characters, that a character can perform.",
                    "Prefer a natural cadence of roughly 3-5 sentences; do not force one beat per sentence.",
                    "Do not repeat the same subject or sentence skeleton in adjacent sentences.",
                    "If storyText would fall short, add a grounded consequence, emotional turn, or next action before returning.",
                    "The storyText must be a natural performable paragraph, not a seed dump, field-by-field summary, or fallback bundle with longer sentences.",
                    "Outside quoted speech, keep one stable third-person narration centered on selectedContext.mainActor; do not switch between the protagonist name, 我, and 他 inside narration.",
                    "Prefer one fixed narrator anchor outside quotes, ideally selectedContext.mainActor.displayName, and keep that anchor stable through the whole paragraph.",
                    "Outside quoted speech, selectedContext.activeTarget must keep one canonical name; do not switch back and forth between alternate forms such as personal name and title.",
                    "Do not let adjacent sentences restate the same beat with near-duplicate wording such as 剛相見 / 方才相見; compress repeated clauses into one sharper progression.",
                    "End storyText on a concrete move, constraint, or decision in the current scene, not on abstract template phrases about 情分, 命運, 去路, or vague feelings alone.",
                    "The six scene elements must feel woven into one advancing scene; if an element is only name-dropped but does not help the scene move, rewrite it more naturally.",
                    "The six elements in sceneSeeds are people, event, time, place, object, and emotion; storyText should naturally integrate at least 4 of them, and ideally 5-6.",
                    "Do not stitch those elements together with semicolons, slashes, labels, bullets, or summary-style field names such as people/event/time/place/objects/emotion.",
                    "Treat draftFragments and resolvedEvidence as noisy source material; if they repeat names, role labels, or evidence-like fragments, compress them into a clean scene summary rather than echoing the duplication.",
                    "usedSceneSeeds must list only the seed categories that are actually shown or clearly implied inside storyText.",
                    "usedBeatFields must list only the beat fields actually visible in the final prose; do not pad them to satisfy a checklist.",
                    "Do not output a field-by-field summary, labels, bullets, metadata, sourceRef, runtime, or analysis phrasing.",
                    "Do not use placeholders such as 此人, 主角, 某人, or parenthetical role labels like （主角）.",
                    "Keep the voice compatible with personaCardSubset voiceStyle and personalityTraits.",
                    "For zh-TW output, do not mix English words, pinyin, simplified Chinese, mojibake, code fragments, or slash artifacts.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "personaCardSubset": package.personaCardSubset,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "keywordDirective": {
                    "mustReflectSelectedKeyword": bool(selected_keyword_labels),
                    "preferredLabels": selected_keyword_labels,
                    "selectedKeywordCount": len(selected_keyword_labels),
                },
                "draftFragments": {
                    "sceneDraft": str((package.memoryContext or {}).get("shortTerm") or "").strip(),
                    "seedSummary": str((package.memoryContext or {}).get("longTerm") or "").strip(),
                    "relationshipDraft": str((package.memoryContext or {}).get("playerProfile") or "").strip(),
                    "directorInstructions": str((package.memoryContext or {}).get("promises") or "").strip(),
                },
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "format": {
                        "memoryText": "string, one protagonist sentence for 想起什麼",
                        "emotionText": "string, one protagonist sentence for 心裡怎麼變",
                        "dialogueText": "string, one quoted or speakable line to activeTarget",
                        "intentText": "string, one protagonist sentence for 接下來想做什麼",
                        "storyText": "string, roughly 220-280 Traditional Chinese characters, a natural performable paragraph that fuses the four beat functions without reading like a beat sheet or field summary, stable actor/target naming, actionable ending, resilient to noisy source fragments",
                        "usedSceneSeeds": ["only the seed categories that are actually shown or clearly implied in storyText"],
                        "usedPersonaAnchors": ["stable persona anchors actually visible in storyText; include only what is truly surfaced"],
                        "usedBeatFields": ["beat fields actually visible in storyText; choose from memory, emotion, dialogue, intent as applicable"],
                        "violations": [],
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False)
        if selected_task == "scene-chorus-batch-v3":
            payload = {
                "task": "Generate strict chorus reaction lines for a Three Kingdoms memory theatre.",
                "hardRules": [
                    "Return JSON only and no markdown.",
                    "Return exactly one chorusLines array with exactly the speakers listed in selectedContext.speakers, preserving each targetId.",
                    "Each item must include targetId, label, role, status, text, usedSeedKeys, usedPersonaAnchors, voiceTag, and violations.",
                    "Set status to ok only when a clean in-character spoken line can be produced from persona, relationship, sceneScript, and sceneGrounding.",
                    "For speakers that have personaSummary, focusHint, or seedHint and the sceneScript is concrete, prefer status ok with a grounded reaction; use no_data only when there is truly not enough identity or scene grounding.",
                    "If a clean line cannot be produced, set status to no_data and text to an empty string. Do not fabricate fallback dialogue.",
                    "When status is ok, keep text within 24-72 zh-TW characters and make it a complete, speakable reaction.",
                    "Do not copy raw tags, runtime markers, source fragments, English persona tags, or fallback-style summary wording into text.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "mainActorPersonaSubset": package.personaCardSubset,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "format": {
                        "chorusLines": [
                            {
                                "targetId": "must equal one selectedContext.speakers[].targetId",
                                "label": "speaker display label",
                                "role": "relationship role",
                                "status": "ok or no_data",
                                "text": "24-72 zh-TW characters when status=ok, otherwise empty string",
                                "usedSeedKeys": ["event or place or objects or emotion"],
                                "usedPersonaAnchors": ["string"],
                                "voiceTag": "string",
                                "violations": [],
                            }
                        ]
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False)
        if selected_task == "scene-chorus-batch-v2":
            payload = {
                "task": "Generate four distinct bystander reaction lines for a Three Kingdoms memory theatre.",
                "hardRules": [
                    "Return JSON only and no markdown.",
                    "Return exactly one chorusLines array with exactly the speakers listed in selectedContext.speakers, preserving each targetId.",
                    "Each line must be spoken from that speaker's persona, relationship, and voice anchors.",
                    "Use each speaker's guidance, focusHint, and seedHint as stance; do not copy raw tags or metadata into the line.",
                    "Each line must react to sceneScript and at least one concrete sceneSeed; do not restate the seed as a flat summary.",
                    "Each line must include at least one exact token from selectedContext.sceneGrounding or the speaker focus/seed hints; prefer the most specific grounding term available.",
                    "Keep each line within 24-72 zh-TW characters and make it a complete, speakable reaction.",
                    "The four lines must not share the same sentence skeleton or generic advice.",
                    "Do not mention the speaker's own name in the line.",
                    "Do not begin with narration markers such as 卻說, 且說, 只見, 此時, 正行間, 忽然, or 原來.",
                    "Do not use placeholders such as 此人, 主角, 某人, or parenthetical role labels like （主角）.",
                    "For zh-TW output, do not mix English words, pinyin, simplified Chinese, mojibake, code fragments, or slash artifacts.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "mainActorPersonaSubset": package.personaCardSubset,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "format": {
                        "chorusLines": [
                            {
                                "targetId": "must equal one selectedContext.speakers[].targetId",
                                "label": "speaker display label",
                                "role": "relationship role",
                                "text": "24-72 zh-TW characters, one speakable line",
                                "usedSeedKeys": ["event or place or objects or emotion"],
                                "usedPersonaAnchors": ["string"],
                                "voiceTag": "string",
                                "violations": [],
                            }
                        ]
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False)
        if tone_mode == "narrative_fusion":
            draft_fragments = {
                "sceneDraft": str((package.memoryContext or {}).get("shortTerm") or "").strip(),
                "emotionDraft": str((package.memoryContext or {}).get("longTerm") or "").strip(),
                "dialogueDraft": str((package.memoryContext or {}).get("playerProfile") or "").strip(),
                "intentDraft": str((package.memoryContext or {}).get("promises") or "").strip(),
            }
            payload = {
                "task": "Fuse draft fragments into one vivid, natural Chinese narrative paragraph for a Three Kingdoms scene.",
                "hardRules": [
                    "Return JSON only.",
                    "Use only personaCardSubset, selectedContext, selectedKeywords, resolvedEvidence, and draftFragments.",
                    "If draftFragments.intentDraft contains 場景導演 Beats, treat those beats as the authoritative story outline.",
                    "If selectedContext.task is scene-director-script, treat draftFragments as seed material and rewrite it as a short directed scene, not a field-by-field summary.",
                    "If selectedContext.sceneFacts is present, use its people/event/time/locations/objects as the first source of scene grounding before writing the scene.",
                    "If selectedContext.storyRoleGuard is present, it is binding: mainActor is the narrative center, activeTarget is the counterpart, and you must not swap their actions, emotions, or quoted speech ownership.",
                    "If selectedContext.task is scene-director-script, any crying, hesitation, speech, or decision in draftFragments belongs to mainActor unless selectedContext explicitly marks another speaker.",
                    "Do not invent major historical facts not supported by resolvedEvidence/draftFragments.",
                    "Do not mention being an AI or model.",
                    "Narrative must be fluent and cinematic, not bullet-style concatenation.",
                    "Keep perspective consistent and preserve key names from draft fragments.",
                    "For zh-TW output, do not mix English words, pinyin, simplified Chinese, mojibake, code fragments, or slash artifacts.",
                    "Do not use analysis phrases such as 這條線索, 心裡先浮起, 決策傾向, runtime, sourceRef, or 人物定位.",
                    "If selectedKeywords is not empty, the final paragraph must clearly allude to at least one selected keyword label.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "narrativeDirective": {
                    "mode": "story-fusion",
                    "goal": "Use scene/memory/emotion/dialogue/intent as story beats, then write a coherent 180-420 Chinese-character short scene with transitions.",
                    "style": [
                        "third-person narrative centered on persona displayName",
                        "make the opening sentence establish the key person, situation, and setting when sceneFacts provides them",
                        "include one quoted line if dialogueDraft is available",
                        "end with a concrete next-step intention",
                        "keep the selected target and current scene as the main thread; do not jump to unrelated memories",
                    ],
                },
                "personaCardSubset": package.personaCardSubset,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "keywordDirective": {
                    "mustReflectSelectedKeyword": bool(selected_keyword_labels),
                    "preferredLabels": selected_keyword_labels,
                    "selectedKeywordCount": len(selected_keyword_labels),
                },
                "draftFragments": draft_fragments,
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "maxChars": package.maxChars,
                    "format": {
                        "text": "string",
                        "usedKeywordKeys": ["keywordKey"],
                        "usedEvidenceRefs": ["evidenceRef"],
                        "usedPersonaAnchors": ["string"],
                        "safetyFallbackUsed": False,
                        "violations": [],
                    },
                },
            }
        else:
            payload = {
                "task": "Write one in-character dialogue line for a Three Kingdoms game NPC.",
                "hardRules": [
                    "Return JSON only.",
                    "Use only personaCardSubset, playerGeneralMemory, selectedContext, selectedKeywords, and resolvedEvidence.",
                    "Do not invent major historical facts not supported by resolvedEvidence.",
                    "Do not mention being an AI or model.",
                    "Never write from the identity of another Three Kingdoms character.",
                    "Do not mention the speaker's own name in the final line.",
                    "If selectedContext.task is chorus-line, speak from that speaker's perspective toward the mainActor and the sceneScript.",
                    "If selectedContext.task is chorus-line or chorus-line-rewrite, the line must reflect speakerPersona voiceStyle/personalityTraits/lore when provided.",
                    "If selectedContext.task is chorus-line-rewrite, rewrite draftLine/fallbackDraft into a line that sounds like this speaker actually reacting after the scene.",
                    "If selectedContext.task is chorus-line-rewrite, do not begin with narration markers such as 卻說, 且說, 只見, 此時, 正行間, 忽然, or 原來.",
                    "If selectedContext.task is chorus-line-rewrite, do not restate the scene seed verbatim; turn it into the speaker's concrete judgment or worry.",
                    "If selectedContext.sceneFacts is present, ground the line in its people/event/time/locations/objects instead of generic advice.",
                    "Avoid generic advice such as '先看證據', '先說清楚', '再說判斷', or lines that could fit any speaker.",
                    "The speechContextDirective is binding: choose the addressee, emotional distance, and public/private register from it.",
                    "For zh-TW output, do not mix English words, pinyin, simplified Chinese, mojibake, code fragments, or slash artifacts.",
                    "If selectedKeywords is not empty, the final line must directly mention or clearly allude to at least one selected keyword label.",
                ],
                "speakerIdentityGuard": self._speaker_identity_guard(package),
                "localeDirective": {
                    "locale": package.locale,
                    "languageLabel": locale_instruction["label"],
                    "instruction": locale_instruction["instruction"],
                },
                "speechContextDirective": {
                    "mode": package.speechContextMode,
                    "label": speech_instruction["label"],
                    "instruction": speech_instruction["instruction"],
                    "keywordAngle": speech_instruction["keywordAngle"],
                    "must": speech_instruction["must"],
                    "avoid": speech_instruction["avoid"],
                },
                "personaCardSubset": package.personaCardSubset,
                "playerGeneralMemory": memory_block,
                "selectedContext": package.selectedContext,
                "selectedKeywords": package.selectedKeywords,
                "keywordDirective": {
                    "mustReflectSelectedKeyword": bool(selected_keyword_labels),
                    "preferredLabels": selected_keyword_labels,
                    "selectedKeywordCount": len(selected_keyword_labels),
                },
                "resolvedEvidence": evidence_payload,
                "outputContract": {
                    "language": package.locale,
                    "maxChars": package.maxChars,
                    "format": {
                        "text": "string",
                        "usedKeywordKeys": ["keywordKey"],
                        "usedEvidenceRefs": ["evidenceRef"],
                        "usedPersonaAnchors": ["string"],
                        "safetyFallbackUsed": False,
                        "violations": [],
                    },
                },
            }
        if memory_block is None:
            payload.pop("playerGeneralMemory", None)
        return json.dumps(payload, ensure_ascii=False)

    def _memory_prompt_block(self, memory_context: dict | None) -> dict | None:
        if not memory_context:
            return None
        short_term = str(memory_context.get("shortTerm") or "").strip()
        long_term = str(memory_context.get("longTerm") or "").strip()
        player_profile = str(memory_context.get("playerProfile") or "").strip()
        promises = str(memory_context.get("promises") or "").strip()
        if not any([short_term, long_term, player_profile, promises]):
            return None
        return {
            "instruction": "此為玩家與本武將的互動記憶壓縮摘要，據此維持對話連貫性，不得捏造摘要以外的事實。",
            "shortTerm": short_term,
            "longTerm": long_term,
            "playerProfile": player_profile,
            "promises": promises,
        }

    def _speaker_identity_guard(self, package: DialoguePromptPackage) -> dict:
        guard = GENERAL_IDENTITY_GUARDS.get(package.generalId, {})
        display_name = str(package.personaCardSubset.get("displayName") or package.generalId)
        return {
            "speakerGeneralId": package.generalId,
            "displayName": display_name,
            "allowedSelfNames": guard.get("allowedSelfNames") or [display_name],
            "rule": "The text must be spoken by speakerGeneralId only. It may mention selected keyword characters, but must not use their first-person self-name.",
        }

    def _extract_text(self, payload: dict) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            raise ProviderOutputError(f"{self.name}:no-candidates")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text_parts = [str(part.get("text") or "") for part in parts if part.get("text")]
        if not text_parts:
            raise ProviderOutputError(f"{self.name}:no-text-part")
        return "".join(text_parts).strip()

    def _parse_json_text(self, text: str) -> dict:
        cleaned = self._strip_reasoning_tags(text.strip())
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            repaired = self._repair_json_contract(cleaned)
            if repaired is None:
                raise ProviderOutputError(f"{self.name}:json-parse:{exc}") from exc
            log_debug_event(
                "provider.response.repaired",
                provider=self.name,
                model=self.model,
                repairedKeys=sorted(repaired.keys()),
                textPreview=_preview_text(str(repaired.get("text") or "")),
            )
            parsed = repaired
        if not isinstance(parsed, dict):
            raise ProviderOutputError("gemini:json-not-object")
        return parsed

    def _strip_reasoning_tags(self, text: str) -> str:
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        cleaned = re.sub(r"^思考[:：].*?(?=\{|```)", "", cleaned, flags=re.DOTALL).strip()
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start > 0 and json_end > json_start:
            return cleaned[json_start:json_end + 1].strip()
        return cleaned

    def _compact_dialogue_text(self, text: str, max_chars: int) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        minimum = max(12, int(max_chars * 0.55))
        candidates = [index + 1 for index, char in enumerate(cleaned[:max_chars]) if char in "。！？!?；;，,"]
        candidates = [index for index in candidates if index >= minimum]
        if candidates:
            return cleaned[:candidates[-1]].rstrip()
        return cleaned[: max(max_chars - 1, 1)].rstrip() + "…"

    def _repair_json_contract(self, cleaned: str) -> dict | None:
        text_value = self._extract_string_field(cleaned, "text")
        if not text_value:
            text_value = self._extract_nested_format_text(cleaned)
        if not text_value:
            text_value = self._extract_relaxed_string_field(cleaned, "text")
        used_keyword_keys = self._extract_string_array_field(cleaned, "usedKeywordKeys")
        used_evidence_refs = self._extract_string_array_field(cleaned, "usedEvidenceRefs")
        used_persona_anchors = self._extract_string_array_field(cleaned, "usedPersonaAnchors")
        used_scene_seeds = self._extract_string_array_field(cleaned, "usedSceneSeeds")
        if not used_scene_seeds:
            used_scene_seeds = self._extract_string_array_field(cleaned, "usedSeedKeys")
        used_beat_fields = self._extract_string_array_field(cleaned, "usedBeatFields")
        violations = self._extract_string_array_field(cleaned, "violations")
        if not any([text_value, used_keyword_keys, used_evidence_refs, used_persona_anchors, used_scene_seeds, used_beat_fields]):
            text_value = self._extract_plain_text_candidate(cleaned)
        if not any([text_value, used_keyword_keys, used_evidence_refs, used_persona_anchors, used_scene_seeds, used_beat_fields]):
            return None
        return {
            "text": text_value or "",
            "usedKeywordKeys": used_keyword_keys,
            "usedEvidenceRefs": used_evidence_refs,
            "usedPersonaAnchors": used_persona_anchors,
            "usedSceneSeeds": used_scene_seeds,
            "usedBeatFields": used_beat_fields,
            "safetyFallbackUsed": False,
            "violations": violations,
        }

    def _extract_nested_format_text(self, text: str) -> str | None:
        format_index = text.find('"format"')
        if format_index < 0:
            return None
        return self._extract_string_field(text[format_index:], "text")

    def _extract_string_field(self, text: str, field_name: str) -> str | None:
        field_index = text.find(f'"{field_name}"')
        if field_index < 0:
            return None
        colon_index = text.find(':', field_index)
        if colon_index < 0:
            return None
        string_start = self._find_next_nonspace(text, colon_index + 1)
        if string_start is None or string_start >= len(text) or text[string_start] != '"':
            return None
        value, _end_index, _closed = self._scan_json_string(text, string_start)
        return value or None

    def _extract_relaxed_string_field(self, text: str, field_name: str) -> str | None:
        match = re.search(rf'(?<![\w])["\']?{re.escape(field_name)}["\']?\s*:\s*(["\'])', text)
        if not match:
            return None
        quote_index = match.end() - 1
        if text[quote_index] == '"':
            value, _end_index, _closed = self._scan_json_string(text, quote_index)
            return value or None
        value, _end_index, _closed = self._scan_single_quoted_string(text, quote_index)
        return value or None

    def _scan_single_quoted_string(self, text: str, quote_index: int) -> tuple[str, int, bool]:
        cursor = quote_index + 1
        chars: list[str] = []
        escaped = False
        while cursor < len(text):
            char = text[cursor]
            if escaped:
                chars.append(char)
                escaped = False
                cursor += 1
                continue
            if char == '\\':
                escaped = True
                cursor += 1
                continue
            if char == "'":
                return ''.join(chars).strip(), cursor + 1, True
            chars.append(char)
            cursor += 1
        return ''.join(chars).strip(), cursor, False

    def _extract_plain_text_candidate(self, text: str) -> str | None:
        candidate = self._strip_reasoning_tags(text.strip())
        candidate = re.sub(r"^```(?:json|text)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
        if not candidate or candidate.startswith("{") or candidate.startswith("["):
            return None
        lowered = candidate.lower()
        if any(marker in lowered for marker in ["traceback", "<html", "exception", "error:"]):
            return None
        if len(re.findall(r"[\u4e00-\u9fff]", candidate)) < 2:
            return None
        return re.sub(r"\s+", " ", candidate).strip()

    def _extract_string_array_field(self, text: str, field_name: str) -> list[str]:
        field_index = text.find(f'"{field_name}"')
        if field_index < 0:
            return []
        colon_index = text.find(':', field_index)
        if colon_index < 0:
            return []
        array_start = text.find('[', colon_index)
        if array_start < 0:
            return []
        items: list[str] = []
        cursor = array_start + 1
        while cursor < len(text):
            next_index = self._find_next_nonspace(text, cursor)
            if next_index is None:
                break
            cursor = next_index
            if text[cursor] == ']':
                break
            if text[cursor] != '"':
                break
            value, cursor, _closed = self._scan_json_string(text, cursor)
            if value:
                items.append(value)
            next_index = self._find_next_nonspace(text, cursor)
            if next_index is None:
                break
            cursor = next_index
            if text[cursor] == ',':
                cursor += 1
                continue
            if text[cursor] == ']':
                break
        return items

    def _find_next_nonspace(self, text: str, start_index: int) -> int | None:
        cursor = start_index
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        return cursor if cursor < len(text) else None

    def _scan_json_string(self, text: str, quote_index: int) -> tuple[str, int, bool]:
        cursor = quote_index + 1
        raw_chars: list[str] = []
        escaped = False
        while cursor < len(text):
            char = text[cursor]
            if escaped:
                raw_chars.append('\\')
                raw_chars.append(char)
                escaped = False
                cursor += 1
                continue
            if char == '\\':
                escaped = True
                cursor += 1
                continue
            if char == '"':
                raw_value = ''.join(raw_chars)
                try:
                    value = json.loads(f'"{raw_value}"')
                except json.JSONDecodeError:
                    value = self._unescape_partial_json_string(raw_value)
                return value, cursor + 1, True
            raw_chars.append(char)
            cursor += 1
        return self._unescape_partial_json_string(''.join(raw_chars)), cursor, False

    def _unescape_partial_json_string(self, raw_value: str) -> str:
        repaired = raw_value
        repaired = repaired.replace('\\n', ' ')
        repaired = repaired.replace('\\r', ' ')
        repaired = repaired.replace('\\t', ' ')
        repaired = repaired.replace('\\"', '"')
        repaired = repaired.replace("\\'", "'")
        repaired = repaired.replace('\\\\', '\\')
        repaired = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match.group(1), 16)), repaired)
        return repaired.strip()

    def _extract_used_keyword_keys(self, parsed: dict, package: DialoguePromptPackage) -> list[str]:
        allowed_keys = {
            str(keyword.get("keywordKey") or "")
            for keyword in package.selectedKeywords
            if keyword.get("keywordKey")
        }
        raw_keys = parsed.get("usedKeywordKeys") or []
        if not isinstance(raw_keys, list):
            return []
        return [str(keyword_key) for keyword_key in raw_keys if str(keyword_key) in allowed_keys]

    def _matches_selected_keyword_focus(self, text: str, package: DialoguePromptPackage, used_keyword_keys: list[str]) -> bool:
        if not package.selectedKeywords:
            return True
        if used_keyword_keys:
            return True
        normalized_text = self._normalize_focus_text(text)
        focus_tokens: list[str] = []
        for keyword in package.selectedKeywords:
            focus_tokens.extend(self._focus_tokens_for_label(str(keyword.get("label") or "")))
        if package.selectedContext is not None:
            focus_tokens.extend(self._focus_tokens_for_label(str(package.selectedContext.get("label") or "")))
        return any(token in normalized_text for token in dict.fromkeys(token for token in focus_tokens if token))

    def _focus_tokens_for_label(self, label: str) -> list[str]:
        normalized = self._normalize_focus_text(label)
        if not normalized:
            return []
        tokens = [normalized]
        if len(normalized) >= 4:
            for index in range(len(normalized) - 1):
                token = normalized[index:index + 2]
                if token not in tokens:
                    tokens.append(token)
                if len(tokens) >= 5:
                    break
        return tokens

    def _normalize_focus_text(self, value: str) -> str:
        return re.sub(r"[\s\-_'\"`，。！？、：；（）()\[\]{}]", "", value or "")

    def _summarize_gemini_payload(self, payload: dict) -> dict:
        candidates = payload.get("candidates") or []
        first_candidate = candidates[0] if candidates else {}
        parts = ((first_candidate.get("content") or {}).get("parts") or [])
        usage = payload.get("usageMetadata") or {}
        return {
            "candidateCount": len(candidates),
            "finishReason": first_candidate.get("finishReason"),
            "partsCount": len(parts),
            "thoughtsTokenCount": usage.get("thoughtsTokenCount"),
            "candidatesTokenCount": usage.get("candidatesTokenCount"),
        }

    def _violates_taboos(self, text: str, package: DialoguePromptPackage) -> bool:
        forbidden = ["AI", "人工智慧", "模型", "網路", "哈哈哈", "lol"]
        persona_taboos = package.personaCardSubset.get("taboos") or []
        if any(token in text for token in forbidden):
            return True
        if "不可怯戰" in persona_taboos and any(token in text for token in ["逃吧", "我怕", "退縮"]):
            return True
        return False

    def _quality_warnings(self, text: str, package: DialoguePromptPackage) -> list[str]:
        warnings: list[str] = []
        if self._violates_taboos(text, package):
            warnings.append("taboo-violation")
        warnings.extend(self._speaker_identity_warnings(text, package))
        warnings.extend(self._language_quality_warnings(text, package))
        warnings.extend(self._gibberish_warnings(text))
        if package.speechContextMode != "meeting_statement" and ("\n" in text or re.search(r"(^|\n)\s*[一二三四五六七八九十0-9]+[、.．]", text)):
            warnings.append("format:too-structured-for-speech-context")
        return list(dict.fromkeys(warnings))

    def _speaker_identity_warnings(self, text: str, package: DialoguePromptPackage) -> list[str]:
        guard = GENERAL_IDENTITY_GUARDS.get(package.generalId)
        if not guard:
            return []
        warnings: list[str] = []
        for pattern in guard.get("forbiddenSelfNamePatterns", []):
            if re.search(pattern, text):
                warnings.append("speaker-identity:wrong-self-name")
                break
        return warnings

    def _language_quality_warnings(self, text: str, package: DialoguePromptPackage) -> list[str]:
        warnings: list[str] = []
        ascii_words = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", text)]
        unexpected_ascii = [word for word in ascii_words if word not in ALLOWED_ASCII_WORDS]
        if package.locale == "zh-TW":
            if unexpected_ascii:
                warnings.append("language:unexpected-ascii")
            simplified_count = sum(1 for char in text if char in ZH_TW_SIMPLIFIED_MARKERS)
            if simplified_count >= 2:
                warnings.append("language:simplified-chinese-mix")
        if package.locale == "en" and re.search(r"[\u3040-\u30ff]", text):
            warnings.append("language:japanese-in-english")
        if package.locale == "ja" and re.search(r"[A-Za-z]{4,}", text):
            warnings.append("language:ascii-in-japanese")
        return warnings

    def _gibberish_warnings(self, text: str) -> list[str]:
        warnings: list[str] = []
        if "\ufffd" in text:
            warnings.append("gibberish:replacement-character")
        if re.search(r"/[A-Za-z]{2,}", text) or re.search(r"[xXfF]{5,}", text):
            warnings.append("gibberish:artifact-token")
        if re.search(r"(.)\1{5,}", text):
            warnings.append("gibberish:repeated-character")
        if "```" in text or "{\"" in text:
            warnings.append("gibberish:code-fragment")
        return warnings


class LocalLlamaDialogueProvider(GeminiDialogueProvider):
    name = "local_llama"

    def __init__(self, api_url: str | None = None, model: str | None = None, timeout_ms: int | None = None) -> None:
        self.model = model or os.environ.get("NPC_LLM_MODEL_LOCAL_LLAMA") or DEFAULT_LOCAL_LLAMA_MODEL
        self.timeout_ms = timeout_ms or int(os.environ.get("NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS") or DEFAULT_LOCAL_LLAMA_TIMEOUT_MS)
        base_url = os.environ.get("NPC_LLM_LOCAL_LLAMA_API_BASE") or DEFAULT_LOCAL_LLAMA_API_BASE
        self.api_url = api_url or os.environ.get("NPC_LLM_LOCAL_LLAMA_API_URL") or f"{base_url.rstrip('/')}{DEFAULT_LOCAL_LLAMA_API_PATH}"
        self.max_output_tokens = int(os.environ.get("NPC_LLM_LOCAL_LLAMA_MAX_OUTPUT_TOKENS") or DEFAULT_GEMINI_MAX_OUTPUT_TOKENS)
        self.temperature = float(os.environ.get("NPC_LLM_LOCAL_LLAMA_TEMPERATURE") or DEFAULT_LOCAL_LLAMA_TEMPERATURE)
        self.top_p = float(os.environ.get("NPC_LLM_LOCAL_LLAMA_TOP_P") or DEFAULT_LOCAL_LLAMA_TOP_P)
        self.repeat_penalty = float(os.environ.get("NPC_LLM_LOCAL_LLAMA_REPEAT_PENALTY") or DEFAULT_LOCAL_LLAMA_REPEAT_PENALTY)
        self.num_ctx = int(os.environ.get("NPC_LLM_LOCAL_LLAMA_NUM_CTX") or DEFAULT_LOCAL_LLAMA_NUM_CTX)
        self.repair_retry_count = max(0, int(os.environ.get("NPC_LLM_LOCAL_LLAMA_REPAIR_RETRY_COUNT") or DEFAULT_LOCAL_LLAMA_REPAIR_RETRY_COUNT))
        self.disable_proxy = _env_flag("NPC_LLM_DISABLE_PROXY_LOCAL", default=True)

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        tone_mode = str(package.toneMode or "").strip().lower()
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        allow_memory_only = tone_mode == "narrative_fusion" or selected_task in {
            "chorus-line",
            "chorus-line-rewrite",
            "scene-script-pack-v2",
            "scene-chorus-batch-v2",
        }
        if not package.resolvedEvidence and not allow_memory_only:
            raise ProviderUnavailableError("local_llama:no-resolved-evidence")

        prompt = self._build_prompt(package)
        request_body = self._build_local_request(package, prompt)
        response_text = self._request_local_llama(request_body, package, prompt, repair=False)
        original_warnings: list[str] = []
        repair_used = False
        try:
            parsed, dialogue_text, used_keyword_keys, used_refs = self._parse_and_validate_local_response(response_text, package)
            original_warnings = self._quality_warnings(dialogue_text, package)
        except ProviderOutputError as exc:
            original_warnings = [str(exc)]
            if self.repair_retry_count <= 0:
                raise
            repair_used = True
            repair_prompt = self._build_repair_prompt(package, response_text, original_warnings)
            response_text = self._request_local_llama(self._build_local_request(package, repair_prompt), package, repair_prompt, repair=True)
            parsed, dialogue_text, used_keyword_keys, used_refs = self._parse_and_validate_local_response(response_text, package)
        if original_warnings and not repair_used and self.repair_retry_count > 0:
            repair_used = True
            repair_prompt = self._build_repair_prompt(package, response_text, original_warnings)
            response_text = self._request_local_llama(self._build_local_request(package, repair_prompt), package, repair_prompt, repair=True)
            parsed, dialogue_text, used_keyword_keys, used_refs = self._parse_and_validate_local_response(response_text, package)

        final_warnings = [] if self._is_structured_scene_task(package) else self._quality_warnings(dialogue_text, package)
        if final_warnings:
            raise ProviderOutputError(f"local_llama:quality:{','.join(final_warnings)}")

        quality_warnings = [f"repaired:{warning}" for warning in original_warnings[:4]] if repair_used else []
        log_debug_event(
            "provider.response.parsed",
            provider=self.name,
            model=self.model,
            usedEvidenceRefs=used_refs,
            usedKeywordKeys=used_keyword_keys,
            repairUsed=repair_used,
            qualityWarnings=quality_warnings,
            textPreview=_preview_text(dialogue_text),
        )
        return DialogueGenerationResult(
            text=dialogue_text if self._is_structured_scene_task(package) else self._compact_dialogue_text(dialogue_text, package.maxChars),
            provider=self.name,
            model=self.model,
            generationMode="local-llama-json-v2+persona-card+quality-guard",
            fallbackUsed=False,
            usedEvidenceRefs=used_refs,
            qualityWarnings=quality_warnings,
            repairUsed=repair_used,
        )

    def _build_local_request(self, package: DialoguePromptPackage, user_prompt: str) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._build_local_system_prompt(package)},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "repeat_penalty": self.repeat_penalty,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_output_tokens,
            },
        }

    def _build_local_system_prompt(self, package: DialoguePromptPackage) -> str:
        locale_instruction = LOCALE_INSTRUCTIONS.get(package.locale, LOCALE_INSTRUCTIONS[DEFAULT_LOCALE])
        speech_instruction = SPEECH_CONTEXT_INSTRUCTIONS.get(package.speechContextMode, SPEECH_CONTEXT_INSTRUCTIONS[DEFAULT_SPEECH_CONTEXT_MODE])
        memory_block = self._memory_prompt_block(package.memoryContext)
        memory_lines = []
        if memory_block is not None:
            memory_lines = [
                "playerGeneralMemory block:",
                json.dumps({"playerGeneralMemory": memory_block}, ensure_ascii=False),
            ]
        identity_guard = self._speaker_identity_guard(package)
        return "\n".join([
            "You are the strict NPC dialogue renderer for a Three Kingdoms game.",
            "Return one JSON object only. Do not include markdown or commentary.",
            f"Speaker generalId: {package.generalId}; displayName: {identity_guard['displayName']}.",
            f"Allowed first-person speaker names: {', '.join(identity_guard['allowedSelfNames'])}.",
            "Never speak as another character, even if that character is the selected keyword.",
            f"Locale: {package.locale}. {locale_instruction['instruction']}",
            f"Speech context: {package.speechContextMode}. {speech_instruction['instruction']}",
            f"Keyword angle: {speech_instruction['keywordAngle']}",
            *memory_lines,
            "Speech context must: " + "; ".join(speech_instruction["must"]),
            "Speech context must avoid: " + "; ".join(speech_instruction["avoid"]),
            "Use only the persona, keywords, context, and evidence provided by the user payload.",
            "For zh-TW: no English words, pinyin, simplified Chinese, mojibake, slash artifacts, or code-like tokens.",
            "The output must follow the requested JSON contract exactly.",
        ])

    def _request_local_llama(self, request_body: dict, package: DialoguePromptPackage, prompt: str, repair: bool) -> str:
        log_debug_event(
            "provider.request",
            provider=self.name,
            model=self.model,
            apiUrl=self.api_url,
            repair=repair,
            generalId=package.generalId,
            selectedKeywordKeys=[str(keyword.get("keywordKey") or "") for keyword in package.selectedKeywords],
            selectedKeywordLabels=[str(keyword.get("label") or "") for keyword in package.selectedKeywords],
            evidenceRefs=package.evidenceRefs,
            resolvedEvidenceRefs=[evidence.evidenceRef for evidence in package.resolvedEvidence],
            requestOptions=request_body["options"],
            promptPreview=_preview_text(prompt, 420),
        )
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _open_url(request, self.timeout_ms / 1000, disable_proxy=self.disable_proxy) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ProviderUnavailableError(f"local_llama:http-{exc.code}:{detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderUnavailableError(f"local_llama:network:{exc}") from exc

        response_text = self._extract_local_llama_text(payload)
        log_debug_event(
            "provider.response.raw",
            provider=self.name,
            model=self.model,
            repair=repair,
            payloadSummary=self._summarize_local_llama_payload(payload),
            textPreview=_preview_text(response_text),
        )
        return response_text

    def _parse_and_validate_local_response(self, response_text: str, package: DialoguePromptPackage) -> tuple[dict, str, list[str], list[str]]:
        parsed = self._parse_json_text(response_text)
        dialogue_text = self._extract_structured_scene_text(parsed, package) if self._is_structured_scene_task(package) else self._extract_dialogue_text(parsed)
        if not dialogue_text:
            raise ProviderOutputError("local_llama:empty-text")
        used_keyword_keys = self._extract_used_keyword_keys(parsed, package)
        if not self._is_structured_scene_task(package) and not self._matches_selected_keyword_focus(dialogue_text, package, used_keyword_keys):
            raise ProviderOutputError("local_llama:missing-keyword-focus")
        if not self._is_structured_scene_task(package) and self._violates_taboos(dialogue_text, package):
            raise ProviderOutputError("local_llama:taboo-violation")

        allowed_refs = {evidence.evidenceRef for evidence in package.resolvedEvidence}
        used_refs = [ref for ref in parsed.get("usedEvidenceRefs", []) if ref in allowed_refs]
        if not used_refs and package.resolvedEvidence:
            used_refs = [package.resolvedEvidence[0].evidenceRef]
        return parsed, dialogue_text, used_keyword_keys, used_refs

    def _build_repair_prompt(self, package: DialoguePromptPackage, raw_text: str, warnings: list[str]) -> str:
        selected_task = str((package.selectedContext or {}).get("task") or "").strip()
        structured_task = self._is_structured_scene_task(package)
        chorus_task = selected_task == "scene-chorus-batch-v2"
        payload = {
            "task": (
                "Repair the previous local LLM output so it follows the requested chorus JSON contract."
                if chorus_task
                else "Repair the previous local LLM output so it follows the requested scene JSON contract."
                if structured_task
                else "Repair the previous local LLM output so it becomes a valid in-character NPC dialogue JSON object."
            ),
            "blockingIssues": warnings,
            "previousOutput": raw_text[:1200],
            "repairRules": [
                "Return JSON only.",
                "Follow originalPrompt.outputContract exactly." if structured_task else "Keep the same single-line dialogue JSON contract.",
                "Keep the same speakerGeneralId, locale, speechContextMode, selectedKeywords, and resolvedEvidence intent.",
                "Fix wrong speaker identity, mixed language, gibberish artifacts, and invalid format.",
                "Do not add unsupported historical facts.",
            ],
            "originalPrompt": json.loads(self._build_prompt(package)),
        }
        if structured_task and chorus_task:
            payload["repairRules"].extend(
                [
                    "Preserve exactly one chorusLines array and keep each selected speaker entry aligned to its targetId.",
                    "Keep each chorus line a direct spoken reaction from that speaker's persona, not narration, summary, or seed dump.",
                    "Use speaker focusHint, seedHint, voiceTag, and selectedContext.sceneGrounding as the repair anchors when available.",
                    "Keep each repaired line within the chorus output shape and do not import scene-script-pack four-beat rules.",
                    "Do not replace a chorus line with a generic scene summary or fallback-style template if a speakable reaction can be recovered.",
                ]
            )
        if structured_task and not chorus_task:
            payload["repairRules"].extend(
                [
                    "Preserve the four-beat fusion so remembered fact, emotional shift, spoken line, and next action stay discernible in the repaired pack, but keep the prose natural rather than checklist-like.",
                    "Do not collapse the scene into a one-line summary, seed dump, or fallback-style template with only longer sentences.",
                    "Keep usedSceneSeeds, usedPersonaAnchors, and usedBeatFields aligned with what is actually visible in the repaired storyText.",
                ]
            )
        return json.dumps(payload, ensure_ascii=False)

    def _extract_local_llama_text(self, payload: dict) -> str:
        message = payload.get("message") or {}
        content = message.get("content")
        if not content:
            raise ProviderOutputError("local_llama:no-content")
        return str(content).strip()

    def _summarize_local_llama_payload(self, payload: dict) -> dict:
        return {
            "model": payload.get("model"),
            "done": payload.get("done"),
            "doneReason": payload.get("done_reason"),
            "evalCount": payload.get("eval_count"),
            "promptEvalCount": payload.get("prompt_eval_count"),
        }


class GeminiFlashDialogueProvider(GeminiDialogueProvider):
    name = "gemini_flash"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
        retry_count: int | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or os.environ.get("NPC_LLM_MODEL_GEMINI_FLASH") or DEFAULT_GEMINI_FLASH_MODEL,
            timeout_ms=timeout_ms,
            retry_count=retry_count,
        )


class GeminiFlashLiteDialogueProvider(GeminiDialogueProvider):
    name = "gemini_flash_lite"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
        retry_count: int | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or os.environ.get("NPC_LLM_MODEL_GEMINI_FLASH_LITE") or DEFAULT_GEMINI_FLASH_LITE_MODEL,
            timeout_ms=timeout_ms,
            retry_count=retry_count,
        )
        self.thinking_budget = int(os.environ.get("NPC_LLM_GEMINI_FLASH_LITE_THINKING_BUDGET") or DEFAULT_GEMINI_FLASH_LITE_THINKING_BUDGET)


class DeepSeekReasoningDialogueProvider(LocalLlamaDialogueProvider):
    name = "deepseek_reasoner"

    def __init__(self, api_url: str | None = None, model: str | None = None, timeout_ms: int | None = None) -> None:
        super().__init__(
            api_url=api_url,
            model=model or os.environ.get("NPC_LLM_MODEL_DEEPSEEK_REASONER") or DEFAULT_DEEPSEEK_REASONER_MODEL,
            timeout_ms=timeout_ms,
        )


class DialogueHistoryCacheProvider:
    name = "history_cache"
    structured_scene_tasks = {"scene-script-pack-v2", "scene-chorus-batch-v2"}

    def __init__(self, cache_path: str | None = None) -> None:
        self.cache_path = Path(cache_path or os.environ.get("NPC_LLM_HISTORY_CACHE_PATH") or DEFAULT_HISTORY_CACHE_PATH)

    def generate(self, package: DialoguePromptPackage) -> DialogueGenerationResult:
        if not self.cache_path.exists():
            raise ProviderUnavailableError("history_cache:empty")
        best_entry = self._find_best_entry(package)
        if best_entry is None:
            raise ProviderUnavailableError("history_cache:no-match")
        text = str(best_entry.get("text") or "").strip()
        if not text:
            raise ProviderOutputError("history_cache:empty-text")
        allowed_refs = {evidence.evidenceRef for evidence in package.resolvedEvidence}
        used_refs = [str(ref) for ref in best_entry.get("usedEvidenceRefs", []) if str(ref) in allowed_refs]
        if not used_refs:
            used_refs = [evidence.evidenceRef for evidence in package.resolvedEvidence[:1]]
        log_debug_event(
            "provider.response.history-cache",
            provider=self.name,
            matchScore=best_entry.get("_score"),
            sourceProvider=best_entry.get("provider"),
            sourceModel=best_entry.get("model"),
            keywordKeys=best_entry.get("keywordKeys"),
            textPreview=_preview_text(text),
        )
        selected_task = str((package.selectedContext or {}).get("task") or "")
        cached_text = text if selected_task in self.structured_scene_tasks else text[: package.maxChars]
        return DialogueGenerationResult(
            text=cached_text,
            provider=self.name,
            model="local-jsonl-history",
            generationMode="dialogue-history-cache-v1",
            fallbackUsed=False,
            usedEvidenceRefs=used_refs,
        )

    def _find_best_entry(self, package: DialoguePromptPackage) -> dict | None:
        selected_keyword_keys = {str(keyword.get("keywordKey") or "") for keyword in package.selectedKeywords if keyword.get("keywordKey")}
        selected_keyword_labels = {str(keyword.get("label") or "") for keyword in package.selectedKeywords if keyword.get("label")}
        evidence_refs = {evidence.evidenceRef for evidence in package.resolvedEvidence}
        selected_context_key = str((package.selectedContext or {}).get("contextKey") or "")
        selected_task = str((package.selectedContext or {}).get("task") or "")
        requested_speaker_ids = [
            str(speaker.get("targetId") or "").strip()
            for speaker in ((package.selectedContext or {}).get("speakers") or [])
            if isinstance(speaker, dict) and str(speaker.get("targetId") or "").strip()
        ]
        best_entry: dict | None = None
        best_score = 0
        for entry in self._iter_entries():
            if entry.get("generalId") != package.generalId:
                continue
            if str(entry.get("locale") or DEFAULT_LOCALE) != package.locale:
                continue
            if str(entry.get("speechContextMode") or DEFAULT_SPEECH_CONTEXT_MODE) != package.speechContextMode:
                continue
            if selected_task in self.structured_scene_tasks:
                if str(entry.get("task") or "") != selected_task:
                    continue
                if int(entry.get("cacheSchemaVersion") or 0) < 3:
                    continue
                if bool(entry.get("fallbackUsed")):
                    continue
                warnings = [str(code or "") for code in entry.get("qualityWarnings", [])]
                if any(code.endswith("_rejected") or "fallback" in code or "deprecated" in code for code in warnings):
                    continue
                if selected_context_key and str(entry.get("contextKey") or "") != selected_context_key:
                    continue
                if selected_task == "scene-chorus-batch-v2":
                    entry_speaker_ids = [str(item or "").strip() for item in entry.get("speakerIds", []) if str(item or "").strip()]
                    if requested_speaker_ids and entry_speaker_ids != requested_speaker_ids:
                        continue
            if selected_task == "chorus-line":
                if str(entry.get("task") or "") != "chorus-line":
                    continue
                if int(entry.get("cacheSchemaVersion") or 0) < 2:
                    continue
                if bool(entry.get("fallbackUsed")):
                    continue
                if any(str(code or "").endswith("_rejected") for code in entry.get("qualityWarnings", [])):
                    continue
            if selected_context_key and selected_task in {"scene-director-script", "chorus-line"}:
                if str(entry.get("contextKey") or "") != selected_context_key:
                    continue
            score = 0
            entry_keyword_keys = {str(key) for key in entry.get("keywordKeys", [])}
            entry_keyword_labels = {str(label) for label in entry.get("keywordLabels", [])}
            exact_keyword_matches = selected_keyword_keys.intersection(entry_keyword_keys)
            label_matches = selected_keyword_labels.intersection(entry_keyword_labels)
            if exact_keyword_matches:
                score += 100 * len(exact_keyword_matches)
            if label_matches:
                score += 70 * len(label_matches)
            if selected_context_key and selected_context_key == entry.get("contextKey"):
                score += 20
            score += 5 * len(evidence_refs.intersection(str(ref) for ref in entry.get("evidenceRefs", [])))
            score += 10 * len(evidence_refs.intersection(str(ref) for ref in entry.get("usedEvidenceRefs", [])))
            if score > best_score:
                best_score = score
                best_entry = {**entry, "_score": score}
        if best_score <= 0:
            return None
        return best_entry

    def _iter_entries(self):
        try:
            lines = self.cache_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in reversed(lines[-500:]):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


class DialogueProviderRouter:
    def __init__(self, provider_order: list[str] | None = None) -> None:
        self.provider_order = provider_order or self._read_provider_order()

    def generate(
        self,
        package: DialoguePromptPackage,
        provider_order: list[str] | None = None,
        model_overrides: dict[str, str] | None = None,
        allow_deterministic_fallback: bool = True,
    ) -> DialogueGenerationResult:
        trace: list[str] = []
        active_provider_order = provider_order or self.provider_order
        for provider_name in active_provider_order:
            try:
                provider = self._create_provider(provider_name, model_overrides or {})
                result = provider.generate(package)
                return DialogueGenerationResult(
                    text=result.text,
                    provider=result.provider,
                    model=result.model,
                    generationMode=result.generationMode,
                    fallbackUsed=result.fallbackUsed,
                    providerTrace=[*trace, f"{provider.name}:ok"],
                    usedEvidenceRefs=result.usedEvidenceRefs,
                    qualityWarnings=result.qualityWarnings,
                    repairUsed=result.repairUsed,
                )
            except (ProviderUnavailableError, ProviderOutputError) as exc:
                trace.append(str(exc))
                continue
        if not allow_deterministic_fallback:
            raise ProviderUnavailableError(f"provider-chain-failed:{' > '.join(trace) or 'no-provider-succeeded'}")
        deterministic = DeterministicTemplateProvider().generate(package)
        return DialogueGenerationResult(
            text=deterministic.text,
            provider=deterministic.provider,
            model=deterministic.model,
            generationMode=deterministic.generationMode,
            fallbackUsed=True,
            providerTrace=[*trace, "deterministic:ok"],
            usedEvidenceRefs=deterministic.usedEvidenceRefs,
            qualityWarnings=deterministic.qualityWarnings,
            repairUsed=deterministic.repairUsed,
        )

    def _read_provider_order(self) -> list[str]:
        raw = os.environ.get("NPC_LLM_PROVIDER_ORDER") or "deterministic"
        providers = [part.strip() for part in raw.split(",") if part.strip()]
        return providers or ["deterministic"]

    def _create_provider(self, provider_name: str, model_overrides: dict[str, str] | None = None) -> DialogueProvider:
        model_overrides = model_overrides or {}
        timeout_ms = int(model_overrides["__timeoutMs"]) if str(model_overrides.get("__timeoutMs") or "").isdigit() else None
        retry_count = int(model_overrides["__retryCount"]) if str(model_overrides.get("__retryCount") or "").isdigit() else None
        if provider_name == "gemini":
            return GeminiDialogueProvider(model=model_overrides.get("gemini"), timeout_ms=timeout_ms, retry_count=retry_count)
        if provider_name == "gemini_flash":
            return GeminiFlashDialogueProvider(model=model_overrides.get("gemini_flash"), timeout_ms=timeout_ms, retry_count=retry_count)
        if provider_name == "gemini_flash_lite":
            return GeminiFlashLiteDialogueProvider(model=model_overrides.get("gemini_flash_lite"), timeout_ms=timeout_ms, retry_count=retry_count)
        if provider_name == "local_llama":
            return LocalLlamaDialogueProvider(model=model_overrides.get("local_llama"))
        if provider_name == "deepseek_reasoner":
            return DeepSeekReasoningDialogueProvider(model=model_overrides.get("deepseek_reasoner"))
        if provider_name == "history_cache":
            return DialogueHistoryCacheProvider()
        if provider_name == "mock":
            return MockDialogueProvider()
        if provider_name == "deterministic":
            return DeterministicTemplateProvider()
        raise ProviderUnavailableError(f"{provider_name}:unsupported-provider")


def load_local_env(repo_root) -> None:
    for path in [repo_root / ".env"]:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
