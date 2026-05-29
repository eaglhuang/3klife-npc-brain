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
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-ruler-subject-historical-phase-lane.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Top50 historical-phase ruler-subject human-review bundles from reviewed-cache supported rows only."
    )
    parser.add_argument("--lane-policy", default=str(DEFAULT_LANE_POLICY_PATH))
    parser.add_argument("--reviewed-cache-path", action="append", default=[])
    parser.add_argument("--human-decisions-path", action="append", default=[])
    parser.add_argument("--output-root", default="")
    parser.add_argument("--tag", default="reviewed-supported")
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


def outputs_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("outputs"))


def review_output_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("reviewOutput"))


def gap_resolution_policy(lane_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(lane_policy.get("gapResolution"))


def reviewed_cache_paths(args: argparse.Namespace, lane_policy: dict[str, Any]) -> list[Path]:
    configured = [resolve_path(path_text) for path_text in args.reviewed_cache_path if str(path_text).strip()]
    if configured:
        return configured
    gap_policy = gap_resolution_policy(lane_policy)
    return [resolve_path(path_text) for path_text in string_list(gap_policy.get("reviewedCachePaths"))]


def human_decision_paths(args: argparse.Namespace, lane_policy: dict[str, Any]) -> list[Path]:
    configured = [resolve_path(path_text) for path_text in args.human_decisions_path if str(path_text).strip()]
    if configured:
        return configured
    gap_policy = gap_resolution_policy(lane_policy)
    return [resolve_path(path_text) for path_text in string_list(gap_policy.get("humanDecisionPaths"))]


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
    return "語意審核快取匯入"


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def compact_sentence(value: Any, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_source_locator(unit: dict[str, Any]) -> str:
    for source_ref in unit.get("sourceRefs") or []:
        if not isinstance(source_ref, dict):
            continue
        locator = str(source_ref.get("locator") or "").strip()
        if locator:
            return locator
    return ""


def output_paths(output_root: Path, lane_policy: dict[str, Any], tag: str) -> dict[str, Path]:
    outputs = outputs_policy(lane_policy)
    stem = f"top50-ruler-subject-historical-phase-{tag}"
    return {
        "jsonl": output_root / str(outputs.get("reviewBundleFileName") or f"{stem}.jsonl"),
        "markdown": output_root / str(outputs.get("reviewBundleMarkdownFileName") or f"{stem}.zh-TW.md"),
        "template": output_root / str(outputs.get("reviewBundleDecisionTemplateFileName") or f"{stem}.template.json"),
        "summary": output_root / f"{stem}.summary.json",
    }


def render_markdown(rows: list[dict[str, Any]], title: str, notes: str) -> str:
    lines = [
        f"# {title}",
        "",
        "- 這份表只收錄 `reviewed-cache verdict=supported` 的歷史階段 `ruler_subject` 候選。",
        "- 這些關係描述的是某一時段的依附，不等同永久穩定白名單。",
        "- `proposal-only` raw queue 不得直接進這張人工審核表。",
        f"- {notes}",
        "- `canonicalWrites=false`。",
        "",
        "| # | trustKey | 主方 | 對方 | 分數 | 信心 | 審核來源 | 定位 | 證據句 | 階段治理理由 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | `{trust_key}` | {from_name} | {to_name} | {score} | {confidence} | {source} | `{locator}` | {quote} | {reason} |".format(
                idx=index,
                trust_key=markdown_escape(row.get("trustKey")),
                from_name=markdown_escape(row.get("fromNameZhTw")),
                to_name=markdown_escape(row.get("toNameZhTw")),
                score=markdown_escape(f"{number_value(row.get('semanticTrustScore')):.1f}"),
                confidence=markdown_escape(f"{number_value(row.get('confidence')):.2f}"),
                source=markdown_escape(row.get("reviewSourceZhTw")),
                locator=markdown_escape(row.get("locator")),
                quote=markdown_escape(compact_sentence(row.get("evidenceSentenceZhTw"))),
                reason=markdown_escape(row.get("relationshipLaneReasonZhTw")),
            )
        )
    lines.append("")
    lines.append("## 審核規則")
    lines.append("")
    lines.append("1. 若證據句確實支撐此歷史階段依附，決策填 `approved`。")
    lines.append("2. 若證據句不支撐、方向錯誤、其實只是結盟或另種關係，決策填 `rejected`。")
    lines.append("3. 通過後應進歷史階段 lane，不應直接當成 stable-baseline 常數白名單。")
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
                "reviewNotesZhTw": f"{tag} 僅允許 reviewed-cache supported / Codex 語意審核匯入；治理模式=historical-phase",
                "canonicalWrites": False,
            }
        )
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": f"top50-ruler-subject-historical-phase-human-decisions.{tag}.template",
        "decisionField": "decision",
        "commandField": "action",
        "approvedStatuses": ["approved"],
        "rejectedStatuses": ["rejected"],
        "availableCommands": {
            "forceWhitelistActions": ["force-whitelist"],
            "forceBlacklistActions": ["force-blacklist"],
            "removeFromIndexActions": ["remove-from-index"]
        },
        "canonicalWrites": False,
        "commands": [],
        "decisions": decisions
    }


def main() -> int:
    args = parse_args()
    lane_policy_path = resolve_path(args.lane_policy)
    lane_policy = read_json(lane_policy_path)
    review_policy = review_output_policy(lane_policy)
    reviewed_paths = reviewed_cache_paths(args, lane_policy)
    decision_paths = human_decision_paths(args, lane_policy)
    resolved_keys = resolved_human_keys(decision_paths)

    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else reviewed_paths[0].parent
    tag = str(args.tag or "reviewed-supported").strip()
    paths = output_paths(output_root, lane_policy, tag)
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
                if str(candidate.get("relationshipType") or "").strip() != "ruler_subject":
                    counters["skippedNotRulerSubject"] += 1
                    continue
                if str(candidate.get("relationshipLaneHint") or "").strip() != "historical-phase":
                    counters["skippedNotHistoricalPhase"] += 1
                    continue
                score, confidence = semantic_score_pair(relation)
                from_id = str(relation.get("fromId") or candidate.get("fromId") or "").strip()
                to_id = str(relation.get("toId") or candidate.get("toId") or "").strip()
                row = {
                    "trustKey": trust_key,
                    "relationshipType": "ruler_subject",
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
                    "relationshipLaneHint": "historical-phase",
                    "relationshipLaneReasonZhTw": str(candidate.get("relationshipLaneReasonZhTw") or ""),
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
            -number_value(item.get("semanticTrustScore")),
            -number_value(item.get("confidence")),
            str(item.get("trustKey") or "")
        ),
    )
    rows = rows[: max(1, int(args.max_rows))]

    version_metadata = build_version_metadata(
        schema_version="top50-ruler-subject-historical-phase-review-bundle.v1",
        artifact_paths=[lane_policy_path, *reviewed_paths, *decision_paths],
        repo_root=REPO_ROOT,
    )
    for row in rows:
        row.update(version_metadata)

    title = str(review_policy.get("humanReviewTitleZhTw") or "Top50 歷史階段君臣審核表")
    notes = str(review_policy.get("humanReviewNotesZhTw") or "本 lane 僅治理歷史階段依附。")
    paths["markdown"].parent.mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(render_markdown(rows, title, notes), encoding="utf-8")
    write_jsonl(paths["jsonl"], rows)
    template = build_decision_template(rows, tag)
    template.update(version_metadata)
    write_json(paths["template"], template)

    summary = {
        "mode": "top50-ruler-subject-historical-phase-reviewed-support-bundle",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "lanePolicyPath": repo_relative(lane_policy_path),
            "reviewedCachePaths": [repo_relative(path) for path in reviewed_paths],
            "humanDecisionPaths": [repo_relative(path) for path in decision_paths],
            "relationshipTypeFilter": "ruler_subject",
            "requiredLaneHint": "historical-phase",
            "governanceMode": "historical-phase"
        },
        "outputs": {
            "jsonlPath": repo_relative(paths["jsonl"]),
            "markdownPath": repo_relative(paths["markdown"]),
            "decisionTemplatePath": repo_relative(paths["template"]),
            "summaryPath": repo_relative(paths["summary"]),
            "rowCount": len(rows)
        },
        "counts": dict(sorted(counters.items())),
        "reviewRules": {
            "rawQueueEligibleForHumanReview": bool(
                review_policy.get("rawQueueEligibleForHumanReview", False)
            ),
            "supportedReviewCacheEligible": bool(
                review_policy.get("supportedReviewCacheEligible", True)
            ),
            "proposalOnly": bool(review_policy.get("proposalOnly", True))
        }
    }
    write_json(paths["summary"], summary)

    print(
        "[build_top50_ruler_subject_historical_phase_review_bundle] "
        f"rows={len(rows)} keptSupported={counters['keptSupported']} "
        f"skippedResolved={counters['skippedResolvedByHuman']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
