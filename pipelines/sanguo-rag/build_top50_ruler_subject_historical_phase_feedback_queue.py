from __future__ import annotations

import argparse
import hashlib
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
    string_list,
    write_json,
    write_jsonl,
)


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_REVIEWED_CACHE_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/top50-ruler-subject-historical-phase-reviewed-cache.jsonl"
)
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-ruler-subject-historical-phase-lane.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build feedback queue from historical-phase extractedRelationships."
    )
    parser.add_argument("--reviewed-cache-path", action="append", default=[])
    parser.add_argument("--lane-policy", default=str(DEFAULT_LANE_POLICY_PATH))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--queue-file-name", default="top50-ruler-subject-historical-phase-feedback-queue.jsonl")
    parser.add_argument("--summary-file-name", default="top50-ruler-subject-historical-phase-feedback-summary.json")
    parser.add_argument(
        "--markdown-file-name",
        default="top50-ruler-subject-historical-phase-feedback-summary.zh-TW.md",
    )
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


def review_output_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("reviewOutput"))


def valid_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return {}
    return read_json(path)


def human_locked_trust_keys(lane_policy: dict[str, Any]) -> set[str]:
    gap_policy = object_map(lane_policy.get("gapResolution"))
    if not bool(gap_policy.get("enabled", False)):
        return set()

    resolved: set[str] = set()
    resolved_actions = {
        str(item or "").strip()
        for item in string_list(gap_policy.get("resolvedDecisionActions"))
        if str(item or "").strip()
    }

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


def split_cue_terms(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    normalized = value
    for token in ["、", "，", ",", "；", ";", "/", "|"]:
        normalized = normalized.replace(token, " ")
    return [part for part in normalized.split() if part]


def first_locator(unit: dict[str, Any]) -> str:
    for source_ref in unit.get("sourceRefs") or []:
        if not isinstance(source_ref, dict):
            continue
        locator = str(source_ref.get("locator") or "").strip()
        if locator:
            return locator
    return ""


def filtered_entities(unit: dict[str, Any], from_id: str, to_id: str, from_name: str, to_name: str) -> list[dict[str, Any]]:
    entity_map: dict[str, dict[str, Any]] = {}
    for row in unit.get("allowedEntities") or []:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("entityId") or "").strip()
        if entity_id:
            entity_map[entity_id] = dict(row)
    if from_id and from_id not in entity_map:
        entity_map[from_id] = {
            "entityId": from_id,
            "nameZhTw": from_name,
            "aliasesZhTw": [from_name] if from_name else [from_id],
            "roleHints": ["from", "controller"],
            "canonicalWrites": False,
            "scopedAmbiguousAliasesZhTw": [],
        }
    if to_id and to_id not in entity_map:
        entity_map[to_id] = {
            "entityId": to_id,
            "nameZhTw": to_name,
            "aliasesZhTw": [to_name] if to_name else [to_id],
            "roleHints": ["to", "subject"],
            "canonicalWrites": False,
            "scopedAmbiguousAliasesZhTw": [],
        }
    return [entity_map[key] for key in [from_id, to_id] if key in entity_map]


def feedback_unit_id(base_unit_id: str, trust_key: str) -> str:
    digest = hashlib.sha1(f"{base_unit_id}|{trust_key}".encode("utf-8")).hexdigest()[:12]
    return f"{base_unit_id}.feedback.{digest}"


def compact_sentence(value: Any, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def summary_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    outputs = object_map(summary.get("outputs"))
    lines = [
        "# Top50 歷史階段 extractedRelationships 回饋候選表",
        "",
        "- 來源是 historical-phase reviewed-cache 裡的 `extractedRelationships`。",
        "- 用途是把一個句子裡真正成立、但原始 pair 候選沒綁到的人際主從，回灌成新的 phase 候選 queue。",
        "- 這些列仍是 `proposal-only`，後續還要再進 semantic / Codex review。",
        "- `canonicalWrites=false`。",
        "",
        f"- 產生時間：`{summary.get('generatedAt')}`",
        f"- 回饋 unit 數：`{outputs.get('unitCount', 0)}`",
        f"- 不重複 trustKey：`{outputs.get('distinctTrustKeyCount', 0)}`",
        "",
        "| # | trustKey | 主方 | 對方 | 分數 | cue | 定位 | 原句 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | `{trust_key}` | {from_name} | {to_name} | {score} | {cue} | `{locator}` | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(row.get("trustKey")),
                from_name=markdown_escape(row.get("fromNameZhTw")),
                to_name=markdown_escape(row.get("toNameZhTw")),
                score=markdown_escape(f"{float(row.get('scoreBeforeSemanticReview') or 0.0):.1f}"),
                cue=markdown_escape("、".join(row.get("matchedCueTerms") or [])),
                locator=markdown_escape(row.get("locator")),
                quote=markdown_escape(compact_sentence(row.get("sourceSentence"))),
            )
        )
    lines.append("")
    lines.append("## 說明")
    lines.append("")
    lines.append("1. 這份 queue 的目的不是覆蓋原 reviewed 結果，而是補出原句真正成立的 phase 關係。")
    lines.append("2. 已被 reviewed-cache 或人類決策解決的 trustKey 會自動跳過。")
    lines.append("3. 這能把『一句多段歷史依附』從打掉錯 pair，提升成產出正確新候選。")
    lines.append("")
    return "\n".join(lines) + "\n"


def reviewed_cache_paths(args: argparse.Namespace) -> list[Path]:
    configured = [resolve_path(path_text) for path_text in args.reviewed_cache_path if str(path_text).strip()]
    if configured:
        return configured
    return [DEFAULT_REVIEWED_CACHE_PATH]


def main() -> int:
    args = parse_args()
    lane_policy_path = resolve_path(args.lane_policy)
    lane_policy = read_json(lane_policy_path)
    resolved_keys = human_locked_trust_keys(lane_policy)
    source_paths = reviewed_cache_paths(args)
    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else source_paths[0].parent.parent
    queue_path = output_root / str(args.queue_file_name).strip()
    summary_path = output_root / str(args.summary_file_name).strip()
    markdown_path = output_root / str(args.markdown_file_name).strip()

    if not args.overwrite and any(path.exists() for path in [queue_path, summary_path, markdown_path]):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    review_policy = review_output_policy(lane_policy)
    units: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []
    kept_keys: set[str] = set()
    counts: Counter[str] = Counter()

    for source_path in source_paths:
        for unit in read_jsonl(source_path):
            if not bool(unit.get("semanticReviewPerformed")):
                counts["skippedNotReviewed"] += 1
                continue
            if str(object_map(unit.get("reviewer")).get("provider") or "").strip() != "codex-skill":
                counts["skippedNonCodex"] += 1
                continue
            focus_gap_ids = string_list(unit.get("focusGapGeneralIds"))
            for relation in valid_rows(unit.get("extractedRelationships")):
                relationship_type = str(relation.get("relationshipType") or "").strip()
                if relationship_type != "ruler_subject":
                    counts["skippedNonRulerSubject"] += 1
                    continue
                if bool(relation.get("stableRelation")):
                    counts["skippedStableRelation"] += 1
                    continue
                trust_key = f"ruler_subject:{str(relation.get('fromId') or '').strip()}:{str(relation.get('toId') or '').strip()}"
                if trust_key in resolved_keys:
                    counts["skippedResolved"] += 1
                    continue
                if trust_key in kept_keys:
                    counts["skippedDuplicate"] += 1
                    continue
                from_id = str(relation.get("fromId") or "").strip()
                to_id = str(relation.get("toId") or "").strip()
                if not from_id or not to_id:
                    counts["skippedMissingPair"] += 1
                    continue
                from_name = str(relation.get("fromNameZhTw") or from_id).strip()
                to_name = str(relation.get("toNameZhTw") or to_id).strip()
                score = float(relation.get("semanticTrustScore") or 0.0)
                confidence = float(relation.get("confidence") or 0.0)
                matched_terms = split_cue_terms(str(relation.get("relationshipCueSpanZhTw") or ""))
                locator = first_locator(unit)
                candidate = {
                    "canonicalWrites": False,
                    "claimSentenceZhTw": str(
                        relation.get("normalizedClaimZhTw")
                        or f"{from_name}是{to_name}的主君或上位者"
                    ),
                    "controllerId": from_id,
                    "controllerNameZhTw": from_name,
                    "counterpartSelectionMode": "semantic-feedback-extracted",
                    "focusGapCount": len(focus_gap_ids),
                    "focusGapGeneralIds": focus_gap_ids,
                    "focusQueuePriority": round(score, 3),
                    "fromId": from_id,
                    "fromNameZhTw": from_name,
                    "matchedCueTermCount": len(matched_terms),
                    "matchedCueTerms": matched_terms,
                    "relationshipLaneHint": "historical-phase",
                    "relationshipLaneReasonZhTw": "來自 historical-phase reviewed-cache 的 extractedRelationships 回饋。",
                    "relationshipType": "ruler_subject",
                    "scoreBeforeSemanticReview": round(score, 3),
                    "stableBaselineEligible": False,
                    "strictDirectionCheckZhTw": "只有句子明確表示 from 是主君、君主、上位者或任命者，to 是其臣屬、部下、受命者時，才可支持此方向。",
                    "subjectId": to_id,
                    "subjectNameZhTw": to_name,
                    "toId": to_id,
                    "toNameZhTw": to_name,
                    "trustKey": trust_key,
                    "feedbackSource": "extractedRelationships",
                    "feedbackSourceSemanticTrustScore": round(score, 3),
                    "feedbackSourceConfidence": round(confidence, 4),
                }
                feedback_unit = {
                    "semanticReviewBaseUnitId": str(unit.get("semanticReviewUnitId") or ""),
                    "semanticReviewUnitId": feedback_unit_id(str(unit.get("semanticReviewUnitId") or ""), trust_key),
                    "promptVersion": f"{str(unit.get('promptVersion') or 'relationship-semantic-review')}.feedback",
                    "sentenceHash": str(unit.get("sentenceHash") or ""),
                    "sourceSentence": str(relation.get("evidenceSentence") or unit.get("sourceSentence") or ""),
                    "sentenceQualityScore": round(score, 3),
                    "sourceRefs": unit.get("sourceRefs") or [],
                    "allowedEntities": filtered_entities(unit, from_id, to_id, from_name, to_name),
                    "allowedRelationshipTypes": ["ruler_subject"],
                    "candidates": [candidate],
                    "candidateCount": 1,
                    "candidateMaxScoreBeforeSemanticReview": round(score, 3),
                    "focusQueuePriority": round(score, 3),
                    "primaryRelationshipType": "ruler_subject",
                    "focusGapGeneralIds": focus_gap_ids,
                    "focusGapCount": len(focus_gap_ids),
                    "reviewMode": "sentence-relation-extraction",
                    "historicalPhaseOnly": True,
                    "relationshipGovernanceMode": str(
                        review_policy.get("relationshipGovernanceMode") or "historical-phase"
                    ),
                    "proposalOnly": True,
                    "rawQueueEligibleForHumanReview": False,
                    "supportedReviewCacheEligible": bool(
                        review_policy.get("supportedReviewCacheEligible", True)
                    ),
                    "feedbackGeneratedFromReviewedCache": repo_relative(source_path),
                    "canonicalWrites": False,
                }
                units.append(feedback_unit)
                kept_keys.add(trust_key)
                flat_rows.append(
                    {
                        "trustKey": trust_key,
                        "fromNameZhTw": from_name,
                        "toNameZhTw": to_name,
                        "scoreBeforeSemanticReview": round(score, 3),
                        "matchedCueTerms": matched_terms,
                        "locator": locator,
                        "sourceSentence": feedback_unit["sourceSentence"],
                    }
                )
                counts["kept"] += 1

    units.sort(
        key=lambda item: (
            -float(item.get("candidateMaxScoreBeforeSemanticReview") or 0.0),
            -float(item.get("focusQueuePriority") or 0.0),
            str(item.get("semanticReviewUnitId") or ""),
        )
    )
    flat_rows.sort(
        key=lambda item: (
            -float(item.get("scoreBeforeSemanticReview") or 0.0),
            str(item.get("trustKey") or ""),
        )
    )

    version_metadata = build_version_metadata(
        schema_version="top50-ruler-subject-historical-phase-feedback-queue.v1",
        artifact_paths=[lane_policy_path, *source_paths],
        repo_root=REPO_ROOT,
    )
    for unit in units:
        unit.update(version_metadata)

    write_jsonl(queue_path, units)
    summary = {
        "mode": "top50-ruler-subject-historical-phase-feedback-queue-builder",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "lanePolicyPath": repo_relative(lane_policy_path),
            "reviewedCachePaths": [repo_relative(path) for path in source_paths],
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
            "unitCount": len(units),
            "distinctTrustKeyCount": len(kept_keys),
            "proposalOnly": True,
            "rawQueueEligibleForHumanReview": False,
            "supportedReviewCacheEligible": True,
            "relationshipGovernanceMode": "historical-phase",
        },
        "counts": dict(sorted(counts.items())),
        "relationshipTypeCounts": {"ruler_subject": len(units)},
    }
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(summary_markdown(summary, flat_rows), encoding="utf-8")

    print(
        "[build_top50_ruler_subject_historical_phase_feedback_queue] "
        f"units={len(units)} kept={counts['kept']} resolvedSkipped={counts['skippedResolved']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
