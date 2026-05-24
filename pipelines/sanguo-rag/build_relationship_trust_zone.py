from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = Path("data/sanguo/policies/policy-relationship-trust-zone.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
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


def read_keyed_jsonl(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(key_field) or "").strip()
        if key:
            keyed[key] = row
    return keyed


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def stable_hash(*parts: Any, length: int = 18) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def trim_text(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 0)] + "..."


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def number_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): number_value(item) for key, item in value.items()}


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def stage_name(policy: dict[str, Any], key: str, default: str) -> str:
    return str(object_map(policy.get("stageNames")).get(key) or default)


def zones_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("zones"))


def score_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("scoreModel"))


def skip_index_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("skipIndex"))


def first_present(row: dict[str, Any], fields: list[str]) -> Any:
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return value
    return None


def scope_payload(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    scope_policy = object_map(policy.get("scope"))
    valid_from = first_present(row, string_list(scope_policy.get("validFromFields")))
    valid_to = first_present(row, string_list(scope_policy.get("validToFields")))
    chapter = first_present(row, string_list(scope_policy.get("chapterFields")))
    key = str(scope_policy.get("unknownScopeKey") or "global")
    if valid_from is not None or valid_to is not None:
        key = f"{valid_from or ''}..{valid_to or ''}"
    elif chapter is not None:
        key = str(chapter)
    return {
        "scopeKey": key,
        "validFrom": valid_from,
        "validTo": valid_to,
        "chapter": chapter,
    }


class SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_name_map(stable_bootstrap: dict[str, Any]) -> dict[str, str]:
    rows = stable_bootstrap.get("identitySeeds")
    if not isinstance(rows, list):
        return {}
    names: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        name = str(row.get("name") or "").strip()
        if general_id and name:
            names[general_id] = name
    return names


def display_name(entity_id: Any, name_map: dict[str, str]) -> str:
    text = str(entity_id or "").strip()
    return name_map.get(text, text)


def faction_display_name(faction_id: Any, policy: dict[str, Any]) -> str:
    text = str(faction_id or "").strip()
    labels = object_map(policy.get("factionLabelsZhTw"))
    return str(labels.get(text) or text)


def source_label(value: Any, policy: dict[str, Any]) -> str:
    text = str(value or "").strip()
    labels = object_map(policy.get("sourceLabelsZhTw"))
    return str(labels.get(text) or text or "-")


def claim_sentence(record: dict[str, Any], policy: dict[str, Any], name_map: dict[str, str]) -> str:
    rel_type = str(record.get("relationshipType") or "").strip()
    templates = object_map(policy.get("claimSentenceTemplatesZhTw"))
    template = str(templates.get(rel_type) or "{fromName} -> {toName}")
    from_id = str(record.get("fromId") or "").strip()
    to_id = str(record.get("toId") or "").strip()
    subject_id = str(record.get("subjectId") or to_id).strip()
    controller_id = str(record.get("controllerId") or from_id).strip()
    context = SafeFormatDict(
        fromId=from_id,
        toId=to_id,
        subjectId=subject_id,
        controllerId=controller_id,
        fromName=display_name(from_id, name_map),
        toName=display_name(to_id, name_map),
        subjectName=display_name(subject_id, name_map),
        controllerName=faction_display_name(controller_id, policy) if rel_type == "faction_membership" else display_name(controller_id, name_map),
        relationshipType=rel_type,
        relationshipLabel=object_map(policy.get("relationshipLabelsZhTw")).get(rel_type) or rel_type,
    )
    return template.format_map(context)


def fact_check_queries(record: dict[str, Any], policy: dict[str, Any], name_map: dict[str, str]) -> list[dict[str, Any]]:
    fact_policy = object_map(object_map(policy.get("skillReview")).get("factCheck"))
    rel_type = str(record.get("relationshipType") or "").strip()
    label = str(object_map(policy.get("relationshipLabelsZhTw")).get(rel_type) or rel_type)
    context = SafeFormatDict(
        fromName=display_name(record.get("fromId"), name_map),
        toName=display_name(record.get("toId"), name_map),
        subjectName=display_name(record.get("subjectId"), name_map),
        controllerName=display_name(record.get("controllerId"), name_map),
        relationshipType=rel_type,
        relationshipLabel=label,
    )
    queries: list[dict[str, Any]] = []
    for polarity, template_key in [("support", "queryTemplateZhTw"), ("challenge", "negativeQueryTemplateZhTw")]:
        template = str(fact_policy.get(template_key) or "").strip()
        if template:
            queries.append(
                {
                    "polarity": polarity,
                    "query": template.format_map(context),
                    "targets": string_list(fact_policy.get("queryTargets")),
                }
            )
    return queries


def source_quote_previews(record: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    fact_policy = object_map(object_map(policy.get("skillReview")).get("factCheck"))
    max_items = int(number_value(fact_policy.get("sourceQuotePreviewMaxItems"), 3.0))
    max_chars = int(number_value(fact_policy.get("sourceQuotePreviewMaxChars"), 180.0))
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        return []
    previews: list[dict[str, Any]] = []
    for support in supports:
        if not isinstance(support, dict):
            continue
        quote = trim_text(support.get("quote"), max_chars)
        if not quote:
            continue
        previews.append(
            {
                "quote": quote,
                "sourceId": support.get("sourceId"),
                "sourceFamily": support.get("sourceFamily"),
                "sourceLayer": support.get("sourceLayer"),
                "locator": support.get("locator"),
                "url": support.get("url"),
                "evidenceRefs": support.get("evidenceRefs") or [],
            }
        )
        if len(previews) >= max_items:
            break
    return previews


def fact_check_mode_label(mode: str) -> str:
    labels = {
        "external-reviewer-packet": "已讀取外部查證紀錄",
        "deterministic-existing-evidence-only": "僅檢查既有證據，尚未實際外部查證",
        "no-evidence": "尚無可查證證據",
    }
    return labels.get(mode, mode or "未標示")


def fact_check_verdict_label(verdict: str, *, verified: bool, external_lookup: bool) -> str:
    if verified:
        return "已通過查證"
    if not external_lookup:
        return "尚未實際外部查證"
    if verdict:
        return "外部查證未通過"
    return "尚待查證"


def evidence_basis_from_support(
    support: dict[str, Any],
    *,
    policy: dict[str, Any],
    requirements: list[Any],
) -> dict[str, Any]:
    fact_policy = object_map(object_map(policy.get("skillReview")).get("factCheck"))
    max_chars = int(number_value(fact_policy.get("evidenceBasisQuoteMaxChars"), 220.0))
    supported = record_supports_any_requirement({"supportingEvidence": [support]}, requirements)
    return {
        "mode": "deterministic-existing-evidence-only",
        "modeZhTw": fact_check_mode_label("deterministic-existing-evidence-only"),
        "sourceId": support.get("sourceId"),
        "sourceEvidenceId": support.get("sourceEvidenceId"),
        "sourceFamily": support.get("sourceFamily"),
        "sourceLayer": support.get("sourceLayer"),
        "sourceClass": support.get("sourceClass"),
        "sourceFile": support.get("sourceFile"),
        "locator": support.get("locator"),
        "url": support.get("url"),
        "textHash": support.get("textHash"),
        "evidenceRefs": support.get("evidenceRefs") or [],
        "originalSentence": trim_text(support.get("quote"), max_chars),
        "basisVerdict": "supports-claim-candidate" if supported else "insufficient-for-claim",
        "basisVerdictZhTw": "原文疑似支持命題" if supported else "只屬既有線索，尚不足以支持命題",
        "externalLookupPerformed": False,
        "canonicalWrites": False,
    }


def normalize_external_evidence_basis(item: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    fact_policy = object_map(object_map(policy.get("skillReview")).get("factCheck"))
    max_chars = int(number_value(fact_policy.get("evidenceBasisQuoteMaxChars"), 220.0))
    quote = item.get("originalSentence") or item.get("quote") or item.get("sourceQuote") or item.get("sentence")
    verdict = str(item.get("basisVerdict") or item.get("verdict") or "").strip()
    verdict_label = str(item.get("basisVerdictZhTw") or "").strip()
    if not verdict_label:
        verdict_label = "外部證據支持命題" if verdict in set(string_list(fact_policy.get("externalVerifiedVerdicts"))) else "外部證據待確認"
    return {
        "mode": "external-reviewer-packet",
        "modeZhTw": fact_check_mode_label("external-reviewer-packet"),
        "sourceId": item.get("sourceId"),
        "sourceEvidenceId": item.get("sourceEvidenceId"),
        "sourceFamily": item.get("sourceFamily"),
        "sourceLayer": item.get("sourceLayer"),
        "sourceClass": item.get("sourceClass"),
        "sourceFile": item.get("sourceFile"),
        "locator": item.get("locator"),
        "url": item.get("url"),
        "textHash": item.get("textHash"),
        "evidenceRefs": item.get("evidenceRefs") or [],
        "originalSentence": trim_text(quote, max_chars),
        "basisVerdict": verdict or "external-reviewer-evidence",
        "basisVerdictZhTw": verdict_label,
        "externalLookupPerformed": True,
        "canonicalWrites": False,
    }


def external_review_packet_verified(packet: dict[str, Any], fact_policy: dict[str, Any]) -> tuple[bool, bool, list[dict[str, Any]]]:
    if not packet:
        return False, False, []
    external_lookup = bool(
        packet.get("externalLookupPerformed")
        or packet.get("lookupPerformed")
        or packet.get("checkedExternalSources")
    )
    verdict = str(packet.get("verdict") or packet.get("reviewVerdict") or packet.get("status") or "").strip()
    verified_verdicts = set(string_list(fact_policy.get("externalVerifiedVerdicts")))
    if not verified_verdicts:
        verified_verdicts = {"verified", "supported", "approved", "pass"}
    basis_items = packet.get("evidenceBasis")
    if not isinstance(basis_items, list):
        basis_items = []
    verified = external_lookup and bool(basis_items) and verdict in verified_verdicts
    return verified, external_lookup, [item for item in basis_items if isinstance(item, dict)]


def evidence_basis_summary_items(record: dict[str, Any], max_items: int = 3) -> list[dict[str, Any]]:
    skill_review = object_map(record.get("skillReview"))
    fact_check = object_map(skill_review.get("factCheck"))
    basis = fact_check.get("evidenceBasis")
    if isinstance(basis, list) and basis:
        return [item for item in basis[:max_items] if isinstance(item, dict)]
    return []


def quote_locator_hash(row: dict[str, Any]) -> bool:
    return bool(row.get("quote")) and bool(row.get("locator") or row.get("evidenceRefs")) and bool(row.get("textHash"))


def row_has_requirement(row: dict[str, Any], requirement: dict[str, Any]) -> bool:
    field = str(requirement.get("field") or "").strip()
    if not field:
        return False
    value = row.get(field)
    if "contains" in requirement:
        target = str(requirement.get("contains") or "")
        return target in {str(item) for item in (value or [])} if isinstance(value, list) else target in str(value or "")
    if "equals" in requirement:
        return str(value or "") == str(requirement.get("equals") or "")
    return False


def stable_requirement_passed(row: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, list[str]]:
    rel_policy = object_map(policy.get("relationshipDimension"))
    type_rules = object_map(rel_policy.get("typeStableRequirements"))
    rel_type = str(row.get("relationshipType") or row.get("type") or "").strip()
    rule = object_map(type_rules.get(rel_type))
    requirements = rule.get("requiresAny")
    if not isinstance(requirements, list) or not requirements:
        return True, []
    passed = [req for req in requirements if isinstance(req, dict) and row_has_requirement(row, req)]
    if passed:
        return True, []
    return False, [f"stable-requirement-not-met:{rel_type}"]


def is_bidirectional(rel_type: str, policy: dict[str, Any]) -> bool:
    rel_policy = object_map(policy.get("relationshipDimension"))
    return rel_type in set(string_list(rel_policy.get("bidirectionalRelationshipTypes")))


def relationship_key(row: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str]:
    rel_type = str(row.get("type") or "").strip()
    from_id = str(row.get("fromId") or "").strip()
    to_id = str(row.get("toId") or "").strip()
    if is_bidirectional(rel_type, policy):
        pair_id = "|".join(sorted([from_id, to_id]))
        return "relationship", f"relationship|{rel_type}|{pair_id}"
    return "relationship", f"relationship|{rel_type}|{from_id}|{to_id}"


def faction_key(row: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str]:
    faction_policy = object_map(policy.get("factionDimension"))
    rel_type = str(faction_policy.get("relationshipType") or "faction_membership")
    subject_id = str(row.get("subjectId") or "").strip()
    faction_id = str(row.get("factionId") or "").strip()
    return "faction", f"faction|{rel_type}|{subject_id}|{faction_id}"


def relationship_score(row: dict[str, Any], policy: dict[str, Any]) -> float:
    score_policy = object_map(policy.get("scoreModel"))
    base_scores = number_map(score_policy.get("claimGradeBaseScores"))
    signal_bonuses = number_map(score_policy.get("confidenceSignalBonuses"))
    trace_bonuses = number_map(score_policy.get("promotionTraceBonuses"))
    boolean_bonuses = number_map(score_policy.get("booleanFieldBonuses"))
    score = base_scores.get(str(row.get("claimGrade") or ""), 0.0)
    score += number_value(row.get("edgeConfidence")) * number_value(score_policy.get("edgeConfidenceWeight"))
    score += number_value(row.get("edgeStrength")) * number_value(score_policy.get("edgeStrengthWeight"))
    for field, bonus in boolean_bonuses.items():
        if bool(row.get(field)):
            score += bonus
    if quote_locator_hash(row):
        score += number_value(score_policy.get("quoteLocatorHashBonus"))
    for signal in string_list(row.get("confidenceSignals")):
        score += signal_bonuses.get(signal, 0.0)
    for trace in string_list(row.get("promotionTrace")):
        score += trace_bonuses.get(trace, 0.0)
    for field in string_list(score_policy.get("humanReviewStatusFields")):
        if str(row.get(field) or "") in set(string_list(score_policy.get("humanApprovedStatuses"))):
            return number_value(score_policy.get("humanReviewedScore"), number_value(policy.get("zones", {}).get("humanReviewedScore"), 100.0))
    return min(score, number_value(score_policy.get("maxScore"), 100.0))


def faction_score(interval: dict[str, Any], policy: dict[str, Any]) -> float:
    faction_policy = object_map(policy.get("factionDimension"))
    raw_score = number_value(interval.get(str(faction_policy.get("confidenceField") or "confidence"))) * 100.0
    return min(raw_score, number_value(object_map(policy.get("scoreModel")).get("maxScore"), 100.0))


def aggregate_score(rows: list[dict[str, Any]], policy: dict[str, Any]) -> float:
    score_policy = object_map(policy.get("scoreModel"))
    if not rows:
        return 0.0
    best = max(number_value(row.get("evidenceScore")) for row in rows)
    extra_cap = int(number_value(score_policy.get("additionalEvidenceCap")))
    extra_bonus = number_value(score_policy.get("additionalEvidenceBonus"))
    distinct_sources = {str(row.get("sourceFamily") or row.get("sourceLayer") or row.get("sourceId") or "") for row in rows}
    score = best + min(max(len(rows) - 1, 0), extra_cap) * extra_bonus
    if len([item for item in distinct_sources if item]) > 1:
        score += number_value(score_policy.get("crossSourceFamilyBonus"))
    return min(score, number_value(score_policy.get("maxScore"), 100.0))


def row_matches_score_cap_requirement(row: dict[str, Any], requirement: dict[str, Any]) -> bool:
    return row_has_requirement(row, requirement) or support_has_requirement(support_row(row), requirement)


def rows_support_any_requirement(rows: list[dict[str, Any]], requirements: list[Any]) -> bool:
    typed_requirements = [item for item in requirements if isinstance(item, dict)]
    if not typed_requirements:
        return True
    return any(
        any(row_matches_score_cap_requirement(row, requirement) for requirement in typed_requirements)
        for row in rows
    )


def apply_score_caps(
    rows: list[dict[str, Any]],
    score: float,
    policy: dict[str, Any],
) -> tuple[float, list[str]]:
    local_score_policy = score_policy(policy)
    caps = local_score_policy.get("scoreCaps")
    if not isinstance(caps, list) or not rows:
        return score, []
    exemplar_type = str(rows[0].get("relationshipType") or rows[0].get("type") or "").strip()
    blockers: list[str] = []
    capped_score = score
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        rel_types = set(string_list(cap.get("relationshipTypes")))
        if rel_types and exemplar_type not in rel_types:
            continue
        unless_requirements = cap.get("unlessSupportingEvidenceRequiresAny")
        if isinstance(unless_requirements, list) and rows_support_any_requirement(rows, unless_requirements):
            continue
        capped_score = min(capped_score, number_value(cap.get("maxScore"), capped_score))
        blocker = str(cap.get("stableBlocker") or "").strip()
        if blocker:
            blockers.append(blocker)
    return capped_score, blockers


def trust_zone_for_score(score: float, stable_allowed: bool, policy: dict[str, Any]) -> str:
    zones = zones_policy(policy)
    stable_min = number_value(zones.get("stableMinScore"), 90.0)
    review_min = number_value(zones.get("reviewMinScore"), 80.0)
    if stable_allowed and score >= stable_min:
        return stage_name(policy, "stable", "stable-90")
    if score >= review_min:
        return stage_name(policy, "review", "review")
    return stage_name(policy, "accumulating", "accumulating")


def no_recompute_allowed(record: dict[str, Any], policy: dict[str, Any]) -> bool:
    stages = set(string_list(skip_index_policy(policy).get("noRecomputeStages")))
    if not stages:
        stages = {stage_name(policy, "stable", "stable-90")}
    stage = str(record.get("zone") or "")
    score = number_value(record.get("score"))
    return stage in stages and score >= number_value(zones_policy(policy).get("noRecomputeMinScore"), 90.0)


def refresh_trust_flags(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    record["noRecompute"] = no_recompute_allowed(record, policy)
    skip_policy = skip_index_policy(policy)
    fixed_stages = set(string_list(skip_policy.get("fixedAliasStages")))
    negative_stages = set(string_list(skip_policy.get("negativeConditionStages")))
    zone = str(record.get("zone") or "")
    record["fixedAliasLike"] = zone in fixed_stages
    record["negativeCondition"] = zone in negative_stages
    return record


def support_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "claimId": row.get("claimId"),
        "sourceId": row.get("sourcePolicyId") or row.get("sourceId"),
        "sourceEvidenceId": row.get("sourceEvidenceId"),
        "sourceFamily": row.get("sourceFamily"),
        "sourceLayer": row.get("sourceLayer"),
        "sourceClass": row.get("sourceClass"),
        "sourceFile": row.get("sourceFile") or row.get("_sourceFile"),
        "claimGrade": row.get("claimGrade"),
        "confidenceSignals": string_list(row.get("confidenceSignals")),
        "promotionTrace": string_list(row.get("promotionTrace")),
        "sourceClaimScopes": string_list(row.get("sourceClaimScopes")),
        "sourceClaimType": row.get("sourceClaimType"),
        "directPairSignal": bool(row.get("directPairSignal")),
        "directPairGrounding": row.get("directPairGrounding"),
        "pairRelationSignal": bool(row.get("pairRelationSignal")),
        "pairRelationCue": row.get("pairRelationCue"),
        "semanticReviewRequired": bool(row.get("semanticReviewRequired")),
        "semanticReviewReason": row.get("semanticReviewReason"),
        "candidateRelationshipType": row.get("candidateRelationshipType"),
        "refinementReasons": string_list(row.get("refinementReasons")),
        "evidenceScore": row.get("evidenceScore"),
        "evidenceRefs": list(row.get("evidenceRefs") or []),
        "locator": row.get("locator"),
        "textHash": row.get("textHash"),
        "url": row.get("url") or row.get("sourceUrl"),
        "quote": row.get("quote"),
    }


def trust_record(
    *,
    dimension: str,
    trust_key: str,
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    stable_blockers: list[str],
) -> dict[str, Any]:
    local_score_policy = score_policy(policy)
    max_supporting = int(number_value(local_score_policy.get("maxSupportingItems"), 10.0))
    exemplar = max(rows, key=lambda row: number_value(row.get("evidenceScore"))) if rows else {}
    score = aggregate_score(rows, policy)
    score, score_cap_blockers = apply_score_caps(rows, score, policy)
    combined_stable_blockers = sorted(set([*stable_blockers, *score_cap_blockers]))
    stable_allowed = not combined_stable_blockers
    zone = trust_zone_for_score(score, stable_allowed, policy)
    scope_values = [scope_payload(row, policy) for row in rows]
    record = {
        "trustZoneId": "trustzone." + stable_hash(trust_key),
        "dimension": dimension,
        "trustKey": trust_key,
        "zone": zone,
        "noRecompute": False,
        "fixedAliasLike": False,
        "relationshipType": exemplar.get("relationshipType") or exemplar.get("type"),
        "fromId": exemplar.get("fromId"),
        "toId": exemplar.get("toId"),
        "subjectId": exemplar.get("subjectId") or exemplar.get("toId"),
        "controllerId": exemplar.get("controllerId") or exemplar.get("fromId") or exemplar.get("factionId"),
        "score": round(score, 3),
        "evidenceCount": len(rows),
        "distinctSourceFamilies": sorted(
            {
                str(row.get("sourceFamily") or row.get("sourceLayer") or row.get("sourceId") or "")
                for row in rows
                if str(row.get("sourceFamily") or row.get("sourceLayer") or row.get("sourceId") or "").strip()
            }
        ),
        "scope": {
            "mode": "metadata-only",
            "values": scope_values,
        },
        "stableBlockers": combined_stable_blockers,
        "supportingEvidence": [support_row(row) for row in sorted(rows, key=lambda item: -number_value(item.get("evidenceScore")))[:max_supporting]],
        "canonicalWrites": False,
    }
    return refresh_trust_flags(record, policy)


def required_field_present(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if isinstance(value, (list, dict)):
        return bool(value)
    return value not in (None, "")


def support_field_present(support: dict[str, Any], field: str) -> bool:
    value = support.get(field)
    if isinstance(value, (list, dict)):
        return bool(value)
    return value not in (None, "")


def support_group_present(record: dict[str, Any], fields: list[str]) -> bool:
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        return False
    return any(
        isinstance(support, dict) and any(support_field_present(support, field) for field in fields)
        for support in supports
    )


def support_has_requirement(support: dict[str, Any], requirement: dict[str, Any]) -> bool:
    field = str(requirement.get("field") or "").strip()
    if not field:
        return False
    value = support.get(field)
    if "contains" in requirement:
        target = str(requirement.get("contains") or "")
        return target in {str(item) for item in (value or [])} if isinstance(value, list) else target in str(value or "")
    if "equals" in requirement:
        expected = requirement.get("equals")
        if isinstance(expected, bool):
            return bool(value) is expected
        return str(value or "") == str(expected or "")
    return False


def record_supports_any_requirement(record: dict[str, Any], requirements: list[Any]) -> bool:
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        return False
    typed_requirements = [item for item in requirements if isinstance(item, dict)]
    if not typed_requirements:
        return True
    return any(
        isinstance(support, dict) and any(support_has_requirement(support, requirement) for requirement in typed_requirements)
        for support in supports
    )


def fact_check_record(
    record: dict[str, Any],
    policy: dict[str, Any],
    name_map: dict[str, str],
    review_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review_policy = object_map(policy.get("skillReview"))
    fact_policy = object_map(review_policy.get("factCheck"))
    type_rules = object_map(review_policy.get("typeEvidenceRequirements"))
    rel_type = str(record.get("relationshipType") or "")
    type_rule = object_map(type_rules.get(rel_type))
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        supports = []
    requirements = fact_policy.get("eligibleEvidenceRequiresAny")
    typed_requirements = requirements if isinstance(requirements, list) else []
    type_requirements = type_rule.get("supportingEvidenceRequiresAny")
    typed_type_requirements = type_requirements if isinstance(type_requirements, list) else []
    trusted_layers = set(string_list(fact_policy.get("trustedSingleSourceLayers")))
    eligible_supports: list[dict[str, Any]] = []
    trusted_single_source = False
    for support in supports:
        if not isinstance(support, dict):
            continue
        global_ok = record_supports_any_requirement({"supportingEvidence": [support]}, typed_requirements)
        type_ok = record_supports_any_requirement({"supportingEvidence": [support]}, typed_type_requirements)
        if global_ok and type_ok:
            eligible_supports.append(support)
        if str(support.get("sourceLayer") or "") in trusted_layers:
            trusted_single_source = True
    distinct_families = {
        str(support.get("sourceFamily") or support.get("sourceLayer") or support.get("sourceId") or "")
        for support in eligible_supports
        if str(support.get("sourceFamily") or support.get("sourceLayer") or support.get("sourceId") or "").strip()
    }
    min_items = int(number_value(fact_policy.get("minEligibleEvidenceItems"), 1.0))
    min_families = int(number_value(fact_policy.get("minDistinctSourceFamilies"), 1.0))
    allow_trusted_single = bool(fact_policy.get("allowTrustedSingleSource", True))
    deterministic_verified = len(eligible_supports) >= min_items and len(distinct_families) >= min_families
    if allow_trusted_single and trusted_single_source and eligible_supports:
        deterministic_verified = True
    packet_verified, external_lookup, packet_basis_items = external_review_packet_verified(object_map(review_packet), fact_policy)
    require_external_lookup = bool(fact_policy.get("requireExternalLookupFor95", False))
    verified = packet_verified if require_external_lookup else bool(deterministic_verified or packet_verified)
    readiness_warnings: list[str] = []
    if typed_type_requirements and not eligible_supports:
        readiness_warnings.append(f"type-evidence-requirement-not-met:{rel_type}")
    verdict = str(fact_policy.get("verifiedVerdict") if verified else fact_policy.get("unverifiedVerdict"))
    max_basis_items = int(number_value(fact_policy.get("evidenceBasisMaxItems"), 5.0))
    evidence_basis = [normalize_external_evidence_basis(item, policy) for item in packet_basis_items]
    if not evidence_basis:
        evidence_basis = [
            evidence_basis_from_support(support, policy=policy, requirements=typed_requirements)
            for support in supports[:max_basis_items]
            if isinstance(support, dict)
        ]
    review_mode = "external-reviewer-packet" if external_lookup else "deterministic-existing-evidence-only"
    if not evidence_basis:
        review_mode = "no-evidence"
    return {
        "claimSentenceZhTw": claim_sentence(record, policy, name_map),
        "verdict": verdict,
        "verdictZhTw": fact_check_verdict_label(verdict, verified=verified, external_lookup=external_lookup),
        "verified": verified,
        "deterministicVerified": deterministic_verified,
        "externalReviewerVerified": packet_verified,
        "externalLookupPerformed": external_lookup,
        "externalReviewerPacketPresent": bool(review_packet),
        "reviewMode": review_mode,
        "reviewModeZhTw": fact_check_mode_label(review_mode),
        "eligibleEvidenceCount": len(eligible_supports),
        "distinctEligibleSourceFamilies": sorted(distinct_families),
        "trustedSingleSource": trusted_single_source,
        "readinessWarnings": readiness_warnings,
        "evidenceBasis": evidence_basis[:max_basis_items],
        "sourceQuotePreviews": source_quote_previews(record, policy),
        "queries": fact_check_queries(record, policy, name_map),
        "instructionsZhTw": string_list(fact_policy.get("reviewInstructionsZhTw")),
        "canonicalWrites": False,
    }


def skill_review_checks(
    record: dict[str, Any],
    policy: dict[str, Any],
    name_map: dict[str, str],
    review_packet: dict[str, Any] | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    review_policy = object_map(policy.get("skillReview"))
    checks: list[dict[str, Any]] = []

    min_score = number_value(review_policy.get("minScore"), number_value(zones_policy(policy).get("stableMinScore"), 90.0))
    score_ok = number_value(record.get("score")) >= min_score
    checks.append({"check": "minScore", "passed": score_ok, "expected": min_score, "actual": record.get("score")})

    min_evidence = int(number_value(review_policy.get("minEvidenceCount"), 1.0))
    evidence_ok = int(number_value(record.get("evidenceCount"))) >= min_evidence
    checks.append({"check": "minEvidenceCount", "passed": evidence_ok, "expected": min_evidence, "actual": record.get("evidenceCount")})

    blockers_ok = not bool(record.get("stableBlockers")) if bool(review_policy.get("requireNoStableBlockers", True)) else True
    checks.append({"check": "stableBlockers", "passed": blockers_ok, "actual": record.get("stableBlockers")})

    for field in string_list(review_policy.get("requiredRecordFields")):
        checks.append({"check": f"recordField:{field}", "passed": required_field_present(record, field)})

    support_groups = review_policy.get("requiredSupportingEvidenceAnyFields")
    if isinstance(support_groups, list):
        for group in support_groups:
            fields = string_list(group)
            if not fields:
                continue
            checks.append({"check": "supportingEvidenceAny:" + "|".join(fields), "passed": support_group_present(record, fields)})

    type_rules = object_map(review_policy.get("typeEvidenceRequirements"))
    rel_type = str(record.get("relationshipType") or "")
    type_rule = object_map(type_rules.get(rel_type))
    requirements = type_rule.get("supportingEvidenceRequiresAny")
    if isinstance(requirements, list) and requirements:
        type_ok = record_supports_any_requirement(record, requirements)
        checks.append({"check": f"typeEvidenceRequirement:{rel_type}", "passed": type_ok})

    fact_check = fact_check_record(record, policy, name_map, review_packet)
    if bool(object_map(review_policy.get("factCheck")).get("enabled", False)):
        checks.append({"check": "claimSentenceFactCheck", "passed": bool(fact_check.get("verified")), "verdict": fact_check.get("verdict")})

    return all(bool(item.get("passed")) for item in checks), checks, fact_check


def apply_skill_review(
    records: list[dict[str, Any]],
    policy: dict[str, Any],
    name_map: dict[str, str],
    review_packets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    review_policy = object_map(policy.get("skillReview"))
    if not bool(review_policy.get("enabled", False)):
        return [refresh_trust_flags(record, policy) for record in records]

    candidate_stages = set(string_list(review_policy.get("candidateStages")))
    applicable_dimensions = set(string_list(review_policy.get("applicableDimensions")))
    skill_stage = stage_name(policy, "skillReviewed", "skill-reviewed-95")
    fail_stage = str(review_policy.get("failStage") or stage_name(policy, "review", "review"))
    pass_status = str(review_policy.get("passStatus") or "skill-reviewed")
    fail_status = str(review_policy.get("failStatus") or "skill-review-needs-human")
    pass_verdict = str(review_policy.get("passVerdict") or pass_status)
    fail_verdict = str(review_policy.get("failVerdict") or fail_status)
    promoted_score = number_value(review_policy.get("promotedScore"), number_value(zones_policy(policy).get("skillReviewedScore"), 95.0))
    reviewer_id = str(review_policy.get("reviewerId") or "relationship-trust-zone-skill-review")

    reviewed: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        dimension_allowed = not applicable_dimensions or str(row.get("dimension") or "") in applicable_dimensions
        if dimension_allowed and str(row.get("zone") or "") in candidate_stages:
            passed, checks, fact_check = skill_review_checks(row, policy, name_map, review_packets.get(str(row.get("trustKey") or "")))
            row["skillReview"] = {
                "status": pass_status if passed else fail_status,
                "verdict": pass_verdict if passed else fail_verdict,
                "reviewerId": reviewer_id,
                "reviewedAt": utc_now(),
                "humanReviewRequiredAfterPass": bool(review_policy.get("humanReviewRequiredAfterPass", True)),
                "factCheck": fact_check,
                "checks": checks,
                "canonicalWrites": False,
            }
            if passed:
                row["zone"] = skill_stage
                row["score"] = round(promoted_score, 3)
            else:
                row["zone"] = fail_stage
                blockers = string_list(row.get("stableBlockers"))
                blockers.append(fail_status)
                row["stableBlockers"] = sorted(set(blockers))
        reviewed.append(refresh_trust_flags(row, policy))
    return reviewed


def read_human_decisions(path: Path, policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    review_policy = object_map(policy.get("humanReview"))
    command_policy = object_map(review_policy.get("overrideCommands"))
    command_list_field = str(command_policy.get("commandListField") or "commands")
    payload = read_json(path)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        raw_rows = payload.get("decisions")
        raw_commands = payload.get(command_list_field)
        if isinstance(raw_rows, list):
            rows = raw_rows
        else:
            rows = [
                dict(value, trustKey=key) if isinstance(value, dict) else {"trustKey": key, "decision": value}
                for key, value in payload.items()
                if key != command_list_field
            ]
        if isinstance(raw_commands, list):
            rows = [*rows, *[dict(command, _controlType="override-command") for command in raw_commands if isinstance(command, dict)]]
    else:
        rows = []
    decisions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        trust_key = str(row.get("trustKey") or "").strip()
        trust_zone_id = str(row.get("trustZoneId") or "").strip()
        if trust_key:
            decisions[trust_key] = row
        if trust_zone_id:
            decisions[trust_zone_id] = row
    return decisions


def apply_human_decisions(records: list[dict[str, Any]], decisions: dict[str, dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    review_policy = object_map(policy.get("humanReview"))
    command_policy = object_map(review_policy.get("overrideCommands"))
    decision_field = str(review_policy.get("decisionField") or "decision")
    action_field = str(command_policy.get("actionField") or "action")
    pending_status = str(review_policy.get("pendingStatus") or "pending")
    approved_statuses = set(string_list(review_policy.get("approvedStatuses")))
    rejected_statuses = set(string_list(review_policy.get("rejectedStatuses")))
    force_whitelist_actions = set(string_list(command_policy.get("forceWhitelistActions")))
    force_blacklist_actions = set(string_list(command_policy.get("forceBlacklistActions")))
    remove_actions = set(string_list(command_policy.get("removeFromIndexActions")))
    candidate_stages = set(string_list(review_policy.get("candidateStages")))
    locked_stage = stage_name(policy, "humanLocked", "human-locked-100")
    rejected_stage = stage_name(policy, "humanRejected", "human-rejected-0")
    removed_stage = stage_name(policy, "humanRemoved", "human-removed-from-index")
    lock_score = number_value(review_policy.get("lockScore"), number_value(zones_policy(policy).get("humanReviewedScore"), 100.0))
    reject_score = number_value(review_policy.get("rejectScore"), number_value(zones_policy(policy).get("humanRejectedScore"), 0.0))
    force_whitelist_score = number_value(command_policy.get("forceWhitelistScore"), lock_score)
    force_blacklist_score = number_value(command_policy.get("forceBlacklistScore"), reject_score)

    output: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        decision = decisions.get(str(row.get("trustKey") or "")) or decisions.get(str(row.get("trustZoneId") or ""))
        if not decision:
            row["humanReview"] = {"decision": pending_status, "canonicalWrites": False}
            output.append(refresh_trust_flags(row, policy))
            continue

        status = str(decision.get(decision_field) or pending_status).strip()
        action = str(decision.get(action_field) or "").strip()
        row["humanReview"] = {
            "decision": status,
            "action": action,
            "controlType": decision.get("_controlType"),
            "reviewer": decision.get("reviewer") or review_policy.get("decisionTemplateReviewer"),
            "reviewedAt": decision.get("reviewedAt"),
            "notes": decision.get("notes"),
            "canonicalWrites": False,
        }

        if action in force_blacklist_actions or status in rejected_statuses:
            row["zone"] = rejected_stage
            row["score"] = round(force_blacklist_score if action in force_blacklist_actions else reject_score, 3)
            row["noRecompute"] = False
            row["fixedAliasLike"] = False
            blockers = string_list(row.get("stableBlockers"))
            blockers.append("human-forced-blacklist" if action in force_blacklist_actions else "human-rejected")
            row["stableBlockers"] = sorted(set(blockers))
            output.append(refresh_trust_flags(row, policy))
            continue
        if action in force_whitelist_actions or (status in approved_statuses and str(row.get("zone") or "") in candidate_stages):
            row["zone"] = locked_stage
            row["score"] = round(force_whitelist_score if action in force_whitelist_actions else lock_score, 3)
            output.append(refresh_trust_flags(row, policy))
            continue
        if action in remove_actions:
            row["zone"] = removed_stage
            row["noRecompute"] = False
            row["fixedAliasLike"] = False
            row["negativeCondition"] = False
            blockers = string_list(row.get("stableBlockers"))
            blockers.append("human-removed-from-index")
            row["stableBlockers"] = sorted(set(blockers))
            output.append(refresh_trust_flags(row, policy))
            continue
        output.append(refresh_trust_flags(row, policy))
    return output


def candidate_review_records(records: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    review_policy = object_map(policy.get("humanReview"))
    candidate_stages = set(string_list(review_policy.get("candidateStages")))
    max_rows = int(number_value(review_policy.get("markdownMaxRows"), 200.0))
    candidates = [record for record in records if str(record.get("zone") or "") in candidate_stages]
    stage_order = {stage: idx for idx, stage in enumerate(string_list(review_policy.get("candidateStages")))}
    candidates.sort(key=lambda row: (stage_order.get(str(row.get("zone") or ""), 999), -number_value(row.get("score")), str(row.get("trustKey") or "")))
    return candidates[:max_rows]


def first_support_quote(record: dict[str, Any], max_chars: int) -> str:
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        return ""
    for support in supports:
        if not isinstance(support, dict):
            continue
        quote = str(support.get("quote") or "").replace("\n", " ").strip()
        if quote:
            return quote if len(quote) <= max_chars else quote[: max(max_chars - 1, 0)] + "..."
    return ""


def scope_summary(record: dict[str, Any]) -> str:
    scope = object_map(record.get("scope"))
    values = scope.get("values")
    if not isinstance(values, list):
        return ""
    rendered: list[str] = []
    for value in values[:5]:
        if not isinstance(value, dict):
            continue
        rendered.append(str(value.get("scopeKey") or ""))
    return ", ".join(item for item in rendered if item)


def render_human_review_markdown(records: list[dict[str, Any]], policy: dict[str, Any]) -> str:
    review_policy = object_map(policy.get("humanReview"))
    quote_max = int(number_value(review_policy.get("quotePreviewMaxChars"), 120.0))

    lines = [
        "# 關係信任區人工審核表",
        "",
        "這份文件列出已達審核門檻的關係候選。請逐條確認命題句是否被原文與來源支持。",
        "",
        "- 若命題正確，請在決策檔把該列標為「通過」，系統會升入 100 分信任區。",
        "- 若命題錯誤或方向相反，請在決策檔把該列標為「打叉」，系統會降為 0 分黑名單。",
        "- 關係是以兩人一組的內部關係鍵判斷；同一人物後續轉陣營或換主君，必須另有自己的關係鍵與時間範圍。",
        "- 直接改白名單、黑名單或移除索引時，請使用決策檔的指令區；Markdown 本身不會改正式資料。",
        "",
        "## 審核選項",
        "- 待審：暫不改分，保留在審核隊列。",
        "- 通過：升為人工鎖定 100 分，等同關係白名單。",
        "- 打叉：降為 0 分，等同關係黑名單。",
    ]

    lines.extend(
        [
            "",
            "## 糾錯指令",
            "- 強制白名單：把指定內部關係鍵直接升入信任區。",
            "- 強制黑名單：把指定內部關係鍵直接降為 0 分，後續遇到相同關係直接視為錯誤。",
            "- 移除索引：把指定內部關係鍵從白名單與黑名單移除，回到一般累積流程。",
            "",
            "## 候選清單",
        ]
    )
    for index, record in enumerate(records, 1):
        lines.extend(
            [
                "",
                f"### 候選 {index}",
                "- 審核結果：待審／通過／打叉",
                f"- 信任階段：{stage_label(record.get('zone'))}",
                f"- 分數：`{record.get('score')}`",
                f"- 關係類型：{relationship_type_label(record.get('relationshipType'), policy)}",
                f"- 命題句：{record.get('claimSentenceZhTw') or '-'}",
                f"- 內部關係鍵：`{record.get('trustKey')}`",
                f"- 主體人物：`{record.get('subjectId')}`",
                f"- 對象／主君／親長：`{record.get('controllerId')}`",
                f"- 時間範圍：{scope_summary(record) or '-'}",
                f"- 證據數：`{record.get('evidenceCount')}`",
                f"- 來源家族：{', '.join(string_list(record.get('distinctSourceFamilies'))) or '-'}",
                f"- 原文句／證據摘錄：{first_support_quote(record, quote_max) or '-'}",
            ]
        )
    lines.append("")
    return "\n".join(line for line in lines if line is not None)


def build_human_decision_template(records: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    review_policy = object_map(policy.get("humanReview"))
    command_policy = object_map(review_policy.get("overrideCommands"))
    pending_status = str(review_policy.get("pendingStatus") or "pending")
    command_list_field = str(command_policy.get("commandListField") or "commands")
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "relationship-trust-zone-human-decisions-template",
        "instructions": string_list(review_policy.get("reviewInstructions")),
        "decisionField": str(review_policy.get("decisionField") or "decision"),
        "commandField": str(command_policy.get("actionField") or "action"),
        "approvedStatuses": string_list(review_policy.get("approvedStatuses")),
        "rejectedStatuses": string_list(review_policy.get("rejectedStatuses")),
        "availableCommands": {
            "forceWhitelistActions": string_list(command_policy.get("forceWhitelistActions")),
            "forceBlacklistActions": string_list(command_policy.get("forceBlacklistActions")),
            "removeFromIndexActions": string_list(command_policy.get("removeFromIndexActions")),
        },
        "canonicalWrites": False,
        command_list_field: [
            {
                "action": "",
                "trustKey": "",
                "reviewer": review_policy.get("decisionTemplateReviewer"),
                "notes": "可填：強制白名單／強制黑名單／移除索引；留空不生效。",
                "canonicalWrites": False,
            }
        ],
        "decisions": [
            {
                "decisionId": "relationship-trust-decision." + stable_hash(record.get("trustKey")),
                "trustZoneId": record.get("trustZoneId"),
                "trustKey": record.get("trustKey"),
                "decision": pending_status,
                "reviewer": review_policy.get("decisionTemplateReviewer"),
                "notes": "",
                "zone": record.get("zone"),
                "score": record.get("score"),
                "relationshipType": record.get("relationshipType"),
                "claimSentenceZhTw": record.get("claimSentenceZhTw"),
                "fromId": record.get("fromId"),
                "toId": record.get("toId"),
                "subjectId": record.get("subjectId"),
                "controllerId": record.get("controllerId"),
                "canonicalWrites": False,
            }
            for record in records
        ],
    }


def build_fact_check_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        skill_review = object_map(record.get("skillReview"))
        fact_check = object_map(skill_review.get("factCheck"))
        if not fact_check and not record.get("factCheckQueries"):
            continue
        rows.append(
            {
                "factCheckId": "relationship-fact-check." + stable_hash(record.get("trustKey")),
                "trustZoneId": record.get("trustZoneId"),
                "trustKey": record.get("trustKey"),
                "zone": record.get("zone"),
                "score": record.get("score"),
                "relationshipType": record.get("relationshipType"),
                "fromId": record.get("fromId"),
                "toId": record.get("toId"),
                "subjectId": record.get("subjectId"),
                "controllerId": record.get("controllerId"),
                "claimSentenceZhTw": record.get("claimSentenceZhTw") or fact_check.get("claimSentenceZhTw"),
                "factCheck": fact_check,
                "sourceQuotePreviews": fact_check.get("sourceQuotePreviews") or source_quote_previews(record, {}),
                "queries": fact_check.get("queries") or record.get("factCheckQueries") or [],
                "canonicalWrites": False,
            }
        )
    rows.sort(key=lambda row: (str(object_map(row.get("factCheck")).get("verdict") or ""), -number_value(row.get("score")), str(row.get("trustKey") or "")))
    return rows


def render_fact_check_markdown(rows: list[dict[str, Any]], policy: dict[str, Any]) -> str:
    fact_policy = object_map(object_map(policy.get("skillReview")).get("factCheck"))
    lines = [
        "# 關係命題技能查證包",
        "",
        "這份文件列出每條關係要審查的繁中命題句、查證狀態、查證依據、原文句與出處。",
        "",
        "注意：如果查證狀態顯示「尚未實際外部查證」，代表目前只是整理既有候選證據，不能視為 95 分已查證通過。",
        "",
    ]
    for instruction in string_list(fact_policy.get("reviewInstructionsZhTw")):
        lines.append(f"- {instruction}")
    lines.extend(["", "## 命題清單"])
    for index, row in enumerate(rows, 1):
        fact_check = object_map(row.get("factCheck"))
        lines.extend(
            [
                "",
                f"### 命題 {index}",
                f"- 命題：{row.get('claimSentenceZhTw') or '-'}",
                f"- 內部關係鍵：`{row.get('trustKey')}`",
                f"- 信任階段：{stage_label(row.get('zone'))}",
                f"- 分數：`{row.get('score')}`",
                f"- 查證結果：{fact_check.get('verdictZhTw') or fact_check_verdict_label(str(fact_check.get('verdict') or ''), verified=bool(fact_check.get('verified')), external_lookup=bool(fact_check.get('externalLookupPerformed')))}",
                f"- 查證方式：{fact_check.get('reviewModeZhTw') or '尚未查證'}",
                f"- 合格證據數：`{fact_check.get('eligibleEvidenceCount', 0)}`",
                f"- 合格來源家族：`{', '.join(string_list(fact_check.get('distinctEligibleSourceFamilies'))) or '-'}`",
            ]
        )
        basis_items = fact_check.get("evidenceBasis")
        if isinstance(basis_items, list) and basis_items:
            lines.append("- 查證依據：")
            for basis_index, basis in enumerate(basis_items, 1):
                if not isinstance(basis, dict):
                    continue
                source = source_label(basis.get("sourceFamily") or basis.get("sourceId") or "未標示來源", policy)
                locator = basis.get("locator") or ", ".join(string_list(basis.get("evidenceRefs"))) or basis.get("url") or "-"
                verdict = basis.get("basisVerdictZhTw") or "待確認"
                sentence = basis.get("originalSentence") or "-"
                lines.append(f"  - 依據 {basis_index}：{verdict}")
                lines.append(f"    - 來源：{source} / {locator}")
                lines.append(f"    - 原文句：{sentence}")
        quote_previews = row.get("sourceQuotePreviews")
        if isinstance(quote_previews, list) and quote_previews:
            for quote_index, preview in enumerate(quote_previews, 1):
                if isinstance(preview, dict):
                    source = source_label(preview.get("sourceFamily") or preview.get("sourceId"), policy)
                    locator = preview.get("locator") or ", ".join(string_list(preview.get("evidenceRefs"))) or "-"
                    lines.append(f"- 原文/證據摘錄 {quote_index}：{preview.get('quote')}")
                    lines.append(f"- 摘錄來源 {quote_index}：`{source}` / `{locator}`")
        queries = row.get("queries")
        if isinstance(queries, list) and queries:
            lines.append("- 建議查詢：")
            for query in queries:
                if isinstance(query, dict):
                    polarity = "支持查詢" if str(query.get("polarity") or "") == "support" else "反證查詢"
                    lines.append(f"  - {polarity}：{query.get('query')}")
    lines.append("")
    return "\n".join(lines)


def markdown_cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    return text.replace("|", "\\|")


def source_summary(record: dict[str, Any], policy: dict[str, Any]) -> str:
    basis_items = evidence_basis_summary_items(record)
    if basis_items:
        parts: list[str] = []
        for item in basis_items:
            source = source_label(item.get("sourceFamily") or item.get("sourceId"), policy)
            layer = source_label(item.get("sourceLayer"), policy)
            locator = item.get("locator") or ", ".join(string_list(item.get("evidenceRefs"))) or item.get("url") or "-"
            parts.append(f"{source} / {layer} / {locator}")
        return "; ".join(parts)
    previews = source_quote_previews(record, policy)
    if previews:
        parts: list[str] = []
        for preview in previews:
            source = source_label(preview.get("sourceFamily") or preview.get("sourceId"), policy)
            layer = source_label(preview.get("sourceLayer"), policy)
            locator = preview.get("locator") or ", ".join(string_list(preview.get("evidenceRefs"))) or "-"
            parts.append(f"{source} / {layer} / {locator}")
        return "; ".join(parts)
    supports = record.get("supportingEvidence")
    if not isinstance(supports, list):
        return "-"
    parts = []
    for support in supports[:3]:
        if not isinstance(support, dict):
            continue
        source = source_label(support.get("sourceFamily") or support.get("sourceId"), policy)
        layer = source_label(support.get("sourceLayer"), policy)
        locator = support.get("locator") or ", ".join(string_list(support.get("evidenceRefs"))) or "-"
        parts.append(f"{source} / {layer} / {locator}")
    return "; ".join(parts) if parts else "-"


def quote_summary(record: dict[str, Any], policy: dict[str, Any]) -> str:
    basis_items = evidence_basis_summary_items(record)
    if basis_items:
        return " / ".join(
            str(item.get("originalSentence") or "").strip()
            for item in basis_items
            if str(item.get("originalSentence") or "").strip()
        ) or "-"
    previews = source_quote_previews(record, policy)
    if previews:
        return " / ".join(str(preview.get("quote") or "").strip() for preview in previews if str(preview.get("quote") or "").strip())
    refs: list[str] = []
    supports = record.get("supportingEvidence")
    if isinstance(supports, list):
        for support in supports[:3]:
            if isinstance(support, dict):
                refs.extend(string_list(support.get("evidenceRefs")))
    return "證據索引：" + ", ".join(refs[:5]) if refs else "-"


def stage_label(stage: Any) -> str:
    labels = {
        "stable-90": "穩定候選區（90 分以上，尚需查證）",
        "skill-reviewed-95": "技能查證通過區（95 分）",
        "human-locked-100": "人工鎖定信任區（100 分）",
        "human-rejected-0": "人工打叉黑名單（0 分）",
        "review": "待複核區",
        "accumulating": "累積證據中",
    }
    return labels.get(str(stage or ""), str(stage or "-"))


def relationship_type_label(rel_type: Any, policy: dict[str, Any]) -> str:
    labels = object_map(policy.get("relationshipLabelsZhTw"))
    defaults = {
        "parent_child": "親子／親長",
        "spouse": "夫妻／婚姻",
        "sibling": "兄弟姊妹",
        "sworn_sibling": "結義兄弟",
        "ruler_subject": "君臣／主從",
        "faction_membership": "陣營歸屬",
    }
    key = str(rel_type or "")
    return str(labels.get(key) or defaults.get(key) or key or "-")


def fact_check_status_summary(record: dict[str, Any]) -> str:
    skill_review = object_map(record.get("skillReview"))
    fact_check = object_map(skill_review.get("factCheck"))
    if not fact_check:
        return "尚未查證"
    verdict_label = str(fact_check.get("verdictZhTw") or "").strip()
    mode_label = str(fact_check.get("reviewModeZhTw") or "").strip()
    parts = [item for item in [verdict_label, mode_label] if item]
    return "；".join(parts) if parts else "尚未查證"


def evidence_basis_summary(record: dict[str, Any], policy: dict[str, Any]) -> str:
    basis_items = evidence_basis_summary_items(record)
    if not basis_items:
        return "尚無查證依據"
    parts: list[str] = []
    for item in basis_items:
        verdict = str(item.get("basisVerdictZhTw") or "").strip() or "待確認"
        source = source_label(item.get("sourceFamily") or item.get("sourceId") or "未標示來源", policy)
        parts.append(f"{verdict}（{source}）")
    return "；".join(parts)


def review_table_row(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "待審",
        "score": record.get("score"),
        "stage": record.get("zone"),
        "stageZhTw": stage_label(record.get("zone")),
        "dimension": record.get("dimension"),
        "relationshipType": record.get("relationshipType"),
        "relationshipTypeZhTw": relationship_type_label(record.get("relationshipType"), policy),
        "claimSentenceZhTw": record.get("claimSentenceZhTw"),
        "factCheckStatusZhTw": fact_check_status_summary(record),
        "evidenceBasisZhTw": evidence_basis_summary(record, policy),
        "fromId": record.get("fromId"),
        "toId": record.get("toId"),
        "subjectId": record.get("subjectId"),
        "controllerId": record.get("controllerId"),
        "scope": scope_summary(record),
        "source": source_summary(record, policy),
        "quote": quote_summary(record, policy),
        "trustKey": record.get("trustKey"),
        "trustZoneId": record.get("trustZoneId"),
        "canonicalWrites": False,
    }


def review_table_records(records: list[dict[str, Any]], policy: dict[str, Any], dimension_kind: str) -> list[dict[str, Any]]:
    table_policy = object_map(policy.get("reviewTables"))
    min_score = number_value(table_policy.get("minScore"), 95.0)
    included_stages = set(string_list(table_policy.get("includedStages")))
    dimension_key = "factionDimensions" if dimension_kind == "faction" else "relationshipDimensions"
    dimensions = set(string_list(table_policy.get(dimension_key)))
    rows: list[dict[str, Any]] = []
    for record in records:
        if dimensions and str(record.get("dimension") or "") not in dimensions:
            continue
        if included_stages and str(record.get("zone") or "") not in included_stages:
            continue
        if number_value(record.get("score")) < min_score:
            continue
        rows.append(review_table_row(record, policy))
    rows.sort(key=lambda row: (-number_value(row.get("score")), str(row.get("relationshipType") or ""), str(row.get("trustKey") or "")))
    return rows


def render_review_table_markdown(rows: list[dict[str, Any]], policy: dict[str, Any], dimension_kind: str) -> str:
    table_policy = object_map(policy.get("reviewTables"))
    titles = object_map(table_policy.get("titleZhTw"))
    title = str(titles.get(dimension_kind + "s") or ("95 分以上陣營審核表" if dimension_kind == "faction" else "95 分以上關係審核表"))
    lines = [
        f"# {title}",
        "",
        f"- 產出時間：`{utc_now()}`",
        f"- 分數門檻：`{number_value(table_policy.get('minScore'), 95.0)}`",
        "- 這份表是審核用 Markdown，所有列都保持 `canonicalWrites=false`，不會直接寫入正式資料。",
        "- 請只依照命題句、查證狀態、查證依據、原文句與出處判斷；若原文沒有支持命題，請在決策檔把該列標為打叉。",
    ]
    for instruction in string_list(table_policy.get("instructionsZhTw")):
        lines.append(f"- {instruction}")
    lines.extend(
        [
            "",
            "| # | 審核結果 | 分數 | 信任階段 | 關係類型 | 命題句 | 查證狀態 | 查證依據 | 原文句／證據摘錄 | 出處 | 內部關係鍵 |",
            "|---:|---|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    for index, row in enumerate(rows, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    "待審／通過／打叉",
                    markdown_cell(row.get("score")),
                    markdown_cell(row.get("stageZhTw")),
                    markdown_cell(row.get("relationshipTypeZhTw")),
                    markdown_cell(row.get("claimSentenceZhTw")),
                    markdown_cell(row.get("factCheckStatusZhTw")),
                    markdown_cell(row.get("evidenceBasisZhTw")),
                    markdown_cell(row.get("quote")),
                    markdown_cell(row.get("source")),
                    f"`{markdown_cell(row.get('trustKey'))}`",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def relationship_rows(claims: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rel_policy = object_map(policy.get("relationshipDimension"))
    if not bool(rel_policy.get("enabled", True)):
        return []
    stable_types = set(string_list(rel_policy.get("stableRelationshipTypes")))
    rows: list[dict[str, Any]] = []
    for claim in claims:
        rel_type = str(claim.get("type") or "").strip()
        if stable_types and rel_type not in stable_types:
            continue
        from_id = str(claim.get("fromId") or "").strip()
        to_id = str(claim.get("toId") or "").strip()
        if not from_id or not to_id or from_id == to_id:
            continue
        dimension, trust_key = relationship_key(claim, policy)
        row = dict(claim)
        row["dimension"] = dimension
        row["relationshipType"] = rel_type
        row["subjectId"] = to_id
        row["controllerId"] = from_id
        row["trustKey"] = trust_key
        row["evidenceScore"] = relationship_score(claim, policy)
        rows.append(row)
    return rows


def faction_rows(stable_bootstrap: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    faction_policy = object_map(policy.get("factionDimension"))
    if not bool(faction_policy.get("enabled", True)):
        return []
    rows: list[dict[str, Any]] = []
    source_rows = stable_bootstrap.get(str(faction_policy.get("inputKey") or "factionTimelines"))
    if not isinstance(source_rows, list):
        return rows
    rel_type = str(faction_policy.get("relationshipType") or "faction_membership")
    for item in source_rows:
        if not isinstance(item, dict):
            continue
        subject_id = str(item.get(str(faction_policy.get("subjectField") or "generalId")) or "").strip()
        if not subject_id:
            continue
        intervals = item.get("intervals")
        if not isinstance(intervals, list):
            continue
        for interval in intervals:
            if not isinstance(interval, dict):
                continue
            faction_id = str(interval.get(str(faction_policy.get("valueField") or "faction")) or "").strip()
            if not faction_id:
                continue
            row = {
                "dimension": "faction",
                "relationshipType": rel_type,
                "subjectId": subject_id,
                "toId": subject_id,
                "factionId": faction_id,
                "controllerId": faction_id,
                "fromId": faction_id,
                "sourceLayer": item.get("sourceLayer") or "stable-bootstrap-seed",
                "sourceFamily": "stable-bootstrap",
                "sourceId": item.get("id"),
                "claimGrade": "A-baseline",
                "confidenceSignals": [str(faction_policy.get("confidenceSignal") or "faction-timeline")],
                "evidenceRefs": list(interval.get("evidenceRefs") or []),
                "validFromChapter": interval.get("validFromChapter"),
                "validToChapter": interval.get("validToChapter"),
                "quote": interval.get("quote"),
                "locator": interval.get("locator"),
                "textHash": interval.get("textHash"),
            }
            _dimension, trust_key = faction_key(row, policy)
            row["trustKey"] = trust_key
            row["evidenceScore"] = faction_score(interval, policy)
            rows.append(row)
    return rows


def human_decision_key_sets(decisions: dict[str, dict[str, Any]], policy: dict[str, Any]) -> dict[str, set[str]]:
    review_policy = object_map(policy.get("humanReview"))
    command_policy = object_map(review_policy.get("overrideCommands"))
    decision_field = str(review_policy.get("decisionField") or "decision")
    action_field = str(command_policy.get("actionField") or "action")
    approved_statuses = set(string_list(review_policy.get("approvedStatuses")))
    rejected_statuses = set(string_list(review_policy.get("rejectedStatuses")))
    force_whitelist_actions = set(string_list(command_policy.get("forceWhitelistActions")))
    force_blacklist_actions = set(string_list(command_policy.get("forceBlacklistActions")))
    remove_actions = set(string_list(command_policy.get("removeFromIndexActions")))

    whitelist_keys: set[str] = set()
    blacklist_keys: set[str] = set()
    removed_keys: set[str] = set()
    for fallback_key, decision in decisions.items():
        if not isinstance(decision, dict):
            continue
        keys = {
            str(decision.get("trustKey") or "").strip(),
            str(decision.get("trustZoneId") or "").strip(),
            str(fallback_key or "").strip(),
        }
        keys.discard("")
        if not keys:
            continue
        action = str(decision.get(action_field) or "").strip()
        status = str(decision.get(decision_field) or "").strip()
        if action in remove_actions:
            removed_keys.update(keys)
            whitelist_keys.difference_update(keys)
            blacklist_keys.difference_update(keys)
            continue
        if action in force_blacklist_actions or status in rejected_statuses:
            blacklist_keys.update(keys)
            whitelist_keys.difference_update(keys)
            removed_keys.difference_update(keys)
            continue
        if action in force_whitelist_actions or status in approved_statuses:
            whitelist_keys.update(keys)
            blacklist_keys.difference_update(keys)
            removed_keys.difference_update(keys)
    return {
        "whitelist": expand_bidirectional_decision_keys(whitelist_keys, policy),
        "blacklist": expand_bidirectional_decision_keys(blacklist_keys, policy),
        "removed": expand_bidirectional_decision_keys(removed_keys, policy),
    }


def expand_bidirectional_decision_keys(keys: set[str], policy: dict[str, Any]) -> set[str]:
    relation_policy = object_map(policy.get("relationshipDimension"))
    bidirectional_types = set(string_list(relation_policy.get("bidirectionalRelationshipTypes")))
    expanded = set(keys)
    for key in keys:
        parts = str(key or "").split("|")
        if len(parts) != 4:
            continue
        dimension, rel_type, first_id, second_id = parts
        if dimension != "relationship" or rel_type not in bidirectional_types:
            continue
        if first_id and second_id and first_id != second_id:
            expanded.add(f"{dimension}|{rel_type}|{second_id}|{first_id}")
    return expanded


def build_skip_index(records: list[dict[str, Any]], policy: dict[str, Any], decisions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    no_recompute_keys = [str(row.get("trustKey") or "") for row in records if row.get("noRecompute") and row.get("trustKey")]
    fixed_alias_keys = [str(row.get("trustKey") or "") for row in records if row.get("fixedAliasLike") and row.get("trustKey")]
    negative_keys = [str(row.get("trustKey") or "") for row in records if row.get("negativeCondition") and row.get("trustKey")]
    decision_sets = human_decision_key_sets(decisions or {}, policy)
    explicit_whitelist = set(decision_sets["whitelist"])
    explicit_blacklist = set(decision_sets["blacklist"])
    removed_keys = set(decision_sets["removed"])
    whitelist_keys = (set(no_recompute_keys) | explicit_whitelist) - explicit_blacklist - removed_keys
    blacklist_keys = (set(negative_keys) | explicit_blacklist) - explicit_whitelist - removed_keys
    skip_policy = skip_index_policy(policy)
    positive_name = str(skip_policy.get("positiveConditionName") or "relationship-trust-whitelist")
    negative_name = str(skip_policy.get("negativeConditionName") or "relationship-trust-blacklist")
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "relationship-trust-zone-skip-index",
        "keySemantics": "relationship-pair-key-only",
        "positiveConditionName": positive_name,
        "negativeConditionName": negative_name,
        "positiveConditionSemantics": str(skip_policy.get("positiveConditionSemantics") or ""),
        "negativeConditionSemantics": str(skip_policy.get("negativeConditionSemantics") or ""),
        "noRecomputeTrustKeys": sorted(whitelist_keys),
        "fixedAliasLikeTrustKeys": sorted(set(fixed_alias_keys)),
        "negativeConditionTrustKeys": sorted(blacklist_keys),
        "blockedRelationshipTrustKeys": sorted(blacklist_keys),
        "whitelistTrustKeys": sorted(whitelist_keys),
        "blacklistTrustKeys": sorted(blacklist_keys),
        "decisionOnlyWhitelistTrustKeys": sorted(explicit_whitelist - set(no_recompute_keys) - removed_keys),
        "decisionOnlyBlacklistTrustKeys": sorted(explicit_blacklist - set(negative_keys) - removed_keys),
        "removedByHumanDecisionTrustKeys": sorted(removed_keys),
        "count": len(whitelist_keys),
        "fixedAliasLikeCount": len(set(fixed_alias_keys)),
        "negativeConditionCount": len(blacklist_keys),
        "whitelistCount": len(whitelist_keys),
        "blacklistCount": len(blacklist_keys),
        "decisionOnlyWhitelistCount": len(explicit_whitelist - set(no_recompute_keys) - removed_keys),
        "decisionOnlyBlacklistCount": len(explicit_blacklist - set(negative_keys) - removed_keys),
        "removedByHumanDecisionCount": len(removed_keys),
        "canonicalWrites": False,
    }


def build_relationship_trust_zone(
    *,
    policy_path: Path,
    relationship_claims_path: Path | None,
    stable_bootstrap_path: Path | None,
    human_decisions_path: Path | None,
    output_root: Path | None,
    overwrite: bool = False,
) -> dict[str, Any]:
    policy = read_json(policy_path)
    inputs = object_map(policy.get("inputs"))
    outputs = object_map(policy.get("outputs"))
    claims_path = relationship_claims_path or resolve_path(str(inputs.get("relationshipClaimsPath") or ""))
    bootstrap_path = stable_bootstrap_path or resolve_path(str(inputs.get("stableBootstrapPath") or ""))
    resolved_human_decisions_path = human_decisions_path or resolve_path(str(inputs.get("humanReviewDecisionsPath") or ""))
    review_evidence_path = resolve_path(str(inputs.get("skillReviewEvidencePath") or "")) if str(inputs.get("skillReviewEvidencePath") or "").strip() else Path("")
    root = output_root or resolve_path(str(outputs.get("outputRoot") or ""))
    if root.exists() and any(root.iterdir()) and not overwrite:
        raise FileExistsError(f"output already exists: {repo_relative(root)}")

    claims = read_jsonl(claims_path)
    stable_bootstrap = read_json(bootstrap_path)
    review_packets = read_keyed_jsonl(review_evidence_path, "trustKey") if str(review_evidence_path) and review_evidence_path.exists() else {}
    name_map = build_name_map(stable_bootstrap)
    source_rows = [*relationship_rows(claims, policy), *faction_rows(stable_bootstrap, policy)]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blockers: dict[str, list[str]] = defaultdict(list)
    dimensions: dict[str, str] = {}
    for row in source_rows:
        trust_key = str(row.get("trustKey") or "").strip()
        if not trust_key:
            continue
        grouped[trust_key].append(row)
        dimensions[trust_key] = str(row.get("dimension") or "")
        passed, row_blockers = stable_requirement_passed(row, policy)
        if not passed:
            blockers[trust_key].extend(row_blockers)

    records = [
        trust_record(
            dimension=dimensions.get(trust_key, ""),
            trust_key=trust_key,
            rows=rows,
            policy=policy,
            stable_blockers=sorted(set(blockers.get(trust_key, []))),
        )
        for trust_key, rows in grouped.items()
    ]
    for record in records:
        record["claimSentenceZhTw"] = claim_sentence(record, policy, name_map)
        record["factCheckQueries"] = fact_check_queries(record, policy, name_map)
    records = apply_skill_review(records, policy, name_map, review_packets)
    human_decisions = read_human_decisions(resolved_human_decisions_path, policy) if str(resolved_human_decisions_path) else {}
    records = apply_human_decisions(records, human_decisions, policy)
    records.sort(key=lambda row: (str(row.get("zone") or ""), str(row.get("dimension") or ""), str(row.get("trustKey") or "")))
    stable_stage = stage_name(policy, "stable", "stable-90")
    skill_stage = stage_name(policy, "skillReviewed", "skill-reviewed-95")
    locked_stage = stage_name(policy, "humanLocked", "human-locked-100")
    rejected_stage = stage_name(policy, "humanRejected", "human-rejected-0")
    removed_stage = stage_name(policy, "humanRemoved", "human-removed-from-index")
    review_stage = stage_name(policy, "review", "review")
    accumulating_stage = stage_name(policy, "accumulating", "accumulating")
    stable_rows = [row for row in records if row.get("zone") == stable_stage]
    skill_reviewed_rows = [row for row in records if row.get("zone") == skill_stage]
    human_locked_rows = [row for row in records if row.get("zone") == locked_stage]
    human_rejected_rows = [row for row in records if row.get("zone") == rejected_stage]
    human_removed_rows = [row for row in records if row.get("zone") == removed_stage]
    review_rows = [row for row in records if row.get("zone") == review_stage]
    accumulating_rows = [row for row in records if row.get("zone") == accumulating_stage]
    conflict_rows: list[dict[str, Any]] = []
    human_review_records = candidate_review_records(records, policy)
    fact_check_rows = build_fact_check_rows(records)
    relationship_table_rows = review_table_records(records, policy, "relationship")
    faction_table_rows = review_table_records(records, policy, "faction")

    root.mkdir(parents=True, exist_ok=True)
    stable_path = root / str(outputs.get("stableFileName") or "relationship-trust-zone.stable.jsonl")
    skill_reviewed_path = root / str(outputs.get("skillReviewedFileName") or "relationship-trust-zone.skill-reviewed.jsonl")
    human_locked_path = root / str(outputs.get("humanLockedFileName") or "relationship-trust-zone.human-locked.jsonl")
    human_rejected_path = root / str(outputs.get("humanRejectedFileName") or "relationship-trust-zone.human-rejected.jsonl")
    human_removed_path = root / str(outputs.get("humanRemovedFileName") or "relationship-trust-zone.human-removed.jsonl")
    review_path = root / str(outputs.get("reviewFileName") or "relationship-trust-zone.review.jsonl")
    accumulating_path = root / str(outputs.get("accumulatingFileName") or "relationship-trust-zone.accumulating.jsonl")
    conflict_path = root / str(outputs.get("conflictFileName") or "relationship-trust-zone.conflicts.jsonl")
    skip_index_path = root / str(outputs.get("skipIndexFileName") or "relationship-trust-zone.skip-index.json")
    human_review_md_path = root / str(outputs.get("humanReviewMarkdownFileName") or "relationship-trust-zone-human-review.md")
    human_decision_template_path = root / str(outputs.get("humanDecisionTemplateFileName") or "relationship-trust-zone.human-decisions.template.json")
    fact_check_path = root / str(outputs.get("factCheckFileName") or "relationship-trust-zone.fact-check.jsonl")
    fact_check_md_path = root / str(outputs.get("factCheckMarkdownFileName") or "relationship-trust-zone-fact-check.md")
    relationship_table_path = root / str(outputs.get("relationshipReviewTable95PlusFileName") or "relationship-trust-zone-review-95plus.relationships.md")
    faction_table_path = root / str(outputs.get("factionReviewTable95PlusFileName") or "relationship-trust-zone-review-95plus.factions.md")
    relationship_table_jsonl_path = root / str(outputs.get("relationshipReviewTable95PlusJsonlFileName") or "relationship-trust-zone-review-95plus.relationships.jsonl")
    faction_table_jsonl_path = root / str(outputs.get("factionReviewTable95PlusJsonlFileName") or "relationship-trust-zone-review-95plus.factions.jsonl")
    summary_path = root / str(outputs.get("summaryFileName") or "relationship-trust-zone-summary.json")

    write_jsonl(stable_path, stable_rows)
    write_jsonl(skill_reviewed_path, skill_reviewed_rows)
    write_jsonl(human_locked_path, human_locked_rows)
    write_jsonl(human_rejected_path, human_rejected_rows)
    write_jsonl(human_removed_path, human_removed_rows)
    write_jsonl(review_path, review_rows)
    write_jsonl(accumulating_path, accumulating_rows)
    write_jsonl(conflict_path, conflict_rows)
    skip_index = build_skip_index(records, policy, human_decisions)
    write_json(skip_index_path, skip_index)
    human_review_md_path.write_text(render_human_review_markdown(human_review_records, policy), encoding="utf-8")
    write_json(human_decision_template_path, build_human_decision_template(human_review_records, policy))
    write_jsonl(fact_check_path, fact_check_rows)
    fact_check_md_path.write_text(render_fact_check_markdown(fact_check_rows, policy), encoding="utf-8")
    write_jsonl(relationship_table_jsonl_path, relationship_table_rows)
    write_jsonl(faction_table_jsonl_path, faction_table_rows)
    relationship_table_path.write_text(render_review_table_markdown(relationship_table_rows, policy, "relationship"), encoding="utf-8")
    faction_table_path.write_text(render_review_table_markdown(faction_table_rows, policy, "faction"), encoding="utf-8")

    zone_counts = Counter(str(row.get("zone") or "") for row in records)
    dimension_counts = Counter(str(row.get("dimension") or "") for row in records)
    stable_type_counts = Counter(str(row.get("relationshipType") or "") for row in [*stable_rows, *skill_reviewed_rows, *human_locked_rows])
    summary = {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "relationship-trust-zone",
        "canonicalWrites": False,
        "policyId": policy.get("id"),
        "inputs": {
            "policyPath": repo_relative(policy_path),
            "relationshipClaimsPath": repo_relative(claims_path),
            "stableBootstrapPath": repo_relative(bootstrap_path),
            "humanReviewDecisionsPath": repo_relative(resolved_human_decisions_path),
            "humanReviewDecisionCount": len(human_decisions),
            "skillReviewEvidencePath": repo_relative(review_evidence_path) if str(review_evidence_path) else "",
            "skillReviewEvidencePacketCount": len(review_packets),
        },
        "outputs": {
            "stable": repo_relative(stable_path),
            "skillReviewed": repo_relative(skill_reviewed_path),
            "humanLocked": repo_relative(human_locked_path),
            "humanRejected": repo_relative(human_rejected_path),
            "humanRemoved": repo_relative(human_removed_path),
            "review": repo_relative(review_path),
            "accumulating": repo_relative(accumulating_path),
            "conflicts": repo_relative(conflict_path),
            "skipIndex": repo_relative(skip_index_path),
            "humanReviewMarkdown": repo_relative(human_review_md_path),
            "humanDecisionTemplate": repo_relative(human_decision_template_path),
            "factCheck": repo_relative(fact_check_path),
            "factCheckMarkdown": repo_relative(fact_check_md_path),
            "relationshipReviewTable95Plus": repo_relative(relationship_table_path),
            "factionReviewTable95Plus": repo_relative(faction_table_path),
            "relationshipReviewTable95PlusJsonl": repo_relative(relationship_table_jsonl_path),
            "factionReviewTable95PlusJsonl": repo_relative(faction_table_jsonl_path),
            "summary": repo_relative(summary_path),
        },
        "metrics": {
            "sourceRelationshipClaimCount": len(claims),
            "sourceTrustEvidenceRowCount": len(source_rows),
            "trustKeyCount": len(records),
            "stableCount": len(stable_rows),
            "skillReviewedCount": len(skill_reviewed_rows),
            "humanLockedCount": len(human_locked_rows),
            "humanRejectedCount": len(human_rejected_rows),
            "humanRemovedCount": len(human_removed_rows),
            "reviewCount": len(review_rows),
            "accumulatingCount": len(accumulating_rows),
            "conflictCount": len(conflict_rows),
            "humanReviewCandidateCount": len(human_review_records),
            "factCheckRowCount": len(fact_check_rows),
            "relationshipReviewTable95PlusCount": len(relationship_table_rows),
            "factionReviewTable95PlusCount": len(faction_table_rows),
            "noRecomputeKeyCount": len(skip_index["noRecomputeTrustKeys"]),
            "whitelistCount": len(skip_index["whitelistTrustKeys"]),
            "blacklistCount": len(skip_index["blacklistTrustKeys"]),
            "zoneCounts": dict(sorted(zone_counts.items())),
            "dimensionCounts": dict(sorted(dimension_counts.items())),
            "stableRelationshipTypeCounts": dict(sorted(stable_type_counts.items())),
        },
        "guards": string_list(policy.get("guards")),
    }
    write_json(summary_path, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build relationship/faction trust-zone datasets from claim graph evidence.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--relationship-claims", default=None)
    parser.add_argument("--stable-bootstrap", default=None)
    parser.add_argument("--human-decisions", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_relationship_trust_zone(
        policy_path=resolve_path(args.policy),
        relationship_claims_path=resolve_path(args.relationship_claims) if args.relationship_claims else None,
        stable_bootstrap_path=resolve_path(args.stable_bootstrap) if args.stable_bootstrap else None,
        human_decisions_path=resolve_path(args.human_decisions) if args.human_decisions else None,
        output_root=resolve_path(args.output_root) if args.output_root else None,
        overwrite=bool(args.overwrite),
    )
    metrics = summary["metrics"]
    print(
        "[build_relationship_trust_zone] "
        f"stable90={metrics['stableCount']} skill95={metrics['skillReviewedCount']} "
        f"human100={metrics['humanLockedCount']} rejected0={metrics['humanRejectedCount']} "
        f"review={metrics['reviewCount']} accumulating={metrics['accumulatingCount']} "
        f"whitelist={metrics['whitelistCount']} blacklist={metrics['blacklistCount']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
