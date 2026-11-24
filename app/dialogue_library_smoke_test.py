"""Mock-only smoke tests for the dialogue library (phase 1). No real Anthropic calls."""

from __future__ import annotations

import copy
import importlib.util
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
    "NPC_DIALOGUE_DB_PATH",
    "NPC_DIALOGUE_LIBRARY_ENABLED",
    "NPC_LLM_HISTORY_CACHE_PATH",
    "NPC_ADMIN_TOKEN",
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
        context_tag="changban",
    )
    base.update(overrides)
    return base


def _test_bucket_keys() -> None:
    key_a = compute_cache_key(normalize_buckets(**_bucket_kwargs()))
    key_b = compute_cache_key(normalize_buckets(**_bucket_kwargs()))
    assert key_a == key_b and len(key_a) == 24, (key_a, key_b)
    # 舊 player 請求的 key 一字不變（修改前用原程式算出的寫死值；BUCKET_VERSION 維持 3）
    assert key_a == "d852dd4e57b1e74f4fe37777", key_a
    assert normalize_buckets(**_bucket_kwargs(target_id="")).as_dict() == normalize_buckets(**_bucket_kwargs()).as_dict()
    assert "targetId" not in normalize_buckets(**_bucket_kwargs()).as_dict()
    # 對象為其他武將：key 與 player 不同，且不同 targetId 不同
    key_g1 = compute_cache_key(normalize_buckets(**_bucket_kwargs(target_id="liu-bei")))
    key_g2 = compute_cache_key(normalize_buckets(**_bucket_kwargs(target_id="guan-yu")))
    assert len({key_a, key_g1, key_g2}) == 3, (key_a, key_g1, key_g2)
    assert compute_cache_key(normalize_buckets(**_bucket_kwargs(target_id="liu-bei"))) == key_g1
    assert compute_cache_key(normalize_buckets(**_bucket_kwargs(mood="joy"))) != key_a
    assert compute_cache_key(normalize_buckets(**_bucket_kwargs(general_id="guan-yu"))) != key_a
    # Out-of-range and unknown values are clamped or defaulted, never rejected.
    clamped = normalize_buckets(**_bucket_kwargs(relation_bucket=9, mood="rage", interaction_type="weird"))
    assert clamped.relationBucket == 4 and clamped.mood == "calm" and clamped.interactionType == "other", clamped
    assert normalize_buckets(**_bucket_kwargs(relation_bucket=-3)).relationBucket == 0
    assert normalize_buckets(**_bucket_kwargs(personality=None)).personality == "unset"
    # 情緒 10 種由資料檔決定，新增的四種要有自己的分桶與中文說明
    from .dialogue_library import MOODS, MOOD_DESCRIPTIONS
    assert len(MOODS) == 10 and {"shy", "jealous", "grateful", "wronged"} <= set(MOODS), MOODS
    assert all(MOOD_DESCRIPTIONS[mood] for mood in MOODS), MOOD_DESCRIPTIONS
    assert normalize_buckets(**_bucket_kwargs(mood="shy")).mood == "shy"
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

    db_path = Path(os.environ["NPC_DIALOGUE_DB_PATH"])
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
            "text": f"曹仁小兒，長坂橋前有種便來！第{index}回。",
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

        # 5b) relationDescription / addressee 真的進提示詞；general 對象用自己的 key，不污染 player 的 key。
        player_system = bodies[0]["system"]
        assert "Relation to addressee: " in player_system and "Addressee: 玩家（主公）" in player_system, player_system[:600]
        assert '"relationDescription"' in player_system, player_system[:600]
        general_interaction = {"type": "battle_victory", "eventId": "changban", "targetKind": "general", "targetId": "liu-bei", "targetName": "劉備"}
        service.build_dialogue(_v2_request(interaction=general_interaction))
        general_system = bodies[-1]["system"]
        assert "Addressee: 另一位武將 劉備，不是玩家，用對同僚的口吻" in general_system, general_system[:600]
        assert "Relation to addressee: " in general_system
        general_key = compute_cache_key(normalize_buckets(**_bucket_kwargs(target_id="liu-bei")))
        assert DialogueLibrary(db_path).count_active(general_key) == 1
        assert DialogueLibrary(db_path).count_active(key) == MAX_LINES_PER_KEY
        # general 但缺 targetId 視為缺欄位
        missing = service._missing_dialogue_library_fields(_v2_request(interaction={"type": "battle_victory", "eventId": "changban", "targetKind": "general"}))
        assert missing == ["interaction.targetId"], missing
        assert service._missing_dialogue_library_fields(_v2_request()) == []
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
    from .dialogue_library import RELATION_DESCRIPTIONS
    assert sorted(RELATION_DESCRIPTIONS) == [0, 1, 2, 3, 4] and all(RELATION_DESCRIPTIONS.values()), RELATION_DESCRIPTIONS
    print("[dialogue-library-smoke] service_v2_flow PASS")


def _admin_client(db_path: Path, token: str | None):
    from fastapi.testclient import TestClient

    from .main import create_app

    os.environ["NPC_DIALOGUE_DB_PATH"] = str(db_path)
    if token is None:
        os.environ.pop("NPC_ADMIN_TOKEN", None)
    else:
        os.environ["NPC_ADMIN_TOKEN"] = token
    return TestClient(create_app())


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _test_admin_endpoints(tmp: Path) -> dict:
    token = "mock-admin-token-0001"
    source_db = tmp / "admin-source.sqlite"
    target_db = tmp / "admin-target.sqlite"
    empty_db = tmp / "admin-empty.sqlite"

    # Seed the source library: two keys, one with an overflow-retired line and one served line.
    source = DialogueLibrary(source_db)
    buckets_a = normalize_buckets(**_bucket_kwargs())
    key_a = compute_cache_key(buckets_a)
    for index in range(MAX_LINES_PER_KEY + 1):
        source.add(key_a, buckets_a, f"admin-line-{index}", model="claude-haiku-5-5", prompt_version="v2",
                   quality_score=0.5 + index / 10, quality_flags=["ok"], extra={"n": index})
    source.pick(key_a)
    buckets_b = normalize_buckets(**_bucket_kwargs(mood="joy"))
    key_b = compute_cache_key(buckets_b)
    source.add(key_b, buckets_b, "joy-line", model=None, prompt_version=None, quality_score=None)

    # 1) Auth gate: 403 when unconfigured, 401 when missing, 403 when wrong.
    unconfigured = _admin_client(source_db, None)
    assert unconfigured.get("/v1/admin/dialogue-library/export", headers=_admin_headers(token)).status_code == 403
    configured = _admin_client(source_db, token)
    assert configured.get("/v1/admin/dialogue-library/export").status_code == 401
    assert configured.get("/v1/admin/dialogue-library/export", headers=_admin_headers("wrong")).status_code == 403
    assert configured.post("/v1/admin/dialogue-library/import", json={"lines": []}).status_code == 401
    assert configured.post("/v1/admin/dialogue-library/import", json={"lines": []},
                           headers=_admin_headers("wrong")).status_code == 403
    # X-API-Key must not be accepted for admin endpoints.
    assert configured.get("/v1/admin/dialogue-library/export", headers={"X-API-Key": token}).status_code == 401
    print("[dialogue-library-smoke] admin_auth PASS")

    # 2) Export is complete (active + retired) and carries every documented field.
    exported = configured.get("/v1/admin/dialogue-library/export", headers=_admin_headers(token))
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["format"] == "3klife-dialogue-library" and payload["count"] == len(payload["lines"]) == 7, payload["count"]
    statuses = sorted(line["status"] for line in payload["lines"])
    assert statuses.count("retired") == 1 and statuses.count("active") == 6, statuses
    served = [line for line in payload["lines"] if line["servedCount"] == 1]
    assert len(served) == 1 and served[0]["lastServedAt"], served
    required = {"key", "generalId", "bucket", "textZhTw", "textEn", "textJa", "textZhCn", "model", "createdAt",
                "promptVersion", "qualityScore", "qualityFlags", "extra", "status", "servedCount", "lastServedAt"}
    assert all(required <= set(line) for line in payload["lines"]), payload["lines"][0].keys()

    # 3) Round trip into an empty library yields identical lines.
    empty_client = _admin_client(empty_db, token)
    summary = empty_client.post("/v1/admin/dialogue-library/import", json=payload,
                                headers=_admin_headers(token)).json()
    assert summary["inserted"] == 7 and summary["updated"] == 0 and summary["skipped"] == 0, summary
    round_trip = empty_client.get("/v1/admin/dialogue-library/export", headers=_admin_headers(token)).json()
    assert round_trip["lines"] == payload["lines"], "round trip mismatch"
    print("[dialogue-library-smoke] export_import_round_trip PASS")

    # 4) Re-importing the same backup is idempotent: nothing duplicated, everything skipped.
    again = empty_client.post("/v1/admin/dialogue-library/import", json=payload,
                              headers=_admin_headers(token)).json()
    assert again["inserted"] == 0 and again["skipped"] == 7, again
    assert empty_client.get("/v1/admin/dialogue-library/export",
                            headers=_admin_headers(token)).json()["count"] == 7
    print("[dialogue-library-smoke] reimport_idempotent PASS")

    # 5) Newer local rows are not overwritten; older local rows are updated by a newer backup.
    stale_backup = copy.deepcopy(payload)
    stale_backup["lines"][0]["createdAt"] = "2000-01-01T00:00:00+00:00"
    stale_backup["lines"][0]["textEn"] = "should-not-apply"
    stale_backup["lines"][1]["createdAt"] = "1999-01-01T00:00:00+00:00"
    update_summary = empty_client.post("/v1/admin/dialogue-library/import", json=stale_backup,
                                       headers=_admin_headers(token)).json()
    assert update_summary["skipped"] == 7 and update_summary["updated"] == 0, update_summary
    # Local rows dated older than the backup are updated by it.
    older_local_db = tmp / "admin-older.sqlite"
    older_client = _admin_client(older_local_db, token)
    seeded = copy.deepcopy(payload)
    for line in seeded["lines"]:
        line["createdAt"] = "1990-01-01T00:00:00+00:00"
    older_client.post("/v1/admin/dialogue-library/import", json=seeded, headers=_admin_headers(token))
    update_summary = older_client.post("/v1/admin/dialogue-library/import", json=payload,
                                       headers=_admin_headers(token)).json()
    assert update_summary["updated"] == 7 and update_summary["skipped"] == 0, update_summary
    print("[dialogue-library-smoke] newer_not_overwritten PASS")

    # 6) Dry-run reports counts but writes nothing.
    dry_db = tmp / "admin-dry.sqlite"
    dry_client = _admin_client(dry_db, token)
    dry = dry_client.post("/v1/admin/dialogue-library/import?dryRun=true", json=payload,
                          headers=_admin_headers(token)).json()
    assert dry["dryRun"] is True and dry["inserted"] == 7, dry
    assert dry_client.get("/v1/admin/dialogue-library/export", headers=_admin_headers(token)).json()["count"] == 0
    print("[dialogue-library-smoke] dry_run PASS")

    # 7) Capacity: an incoming backup cannot push a key above MAX_LINES_PER_KEY active lines.
    cap_db = tmp / "admin-cap.sqlite"
    cap_client = _admin_client(cap_db, token)
    overflow = {"format": "3klife-dialogue-library", "version": 1, "lines": [
        {"key": key_a, "generalId": "zhang-fei", "textZhTw": f"cap-{i}", "createdAt": "2026-01-01T00:00:00+00:00"}
        for i in range(MAX_LINES_PER_KEY + 1)
    ]}
    cap = cap_client.post("/v1/admin/dialogue-library/import", json=overflow, headers=_admin_headers(token)).json()
    assert cap["inserted"] == MAX_LINES_PER_KEY + 1 and cap["cappedToRetired"] == 1, cap
    assert DialogueLibrary(cap_db).count_active(key_a) == MAX_LINES_PER_KEY
    print("[dialogue-library-smoke] import_capacity PASS")

    # 8) Malformed payloads are rejected with 400 and write nothing.
    bad = cap_client.post("/v1/admin/dialogue-library/import",
                          json={"format": "3klife-dialogue-library", "lines": [{"key": "", "textZhTw": "x"}]},
                          headers=_admin_headers(token))
    assert bad.status_code == 400, bad.text
    not_json = cap_client.post("/v1/admin/dialogue-library/import", content=b"not json",
                               headers={**_admin_headers(token), "Content-Type": "application/json"})
    assert not_json.status_code == 400, not_json.text
    print("[dialogue-library-smoke] import_validation PASS")
    return payload


class _FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _test_backup_script(tmp: Path, payload: dict) -> None:
    spec = importlib.util.spec_from_file_location(
        "backup_dialogue_library_under_test",
        Path(__file__).resolve().parents[1] / "tools" / "backup_dialogue_library.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    token = "mock-admin-token-script-9"
    seen: list[tuple[str, str, str | None]] = []

    def fake_opener(request, timeout=None):
        seen.append((request.get_method(), request.full_url, request.get_header("Authorization")))
        assert request.get_header("Authorization") == f"Bearer {token}"
        return _FakeHttpResponse(json.dumps(payload).encode("utf-8"))

    out_dir = tmp / "backup-out"
    target, count = module.backup(out_dir, "https://example.test", token, opener=fake_opener)
    assert count == len(payload["lines"]) and target.parent == out_dir and target.name.startswith("dialogue-library-")
    assert token not in target.read_text(encoding="utf-8"), "token leaked into backup file"
    assert json.loads(target.read_text(encoding="utf-8")) == payload

    summary = module.restore(target, "https://target.example.test", token, dry_run=True, opener=fake_opener)
    assert isinstance(summary, dict)
    assert seen[-1][0] == "POST" and seen[-1][1].endswith("/v1/admin/dialogue-library/import?dryRun=true"), seen[-1]

    try:
        module.post_import("http://remote.example.test", token, payload, opener=fake_opener)
    except module.AdminRequestError:
        pass
    else:
        raise AssertionError("plain http to a non-local host must be refused")
    os.environ.pop("NPC_ADMIN_TOKEN", None)
    try:
        module.require_token("NPC_ADMIN_TOKEN")
    except module.AdminRequestError:
        pass
    else:
        raise AssertionError("missing token env must fail")
    print("[dialogue-library-smoke] backup_script_mock PASS")


def run() -> None:
    original_env = {key: os.environ.get(key) for key in _LIBRARY_ENV_KEYS}
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dialogue-library.sqlite"
        try:
            os.environ["SANGUO_ANTHROPIC_API_KEY"] = _MOCK_ANTHROPIC_KEY
            os.environ["NPC_LLM_PROVIDER_ORDER"] = "anthropic,deterministic"
            os.environ["NPC_LLM_MODEL_ANTHROPIC"] = "claude-haiku-5-5"
            os.environ["NPC_DIALOGUE_DB_PATH"] = str(db_path)
            os.environ["NPC_DIALOGUE_LIBRARY_ENABLED"] = "1"
            os.environ["NPC_LLM_HISTORY_CACHE_PATH"] = str(Path(tmp) / "history.jsonl")
            _test_bucket_keys()
            _test_temperature_gate()
            _test_truncation_fallback()
            _test_library_rotation_and_cap(Path(tmp) / "rotation.sqlite")
            _test_service_flow(db_path)
            payload = _test_admin_endpoints(Path(tmp))
            _test_backup_script(Path(tmp), payload)
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    print("[dialogue-library-smoke] PASS")


if __name__ == "__main__":
    run()
