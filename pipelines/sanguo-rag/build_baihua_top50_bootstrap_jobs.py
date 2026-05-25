from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_RANKING_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress"
DEFAULT_STABLE_KNOWLEDGE_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
DEFAULT_PASSAGES_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/anchor-index/sanguoyanyi-baihua-zh-tw-passages.jsonl"
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build top50 person-centered baihua bootstrap jobs.")
    parser.add_argument("--ranking-path", default="", help="Top ranking JSON path; when empty, auto-pick latest top50*.famous-ranking.json")
    parser.add_argument("--stable-knowledge-path", default=str(DEFAULT_STABLE_KNOWLEDGE_PATH))
    parser.add_argument("--passages-path", default=str(DEFAULT_PASSAGES_PATH))
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--wave-id", default="wave-001")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--max-passage-refs", type=int, default=120)
    parser.add_argument("--manifest-file-name", default="top50-bootstrap-jobs.jsonl")
    parser.add_argument("--summary-file-name", default="top50-bootstrap-jobs-summary.json")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be object: {path}:{line_no}")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pick_latest_ranking_json(root: Path) -> Path:
    candidates = sorted(
        [
            path
            for path in root.glob("top50*.famous-ranking.json")
            if path.is_file()
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No top50*.famous-ranking.json found under: {root}")
    return candidates[0]


def load_top_rows(path: Path, top: int) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"Ranking rows missing: {path}")
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        if general_id.startswith("romance-person-"):
            continue
        selected.append(row)
        if len(selected) >= top:
            break
    return selected


def stable_identity_map(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    seeds = payload.get("identitySeeds")
    if not isinstance(seeds, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in seeds:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        result[general_id] = row
    return result


def passage_refs_by_person(path: Path, corpus_id: str, *, max_per_person: int) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    rows = read_jsonl(path)
    for row in rows:
        if str(row.get("corpusId") or "") != corpus_id:
            continue
        locator = str(row.get("locator") or "").strip()
        if not locator:
            continue
        person_ids = row.get("personIds")
        if not isinstance(person_ids, list):
            continue
        for person_id in person_ids:
            general_id = str(person_id or "").strip()
            if not general_id:
                continue
            values = buckets[general_id]
            if len(values) < max_per_person:
                values.append(locator)
    return buckets


def name_for_row(row: dict[str, Any], stable_row: dict[str, Any] | None) -> str:
    ranking_name = str(row.get("displayName") or row.get("name") or "").strip()
    if ranking_name:
        return ranking_name
    stable_name = str((stable_row or {}).get("name") or "").strip()
    if stable_name:
        return stable_name
    return str(row.get("generalId") or "")


def main() -> int:
    args = parse_args()
    ranking_path = Path(args.ranking_path).resolve() if args.ranking_path.strip() else pick_latest_ranking_json(Path(DEFAULT_RANKING_ROOT))
    stable_path = Path(args.stable_knowledge_path).resolve()
    passages_path = Path(args.passages_path).resolve()
    policy_path = Path(args.policy_path).resolve()
    output_root = Path(args.output_root).resolve()
    manifest_path = output_root / args.manifest_file_name
    summary_path = output_root / args.summary_file_name

    if not args.overwrite and (manifest_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {manifest_path}")

    policy = read_json(policy_path)
    corpus_id = str(policy.get("sourceCorpusId") or "sanguoyanyi-baihua-zh-tw").strip()
    relationship_types = []
    relation_config = policy.get("relationshipTypes")
    if isinstance(relation_config, dict):
        allowed = relation_config.get("allowed")
        if isinstance(allowed, list):
            relationship_types = [str(item).strip() for item in allowed if str(item or "").strip()]
    if not relationship_types:
        raise ValueError(f"policy missing relationshipTypes.allowed: {policy_path}")

    top_rows = load_top_rows(ranking_path, max(1, int(args.top)))
    if not top_rows:
        raise ValueError(f"No top rows loaded from ranking: {ranking_path}")
    stable_map = stable_identity_map(stable_path)
    refs_by_person = passage_refs_by_person(passages_path, corpus_id, max_per_person=max(1, int(args.max_passage_refs)))

    top_ids = [str(row.get("generalId") or "").strip() for row in top_rows]
    top_ids = [general_id for general_id in top_ids if general_id]

    jobs: list[dict[str, Any]] = []
    missing_passage_ids: list[str] = []
    for row in top_rows:
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        stable_row = stable_map.get(general_id)
        focus_name = name_for_row(row, stable_row)
        refs = refs_by_person.get(general_id) or []
        if not refs:
            missing_passage_ids.append(general_id)
        jobs.append(
            {
                "jobId": f"baihua-bootstrap:{args.wave_id}:{general_id}",
                "waveId": args.wave_id,
                "focusGeneralId": general_id,
                "focusNameZhTw": focus_name,
                "candidateCounterpartIds": [target_id for target_id in top_ids if target_id != general_id],
                "allowedRelationshipTypes": relationship_types,
                "sourceCorpusId": corpus_id,
                "passageRefs": refs,
                "canonicalWrites": False,
            }
        )

    write_jsonl(manifest_path, jobs)
    summary = {
        "mode": "baihua-top50-bootstrap-job-builder",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "waveId": args.wave_id,
        "top": int(args.top),
        "inputs": {
            "rankingPath": str(ranking_path),
            "stableKnowledgePath": str(stable_path),
            "passagesPath": str(passages_path),
            "policyPath": str(policy_path),
        },
        "outputs": {
            "manifestPath": str(manifest_path),
            "jobCount": len(jobs),
            "missingPassageFocusCount": len(missing_passage_ids),
            "missingPassageFocusIds": sorted(missing_passage_ids),
        },
    }
    write_json(summary_path, summary)
    print(f"[build_baihua_top50_bootstrap_jobs] wrote {manifest_path}")
    print(f"[build_baihua_top50_bootstrap_jobs] wrote {summary_path}")
    print(f"[build_baihua_top50_bootstrap_jobs] top={args.top} jobs={len(jobs)} missingPassages={len(missing_passage_ids)} canonicalWrites=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
