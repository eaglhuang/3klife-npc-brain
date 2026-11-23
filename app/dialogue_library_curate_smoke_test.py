"""Mock-only tests for tools/dialogue_library_curate.py and the admin delete endpoint (temporary SQLite)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path

from .dialogue_library import MAX_LINES_PER_KEY, DialogueLibrary, compute_cache_key, normalize_buckets, retire_library_lines

TOKEN = "mock-curate-token-7"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "dialogue_library_curate_under_test",
        Path(__file__).resolve().parents[1] / "tools" / "dialogue_library_curate.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _bridge(client, dialogue_calls: list[dict]):
    """urllib-style opener that forwards admin calls to a FastAPI TestClient and mocks /v1/npc/dialogue."""

    def opener(request, timeout=None):
        parsed = urllib.parse.urlparse(request.full_url)
        url = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if parsed.path == "/v1/npc/dialogue":
            dialogue_calls.append(json.loads(request.data.decode("utf-8")))
            body = {"text": f"新台詞{len(dialogue_calls)}。", "provider": "anthropic", "fallbackUsed": False, "qualityWarnings": []}
            return _Resp(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        headers = {"Authorization": request.get_header("Authorization") or ""}
        if request.get_method() == "GET":
            reply = client.get(url, headers=headers)
        else:
            reply = client.post(url, content=request.data, headers={**headers, "Content-Type": "application/json"})
        assert reply.status_code == 200, (url, reply.status_code, reply.text)
        return _Resp(reply.content)

    return opener


def _run(tool, argv: list[str], opener) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = tool.main(argv, opener=opener)
    return code, buffer.getvalue()


def run() -> None:
    from fastapi.testclient import TestClient

    from .main import create_app

    tool = _load_tool()
    saved = {key: os.environ.get(key) for key in ("NPC_ADMIN_TOKEN", "NPC_DIALOGUE_DB_PATH", "NPC_DIALOGUE_LIBRARY_ENABLED")}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        db = tmp / "curate.sqlite"
        try:
            os.environ["NPC_ADMIN_TOKEN"] = TOKEN
            os.environ["NPC_DIALOGUE_DB_PATH"] = str(db)
            library = DialogueLibrary(db)
            buckets = normalize_buckets(
                general_id="zhang-fei", personality="Brave", mood="anger", relation_bucket=3,
                world_phase="crisis", interaction_type="battle_victory", scene="battlefield",
            )
            key = compute_cache_key(buckets)
            texts = ["曹仁小兒，吃俺一矛！", "本王在此，誰敢來戰！", "好", "此戰必勝，隨我殺敵", "曹仁小兒，吃俺一矛啊！"]
            for text in texts:
                library.add(key, buckets, text, model="m", prompt_version="v", quality_score=None, extra={"contextKey": "changban"})
            other = normalize_buckets(
                general_id="guan-yu", personality=None, mood="calm", relation_bucket=2,
                world_phase="tripartite", interaction_type="greeting", scene="lobby",
            )
            library.add(compute_cache_key(other), other, "關某在此。", model="m", prompt_version="v")

            # endpoint: auth, dry run, soft delete only touches active rows
            client = TestClient(create_app())
            url = "/v1/admin/dialogue-library/delete"
            assert client.post(url, json={"keys": [key]}).status_code == 401
            assert client.post(url, json={"keys": [key]}, headers={"Authorization": "Bearer wrong"}).status_code == 403
            assert client.post(url, json={}, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 400
            assert library.count_active(key) == 5
            dry = client.post(
                url + "?dryRun=true", json={"targets": [{"key": key, "textZhTw": "好"}]},
                headers={"Authorization": f"Bearer {TOKEN}"},
            ).json()
            assert dry["dryRun"] and dry["matched"] == 1 and dry["retired"] == 0 and library.count_active(key) == 5
            done = retire_library_lines(library, targets=[{"key": key, "textZhTw": "好"}])
            assert done["retired"] == 1 and library.count_active(key) == 4
            assert retire_library_lines(library, targets=[{"key": key, "textZhTw": "好"}])["matched"] == 0, "already retired"
            library.add(key, buckets, "好", model="m", prompt_version="v")  # refill the slot for the tool tests
            assert library.count_active(key) == 5

            calls: list[dict] = []
            opener = _bridge(client, calls)
            base = ["--url", "https://example.test", "--out-dir", str(tmp / "out")]

            code, out = _run(tool, base + ["list"], opener)
            assert code == 0 and "library:" in out and "吃俺一矛" in out, out
            assert TOKEN not in out

            code, out = _run(tool, base + ["flag", "--text-contains", "本王", "--too-short", "3", "--duplicate-similar", "0.8"], opener)
            assert code == 0 and "flagged 3 of" in out, out
            assert list((tmp / "out").glob("flagged-*.json"))

            # dry-run: no backup, no deletion
            code, out = _run(tool, base + ["delete", "--text-contains", "本王", "--dry-run"], opener)
            assert code == 0 and "would delete 1" in out and library.count_active(key) == 5, out
            assert not list((tmp / "out").glob("dialogue-library-*.json"))

            # real delete needs --yes, then backs up first and writes the deleted file
            code, _ = _run(tool, base + ["delete", "--text-contains", "本王"], opener)
            assert code == 1 and library.count_active(key) == 5
            code, out = _run(tool, base + ["delete", "--text-contains", "本王", "--yes"], opener)
            assert code == 0 and library.count_active(key) == 4, out
            backups = list((tmp / "out").glob("dialogue-library-*.json"))
            assert len(backups) == 1 and TOKEN not in backups[0].read_text(encoding="utf-8")
            assert any("本王在此" in line["textZhTw"] for line in json.loads(backups[0].read_text(encoding="utf-8"))["lines"])
            deleted_file = next((tmp / "out").glob("deleted-*.json"))

            # refuses an unscoped delete
            code, _ = _run(tool, base + ["delete", "--yes"], opener)
            assert code == 1 and library.count_active(key) == 4

            # regenerate: dry-run makes no calls; real run honours --max
            code, out = _run(tool, base + ["regenerate", "--from-deleted", str(deleted_file), "--dry-run"], opener)
            assert code == 0 and "plan: 1 generation" in out and not calls, out
            code, out = _run(tool, base + ["regenerate", "--key", key, "--max", "1"], opener)
            assert code == 0 and len(calls) == 1 and "新台詞1。" in out, out
            request = calls[0]
            assert request["schemaVersion"] == 2 and request["mood"] == "anger" and request["interaction"] == {"type": "battle_victory"}
            assert request["contextKey"] == "changban" and request["personality"] == "Brave"
            rebuilt = normalize_buckets(
                general_id=request["generalId"], personality=request["personality"], mood=request["mood"],
                relation_bucket=request["relationBucket"], world_phase=request["worldPhase"],
                interaction_type=request["interaction"]["type"], scene=request["scene"],
            )
            assert compute_cache_key(rebuilt) == key
            # the key still has 4 active lines (mock does not store): --max 9 only plans the 1 missing slot
            code, out = _run(tool, base + ["regenerate", "--key", key, "--max", "9", "--dry-run"], opener)
            assert "plan: 1 generation" in out, out

            # .env fallback
            env_file = tmp / ".env"
            env_file.write_text(f"# c\nNPC_ADMIN_TOKEN='{TOKEN}'\n", encoding="utf-8")
            assert tool.read_env_file_value("NPC_ADMIN_TOKEN", env_file) == TOKEN
            assert tool.read_env_file_value("MISSING", env_file) is None
            assert MAX_LINES_PER_KEY == tool.MAX_LINES_PER_KEY
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    print("[dialogue-library-curate-smoke] PASS")


if __name__ == "__main__":
    run()
