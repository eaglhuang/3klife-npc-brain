from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PRIMARY_CANON_ROOT = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/primary-canon-relationship-backbone"
)

PRIMARY_CANON_INPUT_PATTERNS = {
    "relationshipEvidence": [
        "merged-*-relationship-evidence.jsonl",
        "relationship-overlay/source-grounded-relationship-edges.external.jsonl",
    ],
    "eventQuestionSeeds": ["event-question-seeds/event-question-seeds.jsonl"],
    "sourceEventPackets": ["source-event-packets/source-event-packets.jsonl"],
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def latest_matching_file(root: Path, patterns: list[str]) -> Path | None:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in root.glob(pattern) if path.is_file())
    if not matches:
        return None
    return sorted(matches, key=lambda path: (path.stat().st_mtime, str(path)))[-1]


def primary_canon_artifact_paths(run_root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for key, patterns in PRIMARY_CANON_INPUT_PATTERNS.items():
        path = latest_matching_file(run_root, patterns)
        if path is not None:
            paths[key] = path
    return paths


def run_sort_key(run_root: Path) -> tuple[str, float, str]:
    generated_values: list[str] = []
    for path in sorted((run_root / "completion-after").glob("*.json")):
        generated_at = str(read_json(path).get("generatedAt") or "").strip()
        if generated_at:
            generated_values.append(generated_at)
    generated = max(generated_values) if generated_values else ""
    return generated, run_root.stat().st_mtime, str(run_root)


def latest_primary_canon_run_root(primary_root: Path = DEFAULT_PRIMARY_CANON_ROOT) -> Path | None:
    if not primary_root.exists():
        return None
    candidates: list[Path] = []
    for path in primary_root.iterdir():
        if path.is_dir() and primary_canon_artifact_paths(path):
            candidates.append(path)
    if not candidates:
        return None
    return sorted(candidates, key=run_sort_key)[-1]


def latest_primary_canon_artifact_paths(
    primary_root: Path = DEFAULT_PRIMARY_CANON_ROOT,
) -> tuple[Path | None, dict[str, Path]]:
    run_root = latest_primary_canon_run_root(primary_root)
    if run_root is None:
        return None, {}
    return run_root, primary_canon_artifact_paths(run_root)


def choose_primary_or_fallback(
    key: str,
    fallback: Path,
    primary_paths: dict[str, Path],
) -> Path:
    path = primary_paths.get(key)
    if path is not None and path.exists():
        return path
    return fallback


def primary_canon_metadata(run_root: Path | None, primary_paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "enabled": run_root is not None,
        "runRoot": str(run_root) if run_root is not None else None,
        "paths": {key: str(path) for key, path in sorted(primary_paths.items())},
    }
