from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class RuntimeProfileStore:
    def __init__(
        self,
        repo_root: Path,
        artifact_root: Path,
        persona_root: Path,
        runtime_profile_root: Path,
        event_root: Path,
    ) -> None:
        self.repo_root = repo_root
        self.artifact_root = self._resolve_path(artifact_root)
        self.persona_root = self._resolve_path(persona_root)
        self.runtime_profile_root = self._resolve_path(runtime_profile_root)
        self.event_root = self._resolve_path(event_root)
        self.runtime_profile_remote_base_url = (os.environ.get("NPC_RUNTIME_PROFILE_REMOTE_BASE_URL") or "").strip().rstrip("/")
        self._ready_events_cache: list[dict] | None = None
        self._source_event_packets_cache: list[dict] | None = None
        self._remote_runtime_json_cache: dict[tuple[str, str], dict | None] = {}

    def read_api_fixture(self, filename: str) -> dict:
        return json.loads((self.artifact_root / filename).read_text(encoding="utf-8"))

    def read_optional_api_fixture(self, filename: str) -> dict | None:
        path = self.artifact_root / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_runtime_persona(self, general_id: str) -> dict | None:
        return self._read_runtime_json(general_id, "persona")

    def read_runtime_keywords(self, general_id: str) -> dict | None:
        return self._read_runtime_json(general_id, "keywords")

    def read_runtime_relationships(self, general_id: str) -> dict | None:
        return self._read_runtime_json(general_id, "relationships")

    def read_persona_card(self, general_id: str) -> dict | None:
        path = self.persona_root / f"{general_id}.persona.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        payload = self.read_optional_api_fixture("persona-card.response.json")
        if payload and payload.get("generalId") == general_id:
            return payload
        return None

    def load_ready_events(self) -> list[dict]:
        if self._ready_events_cache is not None:
            return self._ready_events_cache
        path = self.event_root / "events.jsonl"
        if not path.exists():
            self._ready_events_cache = []
            return self._ready_events_cache
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("reviewStatus") == "ready":
                events.append(payload)
        self._ready_events_cache = events
        return events

    def load_source_event_packets(self) -> list[dict]:
        if self._source_event_packets_cache is not None:
            return self._source_event_packets_cache
        path = self.artifact_root.parent / "source-event-packets" / "source-event-packets.jsonl"
        if not path.exists():
            self._source_event_packets_cache = []
            return self._source_event_packets_cache
        packets: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                packets.append(payload)
        self._source_event_packets_cache = packets
        return packets

    def list_runtime_general_ids(self) -> list[str]:
        if not self.runtime_profile_root.exists():
            return []
        return sorted(
            path.name
            for path in self.runtime_profile_root.iterdir()
            if path.is_dir() and (path / f"{path.name}.persona.json").exists()
        )

    def _read_runtime_json(self, general_id: str, suffix: str) -> dict | None:
        path = self.runtime_profile_root / general_id / f"{general_id}.{suffix}.json"
        if not path.exists():
            return self._read_remote_runtime_json(general_id, suffix)
        return json.loads(path.read_text(encoding="utf-8"))

    def _resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else self.repo_root / path

    def _read_remote_runtime_json(self, general_id: str, suffix: str) -> dict | None:
        if not self.runtime_profile_remote_base_url:
            return None
        cache_key = (general_id, suffix)
        if cache_key in self._remote_runtime_json_cache:
            return self._remote_runtime_json_cache[cache_key]
        url = f"{self.runtime_profile_remote_base_url}/{general_id}/{general_id}.{suffix}.json"
        try:
            with urlopen(url, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        self._remote_runtime_json_cache[cache_key] = payload
        return payload
