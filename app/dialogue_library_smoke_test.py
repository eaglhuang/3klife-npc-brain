"""Mock-only smoke tests for the dialogue library (phase 1). No real Anthropic calls."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
from collections import Counter
from pathlib import Path

from . import llm_dialogue_renderer as renderer
from .llm_dialogue_renderer import DialoguePromptPackage
from .dialogue_library import MAX_LINES_PER_KEY, DialogueLibrary, compute_cache_key, normalize_buckets
from .llm_provider_smoke_test import _FakeAnthropicResponse, _MOCK_ANTHROPIC_KEY, _patch_anthropic_open
from .npc_dialogue_service import DialogueRequest, NpcDialogueService

_LIBRARY_ENV_KEYS = [
    "SANGUO_ANTHROPIC_API_KEY",
    "NPC_LLM_PROVIDER_ORDER",
    "NPC_LLM_MODEL_ANTHROPIC",
    "NPC_DIALOGUE_LIBRARY_DB_PATH",
    "NPC_DIALOGUE_LIBRARY_ENABLED",
    "NPC_LLM_HISTORY_CACHE_PATH",
]


def _bucket_kwargs(**overrides):
    base = dict(
        general_id="zhang-fei",
        personality="Brave",
        mood="anger",
        relation_bucket=3,
        world_phase="crisis",
        interaction_type="battle_victory",
        scene="battlefield",
    )
    base.update(overrides)
    return base


def _test_bucket_keys() -> None:
    key_a = compute_cache_key(normalize_buckets(**_bucket_kwargs()))
    key_b = compute_cache_key(normalize_buckets(**_bucket_kwargs()))
    assert key_a == key_b and len(key_a) == 24, (key_a, key_b)
    assert compute_cache_key(normalize_buckets(**_bucket_kwargs(mood="joy"))) != key_a
    assert compute_cache_key(normalize_buckets(**_bucket_kwargs(general_id="guan-yu"))) != key_a
    # Out-of-range and unknown values are clamped or defaulted, never rejected.
    clamped = normalize_buckets(**_bucket_kwargs(relation_bucket=9, mood="rage", interaction_type="weird"))
    assert clamped.relationBucket == 4 and clamped.mood == "calm" and clamped.interactionType == "other", clamped
    assert normalize_buckets(**_bucket_kwargs(relation_bucket=-3)).relationBucket == 0
    assert normalize_buckets(**_bucket_kwargs(personality=None)).personality == "unset"
    print("[dialogue-library-smoke] bucket_keys PASS")


def _test_library_rotation_and_cap(db_path: Path) -> None:
    library = DialogueLibrary(db_path)
    buckets = normalize_buckets(**_bucket_kwargs())
    key = compute_cache_key(buckets)
    assert library.pick(key) is None, "empty key must miss"
    texts = [f"line-{index}" for index in range(MAX_LINES_PER_KEY)]
    for text in texts:
        assert library.add(key, buckets, text, model="claude-haiku-5-5", prompt_version="v", quality_score=None) is not None
    assert library.count_active(key) == MAX_LINES_PER_KEY
    # Full key and no quality score: the new line must not displace existing ones.
    assert library.add(key, buckets, "overflow", model="m", prompt_version="v", quality_score=None) is None
    assert library.count_active(key) == MAX_LINES_PER_KEY

    served = [library.pick(key).text for _ in range(MAX_LINES_PER_KEY * 2)]
    counts = Counter(served)
    assert set(counts) == set(texts) and all(count == 2 for count in counts.values()), counts

    # A higher quality line replaces the lowest-scored (here: unscored) line.
    assert library.add(key, buckets, "better", model="m", prompt_version="v", quality_score=0.9) is not None
    assert library.count_active(key) == MAX_LINES_PER_KEY
    assert "better" in {library.pick(key).text for _ in range(MAX_LINES_PER_KEY)}
    print("[dialogue-library-smoke] rotation_and_cap PASS")


def _test_temperature_gate() -> None:
    haiku = renderer.AnthropicDialogueProvider(api_key="mock", model="claude-haiku-5-5")
    sonnet = renderer.AnthropicDialogueProvider(api_key="mock", model="claude-sonnet-4-5")
    assert haiku._is_haiku5() is True and sonnet._is_haiku5() is False
    package = DialoguePromptPackage(
        generalId="zhang-fei",
        personaCardSubset={},
        memoryContext=None,
        selectedContext=None,
        selectedKeywords=[],
        resolvedEvidence=[],
        evidenceRefs=[],
        deterministicText="",
        maxChars=90,
        toneMode="in-character",
    )
    haiku_body = haiku._build_local_request(package, "hi")
    assert "temperature" not in haiku_body and haiku_body["thinking"] == {"type": "disabled"}, haiku_body.keys()
    sonnet_body = sonnet._build_local_request(package, "hi")
    assert "thinking" not in sonnet_body and sonnet_body["temperature"] == sonnet.temperature, sonnet_body.keys()
    print("[dialogue-library-smoke] temperature_gate PASS")


def _test_truncation_fallback() -> None:
    """A max_tokens stop must fall back to the next provider and must not be stored in the library."""
    def truncated(request):
        return _FakeAnthropicResponse(
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": _anthropic_text(7)}],
                "stop_reason": "max_tokens",
            }
        )

    db_path = Path(os.environ["NPC_DIALOGUE_LIBRARY_DB_PATH"])
    key = compute_cache_key(normalize_buckets(**_bucket_kwargs(mood="fear")))
    calls, original = _patch_anthropic_open(truncated)
    try:
        response = NpcDialogueService().build_dialogue(_v2_request(mood="fear"))
    finally:
        renderer._open_url = original
    assert response.provider != "anthropic", response.providerTrace
    assert response.providerTrace[0] == "anthropic:truncated", response.providerTrace
    assert len(calls) == 1
    assert DialogueLibrary(db_path).count_active(key) == 0
    print("[dialogue-library-smoke] truncation_fallback PASS")


def _anthropic_text(index: int) -> str:
    return json.dumps(
        {
            "text": f"曹仁小兒，長坂橋前有種便來！第{index}回",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        },
        ensure_ascii=False,
    )


def _v2_request(**overrides) -> DialogueRequest:
    base = dict(
        generalId="zhang-fei",
        contextKey="changban-bridge",
        selectedKeywordKeys=["cao-ren"],
        locale="zh-TW",
        speechContextMode="encounter_speech",
        maxChars=90,
        schemaVersion=2,
        personality="Brave",
        mood="anger",
        relationBucket=3,
        worldPhase="crisis",
        interaction={"type": "battle_victory", "eventId": "changban"},
        scene="battlefield",
    )
    base.update(overrides)
    return DialogueRequest(**base)


def _test_service_flow(db_path: Path) -> None:
    counter = {"n": 0}
    failure = {"on": False}
    bodies: list[dict] = []

    def responder(request):
        counter["n"] += 1
        body = json.loads(request.data.decode("utf-8"))
        bodies.append(body)
        if failure["on"]:
            raise OSError("mock network failure")
        return _FakeAnthropicResponse(
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": _anthropic_text(counter["n"])}],
                "stop_reason": "end_turn",
            }
        )

    calls, original = _patch_anthropic_open(responder)
    try:
        service = NpcDialogueService()

        # 1) First five v2 requests call AI and store each line.
        for index in range(MAX_LINES_PER_KEY):
            response = service.build_dialogue(_v2_request())
            assert response.provider == "anthropic", response.providerTrace
        assert len(calls) == MAX_LINES_PER_KEY, len(calls)
        state_system = bodies[0]["system"]
        assert '"mood": "anger"' in state_system and '"personality": "Brave"' in state_system, state_system[:400]
        key = compute_cache_key(normalize_buckets(**_bucket_kwargs()))
        assert DialogueLibrary(db_path).count_active(key) == MAX_LINES_PER_KEY

        # 2) Sixth v2 request is served from the full key with no AI call.
        response = service.build_dialogue(_v2_request())
        assert response.provider == "dialogue_library", response.providerTrace
        assert response.providerTrace == ["dialogue_library:ok"], response.providerTrace
        assert response.text.startswith("曹仁小兒"), response.text
        assert len(calls) == MAX_LINES_PER_KEY, f"library hit must not call AI, calls={len(calls)}"

        # 3) v1 request (no schemaVersion) keeps the old path: AI call, no library read or write.
        v1 = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                maxChars=90,
            )
        )
        assert v1.provider == "anthropic", v1.providerTrace
        assert len(calls) == MAX_LINES_PER_KEY + 1
        assert "stateContext" not in bodies[-1]["system"] and "Current state" not in bodies[-1]["system"]
        assert DialogueLibrary(db_path).count_active(key) == MAX_LINES_PER_KEY

        # 4) Non-zh-TW locale never touches the library.
        service.build_dialogue(_v2_request(locale="en"))
        assert len(calls) == MAX_LINES_PER_KEY + 2
        assert DialogueLibrary(db_path).count_active(key) == MAX_LINES_PER_KEY

        # 5) Failed AI call is not stored; the response falls back and the key stays empty for that bucket.
        sorrow_key = compute_cache_key(normalize_buckets(**_bucket_kwargs(mood="sorrow")))
        before = len(calls)
        failure["on"] = True
        failing = service.build_dialogue(_v2_request(mood="sorrow"))
        failure["on"] = False
        assert failing.provider != "anthropic" or failing.fallbackUsed, failing.providerTrace
        assert DialogueLibrary(db_path).count_active(sorrow_key) == 0
        assert len(calls) == before + 1, (before, len(calls))
    finally:
        renderer._open_url = original

    # 6) Kill switch: NPC_DIALOGUE_LIBRARY_ENABLED=0 disables read and write.
    os.environ["NPC_DIALOGUE_LIBRARY_ENABLED"] = "0"
    calls2, original2 = _patch_anthropic_open(
        lambda request: _FakeAnthropicResponse(
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": _anthropic_text(99)}],
                "stop_reason": "end_turn",
            }
        )
    )
    try:
        disabled = NpcDialogueService().build_dialogue(_v2_request())
        assert disabled.provider == "anthropic", disabled.providerTrace
        assert len(calls2) == 1, "kill switch must force AI call even when key is full"
    finally:
        renderer._open_url = original2
    print("[dialogue-library-smoke] service_v2_flow PASS")


def run() -> None:
    original_env = {key: os.environ.get(key) for key in _LIBRARY_ENV_KEYS}
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dialogue-library.sqlite"
        try:
            os.environ["SANGUO_ANTHROPIC_API_KEY"] = _MOCK_ANTHROPIC_KEY
            os.environ["NPC_LLM_PROVIDER_ORDER"] = "anthropic,deterministic"
            os.environ["NPC_LLM_MODEL_ANTHROPIC"] = "claude-haiku-5-5"
            os.environ["NPC_DIALOGUE_LIBRARY_DB_PATH"] = str(db_path)
            os.environ["NPC_DIALOGUE_LIBRARY_ENABLED"] = "1"
            os.environ["NPC_LLM_HISTORY_CACHE_PATH"] = str(Path(tmp) / "history.jsonl")
            _test_bucket_keys()
            _test_temperature_gate()
            _test_truncation_fallback()
            _test_library_rotation_and_cap(Path(tmp) / "rotation.sqlite")
            _test_service_flow(db_path)
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    print("[dialogue-library-smoke] PASS")


if __name__ == "__main__":
    run()
