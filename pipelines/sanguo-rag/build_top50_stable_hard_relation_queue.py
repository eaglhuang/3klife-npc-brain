from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata
from run_relationship_semantic_review_cache import (
    object_map,
    read_json,
    read_jsonl,
    semantic_queue_sort_key,
    string_list,
    write_json,
    write_jsonl,
)


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_PERSON_QUEUE_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-focus-semantic-review-queue.jsonl"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-hard-baseline-lane.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Top50 stable hard-relation-only semantic review queue.")
    parser.add_argument("--person-queue", default=str(DEFAULT_PERSON_QUEUE_PATH))
    parser.add_argument("--faction-queue", default="")
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--lane-policy", default=str(DEFAULT_LANE_POLICY_PATH))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--queue-file-name", default="")
    parser.add_argument("--summary-file-name", default="")
    parser.add_argument("--markdown-file-name", default="")
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


def valid_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return {}
    return read_json(path)


def relationship_type_priority_map(lane_policy: dict[str, Any]) -> dict[str, int]:
    review_batch = object_map(lane_policy.get("reviewBatch"))
    order = [str(item or "").strip() for item in string_list(review_batch.get("relationshipTypePriority")) if str(item or "").strip()]
    return {relationship_type: index for index, relationship_type in enumerate(order)}


def review_output_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("reviewOutput"))


def resolved_trust_keys(lane_policy: dict[str, Any]) -> set[str]:
    gap_policy = object_map(lane_policy.get("gapResolution"))
    if not bool(gap_policy.get("enabled", False)):
        return set()

    resolved: set[str] = set()
    resolved_verdicts = {str(item or "").strip() for item in string_list(gap_policy.get("resolvedVerdicts")) if str(item or "").strip()}
    resolved_actions = {str(item or "").strip() for item in string_list(gap_policy.get("resolvedDecisionActions")) if str(item or "").strip()}

    for path_text in string_list(gap_policy.get("reviewedCachePaths")):
        path = resolve_path(path_text)
        if not path.exists():
            continue
        for row in read_jsonl(path):
            if not isinstance(row, dict):
                continue
            for relation in valid_rows(row.get("relationships")):
                trust_key = str(relation.get("trustKey") or "").strip()
                verdict = str(relation.get("verdict") or "").strip()
                if trust_key and verdict in resolved_verdicts:
                    resolved.add(trust_key)

    for path_text in string_list(gap_policy.get("humanDecisionPaths")):
        path = resolve_path(path_text)
        if not path.exists():
            continue
        payload = read_json_if_exists(path)
        if not isinstance(payload, dict):
            continue
        for command in valid_rows(payload.get("commands")):
            trust_key = str(command.get("trustKey") or "").strip()
            action = str(command.get("action") or "").strip()
            if trust_key and action in resolved_actions:
                resolved.add(trust_key)
    return resolved


def candidate_allowed(candidate: dict[str, Any], lane_policy: dict[str, Any]) -> bool:
    hard_types = {str(item or "").strip() for item in string_list(lane_policy.get("hardRelationshipTypes")) if str(item or "").strip()}
    relationship_type = str(candidate.get("relationshipType") or "").strip()
    if relationship_type not in hard_types:
        return False
    if relationship_type != "ruler_subject":
        return True

    ruler_policy = object_map(lane_policy.get("stableRulerSubject"))
    blocked = {str(item or "").strip() for item in string_list(ruler_policy.get("blockedLaneHints"))}
    required = {str(item or "").strip() for item in string_list(ruler_policy.get("requiredLaneHints"))}
    lane_hint = str(candidate.get("relationshipLaneHint") or "").strip()
    if lane_hint in blocked:
        return False
    if required and lane_hint not in required:
        return False
    return bool(candidate.get("stableBaselineEligible", False)) or not lane_hint


def candidate_priority_key(candidate: dict[str, Any], priority_map: dict[str, int]) -> tuple[Any, ...]:
    relationship_type = str(candidate.get("relationshipType") or "").strip()
    return (
        priority_map.get(relationship_type, 999),
        -float(candidate.get("focusGapCount") or 0.0),
        -float(candidate.get("focusQueuePriority") or 0.0),
        -float(candidate.get("scoreBeforeSemanticReview") or 0.0),
        relationship_type,
        str(candidate.get("trustKey") or ""),
    )


def filter_unit(
    unit: dict[str, Any],
    lane_policy: dict[str, Any],
    *,
    resolved_keys: set[str],
    priority_map: dict[str, int],
) -> dict[str, Any] | None:
    review_batch = object_map(lane_policy.get("reviewBatch"))
    candidates: list[dict[str, Any]] = []
    filtered_out_count = 0
    for candidate in unit.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if not candidate_allowed(candidate, lane_policy):
            continue
        trust_key = str(candidate.get("trustKey") or "").strip()
        if trust_key and trust_key in resolved_keys:
            filtered_out_count += 1
            continue
        candidates.append(candidate)
    if not candidates:
        return None

    candidates = sorted(candidates, key=lambda item: candidate_priority_key(item, priority_map))
    primary_relationship_type = str(candidates[0].get("relationshipType") or "").strip()
    if bool(review_batch.get("pruneToPrimaryRelationshipType", False)):
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("relationshipType") or "").strip() == primary_relationship_type
        ]

    allowed_ids = {
        str(candidate.get(field) or "").strip()
        for candidate in candidates
        for field in ("fromId", "toId", "subjectId", "controllerId")
        if str(candidate.get(field) or "").strip()
    }
    filtered_entities = [
        row
        for row in unit.get("allowedEntities") or []
        if isinstance(row, dict) and str(row.get("entityId") or "").strip() in allowed_ids
    ]

    scores = [float(candidate.get("scoreBeforeSemanticReview") or 0.0) for candidate in candidates]
    priorities = [float(candidate.get("focusQueuePriority") or 0.0) for candidate in candidates]
    relationship_types = sorted(
        {
            str(candidate.get("relationshipType") or "").strip()
            for candidate in candidates
            if str(candidate.get("relationshipType") or "").strip()
        }
    )
    focus_gap_ids = sorted(
        {
            focus_id
            for candidate in candidates
            for focus_id in string_list(candidate.get("focusGapGeneralIds"))
            if focus_id
        }
    )

    cloned = dict(unit)
    cloned["candidates"] = candidates
    cloned["allowedEntities"] = filtered_entities
    cloned["allowedRelationshipTypes"] = relationship_types
    cloned["candidateCount"] = len(candidates)
    cloned["candidateMaxScoreBeforeSemanticReview"] = round(max(scores) if scores else 0.0, 3)
    cloned["focusQueuePriority"] = round(max(priorities) if priorities else 0.0, 3)
    cloned["primaryRelationshipType"] = primary_relationship_type
    cloned["focusGapGeneralIds"] = focus_gap_ids
    cloned["focusGapCount"] = len(focus_gap_ids)
    cloned["resolvedTrustKeysFilteredCount"] = filtered_out_count
    cloned["hardBaselineOnly"] = True
    cloned["gapOnlyApplied"] = True
    cloned["proposalOnly"] = bool(review_output_policy(lane_policy).get("proposalOnly", False))
    cloned["rawQueueEligibleForHumanReview"] = bool(
        review_output_policy(lane_policy).get("rawQueueEligibleForHumanReview", False)
    )
    cloned["canonicalWrites"] = False
    return cloned


def unit_sort_key(
    unit: dict[str, Any],
    priority_map: dict[str, int],
    relationship_policy: dict[str, Any],
    lane_policy: dict[str, Any],
) -> tuple[Any, ...]:
    review_batch = object_map(lane_policy.get("reviewBatch"))
    primary_type = str(unit.get("primaryRelationshipType") or "").strip()
    prefer_gap = bool(review_batch.get("preferHigherFocusGapCount", True))
    prefer_sentence = bool(review_batch.get("preferHigherSentenceQuality", True))
    return (
        priority_map.get(primary_type, 999),
        -(int(unit.get("focusGapCount") or 0) if prefer_gap else 0),
        -float(unit.get("candidateMaxScoreBeforeSemanticReview") or 0.0),
        -float(unit.get("focusQueuePriority") or 0.0),
        -float(unit.get("sentenceQualityScore") or 0.0) if prefer_sentence else 0.0,
        *semantic_queue_sort_key(unit, relationship_policy),
    )


def summary_markdown(summary: dict[str, Any]) -> str:
    outputs = object_map(summary.get("outputs"))
    relationship_counts = object_map(outputs.get("candidateRelationshipTypeCounts"))
    primary_counts = object_map(outputs.get("primaryRelationshipTypeCounts"))
    focus_counts = object_map(outputs.get("focusQueueUnitCountsTop20"))
    type_priority = outputs.get("relationshipTypePriority") or []
    lines = [
        "# Top50 硬關係缺口批次 Queue 摘要",
        "",
        f"- 產生時間：{summary.get('generatedAt')}",
        f"- Queue 單元數：{outputs.get('unitCount', 0)}",
        f"- 已跳過已審或已決策 trustKey：{outputs.get('resolvedTrustKeySkipCount', 0)}",
        f"- 人物中心 queue：{outputs.get('personQueuePath', '')}",
    ]
    faction_queue = str(outputs.get("factionQueuePath") or "").strip()
    if faction_queue:
        lines.append(f"- 陣營 queue：{faction_queue}")
    if isinstance(type_priority, list) and type_priority:
        lines.append(f"- 型別優先順序：{' -> '.join(str(item) for item in type_priority)}")
    lines.extend(["", "## 候選型別總量"])
    for relationship_type, count in sorted(relationship_counts.items(), key=lambda item: (-int(item[1]), item[0])):
        lines.append(f"- {relationship_type}：{count}")
    lines.extend(["", "## Queue 主型別分布"])
    for relationship_type, count in sorted(primary_counts.items(), key=lambda item: (-int(item[1]), item[0])):
        lines.append(f"- {relationship_type}：{count}")
    lines.extend(["", "## 焦點人物 Top20"])
    for focus_id, count in focus_counts.items():
        lines.append(f"- {focus_id}：{count}")
    lines.extend(
        [
            "",
            "- 目前 queue 已改成 gap-only，會先跳過已經進 reviewed cache 或人工白黑名單決策的 trustKey。",
            "- 每個單元只保留一種主要關係型別，避免同批混進太多不同關係語意。",
            "- `ruler_subject` 只保留 `stable-baseline`，會排除 `historical-phase`。",
            "- `canonicalWrites=false`，後續仍需 semantic / skill / human review。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    person_queue_path = resolve_path(args.person_queue)
    relationship_policy_path = resolve_path(args.relationship_policy)
    lane_policy_path = resolve_path(args.lane_policy)
    relationship_policy = read_json(relationship_policy_path)
    lane_policy = read_json(lane_policy_path)
    priority_map = relationship_type_priority_map(lane_policy)
    resolved_keys = resolved_trust_keys(lane_policy)

    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else person_queue_path.parent
    output_names = object_map(lane_policy.get("outputs"))
    queue_path = output_root / (str(args.queue_file_name).strip() or str(output_names.get("stableHardQueueFileName") or "top50-stable-hard-relation-queue.jsonl"))
    summary_path = output_root / (str(args.summary_file_name).strip() or str(output_names.get("stableHardSummaryFileName") or "top50-stable-hard-relation-summary.json"))
    markdown_path = output_root / (str(args.markdown_file_name).strip() or str(output_names.get("stableHardMarkdownFileName") or "top50-stable-hard-relation-summary.zh-TW.md"))
    if not args.overwrite and (queue_path.exists() or summary_path.exists() or markdown_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    units: list[dict[str, Any]] = []
    for row in read_jsonl(person_queue_path):
        if not isinstance(row, dict):
            continue
        filtered = filter_unit(row, lane_policy, resolved_keys=resolved_keys, priority_map=priority_map)
        if filtered:
            units.append(filtered)

    faction_queue_path = Path()
    if str(args.faction_queue).strip():
        faction_queue_path = resolve_path(args.faction_queue)
        for row in read_jsonl(faction_queue_path):
            if not isinstance(row, dict):
                continue
            filtered = filter_unit(row, lane_policy, resolved_keys=resolved_keys, priority_map=priority_map)
            if filtered:
                units.append(filtered)

    units = sorted(units, key=lambda item: unit_sort_key(item, priority_map, relationship_policy, lane_policy))
    relationship_counts: Counter[str] = Counter()
    primary_type_counts: Counter[str] = Counter()
    focus_counts: Counter[str] = Counter()
    selection_mode_counts: Counter[str] = Counter()
    for unit in units:
        primary_type_counts[str(unit.get("primaryRelationshipType") or "")] += 1
        for focus_id in string_list(unit.get("focusGapGeneralIds")):
            focus_counts[focus_id] += 1
        for candidate in unit.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            relationship_counts[str(candidate.get("relationshipType") or "")] += 1
            selection_mode_counts[str(candidate.get("counterpartSelectionMode") or "unknown")] += 1

    version_metadata = build_version_metadata(
        schema_version="top50-stable-hard-relation-queue.v1",
        artifact_paths=[person_queue_path, relationship_policy_path, lane_policy_path],
        repo_root=REPO_ROOT,
    )
    for unit in units:
        unit.update(version_metadata)

    write_jsonl(queue_path, units)
    summary = {
        "mode": "top50-stable-hard-relation-queue-builder",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "personQueuePath": repo_relative(person_queue_path),
            "relationshipPolicyPath": repo_relative(relationship_policy_path),
            "lanePolicyPath": repo_relative(lane_policy_path),
            "factionQueuePath": repo_relative(faction_queue_path) if str(faction_queue_path) else "",
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
            "unitCount": len(units),
            "personQueuePath": repo_relative(person_queue_path),
            "factionQueuePath": repo_relative(faction_queue_path) if str(faction_queue_path) else "",
            "candidateRelationshipTypeCounts": dict(sorted(relationship_counts.items())),
            "primaryRelationshipTypeCounts": dict(sorted(primary_type_counts.items())),
            "counterpartSelectionModeCounts": dict(sorted(selection_mode_counts.items())),
            "focusQueueUnitCountsTop20": dict(list(sorted(focus_counts.items(), key=lambda item: (-item[1], item[0]))[:20])),
            "resolvedTrustKeySkipCount": len(resolved_keys),
            "relationshipTypePriority": [key for key, _ in sorted(priority_map.items(), key=lambda item: item[1])],
            "proposalOnly": bool(review_output_policy(lane_policy).get("proposalOnly", False)),
            "rawQueueEligibleForHumanReview": bool(
                review_output_policy(lane_policy).get("rawQueueEligibleForHumanReview", False)
            ),
            "supportedReviewCacheEligible": bool(
                review_output_policy(lane_policy).get("supportedReviewCacheEligible", True)
            ),
            "mixedReviewBlockedRelationshipTypes": string_list(
                review_output_policy(lane_policy).get("mixedReviewBlockedRelationshipTypes")
            ),
            "highRiskRelationshipTypes": string_list(
                review_output_policy(lane_policy).get("highRiskRelationshipTypes")
            ),
        },
    }
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(summary_markdown(summary), encoding="utf-8")
    print(
        "[build_top50_stable_hard_relation_queue] "
        f"units={len(units)} focuses={len(focus_counts)} skippedResolved={len(resolved_keys)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
