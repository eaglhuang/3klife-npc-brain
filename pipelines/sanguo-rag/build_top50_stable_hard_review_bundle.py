from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-hard-baseline-lane.json"
DEFAULT_REVIEWED_CACHE_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-semantic-review-cache.imported.jsonl"
)
DEFAULT_HUMAN_DECISIONS_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Top50 stable-hard human-review bundles from reviewed-cache supported rows only."
    )
    parser.add_argument("--lane-policy", default=str(DEFAULT_LANE_POLICY_PATH))
    parser.add_argument("--reviewed-cache-path", action="append", default=[])
    parser.add_argument("--human-decisions-path", action="append", default=[])
    parser.add_argument("--output-root", default="")
    parser.add_argument("--tag", default="reviewed-supported")
    parser.add_argument("--relationship-type", default="")
    parser.add_argument("--max-rows", type=int, default=80)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    return (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            row.setdefault("_sourceFile", repo_relative(path))
            row.setdefault("_sourceLine", line_no)
            rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def review_output_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("reviewOutput"))


def relationship_type_priority_map(lane_policy: dict[str, Any]) -> dict[str, int]:
    review_batch = object_map(lane_policy.get("reviewBatch"))
    order = [str(item or "").strip() for item in string_list(review_batch.get("relationshipTypePriority")) if str(item or "").strip()]
    return {relationship_type: index for index, relationship_type in enumerate(order)}


def human_decision_paths(args: argparse.Namespace, lane_policy: dict[str, Any]) -> list[Path]:
    configured = [resolve_path(path_text) for path_text in args.human_decisions_path if str(path_text).strip()]
    if configured:
        return configured
    gap_policy = object_map(lane_policy.get("gapResolution"))
    return [resolve_path(path_text) for path_text in string_list(gap_policy.get("humanDecisionPaths"))]


def reviewed_cache_paths(args: argparse.Namespace, lane_policy: dict[str, Any]) -> list[Path]:
    configured = [resolve_path(path_text) for path_text in args.reviewed_cache_path if str(path_text).strip()]
    if configured:
        return configured
    gap_policy = object_map(lane_policy.get("gapResolution"))
    paths = [resolve_path(path_text) for path_text in string_list(gap_policy.get("reviewedCachePaths"))]
    return paths or [DEFAULT_REVIEWED_CACHE_PATH]


def resolved_human_keys(paths: list[Path]) -> set[str]:
    resolved: set[str] = set()
    for path in paths:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        for command in payload.get("commands") or []:
            if not isinstance(command, dict):
                continue
            trust_key = str(command.get("trustKey") or "").strip()
            if trust_key:
                resolved.add(trust_key)
    return resolved


def semantic_score_pair(relation: dict[str, Any]) -> tuple[float, float]:
    score = number_value(relation.get("semanticTrustScore"), -1.0)
    confidence = number_value(relation.get("confidence"), -1.0)
    if 0.0 <= score <= 1.0:
        score *= 100.0
    if score < 0.0 and confidence >= 0.0:
        score = confidence * 100.0 if confidence <= 1.0 else confidence
    if confidence < 0.0 and score >= 0.0:
        confidence = score / 100.0
    if confidence > 1.0:
        confidence = confidence / 100.0
    return max(score, 0.0), max(confidence, 0.0)


def candidate_map(unit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for candidate in unit.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        trust_key = str(candidate.get("trustKey") or "").strip()
        if trust_key and trust_key not in output:
            output[trust_key] = candidate
    return output


def entity_name_map(unit: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for entity in unit.get("allowedEntities") or []:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entityId") or "").strip()
        name = str(entity.get("nameZhTw") or "").strip()
        if entity_id and name:
            output[entity_id] = name
    for candidate in unit.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        for id_field, name_field in (("fromId", "fromNameZhTw"), ("toId", "toNameZhTw")):
            entity_id = str(candidate.get(id_field) or "").strip()
            name = str(candidate.get(name_field) or "").strip()
            if entity_id and name and entity_id not in output:
                output[entity_id] = name
    return output


def best_row(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return candidate
    existing_key = (
        number_value(existing.get("semanticTrustScore")),
        number_value(existing.get("confidence")),
        str(existing.get("reviewedAt") or ""),
    )
    candidate_key = (
        number_value(candidate.get("semanticTrustScore")),
        number_value(candidate.get("confidence")),
        str(candidate.get("reviewedAt") or ""),
    )
    return candidate if candidate_key > existing_key else existing


def review_source_label(provider: str) -> str:
    if provider == "codex-skill":
        return "Codex 語意審核匯入"
    return "語意審核快取支持"


def relationship_label(relationship_type: str) -> str:
    labels = {
        "ruler_subject": "君臣",
        "spouse": "夫妻",
        "parent_child": "親子",
        "adoptive_parent_child": "義父義子",
        "sibling": "兄弟姊妹",
        "sworn_sibling": "結義兄弟",
        "faction_membership": "陣營",
    }
    return labels.get(relationship_type, relationship_type)


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def compact_sentence(value: Any, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_markdown(rows: list[dict[str, Any]], relationship_type: str) -> str:
    lane_title = relationship_label(relationship_type) if relationship_type else "混合安全型"
    lines = [
        f"# Top50 穩定硬關係人工審核表（{lane_title}）",
        "",
        "- 僅收錄 `reviewed-cache verdict=supported` 或 `Codex 語意審核匯入` 的候選。",
        "- `proposal-only`、`fast-gap-reopen` 原始候選、未經語意支持的資料，不得進入本表。",
        "- 本表維持 `canonicalWrites=false`，需人工決策後再轉成白黑名單輸入。",
        "",
        "| # | 信任鍵 | 關係 | 對象一 | 對象二 | 語意分數 | 信心 | 審核來源 | 出處定位 | 證據原文 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | `{trust_key}` | {rtype} | {from_name} | {to_name} | {score} | {confidence} | {source} | `{locator}` | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(row.get("trustKey")),
                rtype=markdown_escape(row.get("relationshipTypeZhTw")),
                from_name=markdown_escape(row.get("fromNameZhTw")),
                to_name=markdown_escape(row.get("toNameZhTw")),
                score=markdown_escape(f"{number_value(row.get('semanticTrustScore')):.1f}"),
                confidence=markdown_escape(f"{number_value(row.get('confidence')):.2f}"),
                source=markdown_escape(row.get("reviewSourceZhTw")),
                locator=markdown_escape(row.get("locator")),
                quote=markdown_escape(compact_sentence(row.get("evidenceSentenceZhTw"))),
            )
        )
    lines.append("")
    lines.append("## 審核說明")
    lines.append("")
    lines.append("1. 若正確，將決策改成 `approved`。")
    lines.append("2. 若錯誤，將決策改成 `rejected`，此工具會轉成 `force-blacklist`。")
    lines.append("3. 這份表不接受 proposal-only 候選，也不應混入未支持或反證資料。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_decision_template(rows: list[dict[str, Any]], tag: str) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for row in rows:
        decisions.append(
            {
                "trustKey": row["trustKey"],
                "decision": "pending",
                "reviewer": "human",
                "relationshipType": row["relationshipType"],
                "fromId": row["fromId"],
                "toId": row["toId"],
                "reviewNotesZhTw": f"{tag} 僅允許 reviewed-cache supported / Codex 語意審核匯入",
                "canonicalWrites": False,
            }
        )
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": f"top50-stable-hard-human-decisions.{tag}.template",
        "decisionField": "decision",
        "commandField": "action",
        "approvedStatuses": ["approved"],
        "rejectedStatuses": ["rejected"],
        "availableCommands": {
            "forceWhitelistActions": ["force-whitelist"],
            "forceBlacklistActions": ["force-blacklist"],
            "removeFromIndexActions": ["remove-from-index"],
        },
        "canonicalWrites": False,
        "commands": [],
        "decisions": decisions,
    }


def output_paths(output_root: Path, tag: str) -> dict[str, Path]:
    stem = f"top50-stable-hard-whitelist-candidates.{tag}"
    return {
        "jsonl": output_root / f"{stem}.jsonl",
        "markdown": output_root / f"{stem}.zh-TW.md",
        "template": output_root / f"top50-stable-hard-human-decisions.{tag}.template.json",
        "summary": output_root / f"{stem}.summary.json",
    }


def main() -> int:
    args = parse_args()
    lane_policy_path = resolve_path(args.lane_policy)
    lane_policy = read_json(lane_policy_path)
    review_policy = review_output_policy(lane_policy)
    relationship_filter = str(args.relationship_type or "").strip()
    blocked_mixed_types = {
        item for item in string_list(review_policy.get("mixedReviewBlockedRelationshipTypes")) if item
    }
    reviewed_paths = reviewed_cache_paths(args, lane_policy)
    decision_paths = human_decision_paths(args, lane_policy)
    resolved_keys = resolved_human_keys(decision_paths)
    priority_map = relationship_type_priority_map(lane_policy)

    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else reviewed_paths[0].parent
    tag = str(args.tag or "reviewed-supported").strip()
    paths = output_paths(output_root, tag)
    if not args.overwrite and any(path.exists() for path in paths.values()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {paths['jsonl']}")

    kept_by_trust_key: dict[str, dict[str, Any]] = {}
    counters: Counter[str] = Counter()

    for reviewed_path in reviewed_paths:
        for unit in read_jsonl(reviewed_path):
            if not bool_value(unit.get("semanticReviewPerformed")):
                counters["skippedNotReviewed"] += 1
                continue
            provider = str(object_map(unit.get("reviewer")).get("provider") or "").strip()
            candidates = candidate_map(unit)
            names = entity_name_map(unit)
            for relation in unit.get("relationships") or []:
                if not isinstance(relation, dict):
                    continue
                verdict = str(relation.get("verdict") or "").strip().lower()
                if verdict != "supported":
                    counters["skippedUnsupported"] += 1
                    continue
                trust_key = str(relation.get("trustKey") or "").strip()
                if not trust_key:
                    counters["skippedMissingTrustKey"] += 1
                    continue
                if trust_key in resolved_keys:
                    counters["skippedResolvedByHuman"] += 1
                    continue
                candidate = candidates.get(trust_key, {})
                relationship_type = str(
                    relation.get("relationshipType") or candidate.get("relationshipType") or ""
                ).strip()
                if relationship_filter:
                    if relationship_type != relationship_filter:
                        counters["skippedRelationshipFilter"] += 1
                        continue
                elif relationship_type in blocked_mixed_types:
                    counters["skippedHighRiskMixed"] += 1
                    continue
                score, confidence = semantic_score_pair(relation)
                from_id = str(relation.get("fromId") or candidate.get("fromId") or "").strip()
                to_id = str(relation.get("toId") or candidate.get("toId") or "").strip()
                row = {
                    "trustKey": trust_key,
                    "relationshipType": relationship_type,
                    "relationshipTypeZhTw": relationship_label(relationship_type),
                    "fromId": from_id,
                    "toId": to_id,
                    "fromNameZhTw": str(candidate.get("fromNameZhTw") or names.get(from_id) or from_id),
                    "toNameZhTw": str(candidate.get("toNameZhTw") or names.get(to_id) or to_id),
                    "semanticTrustScore": round(score, 3),
                    "confidence": round(confidence, 4),
                    "reviewedAt": str(unit.get("reviewedAt") or ""),
                    "reviewSourceZhTw": review_source_label(provider),
                    "reviewSourceType": "reviewed-cache-supported",
                    "provider": provider or "semantic-review",
                    "semanticReviewUnitId": str(unit.get("semanticReviewUnitId") or ""),
                    "sourceReviewedCachePath": repo_relative(reviewed_path),
                    "locator": str(first_source_locator(unit)),
                    "evidenceSentenceZhTw": str(
                        relation.get("evidenceSentence") or unit.get("sourceSentence") or ""
                    ),
                    "canonicalWrites": False,
                }
                kept_by_trust_key[trust_key] = best_row(kept_by_trust_key.get(trust_key), row)
                counters["keptSupported"] += 1

    rows = sorted(
        kept_by_trust_key.values(),
        key=lambda item: (
            priority_map.get(str(item.get("relationshipType") or "").strip(), 999),
            -number_value(item.get("semanticTrustScore")),
            -number_value(item.get("confidence")),
            str(item.get("trustKey") or ""),
        ),
    )
    rows = rows[: max(1, int(args.max_rows))]

    version_metadata = build_version_metadata(
        schema_version="top50-stable-hard-review-bundle.v1",
        artifact_paths=[lane_policy_path, *reviewed_paths, *decision_paths],
        repo_root=REPO_ROOT,
    )
    for row in rows:
        row.update(version_metadata)

    markdown_text = render_markdown(rows, relationship_filter)
    paths["markdown"].parent.mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(markdown_text, encoding="utf-8")
    write_jsonl(paths["jsonl"], rows)
    template = build_decision_template(rows, tag)
    template.update(version_metadata)
    write_json(paths["template"], template)

    summary = {
        "mode": "top50-stable-hard-reviewed-support-bundle",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "lanePolicyPath": repo_relative(lane_policy_path),
            "reviewedCachePaths": [repo_relative(path) for path in reviewed_paths],
            "humanDecisionPaths": [repo_relative(path) for path in decision_paths],
            "relationshipTypeFilter": relationship_filter,
            "mixedReviewBlockedRelationshipTypes": sorted(blocked_mixed_types),
        },
        "outputs": {
            "jsonlPath": repo_relative(paths["jsonl"]),
            "markdownPath": repo_relative(paths["markdown"]),
            "decisionTemplatePath": repo_relative(paths["template"]),
            "summaryPath": repo_relative(paths["summary"]),
            "rowCount": len(rows),
        },
        "counts": dict(sorted(counters.items())),
        "relationshipTypeCounts": dict(
            sorted(Counter(str(row.get("relationshipType") or "") for row in rows).items())
        ),
        "reviewSourceCounts": dict(
            sorted(Counter(str(row.get("reviewSourceZhTw") or "") for row in rows).items())
        ),
        "reviewRules": {
            "rawQueueEligibleForHumanReview": bool(
                review_policy.get("rawQueueEligibleForHumanReview", False)
            ),
            "supportedReviewCacheEligible": bool(
                review_policy.get("supportedReviewCacheEligible", True)
            ),
            "proposalOnly": bool(review_policy.get("proposalOnly", False)),
        },
    }
    write_json(paths["summary"], summary)

    print(
        "[build_top50_stable_hard_review_bundle] "
        f"rows={len(rows)} keptSupported={counters['keptSupported']} "
        f"skippedResolved={counters['skippedResolvedByHuman']} canonicalWrites=false"
    )
    return 0


def first_source_locator(unit: dict[str, Any]) -> str:
    for source_ref in unit.get("sourceRefs") or []:
        if not isinstance(source_ref, dict):
            continue
        locator = str(source_ref.get("locator") or "").strip()
        if locator:
            return locator
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
