from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from .main import create_app


def main() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
    os.environ["NPC_BRAIN_DEPLOY_API_KEY"] = "smoke-test-api-key"
    client = TestClient(create_app())
    repo_root = Path(__file__).resolve().parents[1]
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    health = client.get("/healthz")
    assert health.status_code == 200, health.text
    assert health.json()["ok"] is True
    assert health.json()["dataVersion"] == git_sha, health.json()
    assert health.json()["deployment"]["dataVersion"] == git_sha, health.json()
    service_headers = {"X-API-Key": os.environ["NPC_BRAIN_DEPLOY_API_KEY"]}
    supported_presets = health.json()["llm"]["supportedModelPresets"]
    supported_by_key = {preset["preset"]: preset for preset in supported_presets}
    assert "qwen2_5_7b" in supported_by_key, supported_presets
    assert supported_by_key["qwen2_5_7b"]["providerOrder"] == ["local_llama"], supported_by_key["qwen2_5_7b"]
    assert "deepseek_r1_7b" in supported_by_key, supported_presets
    assert supported_by_key["deepseek_r1_7b"]["providerOrder"] == ["deepseek_reasoner"], supported_by_key["deepseek_r1_7b"]

    contexts = client.get("/v1/npc/context-options", params={"generalId": "zhang-fei"}, headers=service_headers)
    assert contexts.status_code == 200, contexts.text
    assert contexts.json()["options"], "context options should not be empty"

    keywords = client.get("/v1/npc/keyword-options", params={"generalId": "zhang-fei"}, headers=service_headers)
    assert keywords.status_code == 200, keywords.text
    keyword_payload = keywords.json()
    assert keyword_payload["categories"].get("event"), "event keyword options should not be empty"
    selected_keyword_keys = [
        item["keywordKey"]
        for items in keyword_payload["categories"].values()
        for item in items[:1]
    ][:3]
    assert selected_keyword_keys, "runtime keyword options should contain selectable keys"

    dialogue = client.post(
        "/v1/npc/dialogue",
        json={
            "generalId": "zhang-fei",
            "contextKey": "changban-bridge",
            "selectedKeywordKeys": selected_keyword_keys + ["unknown-key"],
            "maxChars": 90,
        },
    )
    assert dialogue.status_code == 200, dialogue.text
    payload = dialogue.json()
    assert payload["evidenceRefs"], "dialogue response should include evidence refs"
    assert payload["llmModelPreset"] == "fallback_chain", "dialogue response should echo default model preset"
    assert payload["rejectedKeywordKeys"] == ["unknown-key"], "unknown keyword should be rejected explicitly"

    # v2 台詞請求缺參數要回 422 並列出缺欄位，不可退回固定句硬生成
    incomplete = client.post(
        "/v1/npc/dialogue",
        json={
            "generalId": "zhang-fei",
            "schemaVersion": 2,
            "locale": "zh-TW",
            "mood": "joy",
            "relationBucket": 0,
            "worldPhase": "early",
            "interaction": {"type": "praised"},
            "scene": "lobby",
        },
    )
    assert incomplete.status_code == 422, incomplete.text
    assert incomplete.json()["detail"]["missingFields"] == ["personality", "contextKey"], incomplete.text

    # interaction.targetKind=general 但缺 targetId：422 並列出 interaction.targetId
    general_no_target = client.post(
        "/v1/npc/dialogue",
        json={
            "generalId": "zhang-fei",
            "contextKey": "lobby-idle",
            "schemaVersion": 2,
            "locale": "zh-TW",
            "personality": "Brave",
            "mood": "joy",
            "relationBucket": 3,
            "worldPhase": "chaos",
            "interaction": {"type": "daily_chat", "targetKind": "general", "targetName": "劉備"},
            "scene": "lobby",
        },
    )
    assert general_no_target.status_code == 422, general_no_target.text
    assert general_no_target.json()["detail"]["missingFields"] == ["interaction.targetId"], general_no_target.text
    # 補上 targetId 後正常回 200；player 對象（缺省）不需要 targetId
    for interaction in (
        {"type": "daily_chat", "targetKind": "general", "targetId": "liu-bei", "targetName": "劉備"},
        {"type": "daily_chat"},
    ):
        ok = client.post(
            "/v1/npc/dialogue",
            json={
                "generalId": "zhang-fei",
                "contextKey": "lobby-idle",
                "schemaVersion": 2,
                "locale": "zh-TW",
                "personality": "Brave",
                "mood": "joy",
                "relationBucket": 3,
                "worldPhase": "chaos",
                "interaction": interaction,
                "scene": "lobby",
            },
        )
        assert ok.status_code == 200, ok.text

    # 日常情境登錄表要列在情境選項裡，所有武將共用
    context_keys = {option["contextKey"] for option in contexts.json()["options"]}
    assert {"lobby-idle", "praised", "gift-received", "pre-battle"} <= context_keys, sorted(context_keys)

    print("[npc-brain-http-smoke] PASS")
    print(f"[npc-brain-http-smoke] contexts={len(contexts.json()['options'])} categories={len(keywords.json()['categories'])} evidenceRefs={len(payload['evidenceRefs'])} presets={len(supported_presets)}")


if __name__ == "__main__":
    main()