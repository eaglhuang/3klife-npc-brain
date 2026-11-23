from __future__ import annotations

import io
import json
import os
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import llm_dialogue_renderer as renderer
from .llm_dialogue_renderer import AnthropicDialogueProvider, GeminiDialogueProvider, ProviderUnavailableError
from .npc_dialogue_service import DialogueRequest, NpcDialogueService


_ENV_KEYS = [
    "NPC_LLM_PROVIDER_ORDER",
    "NPC_LLM_MOCK_TEXT",
    "NPC_LLM_LOCAL_LLAMA_API_URL",
    "NPC_LLM_MODEL_LOCAL_LLAMA",
    "NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS",
    "NPC_LLM_LOCAL_LLAMA_MAX_OUTPUT_TOKENS",
    "NPC_LLM_LOCAL_LLAMA_TEMPERATURE",
    "NPC_LLM_LOCAL_LLAMA_TOP_P",
    "NPC_LLM_LOCAL_LLAMA_REPEAT_PENALTY",
    "NPC_LLM_LOCAL_LLAMA_NUM_CTX",
    "NPC_LLM_LOCAL_LLAMA_REPAIR_RETRY_COUNT",
    "NPC_LLM_HISTORY_CACHE_PATH",
    "NPC_SCENE_CACHE_ENABLED",
    "SANGUO_ANTHROPIC_API_KEY",
    "NPC_LLM_MODEL_ANTHROPIC",
]

_MOCK_ANTHROPIC_KEY = "mock-anthropic-key-not-real"


class _FakeAnthropicResponse:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _patch_anthropic_open(responder):
    """Swap the renderer HTTP opener so no real Anthropic call is made. Returns (recorded_requests, original_opener)."""
    calls: list = []
    original = renderer._open_url

    def fake_open(request, timeout_seconds, *, disable_proxy):
        calls.append(request)
        return responder(request)

    renderer._open_url = fake_open
    return calls, original


def _anthropic_json_text() -> str:
    return json.dumps(
        {
            "text": "曹仁小兒，長坂橋前有種便來！",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        },
        ensure_ascii=False,
    )


def _run_anthropic_provider_unit_smoke() -> None:
    os.environ["SANGUO_ANTHROPIC_API_KEY"] = _MOCK_ANTHROPIC_KEY
    provider = AnthropicDialogueProvider()
    assert provider.api_key == _MOCK_ANTHROPIC_KEY
    assert provider.model == "claude-haiku-5-5", provider.model
    assert provider._extract_anthropic_text({"content": [{"type": "text", "text": " hi "}]}) == "hi"
    try:
        provider._extract_anthropic_text({"content": []})
    except renderer.ProviderOutputError as exc:
        assert str(exc) == "anthropic:no-content", str(exc)
    else:
        raise AssertionError("empty content should raise ProviderOutputError")

    print("[npc-brain-llm-provider-smoke] anthropic_unit PASS")


def _run_anthropic_provider_smoke() -> None:
    os.environ["SANGUO_ANTHROPIC_API_KEY"] = _MOCK_ANTHROPIC_KEY
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "anthropic,deterministic"
    os.environ["NPC_LLM_MODEL_ANTHROPIC"] = "claude-haiku-5-5"
    calls, original = _patch_anthropic_open(
        lambda request: _FakeAnthropicResponse(
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": _anthropic_json_text()}],
                "stop_reason": "end_turn",
            }
        )
    )
    try:
        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                maxChars=90,
            )
        )
    finally:
        renderer._open_url = original

    assert response.provider == "anthropic", response.providerTrace
    assert response.model == "claude-haiku-5-5", response.model
    assert response.generationMode == "anthropic-json-v2+persona-card+quality-guard", response.generationMode
    assert response.providerTrace == ["anthropic:ok"], response.providerTrace
    assert response.usedEvidenceRefs == ["042#p1"], response.usedEvidenceRefs
    assert "曹仁" in response.text, response.text
    assert len(calls) == 1, f"expected exactly one request, got {len(calls)}"
    request = calls[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages", request.full_url
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers.get("x-api-key") == _MOCK_ANTHROPIC_KEY, "key should be sent only as x-api-key"
    assert headers.get("anthropic-version") == "2023-06-01", headers
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "claude-haiku-5-5", body["model"]
    assert body["system"], "system prompt should be sent in the system field"
    assert body["messages"][0]["role"] == "user", body["messages"]
    assert _MOCK_ANTHROPIC_KEY not in json.dumps(body), "key must never be embedded in the request body"
    assert _MOCK_ANTHROPIC_KEY not in json.dumps(response.__dict__, default=str), "key must not leak into response"

    print("[npc-brain-llm-provider-smoke] anthropic_mock_success PASS")


def _run_anthropic_provider_missing_key_smoke() -> None:
    os.environ.pop("SANGUO_ANTHROPIC_API_KEY", None)
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "anthropic,deterministic"

    def must_not_call(request):
        raise AssertionError("no HTTP call should be made without a key")

    calls, original = _patch_anthropic_open(must_not_call)
    try:
        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                maxChars=90,
            )
        )
    finally:
        renderer._open_url = original

    assert calls == [], calls
    assert response.provider == "deterministic", response.providerTrace
    assert response.providerTrace[0] == "anthropic:no-api-key", response.providerTrace

    print("[npc-brain-llm-provider-smoke] anthropic_missing_key_fallback PASS")


def _run_anthropic_provider_http_error_smoke() -> None:
    os.environ["SANGUO_ANTHROPIC_API_KEY"] = _MOCK_ANTHROPIC_KEY
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "anthropic,deterministic"

    def raise_401(request):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"type":"error","error":{"type":"authentication_error"}}'),
        )

    calls, original = _patch_anthropic_open(raise_401)
    try:
        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                maxChars=90,
            )
        )
    finally:
        renderer._open_url = original

    assert len(calls) == 1, calls
    assert response.provider == "deterministic", response.providerTrace
    assert response.providerTrace[0].startswith("anthropic:http-401:"), response.providerTrace
    assert _MOCK_ANTHROPIC_KEY not in " ".join(response.providerTrace), response.providerTrace

    print("[npc-brain-llm-provider-smoke] anthropic_http_error_fallback PASS")


class _LocalLlamaHandler(BaseHTTPRequestHandler):
    request_bodies: list[dict] = []
    response_contents: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_body)
        self.__class__.request_bodies.append(payload)
        response_content = self.__class__.response_contents.pop(0) if self.__class__.response_contents else {
            "text": "曹仁小兒，長坂橋前有種便來！",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        }
        response_body = {
            "model": payload.get("model"),
            "done": True,
            "done_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps(response_content, ensure_ascii=False),
            },
        }
        encoded = json.dumps(response_body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args) -> None:  # noqa: A003
        return


def _run_mock_provider_smoke() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "mock,deterministic"
    os.environ["NPC_LLM_MOCK_TEXT"] = "曹操兵再多又如何？俺張飛守在橋上，先護住主公，誰敢近前！"

    service = NpcDialogueService()
    response = service.build_dialogue(
        DialogueRequest(
            generalId="zhang-fei",
            contextKey="changban-bridge",
            selectedKeywordKeys=["cao-cao", "serpent-spear"],
            maxChars=90,
        )
    )

    assert response.provider == "mock", f"expected mock provider, got {response.provider}"
    assert response.generationMode == "mock-llm-v1+persona-card", response.generationMode
    assert response.providerTrace == ["mock:ok"], response.providerTrace
    assert response.usedEvidenceRefs, "mock provider should receive resolved evidence"
    assert "誰敢近前" in response.text, response.text

    print("[npc-brain-llm-provider-smoke] mock PASS")
    print(
        f"[npc-brain-llm-provider-smoke] provider={response.provider} "
        f"trace={','.join(response.providerTrace)} usedEvidenceRefs={len(response.usedEvidenceRefs)}"
    )


def _run_local_llama_provider_smoke() -> None:
    _LocalLlamaHandler.request_bodies.clear()
    _LocalLlamaHandler.response_contents.clear()
    server = HTTPServer(("127.0.0.1", 0), _LocalLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["NPC_LLM_PROVIDER_ORDER"] = "local_llama,deterministic"
        os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = f"http://127.0.0.1:{server.server_port}/api/chat"
        os.environ["NPC_LLM_MODEL_LOCAL_LLAMA"] = "qwen2.5:test"
        os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "2000"
        os.environ["NPC_LLM_LOCAL_LLAMA_MAX_OUTPUT_TOKENS"] = "128"
        os.environ["NPC_LLM_LOCAL_LLAMA_TEMPERATURE"] = "0.35"
        os.environ["NPC_LLM_LOCAL_LLAMA_TOP_P"] = "0.8"
        os.environ["NPC_LLM_LOCAL_LLAMA_REPEAT_PENALTY"] = "1.15"
        os.environ["NPC_LLM_LOCAL_LLAMA_NUM_CTX"] = "4096"

        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                maxChars=90,
            )
        )

        assert response.provider == "local_llama", f"expected local_llama provider, got {response.provider}"
        assert response.generationMode == "local-llama-json-v2+persona-card+quality-guard", response.generationMode
        assert response.providerTrace == ["local_llama:ok"], response.providerTrace
        assert response.usedEvidenceRefs == ["042#p1"], response.usedEvidenceRefs
        assert response.qualityWarnings == [], response.qualityWarnings
        assert response.repairUsed is False, response.repairUsed
        assert "曹仁" in response.text, response.text
        assert _LocalLlamaHandler.request_bodies, "expected local llama request body"
        request_body = _LocalLlamaHandler.request_bodies[0]
        assert request_body["messages"][0]["role"] == "system", request_body["messages"]
        assert request_body["messages"][1]["role"] == "user", request_body["messages"]
        assert request_body["options"]["temperature"] == 0.35, request_body["options"]
        assert request_body["options"]["top_p"] == 0.8, request_body["options"]
        assert request_body["options"]["repeat_penalty"] == 1.15, request_body["options"]
        assert request_body["options"]["num_ctx"] == 4096, request_body["options"]
        assert request_body["options"]["num_predict"] == 128, request_body["options"]
        prompt = request_body["messages"][1]["content"]
        assert '"selectedKeywords"' in prompt, prompt
        assert '"resolvedEvidence"' in prompt, prompt
        assert '"usedKeywordKeys"' in prompt, prompt
        assert '"localeDirective"' in prompt, prompt
        assert '"speechContextDirective"' in prompt, prompt
        assert '"encounter_speech"' in prompt, prompt
        assert '"keywordAngle"' in prompt, prompt
        assert "standing before the NPC" in prompt, prompt
        assert '"must"' in prompt and '"avoid"' in prompt, prompt

        print("[npc-brain-llm-provider-smoke] local_llama PASS")
        print(
            f"[npc-brain-llm-provider-smoke] provider={response.provider} "
            f"trace={','.join(response.providerTrace)} usedEvidenceRefs={','.join(response.usedEvidenceRefs)}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _run_model_preset_provider_smoke() -> None:
    _LocalLlamaHandler.request_bodies.clear()
    _LocalLlamaHandler.response_contents.clear()
    server = HTTPServer(("127.0.0.1", 0), _LocalLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
        os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = f"http://127.0.0.1:{server.server_port}/api/chat"
        os.environ["NPC_LLM_MODEL_LOCAL_LLAMA"] = "qwen2.5:test-env"
        os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "2000"

        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                llmModelPreset="qwen2_5_3b",
                maxChars=90,
            )
        )

        assert response.provider == "local_llama", response.providerTrace
        assert response.model == "qwen2.5:3b", response.model
        assert response.llmModelPreset == "qwen2_5_3b", response.llmModelPreset
        assert _LocalLlamaHandler.request_bodies[0]["model"] == "qwen2.5:3b", _LocalLlamaHandler.request_bodies[0]

        print("[npc-brain-llm-provider-smoke] model_preset_provider PASS")
        print(
            f"[npc-brain-llm-provider-smoke] preset={response.llmModelPreset} "
            f"provider={response.provider} model={response.model}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _run_deepseek_reasoner_provider_smoke() -> None:
    _LocalLlamaHandler.request_bodies.clear()
    _LocalLlamaHandler.response_contents = [
        {
            "text": "曹仁若在眼前，俺張飛便當面喝他退下！",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        }
    ]
    server = HTTPServer(("127.0.0.1", 0), _LocalLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
        os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = f"http://127.0.0.1:{server.server_port}/api/chat"
        os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "2000"

        original_do_post = _LocalLlamaHandler.do_POST

        def do_post_with_think(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw_body)
            self.__class__.request_bodies.append(payload)
            response_content = self.__class__.response_contents.pop(0)
            encoded_content = "<think>hidden reasoning</think>" + json.dumps(response_content, ensure_ascii=False)
            response_body = {
                "model": payload.get("model"),
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": encoded_content},
            }
            encoded = json.dumps(response_body, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        _LocalLlamaHandler.do_POST = do_post_with_think
        try:
            service = NpcDialogueService()
            response = service.build_dialogue(
                DialogueRequest(
                    generalId="zhang-fei",
                    contextKey="changban-bridge",
                    selectedKeywordKeys=["cao-ren"],
                    locale="zh-TW",
                    speechContextMode="encounter_speech",
                    llmModelPreset="deepseek_r1_7b",
                    maxChars=90,
                )
            )
        finally:
            _LocalLlamaHandler.do_POST = original_do_post

        assert response.provider == "deepseek_reasoner", response.providerTrace
        assert response.model == "deepseek-r1:7b", response.model
        assert response.llmModelPreset == "deepseek_r1_7b", response.llmModelPreset
        assert response.providerTrace == ["deepseek_reasoner:ok"], response.providerTrace
        assert "曹仁" in response.text, response.text
        assert _LocalLlamaHandler.request_bodies[0]["model"] == "deepseek-r1:7b", _LocalLlamaHandler.request_bodies[0]

        print("[npc-brain-llm-provider-smoke] deepseek_reasoner PASS")
        print(
            f"[npc-brain-llm-provider-smoke] preset={response.llmModelPreset} "
            f"provider={response.provider} model={response.model}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _run_model_preset_failure_smoke() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
    os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = "http://127.0.0.1:1/api/chat"
    os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "500"

    service = NpcDialogueService()
    try:
        service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                llmModelPreset="qwen2_5_7b",
                maxChars=90,
            )
        )
    except ProviderUnavailableError as exc:
        message = str(exc)
        assert "provider-chain-failed" in message, message
        assert "local_llama:network" in message, message
        print("[npc-brain-llm-provider-smoke] model_preset_failure PASS")
        print(f"[npc-brain-llm-provider-smoke] failure={message}")
        return
    raise AssertionError("qwen2_5_7b preset should fail loudly when local_llama is unavailable")


def _run_history_cache_provider_smoke() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "local_llama,history_cache,deterministic"
    cache_path = os.path.join(os.getcwd(), "local", "test-npc-dialogue-history.jsonl")
    os.environ["NPC_LLM_HISTORY_CACHE_PATH"] = cache_path
    # NpcDialogueService() clears the history cache when scene cache is disabled (e.g. .env sets it false).
    os.environ["NPC_SCENE_CACHE_ENABLED"] = "true"
    if os.path.exists(cache_path):
        os.remove(cache_path)

    _LocalLlamaHandler.request_bodies.clear()
    _LocalLlamaHandler.response_contents.clear()
    server = HTTPServer(("127.0.0.1", 0), _LocalLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = f"http://127.0.0.1:{server.server_port}/api/chat"
        os.environ["NPC_LLM_MODEL_LOCAL_LLAMA"] = "qwen2.5:test"
        os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "2000"

        service = NpcDialogueService()
        first_response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                speechContextMode="encounter_speech",
                maxChars=90,
            )
        )
        assert first_response.provider == "local_llama", first_response.providerTrace
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    os.environ["NPC_LLM_PROVIDER_ORDER"] = "history_cache,deterministic"
    service = NpcDialogueService()
    cached_response = service.build_dialogue(
        DialogueRequest(
            generalId="zhang-fei",
            contextKey="changban-bridge",
            selectedKeywordKeys=["cao-ren"],
            speechContextMode="encounter_speech",
            maxChars=90,
        )
    )
    assert cached_response.provider == "history_cache", cached_response.providerTrace
    assert "曹仁" in cached_response.text, cached_response.text
    assert os.path.exists(cache_path), cache_path

    print("[npc-brain-llm-provider-smoke] history_cache PASS")
    print(
        "[npc-brain-llm-provider-smoke] history_cache text="
        + json.dumps(cached_response.text, ensure_ascii=True)
    )


def _run_json_repair_smoke() -> None:
    provider = GeminiDialogueProvider(api_key="test-key")
    truncated_json = (
        '{"text":"曹仁若敢踏上長坂橋半步，俺也去當場喝他翻下馬！",'
        '"usedKeywordKeys":["cao-ren"],'
        '"usedEvidenceRefs":["042#p1"],'
        '"usedPersonaAnchors":["voiceStyle.豪烈"],'
        '"violations":["unterminated'
    )
    parsed = provider._parse_json_text(truncated_json)
    assert parsed["text"] == "曹仁若敢踏上長坂橋半步，俺也去當場喝他翻下馬！", parsed
    assert parsed["usedKeywordKeys"] == ["cao-ren"], parsed
    assert parsed["usedEvidenceRefs"] == ["042#p1"], parsed

    print("[npc-brain-llm-provider-smoke] json-repair PASS")
    print(
        "[npc-brain-llm-provider-smoke] repaired="
        + json.dumps(parsed, ensure_ascii=True)
    )


def _run_local_llama_quality_repair_smoke() -> None:
    _LocalLlamaHandler.request_bodies.clear()
    _LocalLlamaHandler.response_contents = [
        {
            "text": "亮豈不知曹仁？beautiful /of 亂語不足取。",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        },
        {
            "text": "曹仁若敢近橋，俺張飛便當面喝他退下！",
            "usedKeywordKeys": ["cao-ren"],
            "usedEvidenceRefs": ["042#p1"],
            "usedPersonaAnchors": ["voiceStyle.豪烈"],
            "safetyFallbackUsed": False,
            "violations": [],
        },
    ]
    server = HTTPServer(("127.0.0.1", 0), _LocalLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["NPC_LLM_PROVIDER_ORDER"] = "local_llama,deterministic"
        os.environ["NPC_LLM_LOCAL_LLAMA_API_URL"] = f"http://127.0.0.1:{server.server_port}/api/chat"
        os.environ["NPC_LLM_MODEL_LOCAL_LLAMA"] = "qwen2.5:test"
        os.environ["NPC_LLM_LOCAL_LLAMA_TIMEOUT_MS"] = "2000"
        os.environ["NPC_LLM_LOCAL_LLAMA_REPAIR_RETRY_COUNT"] = "1"

        service = NpcDialogueService()
        response = service.build_dialogue(
            DialogueRequest(
                generalId="zhang-fei",
                contextKey="changban-bridge",
                selectedKeywordKeys=["cao-ren"],
                locale="zh-TW",
                speechContextMode="encounter_speech",
                maxChars=90,
            )
        )

        assert response.provider == "local_llama", response.providerTrace
        assert response.repairUsed is True, response
        assert response.qualityWarnings, "repair should expose original quality warnings"
        assert "wrong-self-name" in ",".join(response.qualityWarnings), response.qualityWarnings
        assert "曹仁" in response.text and "張飛" in response.text, response.text
        assert len(_LocalLlamaHandler.request_bodies) == 2, _LocalLlamaHandler.request_bodies
        repair_prompt = _LocalLlamaHandler.request_bodies[1]["messages"][1]["content"]
        assert '"blockingIssues"' in repair_prompt, repair_prompt
        assert '"previousOutput"' in repair_prompt, repair_prompt

        print("[npc-brain-llm-provider-smoke] local_llama_quality_repair PASS")
        print(
            "[npc-brain-llm-provider-smoke] repair warnings="
            + json.dumps(response.qualityWarnings, ensure_ascii=True)
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def main() -> None:
    original_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        _run_mock_provider_smoke()
        _run_local_llama_provider_smoke()
        _run_model_preset_provider_smoke()
        _run_deepseek_reasoner_provider_smoke()
        _run_model_preset_failure_smoke()
        _run_history_cache_provider_smoke()
        _run_json_repair_smoke()
        _run_local_llama_quality_repair_smoke()
        _run_anthropic_provider_unit_smoke()
        _run_anthropic_provider_smoke()
        _run_anthropic_provider_missing_key_smoke()
        _run_anthropic_provider_http_error_smoke()
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
