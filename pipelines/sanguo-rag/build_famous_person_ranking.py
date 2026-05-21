from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from estimate_core_person_completion import (
    apply_scoring_policy,
    collect_metrics,
    load_observed_rows,
    read_json,
    read_jsonl,
    resolve_input_paths,
)
from sanguo_governance_loader import load_core_person_completion_policy


DEFAULT_OBSERVED_MENTIONS_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/observed-mentions/observed-mentions.json")
DEFAULT_STABLE_KNOWLEDGE_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json")
DEFAULT_READY_EVENTS_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/events/events.jsonl")
DEFAULT_ROUNDS_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/knowledge-growth-rounds")
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress")
DEFAULT_FAME_POLICY_PATH = Path("data/sanguo/policies/policy-famous-person-ranking.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a data-driven top famous Sanguo person ranking.")
    parser.add_argument("--round-id", default="top50-famous")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--observed-mentions", default=str(DEFAULT_OBSERVED_MENTIONS_PATH))
    parser.add_argument("--stable-knowledge", default=str(DEFAULT_STABLE_KNOWLEDGE_PATH))
    parser.add_argument("--event-question-seeds", default=None)
    parser.add_argument("--source-event-packets", default=None)
    parser.add_argument("--relationship-evidence", default=None)
    parser.add_argument("--ready-events", default=str(DEFAULT_READY_EVENTS_PATH))
    parser.add_argument("--rounds-root", default=str(DEFAULT_ROUNDS_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--governance-root", default=None)
    parser.add_argument("--core-person-completion-policy", default=None)
    parser.add_argument("--fame-ranking-policy", default=str(DEFAULT_FAME_POLICY_PATH))
    parser.add_argument("--no-primary-canon-defaults", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_policy(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def stable_identity_indexes(
    stable: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], set[str], dict[str, dict[str, Any]], Counter[str]]:
    names: dict[str, str] = {}
    factions: dict[str, str] = {}
    genders: dict[str, str] = {}
    ids: set[str] = set()

    def ingest_identity(row: dict[str, Any], *, count_as_candidate: bool) -> None:
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            return
        if count_as_candidate:
            ids.add(general_id)
        name = str(row.get("name") or "").strip()
        if name:
            names[general_id] = name
        faction = str(row.get("faction") or row.get("baseFaction") or "").strip()
        if faction:
            factions[general_id] = faction
        gender = str(row.get("gender") or "").strip()
        if gender:
            genders[general_id] = gender

    for row in stable.get("identitySeeds") or []:
        ingest_identity(row, count_as_candidate=True)

    representation = policy.get("representation") if isinstance(policy.get("representation"), dict) else {}
    priority_section = str(representation.get("priorityProfileSection") or "").strip()
    focus_keys = [str(key) for key in representation.get("priorityProfileFocusIdKeys") or []]
    priority_profiles: dict[str, dict[str, Any]] = {}
    priority_signal_counts: Counter[str] = Counter()
    for row in stable.get(priority_section) or []:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        ingest_identity(row, count_as_candidate=True)
        priority_profiles[general_id] = row
        focus_count = 0
        for key in focus_keys:
            value = row.get(key)
            if isinstance(value, list):
                focus_count += len([item for item in value if str(item or "").strip()])
        priority_signal_counts[general_id] += 1 + focus_count
    return names, factions, genders, ids, priority_profiles, priority_signal_counts


def normalized_group(value: str, policy: dict[str, Any]) -> str:
    representation = policy.get("representation") if isinstance(policy.get("representation"), dict) else {}
    raw = str(value or "").strip().lower()
    for group, aliases in (representation.get("groupAliases") or {}).items():
        normalized_aliases = {str(alias).strip().lower() for alias in aliases or []}
        if raw in normalized_aliases:
            return str(group)
    return raw


def direct_mention_stats(rows: list[dict[str, Any]]) -> tuple[Counter[str], dict[str, set[int]], Counter[str]]:
    direct_counts: Counter[str] = Counter()
    direct_chapters: dict[str, set[int]] = defaultdict(set)
    scene_presence_counts: Counter[str] = Counter()
    for row in rows:
        if row.get("matchStatus") != "resolved":
            continue
        chapter_no = int(row.get("chapterNo") or 0)
        matched_ids = [str(general_id).strip() for general_id in row.get("matchedGeneralIds") or [] if str(general_id).strip()]
        scene_ids = [str(general_id).strip() for general_id in row.get("sceneParticipants") or [] if str(general_id).strip()]
        for general_id in matched_ids:
            direct_counts[general_id] += 1
            if chapter_no > 0:
                direct_chapters[general_id].add(chapter_no)
        for general_id in set(matched_ids + scene_ids):
            scene_presence_counts[general_id] += 1
    return direct_counts, direct_chapters, scene_presence_counts


def collect_ids_from_value(value: Any, id_keys: set[str], id_array_keys: set[str]) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in id_keys and str(item or "").strip():
                ids.add(str(item).strip())
            if key in id_array_keys and isinstance(item, list):
                ids.update(str(child).strip() for child in item if str(child or "").strip())
            ids.update(collect_ids_from_value(item, id_keys, id_array_keys))
    elif isinstance(value, list):
        for item in value:
            ids.update(collect_ids_from_value(item, id_keys, id_array_keys))
    return ids


def stable_signal_counts(stable: dict[str, Any], policy: dict[str, Any]) -> Counter[str]:
    signal_policy = policy.get("stableKnowledgeSignal") if isinstance(policy.get("stableKnowledgeSignal"), dict) else {}
    sections = [str(section) for section in signal_policy.get("sections") or []]
    id_keys = {str(key) for key in signal_policy.get("idKeys") or []}
    id_array_keys = {str(key) for key in signal_policy.get("idArrayKeys") or []}
    counts: Counter[str] = Counter()
    for section in sections:
        value = stable.get(section)
        if isinstance(value, list):
            for item in value:
                for general_id in collect_ids_from_value(item, id_keys, id_array_keys):
                    counts[general_id] += 1
        elif isinstance(value, dict):
            for item in value.values():
                for general_id in collect_ids_from_value(item, id_keys, id_array_keys):
                    counts[general_id] += 1
    return counts


def excluded(general_id: str, policy: dict[str, Any]) -> bool:
    return any(general_id.startswith(str(prefix)) for prefix in policy.get("excludedGeneralIdPrefixes") or [])


def score_row(general_id: str, metrics: dict[str, Any], fame: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    weights = policy.get("scoreWeights") or {}
    normalization = policy.get("normalization") or {}
    direct_count = int(fame["directMentionCounts"][general_id])
    direct_chapter_count = len(fame["directChapters"].get(general_id) or set())
    scene_presence_count = int(fame["scenePresenceCounts"][general_id])
    packet_count = int(metrics["packetCounts"][general_id])
    relationship_count = int(metrics["relationshipCounts"][general_id])
    stable_signal_count = int(fame["stableSignalCounts"][general_id])
    priority_profile_signal_count = int(fame["priorityProfileSignalCounts"][general_id])
    seed_family_count = len(metrics["seedFamilies"].get(general_id) or set())
    ready_event_count = int(metrics["readyEventCounts"][general_id])
    chapter_denominator = max(1.0, float(normalization.get("chapterCoverageDenominator") or 1.0))
    seed_family_denominator = max(1.0, float(normalization.get("seedFamilyDenominator") or 1.0))
    components = {
        "directMentionLog": math.log1p(direct_count) * float(weights.get("directMentionLog") or 0.0),
        "directChapterCoverage": min(1.0, direct_chapter_count / chapter_denominator) * float(weights.get("directChapterCoverage") or 0.0),
        "scenePresenceLog": math.log1p(scene_presence_count) * float(weights.get("scenePresenceLog") or 0.0),
        "sourcePacketLog": math.log1p(packet_count) * float(weights.get("sourcePacketLog") or 0.0),
        "relationshipLog": math.log1p(relationship_count) * float(weights.get("relationshipLog") or 0.0),
        "stableKnowledgeSignalLog": math.log1p(stable_signal_count) * float(weights.get("stableKnowledgeSignalLog") or 0.0),
        "priorityProfileSignalLog": math.log1p(priority_profile_signal_count) * float(weights.get("priorityProfileSignalLog") or 0.0),
        "seedFamilyCoverage": min(1.0, seed_family_count / seed_family_denominator) * float(weights.get("seedFamilyCoverage") or 0.0),
        "readyEventLog": math.log1p(ready_event_count) * float(weights.get("readyEventLog") or 0.0),
    }
    gender = fame["genders"].get(general_id, "")
    priority_profile = fame["priorityProfiles"].get(general_id) or {}
    priority_focus_ids = [
        str(focus_id).strip()
        for focus_id in priority_profile.get("relationshipFocusIds") or []
        if str(focus_id or "").strip()
    ]
    event_hooks = [str(item).strip() for item in priority_profile.get("eventHooks") or [] if str(item or "").strip()]
    affect_tags = [str(item).strip() for item in priority_profile.get("affectTags") or [] if str(item or "").strip()]
    return {
        "generalId": general_id,
        "displayName": fame["names"].get(general_id, general_id),
        "faction": fame["factions"].get(general_id, ""),
        "gender": gender,
        "representationGroup": normalized_group(gender, policy),
        "fameScoreRaw": round(sum(components.values()), 4),
        "scoreComponents": {key: round(value, 4) for key, value in components.items()},
        "signals": {
            "directMentionCount": direct_count,
            "directChapterCount": direct_chapter_count,
            "scenePresenceCount": scene_presence_count,
            "sourceEventPacketCount": packet_count,
            "relationshipEvidenceCount": relationship_count,
            "stableKnowledgeSignalCount": stable_signal_count,
            "priorityProfileSignalCount": priority_profile_signal_count,
            "seedFamilyCount": seed_family_count,
            "readyEventCount": ready_event_count,
        },
        "priorityProfile": {
            "present": bool(priority_profile),
            "relationshipFocusIds": priority_focus_ids,
            "eventHooks": event_hooks,
            "affectTags": affect_tags,
            "externalSourceNeeded": priority_profile.get("externalSourceNeeded"),
            "reviewStatus": priority_profile.get("reviewStatus"),
            "sourceLayer": priority_profile.get("sourceLayer"),
        },
    }


def build_rows(args: argparse.Namespace, metrics: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    stable = read_json(Path(args.stable_knowledge))
    names, factions, genders, stable_ids, priority_profiles, priority_profile_counts = stable_identity_indexes(stable, policy)
    stable_counts = stable_signal_counts(stable, policy)
    observed_rows = load_observed_rows(Path(args.observed_mentions))
    direct_counts, direct_chapters, scene_presence_counts = direct_mention_stats(observed_rows)
    candidates = (
        stable_ids
        | set(direct_counts)
        | set(scene_presence_counts)
        | set(stable_counts)
        | set(metrics["packetCounts"])
        | set(metrics["relationshipCounts"])
        | set(metrics["seedSlots"])
    )
    min_signal_count = int((policy.get("candidateRequirements") or {}).get("minAnySignalCount") or 0)
    fame = {
        "names": names,
        "factions": factions,
        "genders": genders,
        "directMentionCounts": direct_counts,
        "directChapters": direct_chapters,
        "scenePresenceCounts": scene_presence_counts,
        "stableSignalCounts": stable_counts,
        "priorityProfileSignalCounts": priority_profile_counts,
        "priorityProfiles": priority_profiles,
    }
    rows = []
    for general_id in sorted(candidates):
        if not general_id or excluded(general_id, policy):
            continue
        signal_count = (
            direct_counts[general_id]
            + scene_presence_counts[general_id]
            + stable_counts[general_id]
            + priority_profile_counts[general_id]
            + metrics["packetCounts"][general_id]
            + metrics["relationshipCounts"][general_id]
            + metrics["seedSlots"][general_id]
        )
        if signal_count < min_signal_count:
            continue
        rows.append(score_row(general_id, metrics, fame, policy))
    max_score = max((float(row["fameScoreRaw"]) for row in rows), default=1.0)
    for row in rows:
        row["fameScore"] = round(float(row["fameScoreRaw"]) / max_score * 100.0, 2)
    return sorted(
        rows,
        key=lambda row: (
            float(row.get("fameScoreRaw") or 0.0),
            int(row["signals"].get("directMentionCount") or 0),
            int(row["signals"].get("directChapterCount") or 0),
            int(row["signals"].get("sourceEventPacketCount") or 0),
            int(row["signals"].get("relationshipEvidenceCount") or 0),
            int(row["signals"].get("stableKnowledgeSignalCount") or 0),
            int(row["signals"].get("priorityProfileSignalCount") or 0),
            str(row.get("generalId") or ""),
        ),
        reverse=True,
    )


def ranking_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    signals = row.get("signals") or {}
    return (
        float(row.get("fameScoreRaw") or 0.0),
        int(signals.get("directMentionCount") or 0),
        int(signals.get("directChapterCount") or 0),
        int(signals.get("sourceEventPacketCount") or 0),
        int(signals.get("relationshipEvidenceCount") or 0),
        int(signals.get("stableKnowledgeSignalCount") or 0),
        int(signals.get("priorityProfileSignalCount") or 0),
        str(row.get("generalId") or ""),
    )


def representation_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("representationGroup") or "") for row in rows if str(row.get("representationGroup") or ""))


def representation_candidate_sort_key(row: dict[str, Any], policy: dict[str, Any]) -> tuple[Any, ...]:
    representation = policy.get("representation") if isinstance(policy.get("representation"), dict) else {}
    candidate_sort = representation.get("candidateSort") if isinstance(representation.get("candidateSort"), dict) else {}
    profile = row.get("priorityProfile") if isinstance(row.get("priorityProfile"), dict) else {}
    preferred_affect_tags = {str(item) for item in candidate_sort.get("preferredAffectTags") or []}
    affect_tags = {str(item) for item in profile.get("affectTags") or []}
    focus_count = len(profile.get("relationshipFocusIds") or [])
    event_hook_count = len(profile.get("eventHooks") or [])
    preferred_affect_count = len(preferred_affect_tags & affect_tags)
    external_ready = profile.get("externalSourceNeeded") is False
    profile_bonus = 1 if profile.get("present") and candidate_sort.get("preferPriorityProfile") else 0
    source_ready_bonus = 1 if external_ready and candidate_sort.get("preferExternalSourceReady") else 0
    profile_score = (
        focus_count * float(candidate_sort.get("focusIdWeight") or 0.0)
        + event_hook_count * float(candidate_sort.get("eventHookWeight") or 0.0)
        + preferred_affect_count * float(candidate_sort.get("preferredAffectTagWeight") or 0.0)
    )
    fallback_score = float(row.get("fameScoreRaw") or 0.0) if candidate_sort.get("fallbackToFameScore", True) else 0.0
    return (
        profile_bonus,
        source_ready_bonus,
        profile_score,
        fallback_score,
        int(row.get("naturalRank") or 0) * -1,
        str(row.get("generalId") or ""),
    )


def select_rows_with_representation(
    ranked_rows: list[dict[str, Any]],
    top: int,
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    top = max(0, top)
    for natural_rank, row in enumerate(ranked_rows, start=1):
        row["naturalRank"] = natural_rank
        row["selectionReason"] = "natural-rank"

    selected = list(ranked_rows[:top])
    selected_ids = {str(row.get("generalId")) for row in selected}
    representation = policy.get("representation") if isinstance(policy.get("representation"), dict) else {}
    min_counts = {
        str(group): int(count or 0)
        for group, count in (representation.get("minCounts") or {}).items()
        if int(count or 0) > 0
    }
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    unmet: list[dict[str, Any]] = []

    if representation.get("enabled") and min_counts and top > 0:
        counts = representation_counts(selected)

        def can_remove(row: dict[str, Any]) -> bool:
            group = str(row.get("representationGroup") or "")
            if group not in min_counts:
                return True
            return counts[group] - 1 >= min_counts[group]

        for group, required_count in min_counts.items():
            while counts[group] < required_count:
                candidate = next(
                    iter(sorted(
                        [
                            row
                            for row in ranked_rows
                            if str(row.get("representationGroup") or "") == group
                            and str(row.get("generalId")) not in selected_ids
                        ],
                        key=lambda row: representation_candidate_sort_key(row, policy),
                        reverse=True,
                    )),
                    None,
                )
                removable = [row for row in selected if can_remove(row)]
                if candidate is None or not removable:
                    unmet.append({
                        "group": group,
                        "requiredCount": required_count,
                        "actualCount": counts[group],
                    })
                    break
                removed_row = min(removable, key=ranking_sort_key)
                selected.remove(removed_row)
                selected_ids.remove(str(removed_row.get("generalId")))
                counts[str(removed_row.get("representationGroup") or "")] -= 1
                candidate["selectionReason"] = str(representation.get("selectionReason") or "representation-policy")
                selected.append(candidate)
                selected_ids.add(str(candidate.get("generalId")))
                counts[group] += 1
                added.append({
                    "generalId": candidate.get("generalId"),
                    "displayName": candidate.get("displayName"),
                    "representationGroup": group,
                    "naturalRank": candidate.get("naturalRank"),
                })
                removed.append({
                    "generalId": removed_row.get("generalId"),
                    "displayName": removed_row.get("displayName"),
                    "representationGroup": removed_row.get("representationGroup"),
                    "naturalRank": removed_row.get("naturalRank"),
                })

    selected = sorted(selected, key=ranking_sort_key, reverse=True)
    summary = {
        "enabled": bool(representation.get("enabled")),
        "requiredCounts": min_counts,
        "actualCounts": dict(sorted(representation_counts(selected).items())),
        "addedByPolicy": added,
        "removedByPolicy": removed,
        "unmetRequirements": unmet,
    }
    return selected, summary


def edge_confidence(edge: dict[str, Any]) -> float:
    try:
        return float(edge.get("edgeConfidence") if edge.get("edgeConfidence") is not None else edge.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_relationship_edge(edge: dict[str, Any], source_kind: str) -> dict[str, Any] | None:
    from_id = str(edge.get("fromId") or edge.get("sourceGeneralId") or "").strip()
    to_id = str(edge.get("toId") or edge.get("targetGeneralId") or "").strip()
    relation_type = str(edge.get("type") or edge.get("relationshipType") or edge.get("refinedType") or "").strip()
    if not from_id or not to_id or not relation_type:
        return None
    return {
        **edge,
        "fromId": from_id,
        "toId": to_id,
        "type": relation_type,
        "edgeConfidence": edge_confidence(edge),
        "edgeSourceKind": source_kind,
    }


def load_relationship_edges(stable: dict[str, Any], relationship_evidence_path: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    audit_policy = policy.get("relationshipAudit") if isinstance(policy.get("relationshipAudit"), dict) else {}
    stable_section = str(audit_policy.get("stableEdgeSection") or "").strip()
    raw_edges: list[tuple[dict[str, Any], str]] = []
    for edge in stable.get(stable_section) or []:
        if isinstance(edge, dict):
            raw_edges.append((edge, "stable"))
    for edge in read_jsonl(relationship_evidence_path):
        raw_edges.append((edge, "relationship-evidence"))

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for edge, source_kind in raw_edges:
        normalized_edge = normalize_relationship_edge(edge, source_kind)
        if normalized_edge is None:
            continue
        refs = normalized_edge.get("evidenceRefs") or []
        first_ref = str(refs[0]) if refs else ""
        key = (
            normalized_edge["fromId"],
            normalized_edge["toId"],
            normalized_edge["type"],
            first_ref,
            str(normalized_edge.get("sourceLayer") or source_kind),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_edge)
    return normalized


def has_required_evidence(edge: dict[str, Any], required_fields: list[str]) -> bool:
    for field in required_fields:
        value = edge.get(field)
        if isinstance(value, list) and value:
            continue
        if isinstance(value, str) and value.strip():
            continue
        return False
    return True


def edge_connects(edge: dict[str, Any], left: str, right: str, allowed_types: set[str]) -> bool:
    if allowed_types and str(edge.get("type") or "") not in allowed_types:
        return False
    return {str(edge.get("fromId") or ""), str(edge.get("toId") or "")} == {left, right}


def dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for issue in issues:
        key = (
            str(issue.get("severity") or ""),
            str(issue.get("issueType") or ""),
            str(issue.get("fromId") or ""),
            str(issue.get("toId") or ""),
            str(issue.get("type") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def role_activity_tags_by_general(stable: dict[str, Any], sections: list[str]) -> dict[str, set[str]]:
    tags_by_general: dict[str, set[str]] = defaultdict(set)
    for section in sections:
        for row in stable.get(section) or []:
            if not isinstance(row, dict):
                continue
            general_id = str(row.get("generalId") or row.get("sourceGeneralId") or "").strip()
            if not general_id:
                continue
            for tag in row.get("roleActivityTags") or []:
                tag_text = str(tag).strip()
                if tag_text:
                    tags_by_general[general_id].add(tag_text)
    return tags_by_general


def authority_coverage_required(
    row: dict[str, Any],
    role_tags: set[str],
    authority_policy: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    representation_group = str(row.get("representationGroup") or "").strip()
    exempt_groups = {str(item) for item in authority_policy.get("exemptRepresentationGroups") or []}
    if representation_group in exempt_groups:
        reasons.append(f"representationGroup:{representation_group}")

    exempt_role_tags = {str(item) for item in authority_policy.get("exemptRoleActivityTags") or []}
    matched_exempt_tags = sorted(role_tags & exempt_role_tags)
    reasons.extend(f"roleActivityTag:{tag}" for tag in matched_exempt_tags)
    if reasons:
        return False, reasons

    required_role_tags = {str(item) for item in authority_policy.get("requiredRoleActivityTags") or []}
    return bool(role_tags & required_role_tags), []


def build_relationship_audit(
    rows: list[dict[str, Any]],
    stable: dict[str, Any],
    relationship_evidence_path: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    audit_policy = policy.get("relationshipAudit") if isinstance(policy.get("relationshipAudit"), dict) else {}
    allowed_types = {str(item) for item in audit_policy.get("allowedTypes") or []}
    core_types = {str(item) for item in audit_policy.get("coreTypes") or []}
    non_bond_hint_types = {str(item) for item in audit_policy.get("nonBondHintTypes") or []}
    min_confidence = float(audit_policy.get("minEdgeConfidence") or 0.0)
    authority_policy = audit_policy.get("authorityCoverage") if isinstance(audit_policy.get("authorityCoverage"), dict) else {}
    authority_coverage_enabled = bool(authority_policy.get("enabled"))
    authority_types = {str(item) for item in authority_policy.get("authorityTypes") or []}
    authority_min_confidence = float(authority_policy.get("minEdgeConfidence") or min_confidence)
    authority_issue_type = str(authority_policy.get("issueType") or "missing-authority-anchor")
    authority_issue_severity = str(authority_policy.get("severity") or "warning")
    authority_role_tags = role_activity_tags_by_general(
        stable,
        [str(section) for section in authority_policy.get("roleSections") or []],
    )
    symmetric_types = {str(item) for item in audit_policy.get("symmetricTypes") or []}
    strict_symmetric_types = {str(item) for item in audit_policy.get("strictSymmetricTypes") or []}
    exclusive_type_groups = [
        {str(item) for item in group or []}
        for group in audit_policy.get("exclusiveTypeGroups") or []
        if isinstance(group, list)
    ]
    focus_relation_types = {str(item) for item in audit_policy.get("focusRelationTypes") or []}
    required_evidence_fields = [str(item) for item in audit_policy.get("requiredEvidenceFields") or []]
    unknown_type_severity = str(audit_policy.get("unknownTypeSeverity") or "warning")
    exclude_unknown_types = bool(audit_policy.get("excludeUnknownTypesFromCounts", True))
    selected_ids = {str(row.get("generalId") or "") for row in rows}
    edges = load_relationship_edges(stable, relationship_evidence_path, policy)
    edges_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edge_key_set = {(edge["fromId"], edge["toId"], edge["type"]) for edge in edges}
    issues: list[dict[str, Any]] = []
    selected_pair_edges: list[dict[str, Any]] = []
    relation_types_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    authority_inbound_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for edge in edges:
        from_id = edge["fromId"]
        to_id = edge["toId"]
        relation_type = edge["type"]
        if relation_type in non_bond_hint_types:
            continue
        relation_allowed = not allowed_types or relation_type in allowed_types
        if not relation_allowed:
            involved = [general_id for general_id in [from_id, to_id] if general_id in selected_ids]
            if involved:
                issues.append({
                    "severity": unknown_type_severity,
                    "issueType": "unknown-relationship-type",
                    "generalIds": involved,
                    "fromId": from_id,
                    "toId": to_id,
                    "type": relation_type,
                })
            if exclude_unknown_types:
                continue
        if from_id in selected_ids:
            edges_by_person[from_id].append(edge)
        if to_id in selected_ids:
            edges_by_person[to_id].append(edge)
        if relation_type in authority_types and to_id in selected_ids:
            authority_inbound_edges[to_id].append(edge)
        if from_id in selected_ids or to_id in selected_ids:
            relation_types_by_pair[tuple(sorted([from_id, to_id]))].add(relation_type)
        if from_id in selected_ids and to_id in selected_ids:
            selected_pair_edges.append({
                "fromId": from_id,
                "toId": to_id,
                "type": relation_type,
                "edgeConfidence": round(edge_confidence(edge), 4),
                "evidenceRefs": edge.get("evidenceRefs") or [],
                "sourceLayer": edge.get("sourceLayer"),
                "edgeSourceKind": edge.get("edgeSourceKind"),
            })
        if from_id in selected_ids or to_id in selected_ids:
            involved = [general_id for general_id in [from_id, to_id] if general_id in selected_ids]
            if required_evidence_fields and not has_required_evidence(edge, required_evidence_fields):
                issues.append({
                    "severity": "warning",
                    "issueType": "missing-edge-evidence",
                    "generalIds": involved,
                    "fromId": from_id,
                    "toId": to_id,
                    "type": relation_type,
                })
            if edge_confidence(edge) < min_confidence:
                issues.append({
                    "severity": "warning",
                    "issueType": "low-confidence-edge",
                    "generalIds": involved,
                    "fromId": from_id,
                    "toId": to_id,
                    "type": relation_type,
                    "edgeConfidence": round(edge_confidence(edge), 4),
                })
            if relation_type in symmetric_types and (to_id, from_id, relation_type) not in edge_key_set:
                symmetric_severity = "error" if relation_type in strict_symmetric_types else "warning"
                issues.append({
                    "severity": symmetric_severity,
                    "issueType": "missing-symmetric-edge",
                    "generalIds": involved,
                    "fromId": from_id,
                    "toId": to_id,
                    "type": relation_type,
                })

    for (left_id, right_id), relation_types in relation_types_by_pair.items():
        for exclusive_group in exclusive_type_groups:
            overlapping_types = sorted(relation_types & exclusive_group)
            if len(overlapping_types) <= 1:
                continue
            issues.append({
                "severity": "error",
                "issueType": "conflicting-exclusive-relationship-types",
                "generalIds": [general_id for general_id in [left_id, right_id] if general_id in selected_ids],
                "fromId": left_id,
                "toId": right_id,
                "type": ",".join(overlapping_types),
            })

    for row in rows:
        general_id = str(row.get("generalId") or "")
        focus_ids = [str(item) for item in (row.get("priorityProfile") or {}).get("relationshipFocusIds") or [] if str(item or "")]
        missing_focus_ids = [
            focus_id
            for focus_id in focus_ids
            if not any(edge_connects(edge, general_id, focus_id, focus_relation_types) for edge in edges)
        ]
        for focus_id in missing_focus_ids:
            issues.append({
                "severity": "warning",
                "issueType": "missing-focus-relationship",
                "generalIds": [general_id, focus_id],
                "fromId": general_id,
                "toId": focus_id,
            })

        if authority_coverage_enabled:
            role_tags = authority_role_tags.get(general_id, set())
            requires_authority, exempt_reasons = authority_coverage_required(row, role_tags, authority_policy)
            ready_authority_edges = [
                edge
                for edge in authority_inbound_edges.get(general_id) or []
                if (not required_evidence_fields or has_required_evidence(edge, required_evidence_fields))
                and edge_confidence(edge) >= authority_min_confidence
            ]
            if requires_authority and not ready_authority_edges:
                issues.append({
                    "severity": authority_issue_severity,
                    "issueType": authority_issue_type,
                    "generalIds": [general_id],
                    "fromId": None,
                    "toId": general_id,
                    "type": ",".join(sorted(authority_types)),
                    "roleActivityTags": sorted(role_tags),
                    "authorityInboundEdgeCount": len(authority_inbound_edges.get(general_id) or []),
                })

    issues = dedupe_issues(issues)
    issue_counts = Counter(str(issue.get("issueType") or "") for issue in issues)
    severity_counts = Counter(str(issue.get("severity") or "") for issue in issues)
    per_person: dict[str, dict[str, Any]] = {}
    for row in rows:
        general_id = str(row.get("generalId") or "")
        person_edges = edges_by_person.get(general_id) or []
        focus_ids = [str(item) for item in (row.get("priorityProfile") or {}).get("relationshipFocusIds") or [] if str(item or "")]
        matched_focus_ids = [
            focus_id
            for focus_id in focus_ids
            if any(edge_connects(edge, general_id, focus_id, focus_relation_types) for edge in edges)
        ]
        evidence_ready_edges = [
            edge
            for edge in person_edges
            if (not required_evidence_fields or has_required_evidence(edge, required_evidence_fields))
            and edge_confidence(edge) >= min_confidence
        ]
        per_person[general_id] = {
            "edgeCount": len(person_edges),
            "coreEdgeCount": len([edge for edge in person_edges if not core_types or str(edge.get("type") or "") in core_types]),
            "typeCounts": dict(sorted(Counter(str(edge.get("type") or "") for edge in person_edges).items())),
            "evidenceReadyEdgeCount": len(evidence_ready_edges),
            "evidenceReadyRatio": round(len(evidence_ready_edges) / max(1, len(person_edges)), 4) if person_edges else 0.0,
            "authorityCoverageRequired": authority_coverage_required(
                row,
                authority_role_tags.get(general_id, set()),
                authority_policy,
            )[0] if authority_coverage_enabled else False,
            "authorityCoverageExemptReasons": authority_coverage_required(
                row,
                authority_role_tags.get(general_id, set()),
                authority_policy,
            )[1] if authority_coverage_enabled else [],
            "authorityRoleActivityTags": sorted(authority_role_tags.get(general_id, set())),
            "authorityInboundEdgeCount": len(authority_inbound_edges.get(general_id) or []),
            "authorityInboundReadyEdgeCount": len([
                edge
                for edge in authority_inbound_edges.get(general_id) or []
                if (not required_evidence_fields or has_required_evidence(edge, required_evidence_fields))
                and edge_confidence(edge) >= authority_min_confidence
            ]),
            "focusIds": focus_ids,
            "matchedFocusIds": matched_focus_ids,
            "missingFocusIds": [focus_id for focus_id in focus_ids if focus_id not in matched_focus_ids],
            "focusCoverage": round(len(matched_focus_ids) / max(1, len(focus_ids)), 4) if focus_ids else 1.0,
        }

    return {
        "relationshipEvidencePath": str(relationship_evidence_path),
        "edgeCount": len(edges),
        "selectedPairEdgeCount": len(selected_pair_edges),
        "selectedPairEdges": selected_pair_edges,
        "issues": issues,
        "summary": {
            "issueCounts": dict(sorted(issue_counts.items())),
            "severityCounts": dict(sorted(severity_counts.items())),
            "qualityGate": "pass" if severity_counts.get("error", 0) == 0 else "fail",
        },
        "perPerson": per_person,
    }


def unit_ratio(value: float, target: float) -> float:
    if target <= 0:
        return 1.0
    return max(0.0, min(1.0, value / target))


def attach_completeness(
    rows: list[dict[str, Any]],
    relationship_audit: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    completeness_policy = policy.get("dataCompleteness") if isinstance(policy.get("dataCompleteness"), dict) else {}
    weights = {str(key): float(value or 0.0) for key, value in (completeness_policy.get("weights") or {}).items()}
    targets = {str(key): float(value or 0.0) for key, value in (completeness_policy.get("targets") or {}).items()}
    thresholds = completeness_policy.get("gradeThresholds") if isinstance(completeness_policy.get("gradeThresholds"), dict) else {}
    pass_threshold = float(thresholds.get("pass") or 80.0)
    warn_threshold = float(thresholds.get("warn") or 60.0)
    grades: Counter[str] = Counter()
    scores: list[float] = []

    for row in rows:
        signals = row.get("signals") or {}
        per_person = (relationship_audit.get("perPerson") or {}).get(str(row.get("generalId") or ""), {})
        identity_fields = [
            bool(str(row.get("displayName") or "").strip()),
            bool(str(row.get("faction") or "").strip()),
            bool(str(row.get("gender") or "").strip()),
        ]
        relationship_count = float(signals.get("relationshipEvidenceCount") or 0.0)
        stable_edge_count = float(per_person.get("edgeCount") or 0.0)
        evidence_ratio = float(per_person.get("evidenceReadyRatio") or 0.0)
        relationship_unit = max(
            unit_ratio(relationship_count, targets.get("relationshipEvidenceCount") or 1.0),
            unit_ratio(stable_edge_count, targets.get("relationshipEvidenceCount") or 1.0),
        )
        relationship_unit = relationship_unit * 0.65 + evidence_ratio * 0.35 if (relationship_count or stable_edge_count) else 0.0
        components = {
            "identity": sum(1.0 for item in identity_fields if item) / max(1, len(identity_fields)),
            "observedMentions": unit_ratio(float(signals.get("directMentionCount") or 0.0), targets.get("directMentionCount") or 1.0),
            "sourcePackets": unit_ratio(float(signals.get("sourceEventPacketCount") or 0.0), targets.get("sourceEventPacketCount") or 1.0),
            "relationshipEvidence": relationship_unit,
            "seedFamilies": unit_ratio(float(signals.get("seedFamilyCount") or 0.0), targets.get("seedFamilyCount") or 1.0),
            "readyEvents": unit_ratio(float(signals.get("readyEventCount") or 0.0), targets.get("readyEventCount") or 1.0),
            "priorityFocusCoverage": float(per_person.get("focusCoverage") if per_person.get("focusCoverage") is not None else 1.0),
        }
        total_weight = sum(weights.values()) or 1.0
        score = sum(components[key] * weights.get(key, 0.0) for key in components) / total_weight * 100.0
        grade = "pass" if score >= pass_threshold else "warn" if score >= warn_threshold else "fail"
        grades[grade] += 1
        scores.append(score)
        row["dataCompleteness"] = {
            "score": round(score, 2),
            "grade": grade,
            "components": {key: round(value, 4) for key, value in components.items()},
        }
        row["relationshipAudit"] = per_person

    return {
        "averageScore": round(sum(scores) / max(1, len(scores)), 2),
        "minScore": round(min(scores), 2) if scores else 0.0,
        "maxScore": round(max(scores), 2) if scores else 0.0,
        "gradeCounts": dict(sorted(grades.items())),
        "thresholds": {
            "pass": pass_threshold,
            "warn": warn_threshold,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    representation = report.get("representationSummary") or {}
    completeness = report.get("dataCompletenessSummary") or {}
    relationship_audit = report.get("relationshipAudit") or {}
    relationship_summary = relationship_audit.get("summary") or {}
    lines = [
        "# Famous Sanguo Person Ranking",
        "",
        f"- Round ID: `{report['roundId']}`",
        f"- Generated At: `{report['generatedAt']}`",
        f"- Top: `{report['top']}`",
        f"- Candidate Count: `{report['candidateCount']}`",
        f"- Canonical Writes: `{report['canonicalWrites']}`",
        f"- Representation Actual Counts: `{representation.get('actualCounts')}`",
        f"- Data Completeness Average: `{completeness.get('averageScore')}`",
        f"- Relationship Quality Gate: `{relationship_summary.get('qualityGate')}`",
        "",
        "## Ranking",
        "",
        "| Rank | Person | Group | Fame | Complete | Direct Mentions | Chapters | Packets | Relationships | Focus | Reason |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in report["rows"]:
        signals = row["signals"]
        audit = row.get("relationshipAudit") or {}
        focus = f"{len(audit.get('matchedFocusIds') or [])}/{len(audit.get('focusIds') or [])}" if audit.get("focusIds") else "-"
        lines.append(
            f"| {row['rank']} | `{row['displayName']}` (`{row['generalId']}`) | "
            f"`{row.get('representationGroup') or '-'}` | {row['fameScore']:.2f} | "
            f"{(row.get('dataCompleteness') or {}).get('score', 0):.2f} | "
            f"{signals['directMentionCount']} | {signals['directChapterCount']} | "
            f"{signals['sourceEventPacketCount']} | {audit.get('edgeCount', 0)} | {focus} | "
            f"`{row.get('selectionReason')}` |"
        )
    lines.extend([
        "",
        "## Relationship Audit",
        "",
        f"- Edge Count: `{relationship_audit.get('edgeCount')}`",
        f"- Selected Pair Edge Count: `{relationship_audit.get('selectedPairEdgeCount')}`",
        f"- Issue Counts: `{relationship_summary.get('issueCounts')}`",
        f"- Severity Counts: `{relationship_summary.get('severityCounts')}`",
        "",
        "### Top Issues",
        "",
    ])
    for issue in (relationship_audit.get("issues") or [])[:30]:
        lines.append(
            f"- `{issue.get('severity')}` `{issue.get('issueType')}` "
            f"`{issue.get('fromId')}` -> `{issue.get('toId')}` `{issue.get('type') or ''}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    apply_scoring_policy(
        load_core_person_completion_policy(
            args.governance_root,
            core_person_completion_policy=args.core_person_completion_policy,
        )
    )
    policy = read_policy(Path(args.fame_ranking_policy))
    input_paths = resolve_input_paths(args)
    metrics = collect_metrics(args, input_paths)
    ranked_rows = build_rows(args, metrics, policy)
    selected_rows, representation_summary = select_rows_with_representation(ranked_rows, args.top, policy)
    stable = read_json(Path(args.stable_knowledge))
    relationship_audit = build_relationship_audit(
        selected_rows,
        stable,
        Path(input_paths["relationshipEvidence"]),
        policy,
    )
    completeness_summary = attach_completeness(selected_rows, relationship_audit, policy)
    for rank, row in enumerate(selected_rows, start=1):
        row["rank"] = rank
    output_root = Path(args.output_root)
    json_path = output_root / f"{args.round_id}.famous-ranking.json"
    md_path = output_root / f"{args.round_id}.famous-ranking.md"
    ids_path = output_root / f"{args.round_id}.general-ids.txt"
    if not args.overwrite and any(path.exists() for path in [json_path, md_path, ids_path]):
        raise FileExistsError("Famous ranking outputs already exist. Re-run with --overwrite.")
    report = {
        "version": "1.1.0",
        "roundId": args.round_id,
        "generatedAt": utc_now(),
        "mode": "famous-person-ranking",
        "canonicalWrites": False,
        "top": args.top,
        "candidateCount": len(ranked_rows),
        "inputs": {
            "observedMentionsPath": args.observed_mentions,
            "stableKnowledgePath": args.stable_knowledge,
            "eventQuestionSeedsPath": str(input_paths["eventQuestionSeeds"]),
            "sourceEventPacketsPath": str(input_paths["sourceEventPackets"]),
            "relationshipEvidencePath": str(input_paths["relationshipEvidence"]),
            "readyEventsPath": args.ready_events,
            "fameRankingPolicyPath": args.fame_ranking_policy,
            "primaryCanonDefaults": input_paths["primaryCanonDefaults"],
        },
        "policy": policy,
        "representationSummary": representation_summary,
        "dataCompletenessSummary": completeness_summary,
        "relationshipAudit": relationship_audit,
        "rows": selected_rows,
        "outputs": {
            "jsonPath": str(json_path),
            "markdownPath": str(md_path),
            "generalIdsPath": str(ids_path),
        },
    }
    write_json(json_path, report)
    write_text(md_path, render_markdown(report))
    write_text(ids_path, "\n".join(str(row["generalId"]) for row in selected_rows) + "\n")
    print(f"[build_famous_person_ranking] wrote {json_path}")
    print(f"[build_famous_person_ranking] wrote {md_path}")
    print(f"[build_famous_person_ranking] wrote {ids_path}")
    print(f"[build_famous_person_ranking] top={args.top} candidateCount={len(ranked_rows)} canonicalWrites=false")


if __name__ == "__main__":
    main()
