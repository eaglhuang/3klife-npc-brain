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
DEFAULT_PERSON_QUEUE_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-focus-semantic-review-queue.jsonl"
)
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-ruler-subject-historical-phase-lane.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Top50 historical-phase ruler-subject semantic review queue."
    )
    parser.add_argument("--person-queue", default=str(DEFAULT_PERSON_QUEUE_PATH))
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


def resolved_trust_keys(lane_policy: dict[str, Any]) -> set[str]:
    gap_policy = object_map(lane_policy.get("gapResolution"))
    if not bool(gap_policy.get("enabled", False)):
        return set()

    resolved: set[str] = set()
    resolved_verdicts = {
        str(item or "").strip()
        for item in string_list(gap_policy.get("resolvedVerdicts"))
        if str(item or "").strip()
    }
    resolved_actions = {
        str(item or "").strip()
        for item in string_list(gap_policy.get("resolvedDecisionActions"))
        if str(item or "").strip()
    }

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


def queue_filter_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("queueFilter"))


def review_output_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("reviewOutput"))


def candidate_allowed(candidate: dict[str, Any], lane_policy: dict[str, Any]) -> bool:
    filter_policy = queue_filter_policy(lane_policy)
    relationship_type = str(candidate.get("relationshipType") or "").strip()
    required_type = str(filter_policy.get("relationshipType") or "").strip()
    if required_type and relationship_type != required_type:
        return False
    required_hints = {
        str(item or "").strip()
        for item in string_list(filter_policy.get("requiredLaneHints"))
        if str(item or "").strip()
    }
    lane_hint = str(candidate.get("relationshipLaneHint") or "").strip()
    if required_hints and lane_hint not in required_hints:
        return False
    return True


def candidate_priority_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -float(candidate.get("focusGapCount") or 0.0),
        -float(candidate.get("focusQueuePriority") or 0.0),
        -float(candidate.get("scoreBeforeSemanticReview") or 0.0),
        str(candidate.get("trustKey") or ""),
    )


def filter_unit(
    unit: dict[str, Any],
    lane_policy: dict[str, Any],
    *,
    resolved_keys: set[str],
) -> dict[str, Any] | None:
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

    candidates = sorted(candidates, key=candidate_priority_key)
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
    focus_gap_ids = sorted(
        {
            focus_id
            for candidate in candidates
            for focus_id in string_list(candidate.get("focusGapGeneralIds"))
            if focus_id
        }
    )
    review_policy = review_output_policy(lane_policy)
    cloned = dict(unit)
    cloned["candidates"] = candidates
    cloned["allowedEntities"] = filtered_entities
    cloned["allowedRelationshipTypes"] = ["ruler_subject"]
    cloned["candidateCount"] = len(candidates)
    cloned["candidateMaxScoreBeforeSemanticReview"] = round(max(scores) if scores else 0.0, 3)
    cloned["focusQueuePriority"] = round(max(priorities) if priorities else 0.0, 3)
    cloned["primaryRelationshipType"] = "ruler_subject"
    cloned["focusGapGeneralIds"] = focus_gap_ids
    cloned["focusGapCount"] = len(focus_gap_ids)
    cloned["resolvedTrustKeysFilteredCount"] = filtered_out_count
    cloned["historicalPhaseOnly"] = True
    cloned["relationshipGovernanceMode"] = str(
        review_policy.get("relationshipGovernanceMode") or "historical-phase"
    )
    cloned["proposalOnly"] = bool(review_policy.get("proposalOnly", True))
    cloned["rawQueueEligibleForHumanReview"] = bool(
        review_policy.get("rawQueueEligibleForHumanReview", False)
    )
    cloned["supportedReviewCacheEligible"] = bool(
        review_policy.get("supportedReviewCacheEligible", True)
    )
    cloned["canonicalWrites"] = False
    return cloned


def unit_sort_key(
    unit: dict[str, Any],
    relationship_policy: dict[str, Any],
    lane_policy: dict[str, Any],
) -> tuple[Any, ...]:
    review_batch = object_map(lane_policy.get("reviewBatch"))
    prefer_gap = bool(review_batch.get("preferHigherFocusGapCount", True))
    prefer_sentence = bool(review_batch.get("preferHigherSentenceQuality", True))
    prefer_score = bool(review_batch.get("preferHigherScoreBeforeSemanticReview", True))
    return (
        -(int(unit.get("focusGapCount") or 0) if prefer_gap else 0),
        -float(unit.get("candidateMaxScoreBeforeSemanticReview") or 0.0) if prefer_score else 0.0,
        -float(unit.get("focusQueuePriority") or 0.0),
        -float(unit.get("sentenceQualityScore") or 0.0) if prefer_sentence else 0.0,
        *semantic_queue_sort_key(unit, relationship_policy),
    )


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def compact_sentence(value: Any, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_locator(unit: dict[str, Any]) -> str:
    for source_ref in unit.get("sourceRefs") or []:
        if not isinstance(source_ref, dict):
            continue
        locator = str(source_ref.get("locator") or "").strip()
        if locator:
            return locator
    return ""


def summary_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    outputs = object_map(summary.get("outputs"))
    lines = [
        "# Top50 歷史階段君臣候選審核表",
        "",
        "- 這份清單只收錄 `relationshipLaneHint=historical-phase` 的 `ruler_subject` 候選。",
        "- 治理語意是「階段關係」，用來描述投奔、跟從、侍奉、歸附等一段時期的依附，不是常數白名單。",
        "- 目前仍屬 `proposal-only` 的語意審查候選；會先送 semantic / Codex review，再視 supported 結果進後續人工決策。",
        "- `canonicalWrites=false`。",
        "",
        f"- 產生時間：`{summary.get('generatedAt')}`",
        f"- Queue 單元數：`{outputs.get('unitCount', 0)}`",
        f"- 候選 trustKey 數：`{outputs.get('candidateCount', 0)}`",
        f"- 已跳過已解決 trustKey：`{outputs.get('resolvedTrustKeySkipCount', 0)}`",
        "",
        "| # | trustKey | 主方 | 對方 | 句內 cue | 階段治理理由 | 分數 | 定位 | 原文 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | `{trust_key}` | {from_name} | {to_name} | {cue_terms} | {lane_reason} | {score} | `{locator}` | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(row.get("trustKey")),
                from_name=markdown_escape(row.get("fromNameZhTw")),
                to_name=markdown_escape(row.get("toNameZhTw")),
                cue_terms=markdown_escape("、".join(row.get("matchedCueTerms") or [])),
                lane_reason=markdown_escape(row.get("relationshipLaneReasonZhTw")),
                score=markdown_escape(f"{float(row.get('scoreBeforeSemanticReview') or 0.0):.1f}"),
                locator=markdown_escape(row.get("locator")),
                quote=markdown_escape(compact_sentence(row.get("sourceSentence"))),
            )
        )
    lines.append("")
    lines.append("## 使用方式")
    lines.append("")
    lines.append("1. 先把這份 queue 送進 semantic / Codex review packet。")
    lines.append("2. 只有 reviewed-cache `supported` 的列，才會再進 dedicated historical-phase 人工審核表。")
    lines.append("3. 通過後應寫入歷史階段 lane，而不是穩定白名單常數關係。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    person_queue_path = resolve_path(args.person_queue)
    relationship_policy_path = resolve_path(args.relationship_policy)
    lane_policy_path = resolve_path(args.lane_policy)
    relationship_policy = read_json(relationship_policy_path)
    lane_policy = read_json(lane_policy_path)
    resolved_keys = resolved_trust_keys(lane_policy)

    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else person_queue_path.parent
    output_names = object_map(lane_policy.get("outputs"))
    queue_path = output_root / (
        str(args.queue_file_name).strip()
        or str(output_names.get("queueFileName") or "top50-ruler-subject-historical-phase-queue.jsonl")
    )
    summary_path = output_root / (
        str(args.summary_file_name).strip()
        or str(output_names.get("summaryFileName") or "top50-ruler-subject-historical-phase-summary.json")
    )
    markdown_path = output_root / (
        str(args.markdown_file_name).strip()
        or str(output_names.get("markdownFileName") or "top50-ruler-subject-historical-phase-summary.zh-TW.md")
    )
    if not args.overwrite and (queue_path.exists() or summary_path.exists() or markdown_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    units: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    relationship_counts: Counter[str] = Counter()
    lane_reason_counts: Counter[str] = Counter()
    focus_counts: Counter[str] = Counter()
    selection_mode_counts: Counter[str] = Counter()
    resolved_skip_count = 0

    for row in read_jsonl(person_queue_path):
        if not isinstance(row, dict):
            continue
        filtered = filter_unit(row, lane_policy, resolved_keys=resolved_keys)
        if not filtered:
            continue
        units.append(filtered)
        resolved_skip_count += int(filtered.get("resolvedTrustKeysFilteredCount") or 0)
        locator = first_locator(filtered)
        for focus_id in string_list(filtered.get("focusGapGeneralIds")):
            focus_counts[focus_id] += 1
        for candidate in filtered.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            relationship_counts[str(candidate.get("relationshipType") or "")] += 1
            lane_reason_counts[str(candidate.get("relationshipLaneReasonZhTw") or "")] += 1
            selection_mode_counts[str(candidate.get("counterpartSelectionMode") or "unknown")] += 1
            candidate_rows.append(
                {
                    "trustKey": str(candidate.get("trustKey") or ""),
                    "fromId": str(candidate.get("fromId") or ""),
                    "toId": str(candidate.get("toId") or ""),
                    "fromNameZhTw": str(candidate.get("fromNameZhTw") or ""),
                    "toNameZhTw": str(candidate.get("toNameZhTw") or ""),
                    "matchedCueTerms": [
                        str(item or "").strip()
                        for item in candidate.get("matchedCueTerms") or []
                        if str(item or "").strip()
                    ],
                    "relationshipLaneReasonZhTw": str(candidate.get("relationshipLaneReasonZhTw") or ""),
                    "scoreBeforeSemanticReview": float(candidate.get("scoreBeforeSemanticReview") or 0.0),
                    "sourceSentence": str(filtered.get("sourceSentence") or ""),
                    "locator": locator,
                    "canonicalWrites": False,
                }
            )

    units = sorted(units, key=lambda item: unit_sort_key(item, relationship_policy, lane_policy))
    candidate_rows = sorted(
        candidate_rows,
        key=lambda item: (
            -float(item.get("scoreBeforeSemanticReview") or 0.0),
            str(item.get("trustKey") or ""),
        ),
    )

    version_metadata = build_version_metadata(
        schema_version="top50-ruler-subject-historical-phase-queue.v1",
        artifact_paths=[person_queue_path, relationship_policy_path, lane_policy_path],
        repo_root=REPO_ROOT,
    )
    for unit in units:
        unit.update(version_metadata)
    for row in candidate_rows:
        row.update(version_metadata)

    write_jsonl(queue_path, units)
    summary = {
        "mode": "top50-ruler-subject-historical-phase-queue-builder",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "personQueuePath": repo_relative(person_queue_path),
            "relationshipPolicyPath": repo_relative(relationship_policy_path),
            "lanePolicyPath": repo_relative(lane_policy_path)
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
            "unitCount": len(units),
            "candidateCount": len(candidate_rows),
            "resolvedTrustKeySkipCount": resolved_skip_count,
            "proposalOnly": True,
            "rawQueueEligibleForHumanReview": False,
            "supportedReviewCacheEligible": True,
            "relationshipGovernanceMode": "historical-phase"
        },
        "counts": {
            "candidateRelationshipTypeCounts": dict(sorted(relationship_counts.items())),
            "laneReasonCounts": dict(sorted(lane_reason_counts.items())),
            "counterpartSelectionModeCounts": dict(sorted(selection_mode_counts.items())),
            "focusQueueUnitCountsTop20": dict(focus_counts.most_common(20))
        }
    }
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(summary_markdown(summary, candidate_rows), encoding="utf-8")

    print(
        "[build_top50_ruler_subject_historical_phase_queue] "
        f"units={len(units)} candidates={len(candidate_rows)} resolvedSkipped={resolved_skip_count} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
