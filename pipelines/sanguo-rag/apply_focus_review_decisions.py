from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import (
    build_alias_map,
    build_name_map,
    build_general_scoped_ambiguous_alias_map,
    read_json,
    stable_hash,
)


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_PACKET_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/top50-low-coverage-focus.json"
)
DEFAULT_DECISIONS_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/top50-low-coverage-focus.decisions.json"
)
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/top50-low-coverage-focus-reviewed-cache.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply person-centered focus review decisions and emit reviewed-cache JSONL rows."
    )
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--packet", default=str(DEFAULT_PACKET_PATH))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--summary-out", default="")
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def build_identity_maps(policy_path: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    policy = read_json(policy_path)
    inputs = object_map(policy.get("inputs"))
    stable_bootstrap_path = resolve_path(
        str(inputs.get("stableBootstrapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap.json")
    )
    formal_mention_map_path = resolve_path(
        str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
    )
    general_alias_records_path = resolve_path(
        str(inputs.get("generalAliasRecordsPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json")
    )
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    general_alias_records = read_json(general_alias_records_path) if general_alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, general_alias_records)
    ambiguous_map = build_general_scoped_ambiguous_alias_map(general_alias_records)
    return name_map, alias_map, ambiguous_map


def decision_index(decisions: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in object_list(decisions.get("entries")):
        focus_id = str(entry.get("focusGeneralId") or "").strip()
        locator = str(entry.get("locator") or "").strip()
        if focus_id and locator:
            index[(focus_id, locator)] = entry
    return index


def name_for(entity_id: str, name_map: dict[str, str]) -> str:
    return str(name_map.get(entity_id) or entity_id)


def alias_rows(entity_id: str, name_map: dict[str, str], alias_map: dict[str, list[str]], ambiguous_map: dict[str, list[str]]) -> dict[str, Any]:
    aliases: list[str] = []
    canonical_name = name_for(entity_id, name_map)
    if canonical_name:
        aliases.append(canonical_name)
    aliases.extend(string_list(alias_map.get(entity_id)))
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias and alias not in seen:
            seen.add(alias)
            deduped.append(alias)
    return {
        "entityId": entity_id,
        "nameZhTw": canonical_name,
        "aliasesZhTw": deduped,
        "scopedAmbiguousAliasesZhTw": string_list(ambiguous_map.get(entity_id)),
        "roleHints": ["from", "to", "subject", "controller"],
        "canonicalWrites": False,
    }


def infer_lane_hint(relationship_type: str, lane_hint: str) -> tuple[str, str]:
    explicit = str(lane_hint or "").strip()
    if explicit:
        return explicit, "由審查決策明確指定。"
    if relationship_type == "ruler_subject":
        return "historical-phase", "人物中心低覆蓋句窗出現投奔/歸附類主從語意，預設分流到歷史階段 lane。"
    return "stable-baseline", "人物中心低覆蓋句窗已形成直接硬關係語意，預設進穩定硬關係 lane。"


def build_candidate(relation: dict[str, Any], name_map: dict[str, str]) -> dict[str, Any]:
    relationship_type = str(relation.get("relationshipType") or "").strip()
    from_id = str(relation.get("fromId") or "").strip()
    to_id = str(relation.get("toId") or "").strip()
    lane_hint, lane_reason = infer_lane_hint(relationship_type, str(relation.get("relationshipLaneHint") or ""))
    trust_key = str(relation.get("trustKey") or f"{relationship_type}:{from_id}:{to_id}").strip()
    score = round(number_value(relation.get("semanticTrustScore"), 0.0), 3)
    return {
        "trustKey": trust_key,
        "relationshipType": relationship_type,
        "fromId": from_id,
        "toId": to_id,
        "fromNameZhTw": name_for(from_id, name_map),
        "toNameZhTw": name_for(to_id, name_map),
        "claimSentenceZhTw": compact_text(relation.get("normalizedClaimZhTw") or relation.get("claimSentenceZhTw")),
        "scoreBeforeSemanticReview": score,
        "focusQueuePriority": score,
        "counterpartSelectionMode": "focus-low-coverage-reviewed",
        "relationshipLaneHint": lane_hint,
        "relationshipLaneReasonZhTw": lane_reason,
        "stableBaselineEligible": lane_hint == "stable-baseline",
        "strictDirectionCheckZhTw": compact_text(relation.get("strictDirectionCheckZhTw")),
        "matchedCueTerms": string_list(relation.get("matchedCueTerms")),
        "matchedCueTermCount": len(string_list(relation.get("matchedCueTerms"))),
        "subjectId": str(relation.get("subjectId") or "").strip(),
        "subjectNameZhTw": compact_text(relation.get("subjectNameZhTw")),
        "controllerId": str(relation.get("controllerId") or "").strip(),
        "controllerNameZhTw": compact_text(relation.get("controllerNameZhTw")),
        "canonicalWrites": False,
    }


def build_relationship(relation: dict[str, Any], source_sentence: str) -> dict[str, Any]:
    relationship_type = str(relation.get("relationshipType") or "").strip()
    from_id = str(relation.get("fromId") or "").strip()
    to_id = str(relation.get("toId") or "").strip()
    trust_key = str(relation.get("trustKey") or f"{relationship_type}:{from_id}:{to_id}").strip()
    verdict = str(relation.get("verdict") or "").strip().lower() or "not_enough_context"
    return {
        "trustKey": trust_key,
        "relationshipType": relationship_type,
        "fromId": from_id,
        "toId": to_id,
        "verdict": verdict,
        "semanticTrustScore": round(number_value(relation.get("semanticTrustScore"), 0.0), 3),
        "confidence": round(number_value(relation.get("confidence"), 0.0), 4),
        "evidenceSentence": compact_text(relation.get("evidenceSentence") or source_sentence),
        "normalizedClaimZhTw": compact_text(relation.get("normalizedClaimZhTw")),
        "rationaleZhTw": compact_text(relation.get("rationaleZhTw")),
        "mismatchReasonZhTw": compact_text(relation.get("mismatchReasonZhTw")),
        "relationshipCueSpanZhTw": compact_text(relation.get("relationshipCueSpanZhTw")),
        "fromEvidenceSpanZhTw": compact_text(relation.get("fromEvidenceSpanZhTw")),
        "toEvidenceSpanZhTw": compact_text(relation.get("toEvidenceSpanZhTw")),
        "cueCategory": str(relation.get("cueCategory") or "focus-low-coverage-passage"),
        "reviewMode": str(relation.get("reviewMode") or "sentence-relation-extraction"),
        "directionMatched": relation.get("directionMatched"),
        "typeMatched": relation.get("typeMatched"),
        "polarity": str(relation.get("polarity") or "positive"),
        "stableRelation": bool(relation.get("stableRelation")),
        "canonicalWrites": False,
    }


def build_review_unit(
    focus_entry: dict[str, Any],
    passage: dict[str, Any],
    decision: dict[str, Any],
    *,
    name_map: dict[str, str],
    alias_map: dict[str, list[str]],
    ambiguous_map: dict[str, list[str]],
) -> dict[str, Any]:
    focus_id = str(focus_entry.get("focusGeneralId") or "").strip()
    relationships = [
        build_relationship(relation, str(passage.get("normalizedText") or ""))
        for relation in object_list(decision.get("relationships"))
    ]
    candidates = [build_candidate(relation, name_map) for relation in object_list(decision.get("relationships"))]
    reviewed_keys = [row["trustKey"] for row in relationships if row.get("trustKey")]
    entity_ids = {focus_id}
    for relation in object_list(decision.get("relationships")):
        entity_ids.add(str(relation.get("fromId") or "").strip())
        entity_ids.add(str(relation.get("toId") or "").strip())
    entity_ids.update(string_list(focus_entry.get("topCounterpartIds")))
    entity_ids.discard("")
    allowed_entities = [alias_rows(entity_id, name_map, alias_map, ambiguous_map) for entity_id in sorted(entity_ids)]
    unit_id = "lowcov." + stable_hash(
        focus_id,
        passage.get("locator"),
        passage.get("normalizedText"),
        length=24,
    )
    provider = object_map(decision.get("reviewer")) or {
        "provider": "codex-skill",
        "model": "codex",
        "preset": "sanguo-low-coverage-focus-review",
        "apiUrl": None,
    }
    return {
        "semanticReviewUnitId": unit_id,
        "promptVersion": str(decision.get("promptVersion") or "relationship-semantic-review.low-coverage-focus.v1"),
        "sentenceHash": "sha256:" + stable_hash(passage.get("normalizedText"), length=32),
        "sourceSentence": str(passage.get("normalizedText") or ""),
        "sentenceQualityScore": round(number_value(decision.get("sentenceQualityScore"), 92.0), 3),
        "sourceRefs": [
            {
                "sourceId": "sanguoyanyi-baihua-zh-tw",
                "sourceFamily": "baihua-bootstrap-focus",
                "sourceLayer": "baihua-focus-packets",
                "locator": str(passage.get("locator") or ""),
                "url": "",
                "evidenceRefs": [str(passage.get("chapterRef") or ""), str(passage.get("locator") or "")],
                "confidenceSignals": [
                    "baihua-translation-anchor",
                    "person-centered-low-coverage-review",
                    *[f"pair-cue:{value}" for value in string_list(passage.get("candidateRelationshipTypes"))],
                ],
                "canonicalWrites": False,
            }
        ],
        "allowedEntities": allowed_entities,
        "allowedRelationshipTypes": sorted({str(item.get("relationshipType") or "") for item in relationships if str(item.get("relationshipType") or "")}),
        "candidates": candidates,
        "semanticReviewPerformed": True,
        "reviewedAt": str(decision.get("reviewedAt") or utc_now()),
        "reviewedCandidateKeys": reviewed_keys,
        "reviewer": provider,
        "reviewMode": str(decision.get("reviewMode") or "sentence-relation-extraction"),
        "relationships": relationships,
        "extractedRelationships": object_list(decision.get("extractedRelationships")),
        "rawReviewerSummary": {
            "reviewer": str(provider.get("provider") or "codex-skill"),
            "notesZhTw": compact_text(decision.get("notesZhTw")),
        },
        "focusGeneralId": focus_id,
        "focusNameZhTw": str(focus_entry.get("focusNameZhTw") or name_for(focus_id, name_map)),
        "canonicalWrites": False,
    }


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy)
    packet_path = resolve_path(args.packet)
    decisions_path = resolve_path(args.decisions)
    output_path = resolve_path(args.output)
    summary_path = resolve_path(args.summary_out) if str(args.summary_out).strip() else output_path.with_suffix(".summary.json")
    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    packet = read_json(packet_path)
    decisions = read_json(decisions_path)
    name_map, alias_map, ambiguous_map = build_identity_maps(policy_path)
    decision_map = decision_index(decisions)

    reviewed_rows: list[dict[str, Any]] = []
    missing_decisions: list[dict[str, str]] = []
    applied_count = 0
    supported_count = 0
    contradicted_count = 0
    context_count = 0

    for focus_entry in object_list(packet.get("entries")):
        focus_id = str(focus_entry.get("focusGeneralId") or "").strip()
        for passage in object_list(focus_entry.get("selectedPassages")):
            locator = str(passage.get("locator") or "").strip()
            decision = decision_map.get((focus_id, locator))
            if not decision:
                missing_decisions.append({"focusGeneralId": focus_id, "locator": locator})
                continue
            unit = build_review_unit(
                focus_entry,
                passage,
                decision,
                name_map=name_map,
                alias_map=alias_map,
                ambiguous_map=ambiguous_map,
            )
            reviewed_rows.append(unit)
            applied_count += 1
            for relation in object_list(unit.get("relationships")):
                verdict = str(relation.get("verdict") or "").strip().lower()
                if verdict == "supported":
                    supported_count += 1
                elif verdict == "contradicted":
                    contradicted_count += 1
                elif verdict == "not_enough_context":
                    context_count += 1

    write_jsonl(output_path, reviewed_rows)
    summary = {
        "mode": "apply-focus-review-decisions",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "packetPath": repo_relative(packet_path),
        "decisionsPath": repo_relative(decisions_path),
        "outputPath": repo_relative(output_path),
        "reviewedUnitCount": len(reviewed_rows),
        "appliedDecisionCount": applied_count,
        "supportedRelationshipCount": supported_count,
        "contradictedRelationshipCount": contradicted_count,
        "notEnoughContextRelationshipCount": context_count,
        "missingDecisionCount": len(missing_decisions),
        "missingDecisions": missing_decisions[:20],
    }
    write_json(summary_path, summary)
    print(
        "[apply_focus_review_decisions] "
        f"units={len(reviewed_rows)} supported={supported_count} contradicted={contradicted_count} "
        f"notEnoughContext={context_count} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
