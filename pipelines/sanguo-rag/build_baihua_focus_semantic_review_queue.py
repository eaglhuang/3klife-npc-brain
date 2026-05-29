from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import (
    allowed_entities_from_candidates,
    build_alias_map,
    build_general_scoped_ambiguous_alias_map,
    build_name_map,
    cache_unit_id,
    object_map,
    read_json,
    read_jsonl,
    semantic_queue_sort_key,
    sentence_quality_score,
    string_list,
    write_json,
    write_jsonl,
)
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_PACKET_MANIFEST_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-focus-skill-packets.jsonl"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_BAIHUA_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build sentence-level semantic review queue from baihua focus skill packets.")
    parser.add_argument("--packet-manifest", default=str(DEFAULT_PACKET_MANIFEST_PATH))
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--baihua-policy", default=str(DEFAULT_BAIHUA_POLICY_PATH))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--queue-file-name", default="top50-focus-semantic-review-queue.jsonl")
    parser.add_argument("--summary-file-name", default="top50-focus-semantic-review-queue-summary.json")
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


def symmetric_relationship_types(policy: dict[str, Any]) -> set[str]:
    relationship_types = object_map(policy.get("relationshipTypes"))
    return {str(item or "").strip() for item in string_list(relationship_types.get("symmetric")) if str(item or "").strip()}


def default_relationship_types(policy: dict[str, Any]) -> list[str]:
    relationship_types = object_map(policy.get("relationshipTypes"))
    return [str(item or "").strip() for item in string_list(relationship_types.get("allowed")) if str(item or "").strip()]


def stable_inputs(relationship_policy: dict[str, Any]) -> tuple[Path, Path, Path]:
    inputs = object_map(relationship_policy.get("inputs"))
    stable_bootstrap = resolve_path(str(inputs.get("stableBootstrapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"))
    formal_mention_map = resolve_path(str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json"))
    alias_records = resolve_path(str(inputs.get("generalAliasRecordsPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json"))
    return stable_bootstrap, formal_mention_map, alias_records


def relationship_score_boosts(baihua_policy: dict[str, Any]) -> dict[str, float]:
    focus = object_map(baihua_policy.get("focusSentenceExtraction"))
    boosts = object_map(focus.get("confidenceByRelationshipType"))
    result: dict[str, float] = {}
    for key, value in boosts.items():
        try:
            result[str(key)] = float(value) * 100.0 if float(value) <= 1.0 else float(value)
        except (TypeError, ValueError):
            continue
    return result


def relationship_priority_index(baihua_policy: dict[str, Any]) -> dict[str, int]:
    prompt = object_map(baihua_policy.get("focusSkillPrompt"))
    ordered = [str(item or "").strip() for item in string_list(prompt.get("relationshipPriority")) if str(item or "").strip()]
    return {relationship_type: index for index, relationship_type in enumerate(ordered)}


def focus_semantic_queue_policy(baihua_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(baihua_policy.get("focusSemanticReviewQueue"))


def ruler_eligibility_queue_policy(queue_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(queue_policy.get("rulerEligibilityGate"))


def load_ruler_eligibility_snapshot(
    *,
    relationship_policy: dict[str, Any],
    baihua_policy: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    queue_policy = focus_semantic_queue_policy(baihua_policy)
    gate_policy = ruler_eligibility_queue_policy(queue_policy)
    if not bool(gate_policy.get("enabled", False)):
        return {
            "enabled": False,
            "gateActive": False,
            "eligibleControllerIds": [],
            "canonicalWrites": False,
        }
    snapshot_path_text = str(gate_policy.get("snapshotPath") or "").strip()
    if snapshot_path_text:
        snapshot_path = resolve_path(snapshot_path_text)
    else:
        relationship_outputs = object_map(relationship_policy.get("outputs"))
        relationship_root = resolve_path(str(relationship_outputs.get("outputRoot") or output_root))
        snapshot_file_name = str(
            relationship_outputs.get("rulerEligibilitySnapshotFileName")
            or "relationship-trust-zone.ruler-eligibility.snapshot.json"
        ).strip()
        snapshot_path = relationship_root / snapshot_file_name
    if not snapshot_path.exists():
        return {
            "enabled": True,
            "gateActive": False,
            "eligibleControllerIds": [],
            "snapshotPath": repo_relative(snapshot_path),
            "reason": "snapshot-not-found",
            "canonicalWrites": False,
        }
    snapshot = object_map(read_json(snapshot_path))
    snapshot["enabled"] = True
    snapshot["snapshotPath"] = repo_relative(snapshot_path)
    snapshot.setdefault("gateActive", bool(snapshot.get("gateActive", False)))
    snapshot.setdefault("eligibleControllerIds", [])
    snapshot.setdefault("canonicalWrites", False)
    return snapshot


def ruler_subject_lane_split_policy(queue_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(queue_policy.get("rulerSubjectLaneSplit"))


def queue_direct_counterpart_required_types(queue_policy: dict[str, Any]) -> set[str]:
    return {
        str(item or "").strip()
        for item in string_list(queue_policy.get("directCounterpartRequiredTypes"))
        if str(item or "").strip()
    }


def local_cue_binding_policy(queue_policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(queue_policy.get("localCueBinding"))


def cue_terms_for_type(passage: dict[str, Any], relationship_type: str) -> list[str]:
    cue_terms_by_type = object_map(passage.get("cueTermsByType"))
    return [item for item in string_list(cue_terms_by_type.get(relationship_type)) if item]


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def text_positions(text: str, needle: str) -> list[int]:
    token = str(needle or "").strip()
    if not text or not token:
        return []
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            break
        positions.append(index)
        start = index + max(1, len(token))
    return positions


def alias_positions(text: str, aliases: list[str], *, min_alias_length: int) -> list[int]:
    if not text:
        return []
    positions: list[int] = []
    for alias in unique_strings(aliases):
        if len(alias) < min_alias_length:
            continue
        positions.extend(text_positions(text, alias))
    return sorted(set(positions))


def minimum_position_distance(left: list[int], right: list[int]) -> int | None:
    if not left or not right:
        return None
    return min(abs(a - b) for a in left for b in right)


def int_map(raw: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            result[name] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def locally_bound_counterpart_ids(
    *,
    sentence: str,
    passage: dict[str, Any],
    relationship_type: str,
    counterpart_ids: list[str],
    focus_aliases: list[str],
    counterpart_alias_map: dict[str, list[str]],
    queue_policy: dict[str, Any],
) -> list[str]:
    binding_policy = local_cue_binding_policy(queue_policy)
    if not bool(binding_policy.get("enabled", False)):
        return counterpart_ids

    allowed_types = {
        str(item or "").strip()
        for item in string_list(binding_policy.get("allowedRelationshipTypes"))
        if str(item or "").strip()
    }
    if allowed_types and relationship_type not in allowed_types:
        return counterpart_ids

    try:
        min_alias_length = max(1, int(binding_policy.get("minAliasLength") or 2))
    except (TypeError, ValueError):
        min_alias_length = 2
    try:
        min_cue_length = max(1, int(binding_policy.get("minCueLength") or 1))
    except (TypeError, ValueError):
        min_cue_length = 1

    focus_distance_map = int_map(binding_policy.get("maxFocusDistanceByType"))
    counterpart_distance_map = int_map(binding_policy.get("maxCounterpartDistanceByType"))
    counterpart_limit_map = int_map(binding_policy.get("maxCounterpartsByType"))
    pair_span_map = int_map(binding_policy.get("maxEntityPairSpanByType"))
    try:
        default_focus_distance = max(1, int(binding_policy.get("defaultMaxFocusDistance") or 24))
    except (TypeError, ValueError):
        default_focus_distance = 24
    try:
        default_counterpart_distance = max(1, int(binding_policy.get("defaultMaxCounterpartDistance") or 24))
    except (TypeError, ValueError):
        default_counterpart_distance = 24
    try:
        default_counterpart_limit = max(1, int(binding_policy.get("defaultMaxCounterparts") or 1))
    except (TypeError, ValueError):
        default_counterpart_limit = 1
    try:
        default_pair_span = max(1, int(binding_policy.get("defaultMaxEntityPairSpan") or 12))
    except (TypeError, ValueError):
        default_pair_span = 12

    cue_positions = alias_positions(
        sentence,
        cue_terms_for_type(passage, relationship_type),
        min_alias_length=min_cue_length,
    )
    if not cue_positions:
        return []

    focus_positions = alias_positions(sentence, focus_aliases, min_alias_length=min_alias_length)
    focus_distance = minimum_position_distance(cue_positions, focus_positions)
    if bool(binding_policy.get("dropIfFocusNotBound", True)):
        max_focus_distance = focus_distance_map.get(relationship_type, default_focus_distance)
        if focus_distance is None or focus_distance > max_focus_distance:
            return []

    max_counterpart_distance = counterpart_distance_map.get(relationship_type, default_counterpart_distance)
    max_pair_span = pair_span_map.get(relationship_type, default_pair_span)
    kept: list[tuple[int, str]] = []
    for counterpart_id in counterpart_ids:
        counterpart_positions = alias_positions(
            sentence,
            counterpart_alias_map.get(counterpart_id) or [counterpart_id],
            min_alias_length=min_alias_length,
        )
        counterpart_distance = minimum_position_distance(cue_positions, counterpart_positions)
        pair_span = minimum_position_distance(focus_positions, counterpart_positions)
        if counterpart_distance is None or counterpart_distance > max_counterpart_distance:
            continue
        if pair_span is None or pair_span > max_pair_span:
            continue
        kept.append((counterpart_distance, counterpart_id))

    if not kept:
        return []

    if bool(binding_policy.get("preferNearest", True)):
        kept.sort(key=lambda item: (item[0], item[1]))
    max_counterparts = counterpart_limit_map.get(relationship_type, default_counterpart_limit)
    return [counterpart_id for _, counterpart_id in kept[:max_counterparts]]


def classify_ruler_subject_lane(
    relationship_type: str,
    matched_cue_terms: list[str],
    queue_policy: dict[str, Any],
) -> tuple[str, str, bool]:
    if relationship_type != "ruler_subject":
        return "", "", True
    lane_policy = ruler_subject_lane_split_policy(queue_policy)
    if not bool(lane_policy.get("enabled", False)):
        return "", "", True

    matched_terms = {str(item or "").strip() for item in matched_cue_terms if str(item or "").strip()}
    stable_terms = {
        str(item or "").strip()
        for item in string_list(lane_policy.get("stableBaselineCueTerms"))
        if str(item or "").strip()
    }
    historical_terms = {
        str(item or "").strip()
        for item in string_list(lane_policy.get("historicalPhaseCueTerms"))
        if str(item or "").strip()
    }
    if matched_terms & stable_terms:
        return "stable-baseline", "命中主公/屬下/奉命等穩定主從 cue。", True
    if matched_terms & historical_terms:
        return "historical-phase", "命中投奔/跟從/侍奉等歷史階段 cue。", False

    default_lane = str(lane_policy.get("defaultLane") or "stable-baseline").strip() or "stable-baseline"
    return default_lane, "未命中特殊分流 cue，採用預設 lane。", default_lane == "stable-baseline"


def refine_relationship_types(
    relationship_types: list[str],
    passage: dict[str, Any],
    queue_policy: dict[str, Any],
) -> list[str]:
    if not relationship_types:
        return []
    active = list(dict.fromkeys([item for item in relationship_types if item]))
    active_set = set(active)
    for raw_rule in queue_policy.get("typeSuppressionRules") or []:
        if not isinstance(raw_rule, dict):
            continue
        when_present = str(raw_rule.get("whenPresent") or "").strip()
        suppress = str(raw_rule.get("suppress") or "").strip()
        if not when_present or not suppress:
            continue
        if when_present not in active_set or suppress not in active_set:
            continue
        try:
            min_cue_term_count = max(1, int(raw_rule.get("minCueTermCount") or 1))
        except (TypeError, ValueError):
            min_cue_term_count = 1
        if len(cue_terms_for_type(passage, when_present)) < min_cue_term_count:
            continue
        active_set.discard(suppress)
    return [item for item in active if item in active_set]


def counterpart_ids_for_relationship(
    passage: dict[str, Any],
    relationship_type: str,
    queue_policy: dict[str, Any],
    *,
    sentence: str,
    focus_aliases: list[str],
    counterpart_alias_map: dict[str, list[str]],
) -> tuple[list[str], str]:
    direct_hits = [item for item in string_list(passage.get("counterpartHits")) if item]
    direct_required_types = queue_direct_counterpart_required_types(queue_policy)
    if direct_hits:
        bound_direct_hits = locally_bound_counterpart_ids(
            sentence=sentence,
            passage=passage,
            relationship_type=relationship_type,
            counterpart_ids=direct_hits,
            focus_aliases=focus_aliases,
            counterpart_alias_map=counterpart_alias_map,
            queue_policy=queue_policy,
        )
        if bound_direct_hits:
            return bound_direct_hits, "direct"
        if relationship_type in direct_required_types:
            return [], ""

    fallback_policy = object_map(queue_policy.get("contextualCounterpartFallback"))
    if not bool(fallback_policy.get("enabled", False)):
        return [], ""
    if relationship_type in direct_required_types:
        return [], ""
    if bool(fallback_policy.get("sentenceWindowsOnly", True)) and str(passage.get("windowType") or "") != "sentence":
        return [], ""

    allowed_types = {
        str(item or "").strip()
        for item in string_list(fallback_policy.get("allowedRelationshipTypes"))
        if str(item or "").strip()
    }
    if allowed_types and relationship_type not in allowed_types:
        return [], ""

    context_hits = [item for item in string_list(passage.get("contextCounterpartHits")) if item]
    if not context_hits:
        return [], ""
    try:
        max_context_counterparts = max(1, int(fallback_policy.get("maxContextCounterparts") or 3))
    except (TypeError, ValueError):
        max_context_counterparts = 3
    if len(context_hits) > max_context_counterparts:
        return [], ""

    cue_min_map = object_map(fallback_policy.get("minCueTermCountByType"))
    try:
        default_min_cue_terms = max(1, int(fallback_policy.get("defaultMinCueTermCount") or 1))
    except (TypeError, ValueError):
        default_min_cue_terms = 1
    try:
        min_cue_terms = max(
            1,
            int(cue_min_map.get(relationship_type, default_min_cue_terms) or default_min_cue_terms),
        )
    except (TypeError, ValueError):
        min_cue_terms = default_min_cue_terms
    if len(cue_terms_for_type(passage, relationship_type)) < min_cue_terms:
        return [], ""

    bound_context_hits = locally_bound_counterpart_ids(
        sentence=sentence,
        passage=passage,
        relationship_type=relationship_type,
        counterpart_ids=context_hits,
        focus_aliases=focus_aliases,
        counterpart_alias_map=counterpart_alias_map,
        queue_policy=queue_policy,
    )
    if not bound_context_hits:
        return [], ""
    return bound_context_hits, "context"


def trust_key(from_id: str, to_id: str, relationship_type: str, symmetric_types: set[str]) -> str:
    if relationship_type in symmetric_types:
        left, right = sorted([from_id, to_id])
        return f"{relationship_type}:{left}:{right}"
    return f"{relationship_type}:{from_id}:{to_id}"


def claim_sentence(from_name: str, to_name: str, relationship_type: str) -> str:
    if relationship_type == "ruler_subject":
        return f"{from_name}是{to_name}的主君或上位者"
    if relationship_type == "faction_membership":
        return f"{to_name}屬於{from_name}陣營"
    if relationship_type == "parent_child":
        return f"{from_name}是{to_name}的父母或親長"
    if relationship_type == "adoptive_parent_child":
        return f"{from_name}是{to_name}的義父義母或收養親長"
    if relationship_type == "spouse":
        return f"{from_name}與{to_name}是配偶"
    if relationship_type == "sibling":
        return f"{from_name}與{to_name}是兄弟姊妹"
    if relationship_type == "sworn_sibling":
        return f"{from_name}與{to_name}是結義兄弟姊妹"
    return f"{from_name}與{to_name}存在{relationship_type}關係"


def direction_instruction(relationship_type: str) -> str:
    if relationship_type == "parent_child":
        return "只有句子明確表示 from 是父母或親長、to 是子女時，才可支持此方向。"
    if relationship_type == "adoptive_parent_child":
        return "只有句子明確表示 from 是義父義母或收養親長、to 是義子義女或養子養女時，才可支持此方向。"
    if relationship_type == "ruler_subject":
        return "只有句子明確表示 from 是主君、君主、上位者或任命者，to 是其臣屬、部下、受命者時，才可支持此方向。"
    if relationship_type == "faction_membership":
        return "只有句子明確表示 to 隸屬於 from 的陣營、政權或勢力時，才可支持此方向。"
    if relationship_type == "spouse":
        return "只有句子明確表示兩人本身有婚配或妻妾關係時，才可支持此候選。"
    if relationship_type == "sibling":
        return "只有句子明確表示兩人是血緣兄弟姊妹時，才可支持此候選。"
    if relationship_type == "sworn_sibling":
        return "只有句子明確表示兩人結義、義兄弟姊妹或桃園結義等誓盟時，才可支持此候選。"
    return "只有句子明確支持這一對人物與這個關係型別時，才可支持此候選。"


def source_ref_for_sentence(packet: dict[str, Any], passage: dict[str, Any]) -> dict[str, Any]:
    return {
        "sourceId": packet.get("sourceCorpusId") or "sanguoyanyi-baihua-zh-tw",
        "sourceFamily": "baihua-bootstrap-focus",
        "sourceLayer": "baihua-focus-packets",
        "confidenceSignals": [
            "baihua-translation-anchor",
            "focus-sentence-window",
            *[f"pair-cue:{item}" for item in string_list(passage.get("candidateRelationshipTypes"))],
        ],
        "locator": passage.get("locator"),
        "url": "",
        "evidenceRefs": [passage.get("chapterRef"), passage.get("locator")],
        "canonicalWrites": False,
    }


def pair_candidates_for_passage(
    *,
    packet: dict[str, Any],
    passage: dict[str, Any],
    focus_id: str,
    focus_name: str,
    focus_aliases: list[str],
    counterpart_name_map: dict[str, str],
    counterpart_alias_map: dict[str, list[str]],
    symmetric_types: set[str],
    score_boosts: dict[str, float],
    priority_index: dict[str, int],
    queue_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    relationship_types = refine_relationship_types(
        [item for item in string_list(passage.get("candidateRelationshipTypes")) if item],
        passage,
        queue_policy,
    )
    candidates: list[dict[str, Any]] = []
    sentence_score = sentence_quality_score(passage.get("normalizedText"))
    fallback_policy = object_map(queue_policy.get("contextualCounterpartFallback"))
    lane_policy = ruler_subject_lane_split_policy(queue_policy)
    try:
        context_score_penalty = max(0.0, float(fallback_policy.get("scorePenalty") or 0.0))
    except (TypeError, ValueError):
        context_score_penalty = 0.0
    try:
        context_priority_penalty = max(0.0, float(fallback_policy.get("priorityPenalty") or 0.0))
    except (TypeError, ValueError):
        context_priority_penalty = context_score_penalty / 2.0
    score_penalty_by_lane = {
        str(key or "").strip(): max(0.0, float(value or 0.0))
        for key, value in object_map(lane_policy.get("scorePenaltyByLane")).items()
        if str(key or "").strip()
    }
    priority_penalty_by_lane = {
        str(key or "").strip(): max(0.0, float(value or 0.0))
        for key, value in object_map(lane_policy.get("priorityPenaltyByLane")).items()
        if str(key or "").strip()
    }

    for relationship_type in relationship_types:
        counterpart_ids, counterpart_mode = counterpart_ids_for_relationship(
            passage,
            relationship_type,
            queue_policy,
            sentence=str(passage.get("normalizedText") or ""),
            focus_aliases=focus_aliases,
            counterpart_alias_map=counterpart_alias_map,
        )
        if not counterpart_ids:
            continue
        relation_priority = priority_index.get(relationship_type, 99)
        base_score = max(sentence_score, score_boosts.get(relationship_type, 78.0))
        if counterpart_mode == "context":
            base_score = max(0.0, base_score - context_score_penalty)
        focus_queue_priority = max(0.0, 120.0 - relation_priority * 10.0 + min(20.0, sentence_score / 5.0))
        if counterpart_mode == "context":
            focus_queue_priority = max(0.0, focus_queue_priority - context_priority_penalty)
        matched_cue_terms = cue_terms_for_type(passage, relationship_type)
        relationship_lane_hint, lane_reason, stable_baseline_eligible = classify_ruler_subject_lane(
            relationship_type,
            matched_cue_terms,
            queue_policy,
        )
        if relationship_lane_hint:
            base_score = max(0.0, base_score - score_penalty_by_lane.get(relationship_lane_hint, 0.0))
            focus_queue_priority = max(0.0, focus_queue_priority - priority_penalty_by_lane.get(relationship_lane_hint, 0.0))
        focus_queue_priority = round(focus_queue_priority, 3)
        for counterpart_id in counterpart_ids:
            counterpart_name = counterpart_name_map.get(counterpart_id) or counterpart_id
            if relationship_type in symmetric_types:
                left_id, right_id = sorted([focus_id, counterpart_id])
                left_name = counterpart_name if left_id == counterpart_id else focus_name
                right_name = counterpart_name if right_id == counterpart_id else focus_name
                candidates.append(
                    {
                        "trustKey": trust_key(focus_id, counterpart_id, relationship_type, symmetric_types),
                        "claimSentenceZhTw": claim_sentence(left_name, right_name, relationship_type),
                        "relationshipType": relationship_type,
                        "fromId": left_id,
                        "toId": right_id,
                        "subjectId": "",
                        "controllerId": "",
                        "fromNameZhTw": left_name,
                        "toNameZhTw": right_name,
                        "subjectNameZhTw": "",
                        "controllerNameZhTw": "",
                        "strictDirectionCheckZhTw": direction_instruction(relationship_type),
                        "scoreBeforeSemanticReview": round(base_score, 3),
                        "focusQueuePriority": focus_queue_priority,
                        "counterpartSelectionMode": counterpart_mode,
                        "matchedCueTerms": matched_cue_terms,
                        "matchedCueTermCount": len(matched_cue_terms),
                        "focusGapCount": 1,
                        "focusGapGeneralIds": [focus_id],
                        "relationshipLaneHint": relationship_lane_hint,
                        "relationshipLaneReasonZhTw": lane_reason,
                        "stableBaselineEligible": stable_baseline_eligible,
                        "canonicalWrites": False,
                    }
                )
                continue

            direction_rows = [
                (focus_id, focus_name, counterpart_id, counterpart_name),
                (counterpart_id, counterpart_name, focus_id, focus_name),
            ]
            for from_id, from_name, to_id, to_name in direction_rows:
                candidates.append(
                    {
                        "trustKey": trust_key(from_id, to_id, relationship_type, symmetric_types),
                        "claimSentenceZhTw": claim_sentence(from_name, to_name, relationship_type),
                        "relationshipType": relationship_type,
                        "fromId": from_id,
                        "toId": to_id,
                        "subjectId": to_id if relationship_type == "ruler_subject" else "",
                        "controllerId": from_id if relationship_type == "ruler_subject" else "",
                        "fromNameZhTw": from_name,
                        "toNameZhTw": to_name,
                        "subjectNameZhTw": to_name if relationship_type == "ruler_subject" else "",
                        "controllerNameZhTw": from_name if relationship_type == "ruler_subject" else "",
                        "strictDirectionCheckZhTw": direction_instruction(relationship_type),
                        "scoreBeforeSemanticReview": round(base_score, 3),
                        "focusQueuePriority": focus_queue_priority,
                        "counterpartSelectionMode": counterpart_mode,
                        "matchedCueTerms": matched_cue_terms,
                        "matchedCueTermCount": len(matched_cue_terms),
                        "focusGapCount": 1,
                        "focusGapGeneralIds": [focus_id],
                        "relationshipLaneHint": relationship_lane_hint,
                        "relationshipLaneReasonZhTw": lane_reason,
                        "stableBaselineEligible": stable_baseline_eligible,
                        "canonicalWrites": False,
                    }
                )
    return candidates


def apply_ruler_eligibility_gate(
    candidates: list[dict[str, Any]],
    *,
    ruler_eligibility_snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    enabled = bool(ruler_eligibility_snapshot.get("enabled", False))
    gate_active = bool(ruler_eligibility_snapshot.get("gateActive", False))
    if not enabled:
        return candidates, 0
    eligible_controllers = {
        str(item or "").strip()
        for item in string_list(ruler_eligibility_snapshot.get("eligibleControllerIds"))
        if str(item or "").strip()
    }
    filtered: list[dict[str, Any]] = []
    dropped = 0
    for candidate in candidates:
        relationship_type = str(candidate.get("relationshipType") or "").strip()
        if relationship_type != "ruler_subject":
            filtered.append(candidate)
            continue
        controller_id = str(candidate.get("fromId") or "").strip()
        controller_eligible = controller_id in eligible_controllers
        candidate["rulerEligibilityGateEnabled"] = True
        candidate["rulerEligibilityGateActive"] = gate_active
        candidate["controllerEverRuler"] = controller_eligible
        candidate["controllerEverRulerStatus"] = "yes" if controller_eligible else "no"
        if gate_active and not controller_eligible:
            dropped += 1
            continue
        filtered.append(candidate)
    return filtered, dropped


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(candidate.get("trustKey") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def build_queue_units(
    packet_rows: list[dict[str, Any]],
    *,
    relationship_policy: dict[str, Any],
    baihua_policy: dict[str, Any],
    name_map: dict[str, str],
    alias_map: dict[str, list[str]],
    scoped_ambiguous_alias_map: dict[str, list[str]],
    ruler_eligibility_snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    semantic_policy = object_map(relationship_policy.get("semanticReview"))
    prompt_version = str(semantic_policy.get("promptVersion") or "relationship-semantic-review.v1")
    max_candidates = max(1, int(semantic_policy.get("maxCandidatesPerSentence") or 6))
    symmetric_types = symmetric_relationship_types(baihua_policy)
    score_boosts = relationship_score_boosts(baihua_policy)
    priority_index = relationship_priority_index(baihua_policy)
    queue_policy = focus_semantic_queue_policy(baihua_policy)

    units_by_id: dict[str, dict[str, Any]] = {}
    candidate_seen_by_sentence: dict[str, set[str]] = defaultdict(set)
    focus_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    selection_mode_counts: Counter[str] = Counter()
    cue_sentence_count = 0
    ruler_eligibility_dropped = 0

    for packet_row in packet_rows:
        packet_path = resolve_path(packet_row.get("packetPath") or "")
        if not packet_path.exists():
            continue
        packet = read_json(packet_path)
        focus_id = str(packet.get("focusGeneralId") or "").strip()
        focus_name = str(packet.get("focusNameZhTw") or focus_id).strip()
        if not focus_id:
            continue
        focus_aliases = unique_strings((alias_map.get(focus_id) or []) + [focus_name])
        counterpart_name_map = {
            str(row.get("counterpartId") or "").strip(): name_map.get(str(row.get("counterpartId") or "").strip(), str(row.get("counterpartId") or "").strip())
            for row in (packet.get("counterpartRanking") or [])
            if isinstance(row, dict)
        }
        counterpart_alias_map = {
            counterpart_id: unique_strings((alias_map.get(counterpart_id) or []) + [counterpart_name])
            for counterpart_id, counterpart_name in counterpart_name_map.items()
            if counterpart_id
        }
        for passage in packet.get("selectedPassages") or []:
            if not isinstance(passage, dict):
                continue
            candidate_types = refine_relationship_types(
                string_list(passage.get("candidateRelationshipTypes")),
                passage,
                queue_policy,
            )
            if not candidate_types:
                continue
            cue_sentence_count += 1
            focus_counts[focus_id] += 1
            sentence = str(passage.get("normalizedText") or "").strip()
            if not sentence:
                continue
            source_ref = source_ref_for_sentence(packet, passage)
            new_candidates = dedupe_candidates(
                pair_candidates_for_passage(
                    packet=packet,
                    passage=passage,
                    focus_id=focus_id,
                    focus_name=focus_name,
                    focus_aliases=focus_aliases,
                    counterpart_name_map=counterpart_name_map,
                    counterpart_alias_map=counterpart_alias_map,
                    symmetric_types=symmetric_types,
                    score_boosts=score_boosts,
                    priority_index=priority_index,
                    queue_policy=queue_policy,
                )
            )
            new_candidates, dropped_by_gate = apply_ruler_eligibility_gate(
                new_candidates,
                ruler_eligibility_snapshot=ruler_eligibility_snapshot,
            )
            ruler_eligibility_dropped += dropped_by_gate
            if not new_candidates:
                continue
            base_unit_id = cache_unit_id(prompt_version, sentence)
            for candidate in new_candidates:
                relation_counts[str(candidate.get("relationshipType") or "")] += 1
                selection_mode_counts[str(candidate.get("counterpartSelectionMode") or "unknown")] += 1
                trust_key_value = str(candidate.get("trustKey") or "").strip()
                if trust_key_value in candidate_seen_by_sentence[base_unit_id]:
                    continue
                chunk_index = 0
                while True:
                    unit_id = base_unit_id if chunk_index == 0 else f"{base_unit_id}.part{chunk_index + 1:03d}"
                    unit = units_by_id.setdefault(
                        unit_id,
                        {
                            "semanticReviewUnitId": unit_id,
                            "semanticReviewBaseUnitId": base_unit_id,
                            "candidateChunkIndex": chunk_index,
                            "promptVersion": prompt_version,
                            "sentenceHash": "sha256:" + hashlib.sha256(sentence.replace(" ", "").encode("utf-8")).hexdigest(),
                            "sourceSentence": sentence,
                            "sentenceQualityScore": sentence_quality_score(sentence),
                            "sourceRefs": [],
                            "sourcePreviewPriorityMax": 0.0,
                            "candidates": [],
                            "reviewMode": "sentence-relation-extraction",
                            "canonicalWrites": False,
                        },
                    )
                    if len(unit["candidates"]) < max_candidates:
                        break
                    chunk_index += 1
                if source_ref not in unit["sourceRefs"]:
                    unit["sourceRefs"].append(source_ref)
                unit["sourcePreviewPriorityMax"] = round(
                    max(float(unit.get("sourcePreviewPriorityMax") or 0.0), float(candidate.get("scoreBeforeSemanticReview") or 0.0)),
                    4,
                )
                unit["candidates"].append(candidate)
                candidate_seen_by_sentence[base_unit_id].add(trust_key_value)

    units: list[dict[str, Any]] = []
    for unit in units_by_id.values():
        scores = [float(candidate.get("scoreBeforeSemanticReview") or 0.0) for candidate in unit.get("candidates") or [] if isinstance(candidate, dict)]
        priorities = [float(candidate.get("focusQueuePriority") or 0.0) for candidate in unit.get("candidates") or [] if isinstance(candidate, dict)]
        unit["candidateMaxScoreBeforeSemanticReview"] = round(max(scores) if scores else 0.0, 3)
        unit["candidateCount"] = len(unit.get("candidates") or [])
        unit["focusQueuePriority"] = round(max(priorities) if priorities else 0.0, 3)
        unit["allowedEntities"] = allowed_entities_from_candidates(unit.get("candidates") or [], alias_map, scoped_ambiguous_alias_map)
        unit["allowedRelationshipTypes"] = sorted(
            {
                str(candidate.get("relationshipType") or "").strip()
                for candidate in unit.get("candidates") or []
                if isinstance(candidate, dict) and str(candidate.get("relationshipType") or "").strip()
            }
        )
        units.append(unit)

    units = sorted(units, key=lambda item: semantic_queue_sort_key(item, relationship_policy))
    metrics = {
        "focusCueSentenceCounts": dict(sorted(focus_counts.items())),
        "candidateRelationshipTypeCounts": dict(sorted(relation_counts.items())),
        "counterpartSelectionModeCounts": dict(sorted(selection_mode_counts.items())),
        "cueSentenceCount": cue_sentence_count,
        "rulerEligibilityDroppedCandidateCount": ruler_eligibility_dropped,
    }
    return units, metrics


def main() -> int:
    args = parse_args()
    packet_manifest_path = resolve_path(args.packet_manifest)
    relationship_policy_path = resolve_path(args.relationship_policy)
    baihua_policy_path = resolve_path(args.baihua_policy)
    relationship_policy = read_json(relationship_policy_path)
    baihua_policy = read_json(baihua_policy_path)

    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else packet_manifest_path.parent
    queue_path = output_root / args.queue_file_name
    summary_path = output_root / args.summary_file_name
    if not args.overwrite and (queue_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)
    scoped_ambiguous_alias_map = build_general_scoped_ambiguous_alias_map(alias_records)
    ruler_eligibility_snapshot = load_ruler_eligibility_snapshot(
        relationship_policy=relationship_policy,
        baihua_policy=baihua_policy,
        output_root=output_root,
    )
    version_metadata = build_version_metadata(
        schema_version="baihua-focus-semantic-review-queue.v1",
        artifact_paths=[
            packet_manifest_path,
            relationship_policy_path,
            baihua_policy_path,
            stable_bootstrap_path,
            formal_mention_map_path,
            alias_records_path,
            *(
                [resolve_path(str(ruler_eligibility_snapshot.get("snapshotPath") or "").strip())]
                if str(ruler_eligibility_snapshot.get("snapshotPath") or "").strip()
                else []
            ),
        ],
        repo_root=REPO_ROOT,
    )

    packet_rows = read_jsonl(packet_manifest_path)
    units, metrics = build_queue_units(
        packet_rows,
        relationship_policy=relationship_policy,
        baihua_policy=baihua_policy,
        name_map=name_map,
        alias_map=alias_map,
        scoped_ambiguous_alias_map=scoped_ambiguous_alias_map,
        ruler_eligibility_snapshot=ruler_eligibility_snapshot,
    )
    for unit in units:
        unit.update(version_metadata)

    write_jsonl(queue_path, units)
    summary = {
        "mode": "baihua-focus-semantic-review-queue-builder",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "packetManifestPath": repo_relative(packet_manifest_path),
            "relationshipPolicyPath": repo_relative(relationship_policy_path),
            "baihuaPolicyPath": repo_relative(baihua_policy_path),
            "stableBootstrapPath": repo_relative(stable_bootstrap_path) if stable_bootstrap_path.exists() else "",
            "formalMentionMapPath": repo_relative(formal_mention_map_path) if formal_mention_map_path.exists() else "",
            "generalAliasRecordsPath": repo_relative(alias_records_path) if alias_records_path.exists() else "",
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "unitCount": len(units),
            "focusCount": len(metrics["focusCueSentenceCounts"]),
            "cueSentenceCount": metrics["cueSentenceCount"],
            "candidateRelationshipTypeCounts": metrics["candidateRelationshipTypeCounts"],
            "counterpartSelectionModeCounts": metrics["counterpartSelectionModeCounts"],
            "rulerEligibilityGateEnabled": bool(ruler_eligibility_snapshot.get("enabled", False)),
            "rulerEligibilityGateActive": bool(ruler_eligibility_snapshot.get("gateActive", False)),
            "rulerEligibilityControllerCount": len(string_list(ruler_eligibility_snapshot.get("eligibleControllerIds"))),
            "rulerEligibilitySnapshotPath": str(ruler_eligibility_snapshot.get("snapshotPath") or ""),
            "rulerEligibilityDroppedCandidateCount": metrics["rulerEligibilityDroppedCandidateCount"],
            "focusCueSentenceCountsTop20": dict(list(sorted(metrics["focusCueSentenceCounts"].items(), key=lambda item: (-item[1], item[0]))[:20])),
        },
    }
    write_json(summary_path, summary)
    print(
        "[build_baihua_focus_semantic_review_queue] "
        f"units={len(units)} cueSentences={metrics['cueSentenceCount']} "
        f"focuses={len(metrics['focusCueSentenceCounts'])} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
