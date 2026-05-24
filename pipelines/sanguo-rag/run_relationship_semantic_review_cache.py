from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from reviewer_adapters import resolve_reviewer_adapter


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


def stable_hash(*parts: Any, length: int = 18) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").split("\n"), 1):
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            row.setdefault("_sourceFile", repo_relative(path))
            row.setdefault("_sourceLine", line_no)
            rows.append(row)
    return rows


def read_text_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    return len(rows)


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def first_object(value: Any) -> dict[str, Any]:
    items = object_list(value)
    return items[0] if items else {}


def semantic_score_pair(relation: dict[str, Any]) -> tuple[float, float]:
    raw_score = (
        relation.get("semanticTrustScore")
        if relation.get("semanticTrustScore") is not None
        else relation.get("trustScore")
    )
    raw_confidence = relation.get("confidence")
    score = number_value(raw_score, -1.0)
    confidence = number_value(raw_confidence, -1.0)
    if 0.0 <= score <= 1.0:
        score = score * 100.0
    if score < 0.0 and confidence >= 0.0:
        score = confidence * 100.0 if confidence <= 1.0 else confidence
    if confidence < 0.0 and score >= 0.0:
        confidence = score / 100.0
    if score < 0.0:
        score = 0.0
    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = confidence / 100.0
    return clamp(score, 0.0, 100.0), clamp(confidence, 0.0, 1.0)


def semantic_score_band(score: float) -> str:
    if score >= 95.0:
        return "95-100"
    if score >= 90.0:
        return "90-94"
    if score >= 80.0:
        return "80-89"
    if score >= 60.0:
        return "60-79"
    if score > 0.0:
        return "1-59"
    return "0"


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_sentence(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def contains_cjk_char(value: Any) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))


def sentence_quality_score(value: Any) -> float:
    text = compact_text(value)
    if not text:
        return 0.0
    compact = normalized_sentence(text)
    if not compact:
        return 0.0
    total = len(compact)
    cjk = len(re.findall(r"[\u3400-\u9fff]", compact))
    digits = len(re.findall(r"\d", compact))
    ascii_letters = len(re.findall(r"[A-Za-z]", compact))
    punctuation = len(re.findall(r"[^\w\u3400-\u9fff]", compact))
    cjk_ratio = cjk / total
    digit_ratio = digits / total
    ascii_ratio = ascii_letters / total
    punctuation_ratio = punctuation / total
    length_penalty = 0.0
    if total < 8:
        length_penalty += 20.0
    if total > 360:
        length_penalty += min(35.0, (total - 360) / 20.0)
    score = cjk_ratio * 100.0 - digit_ratio * 35.0 - ascii_ratio * 20.0 - punctuation_ratio * 15.0 - length_penalty
    return round(clamp(score, 0.0, 100.0), 3)


def semantic_page_shape_policy(policy: dict[str, Any]) -> dict[str, Any]:
    semantic_policy = object_map(policy.get("semanticReview"))
    return object_map(semantic_policy.get("pageShapeTelemetry"))


def text_keyword_hits(text: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        token = compact_text(keyword)
        if token and token in text and token not in hits:
            hits.append(token)
    return hits


def numeric_threshold_trigger(value: float, warn_threshold: float, strong_threshold: float) -> str:
    if strong_threshold > 0.0 and value >= strong_threshold:
        return "strong"
    if warn_threshold > 0.0 and value >= warn_threshold:
        return "warn"
    return ""


def integer_threshold_trigger(value: int, warn_threshold: int, strong_threshold: int) -> str:
    if strong_threshold > 0 and value >= strong_threshold:
        return "strong"
    if warn_threshold > 0 and value >= warn_threshold:
        return "warn"
    return ""


def sentence_page_shape_metrics(sentence: str, policy: dict[str, Any]) -> dict[str, Any]:
    page_policy = semantic_page_shape_policy(policy)
    thresholds = object_map(page_policy.get("structuralThresholds"))
    penalties = object_map(page_policy.get("penalties"))
    keywords_policy = object_map(page_policy.get("keywordsZhTw"))
    text = compact_text(sentence)
    compact = normalized_sentence(text)
    total = len(compact)
    digits = len(re.findall(r"\d", compact))
    punctuation = len(re.findall(r"[^\w\u3400-\u9fff]", compact))
    segments = [segment for segment in re.split(r"\s+", text) if segment]
    short_segment_max_length = int(number_value(thresholds.get("shortSegmentMaxLength"), 12.0))
    short_segment_count = len(
        [
            segment
            for segment in segments
            if contains_cjk_char(segment) and 2 <= len(normalized_sentence(segment)) <= short_segment_max_length
        ]
    )
    fullwidth_paren_count = sum(text.count(char) for char in ("（", "）", "(", ")"))
    year_like_count = len(re.findall(r"\d{2,4}\s*[—－\-~～]\s*\d{1,4}", text))
    number_token_count = len(re.findall(r"\d+", text))
    digit_ratio = digits / total if total else 0.0
    punctuation_ratio = punctuation / total if total else 0.0
    negative_keyword_hits = text_keyword_hits(text, string_list(keywords_policy.get("negativePageType")))
    narrative_keyword_hits = text_keyword_hits(text, string_list(keywords_policy.get("narrativePositive")))
    pair_cue_keyword_hits = text_keyword_hits(text, string_list(keywords_policy.get("pairCuePositive")))

    structural_signals: list[str] = []
    penalty_score = 0.0
    boost_score = 0.0

    digit_ratio_trigger = numeric_threshold_trigger(
        digit_ratio,
        number_value(thresholds.get("digitRatioWarn"), 0.12),
        number_value(thresholds.get("digitRatioStrong"), 0.18),
    )
    if digit_ratio_trigger:
        structural_signals.append(f"digit-ratio-{digit_ratio_trigger}")
        penalty_score += number_value(penalties.get(f"digitRatio{digit_ratio_trigger.title()}"), 0.0)

    punctuation_ratio_trigger = numeric_threshold_trigger(
        punctuation_ratio,
        number_value(thresholds.get("punctuationRatioWarn"), 0.08),
        number_value(thresholds.get("punctuationRatioStrong"), 0.14),
    )
    if punctuation_ratio_trigger:
        structural_signals.append(f"punctuation-ratio-{punctuation_ratio_trigger}")
        penalty_score += number_value(penalties.get(f"punctuationRatio{punctuation_ratio_trigger.title()}"), 0.0)

    fullwidth_paren_trigger = integer_threshold_trigger(
        fullwidth_paren_count,
        int(number_value(thresholds.get("fullwidthParenWarnCount"), 4.0)),
        int(number_value(thresholds.get("fullwidthParenStrongCount"), 8.0)),
    )
    if fullwidth_paren_trigger:
        structural_signals.append(f"fullwidth-paren-{fullwidth_paren_trigger}")
        penalty_score += number_value(penalties.get(f"fullwidthParen{fullwidth_paren_trigger.title()}"), 0.0)

    year_like_trigger = integer_threshold_trigger(
        year_like_count,
        int(number_value(thresholds.get("yearLikeWarnCount"), 2.0)),
        int(number_value(thresholds.get("yearLikeStrongCount"), 4.0)),
    )
    if year_like_trigger:
        structural_signals.append(f"year-like-{year_like_trigger}")
        penalty_score += number_value(penalties.get(f"yearLike{year_like_trigger.title()}"), 0.0)

    short_segment_trigger = integer_threshold_trigger(
        short_segment_count,
        int(number_value(thresholds.get("shortSegmentWarnCount"), 5.0)),
        int(number_value(thresholds.get("shortSegmentStrongCount"), 8.0)),
    )
    if short_segment_trigger:
        structural_signals.append(f"short-segment-{short_segment_trigger}")
        penalty_score += number_value(penalties.get(f"shortSegment{short_segment_trigger.title()}"), 0.0)

    number_token_trigger = integer_threshold_trigger(
        number_token_count,
        int(number_value(thresholds.get("numberTokenWarnCount"), 4.0)),
        int(number_value(thresholds.get("numberTokenStrongCount"), 8.0)),
    )
    if number_token_trigger:
        structural_signals.append(f"number-token-{number_token_trigger}")
        penalty_score += number_value(penalties.get(f"numberToken{number_token_trigger.title()}"), 0.0)

    penalty_score += len(negative_keyword_hits) * number_value(penalties.get("negativeKeywordHit"), 0.0)
    boost_score += len(narrative_keyword_hits) * number_value(penalties.get("narrativeKeywordHitBoost"), 0.0)
    boost_score += len(pair_cue_keyword_hits) * number_value(penalties.get("pairCueKeywordHitBoost"), 0.0)

    suppression_policy = object_map(page_policy.get("suppression"))
    min_penalty = number_value(suppression_policy.get("minPenaltyScore"), 0.0)
    min_structural_triggers = int(number_value(suppression_policy.get("minStructuralTriggerCount"), 0.0))
    max_narrative_hits = int(number_value(suppression_policy.get("maxNarrativeKeywordHits"), 0.0))
    max_pair_cue_hits = int(number_value(suppression_policy.get("maxPairCueKeywordHits"), 0.0))
    suppressed = (
        penalty_score >= min_penalty
        and len(structural_signals) >= min_structural_triggers
        and len(narrative_keyword_hits) <= max_narrative_hits
        and len(pair_cue_keyword_hits) <= max_pair_cue_hits
    )

    if year_like_count >= 2 and fullwidth_paren_count >= 4 and short_segment_count >= 5:
        category = "dense-bio-list"
    elif digit_ratio >= number_value(thresholds.get("digitRatioWarn"), 0.12) and short_segment_count >= 5:
        category = "numeric-table-like"
    elif boost_score > penalty_score:
        category = "narrative-like"
    else:
        category = "mixed"

    adjusted_priority = round(max(0.0, number_value(page_policy.get("basePriority"), 0.0) - penalty_score + boost_score), 4)
    return {
        "category": category,
        "suppressed": suppressed,
        "penaltyScore": round(penalty_score, 4),
        "boostScore": round(boost_score, 4),
        "adjustedPriorityOffset": adjusted_priority,
        "structuralSignals": structural_signals,
        "negativeKeywordHits": negative_keyword_hits,
        "narrativeKeywordHits": narrative_keyword_hits,
        "pairCueKeywordHits": pair_cue_keyword_hits,
        "metrics": {
            "digitRatio": round(digit_ratio, 4),
            "punctuationRatio": round(punctuation_ratio, 4),
            "fullwidthParenCount": fullwidth_paren_count,
            "yearLikeCount": year_like_count,
            "shortSegmentCount": short_segment_count,
            "numberTokenCount": number_token_count,
            "segmentCount": len(segments),
            "compactLength": total,
        },
    }


def row_stage(row: dict[str, Any]) -> str:
    return str(row.get("zone") or row.get("currentZone") or row.get("stage") or "").strip()


def source_preview_key(preview: dict[str, Any]) -> str:
    quote = normalized_sentence(preview.get("quote") or preview.get("originalSentence"))
    return stable_hash(quote, length=32)


def cache_unit_id(prompt_version: str, sentence: str) -> str:
    return "relsem." + stable_hash(prompt_version, normalized_sentence(sentence), length=24)


def load_focus_general_ids(path: Path) -> set[str]:
    return {line for line in read_text_lines(path) if line}


def row_general_ids(row: dict[str, Any]) -> set[str]:
    keys = (
        "generalId",
        "targetGeneralId",
        "sourceGeneralId",
        "fromId",
        "toId",
        "subjectId",
        "controllerId",
    )
    values = {str(row.get(key) or "").strip() for key in keys}
    return {value for value in values if value}


def row_matches_focus_general_ids(row: dict[str, Any], focus_general_ids: set[str]) -> bool:
    if not focus_general_ids:
        return True
    return bool(row_general_ids(row) & focus_general_ids)


def build_formal_mention_name_map(formal_mention_map: dict[str, Any]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    entries = formal_mention_map.get("entries")
    if not isinstance(entries, list):
        return name_map
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("alias") or "").strip()
        if not alias or not contains_cjk_char(alias):
            continue
        general_ids = string_list(entry.get("generalIds"))
        alias_type_by_general = object_map(entry.get("aliasTypeByGeneral"))
        sources_by_general = object_map(entry.get("sourcesByGeneral"))
        review_status_by_general = object_map(entry.get("reviewStatusByGeneral"))
        for general_id in general_ids:
            review_status = str(review_status_by_general.get(general_id) or "accepted").strip().lower()
            if review_status and review_status not in {"accepted", "approved", "high-confidence"}:
                continue
            alias_type = str(alias_type_by_general.get(general_id) or "").strip().lower()
            sources = [str(item).strip().lower() for item in sources_by_general.get(general_id) or []]
            if alias_type == "canonical-name" or "name" in sources:
                name_map.setdefault(general_id, alias)
    return name_map


def build_formal_mention_alias_map(formal_mention_map: dict[str, Any]) -> dict[str, list[str]]:
    alias_map: dict[str, set[str]] = defaultdict(set)
    entries = formal_mention_map.get("entries")
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("alias") or "").strip()
        if not alias or not contains_cjk_char(alias):
            continue
        general_ids = string_list(entry.get("generalIds"))
        review_status_by_general = object_map(entry.get("reviewStatusByGeneral"))
        for general_id in general_ids:
            review_status = str(review_status_by_general.get(general_id) or "accepted").strip().lower()
            if review_status and review_status not in {"accepted", "approved", "high-confidence"}:
                continue
            alias_map[general_id].add(alias)
    return {
        general_id: sorted(values, key=lambda item: (-len(item), item))
        for general_id, values in alias_map.items()
        if values
    }


def build_general_alias_record_map(general_alias_records: dict[str, Any]) -> dict[str, list[str]]:
    alias_map: dict[str, set[str]] = defaultdict(set)
    rows = general_alias_records.get("data")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        for alias_row in object_list(row.get("aliases")):
            alias = compact_text(alias_row.get("label"))
            review_status = str(alias_row.get("reviewStatus") or "accepted").strip().lower()
            if not alias or not contains_cjk_char(alias):
                continue
            if review_status and review_status not in {"accepted", "approved", "high-confidence"}:
                continue
            alias_map[general_id].add(alias)
    return {
        general_id: sorted(values, key=lambda item: (-len(item), item))
        for general_id, values in alias_map.items()
        if values
    }


def build_general_scoped_ambiguous_alias_map(general_alias_records: dict[str, Any]) -> dict[str, list[str]]:
    ambiguous_map: dict[str, set[str]] = defaultdict(set)
    rows = general_alias_records.get("data")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        raw_aliases = [*string_list(row.get("scopedAliasesZhTw")), *string_list(row.get("ambiguousAliasesZhTw"))]
        if not raw_aliases:
            for alias_row in object_list(row.get("aliases")):
                alias = compact_text(alias_row.get("label"))
                review_status = str(alias_row.get("reviewStatus") or "").strip().lower()
                if review_status == "collision" and alias and contains_cjk_char(alias):
                    raw_aliases.append(alias)
        for alias in raw_aliases:
            cleaned = compact_text(alias)
            if cleaned and contains_cjk_char(cleaned):
                ambiguous_map[general_id].add(cleaned)
    return {
        general_id: sorted(values, key=lambda item: (-len(item), item))
        for general_id, values in ambiguous_map.items()
        if values
    }


def build_name_map(stable_bootstrap: dict[str, Any], formal_mention_map: dict[str, Any]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for section in ("identitySeeds", "basicProfileSeeds", "femalePriorityProfiles"):
        rows = stable_bootstrap.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            general_id = str(row.get("generalId") or "").strip()
            name = str(row.get("name") or "").strip()
            if general_id and name:
                name_map[general_id] = name
    for general_id, name in build_formal_mention_name_map(formal_mention_map).items():
        name_map.setdefault(general_id, name)
    return name_map


def build_alias_map(
    name_map: dict[str, str],
    formal_mention_map: dict[str, Any],
    general_alias_records: dict[str, Any],
) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = defaultdict(set)
    for general_id, name in name_map.items():
        if name and contains_cjk_char(name):
            merged[general_id].add(compact_text(name))
    for source_map in (
        build_formal_mention_alias_map(formal_mention_map),
        build_general_alias_record_map(general_alias_records),
    ):
        for general_id, aliases in source_map.items():
            for alias in aliases:
                if alias and contains_cjk_char(alias):
                    merged[general_id].add(compact_text(alias))
    return {
        general_id: sorted(values, key=lambda item: (-len(item), item))
        for general_id, values in merged.items()
        if values
    }


def display_name(entity_id: Any, name_map: dict[str, str]) -> str:
    key = str(entity_id or "").strip()
    if not key:
        return "-"
    mapped = str(name_map.get(key) or "").strip()
    return mapped or key


def relationship_dimension_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("relationshipDimension"))


def bidirectional_relationship_types(policy: dict[str, Any]) -> set[str]:
    return set(string_list(relationship_dimension_policy(policy).get("bidirectionalRelationshipTypes")))


def semantic_review_mode(policy: dict[str, Any], args: argparse.Namespace | None = None) -> str:
    semantic_policy = object_map(policy.get("semanticReview"))
    raw = str(
        (getattr(args, "review_mode", "") if args is not None else "")
        or semantic_policy.get("reviewMode")
        or "pair-validation"
    ).strip().lower()
    if raw in {"extract", "sentence-extraction", "relation-extraction", "sentence-relation-extraction"}:
        return "sentence-relation-extraction"
    return "pair-validation"


def semantic_runner_policy(policy: dict[str, Any], runner_name: str) -> dict[str, Any]:
    semantic_policy = object_map(policy.get("semanticReview"))
    selected_runner = str(runner_name or "primary").strip().lower()
    if selected_runner != "secondary":
        return semantic_policy
    secondary_runner = object_map(semantic_policy.get("secondaryRunner"))
    merged = dict(semantic_policy)
    field_map = {
        "reviewerPreset": "secondaryReviewerPreset",
        "reviewerProvider": "secondaryReviewerProvider",
        "apiUrl": "secondaryApiUrl",
        "model": "secondaryModel",
        "timeoutMs": "secondaryTimeoutMs",
        "numCtx": "secondaryNumCtx",
        "numPredict": "secondaryNumPredict",
        "cacheFileName": "secondaryCacheFileName",
        "queueFileName": "secondaryQueueFileName",
        "evidenceFileName": "secondaryEvidenceFileName",
        "summaryFileName": "secondarySummaryFileName",
    }
    for field, legacy_field in field_map.items():
        value = secondary_runner.get(field)
        if value in (None, "", []):
            value = semantic_policy.get(legacy_field)
        if value not in (None, "", []):
            merged[field] = value
    merged["runnerName"] = "secondary"
    return merged


def expand_bidirectional_decision_keys(keys: set[str], policy: dict[str, Any]) -> set[str]:
    bidirectional_types = bidirectional_relationship_types(policy)
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


def read_human_decision_sets(path: Path, policy: dict[str, Any]) -> dict[str, set[str]]:
    if not path.exists():
        return {"whitelist": set(), "blacklist": set(), "removed": set()}
    review_policy = object_map(policy.get("humanReview"))
    command_policy = object_map(review_policy.get("overrideCommands"))
    command_list_field = str(command_policy.get("commandListField") or "commands")
    decision_field = str(review_policy.get("decisionField") or "decision")
    action_field = str(command_policy.get("actionField") or "action")
    approved_statuses = set(string_list(review_policy.get("approvedStatuses")))
    rejected_statuses = set(string_list(review_policy.get("rejectedStatuses")))
    force_whitelist_actions = set(string_list(command_policy.get("forceWhitelistActions")))
    force_blacklist_actions = set(string_list(command_policy.get("forceBlacklistActions")))
    remove_actions = set(string_list(command_policy.get("removeFromIndexActions")))

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
            rows = [*rows, *[command for command in raw_commands if isinstance(command, dict)]]
    else:
        rows = []

    latest_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        keys = {
            str(row.get("trustKey") or "").strip(),
            str(row.get("trustZoneId") or "").strip(),
        }
        keys.discard("")
        for key in keys:
            latest_by_key[key] = row

    whitelist_keys: set[str] = set()
    blacklist_keys: set[str] = set()
    removed_keys: set[str] = set()
    for key, row in latest_by_key.items():
        action = str(row.get(action_field) or "").strip()
        status = str(row.get(decision_field) or "").strip()
        if action in remove_actions:
            whitelist_keys.discard(key)
            blacklist_keys.discard(key)
            removed_keys.add(key)
            continue
        if action in force_blacklist_actions or status in rejected_statuses:
            blacklist_keys.add(key)
            whitelist_keys.discard(key)
            removed_keys.discard(key)
            continue
        if action in force_whitelist_actions or status in approved_statuses:
            whitelist_keys.add(key)
            blacklist_keys.discard(key)
            removed_keys.discard(key)
    return {
        "whitelist": expand_bidirectional_decision_keys(whitelist_keys, policy),
        "blacklist": expand_bidirectional_decision_keys(blacklist_keys, policy),
        "removed": expand_bidirectional_decision_keys(removed_keys, policy),
    }


def focus_queue_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("focusQueue"))


def focus_relationship_types(policy: dict[str, Any]) -> list[str]:
    return string_list(focus_queue_policy(policy).get("relationshipTypePriority"))


def focus_slot_fields(policy: dict[str, Any]) -> dict[str, list[str]]:
    raw = object_map(focus_queue_policy(policy).get("slotRoleFields"))
    return {key: string_list(value) for key, value in raw.items()}


def focus_slot_cap(policy: dict[str, Any], relationship_type: str) -> int:
    caps = object_map(focus_queue_policy(policy).get("slotCaps"))
    return max(1, int(number_value(caps.get(relationship_type), 1.0)))


def focus_resolved_stages(policy: dict[str, Any]) -> set[str]:
    return set(string_list(focus_queue_policy(policy).get("resolvedStages")))


def focus_priority_score(policy: dict[str, Any], relationship_type: str, missing_slot_count: int, row_score: float) -> float:
    config = focus_queue_policy(policy)
    weights = object_map(config.get("priorityWeights"))
    ordered_types = focus_relationship_types(policy)
    priority_index = {rel_type: index for index, rel_type in enumerate(ordered_types)}
    relation_weight = number_value(weights.get("relationshipTypeWeight"), 1000.0)
    missing_weight = number_value(weights.get("missingSlotWeight"), 100.0)
    row_weight = number_value(weights.get("rowScoreWeight"), 1.0)
    rank = priority_index.get(relationship_type, len(priority_index))
    relation_score = max(len(ordered_types) - rank, 0) * relation_weight
    return round(relation_score + max(missing_slot_count, 0) * missing_weight + row_score * row_weight, 3)


def row_slot_general_ids(row: dict[str, Any], policy: dict[str, Any], focus_general_ids: set[str]) -> list[str]:
    relationship_type = str(row.get("relationshipType") or "").strip()
    slot_fields_map = focus_slot_fields(policy)
    fields = slot_fields_map.get(relationship_type) or ["fromId", "toId", "subjectId", "controllerId"]
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for field in fields:
        general_id = str(row.get(field) or "").strip()
        if not general_id or general_id not in focus_general_ids or general_id in seen:
            continue
        seen.add(general_id)
        ordered_ids.append(general_id)
    return ordered_ids


def build_focus_gap_index(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    focus_general_ids: set[str],
    human_sets: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    config = focus_queue_policy(policy)
    if not bool_value(config.get("enabled"), True) or not focus_general_ids:
        return {}
    ordered_types = focus_relationship_types(policy)
    if not ordered_types:
        return {}
    allowed_types = set(ordered_types)
    resolved_stages = focus_resolved_stages(policy)
    resolved_slot_counts: Counter[tuple[str, str]] = Counter()
    resolved_keys = set(human_sets.get("whitelist") or set()) | set(human_sets.get("blacklist") or set())
    for row in rows:
        relationship_type = str(row.get("relationshipType") or "").strip()
        if relationship_type not in allowed_types:
            continue
        trust_key = str(row.get("trustKey") or "").strip()
        if not trust_key:
            continue
        if str(row.get("zone") or "").strip() in resolved_stages:
            resolved_keys.add(trust_key)
            for general_id in row_slot_general_ids(row, policy, focus_general_ids):
                resolved_slot_counts[(relationship_type, general_id)] += 1

    gap_index: dict[str, dict[str, Any]] = {}
    for row in rows:
        relationship_type = str(row.get("relationshipType") or "").strip()
        trust_key = str(row.get("trustKey") or "").strip()
        if relationship_type not in allowed_types or not trust_key or trust_key in resolved_keys:
            continue
        gap_general_ids: list[str] = []
        for general_id in row_slot_general_ids(row, policy, focus_general_ids):
            if resolved_slot_counts[(relationship_type, general_id)] < focus_slot_cap(policy, relationship_type):
                gap_general_ids.append(general_id)
        if not gap_general_ids:
            continue
        gap_index[trust_key] = {
            "focusQueuePriority": focus_priority_score(
                policy,
                relationship_type,
                len(gap_general_ids),
                number_value(row.get("score")),
            ),
            "focusGapGeneralIds": gap_general_ids,
            "focusGapCount": len(gap_general_ids),
        }
    return gap_index


def apply_focus_gap_filter(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    focus_general_ids: set[str],
    human_sets: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    gap_index = build_focus_gap_index(rows, policy, focus_general_ids, human_sets)
    if not gap_index:
        return rows, {}
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        trust_key = str(row.get("trustKey") or "").strip()
        metadata = gap_index.get(trust_key)
        if not metadata:
            continue
        filtered_rows.append({**row, **metadata})
    return filtered_rows, gap_index


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        unit_id = str(row.get("semanticReviewUnitId") or "").strip()
        if unit_id:
            cache[unit_id] = row
    return cache


def semantic_queue_ranking_fields(policy: dict[str, Any]) -> list[dict[str, str]]:
    semantic_policy = object_map(policy.get("semanticReview"))
    ranking_policy = object_map(semantic_policy.get("queueRanking"))
    typed_fields = [item for item in ranking_policy.get("sortFields") or [] if isinstance(item, dict)]
    if typed_fields:
        return typed_fields
    return [
        {"field": "focusQueuePriority", "direction": "desc"},
        {"field": "sourcePreviewPriorityMax", "direction": "desc"},
        {"field": "candidateMaxScoreBeforeSemanticReview", "direction": "desc"},
        {"field": "candidateCount", "direction": "asc"},
        {"field": "sentenceQualityScore", "direction": "desc"},
        {"field": "semanticReviewUnitId", "direction": "asc"},
    ]


def semantic_queue_sort_key(unit: dict[str, Any], policy: dict[str, Any]) -> tuple[Any, ...]:
    key: list[Any] = []
    for item in semantic_queue_ranking_fields(policy):
        field = str(item.get("field") or "").strip()
        direction = str(item.get("direction") or "asc").strip().lower()
        value = unit.get(field)
        if isinstance(value, (int, float)) or value is None:
            numeric = number_value(value)
            key.append(-numeric if direction == "desc" else numeric)
            continue
        text = str(value or "")
        if direction == "desc":
            key.append("".join(chr(0x10FFFF - ord(char)) for char in text))
        else:
            key.append(text)
    return tuple(key)


def support_previews(row: dict[str, Any]) -> list[dict[str, Any]]:
    previews = row.get("sourceQuotePreviews")
    if not isinstance(previews, list):
        return []
    result: list[dict[str, Any]] = []
    for preview in previews:
        if not isinstance(preview, dict):
            continue
        quote = compact_text(preview.get("quote") or preview.get("originalSentence"))
        if not quote:
            continue
        result.append(preview)
    return result


def direction_check_instruction(relationship_type: str) -> str:
    if relationship_type == "parent_child":
        return "必須逐字確認方向：fromId/fromName 是父母或親長，toId/toName 是子女或晚輩；若原文是「A之子B」，正確方向是 A -> B，反向必須判為不支持。"
    if relationship_type == "adoptive_parent_child":
        return "必須逐字確認方向：fromId/fromName 是義父、義母或收養方，toId/toName 是義子、義女或被收養方；不能混成真實親子。"
    if relationship_type == "ruler_subject":
        return "必須逐字確認方向：fromId/fromName 是主君、效力對象或明確上級，toId/toName 是臣屬、部下或投奔者；同朝任官、共同列名、派遣或推薦本身不等於君臣。"
    if relationship_type == "spouse":
        return "必須逐字確認類型：兩人確實是夫妻、配偶、妻妾或婚配對象；不能把同句其他人的婚姻誤套到候選兩人。"
    if relationship_type == "sibling":
        return "必須逐字確認類型：兩人是真實兄弟姊妹；結義兄弟、同僚、同陣營或同行不能算兄弟姊妹。"
    if relationship_type == "sworn_sibling":
        return "必須逐字確認類型：兩人有結義、義兄弟或桃園結義等明確訊號；真實兄弟姊妹不能混成結義。"
    return "必須確認原文同時支持關係類型與 pair-key 方向；只要類型或方向不明就判為不支持。"


def candidate_payload(row: dict[str, Any], name_map: dict[str, str]) -> dict[str, Any]:
    relationship_type = str(row.get("relationshipType") or "").strip()
    return {
        "trustKey": row.get("trustKey"),
        "claimSentenceZhTw": row.get("claimSentenceZhTw"),
        "relationshipType": relationship_type,
        "fromId": row.get("fromId"),
        "toId": row.get("toId"),
        "subjectId": row.get("subjectId"),
        "controllerId": row.get("controllerId"),
        "fromNameZhTw": display_name(row.get("fromId"), name_map),
        "toNameZhTw": display_name(row.get("toId"), name_map),
        "subjectNameZhTw": display_name(row.get("subjectId"), name_map),
        "controllerNameZhTw": display_name(row.get("controllerId"), name_map),
        "strictDirectionCheckZhTw": direction_check_instruction(relationship_type),
        "scoreBeforeSemanticReview": row.get("score"),
        "focusQueuePriority": row.get("focusQueuePriority"),
        "focusGapCount": row.get("focusGapCount"),
        "focusGapGeneralIds": row.get("focusGapGeneralIds") or [],
        "canonicalWrites": False,
    }


def allowed_entities_from_candidates(
    candidates: list[dict[str, Any]],
    alias_map: dict[str, list[str]],
    scoped_ambiguous_alias_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for field, name_field, role_label in (
            ("fromId", "fromNameZhTw", "from"),
            ("toId", "toNameZhTw", "to"),
            ("subjectId", "subjectNameZhTw", "subject"),
            ("controllerId", "controllerNameZhTw", "controller"),
        ):
            entity_id = str(candidate.get(field) or "").strip()
            entity_name = compact_text(candidate.get(name_field))
            if not entity_id:
                continue
            entry = entities.setdefault(
                entity_id,
                {
                    "entityId": entity_id,
                    "nameZhTw": entity_name or entity_id,
                    "aliasesZhTw": list(alias_map.get(entity_id) or ([entity_name] if entity_name else [])),
                    "scopedAmbiguousAliasesZhTw": list(scoped_ambiguous_alias_map.get(entity_id) or []),
                    "roleHints": [],
                    "canonicalWrites": False,
                },
            )
            if entity_name and entity_name not in entry["aliasesZhTw"]:
                entry["aliasesZhTw"].insert(0, entity_name)
            if role_label not in entry["roleHints"]:
                entry["roleHints"].append(role_label)
    return sorted(entities.values(), key=lambda item: (str(item.get("nameZhTw") or ""), str(item.get("entityId") or "")))


def annotate_page_shape(unit: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    page_policy = semantic_page_shape_policy(policy)
    if not bool_value(page_policy.get("enabled"), False):
        unit["pageShapeCategory"] = "disabled"
        unit["pageShapeSuppressed"] = False
        unit["pageShapePenaltyScore"] = 0.0
        unit["pageShapeNarrativeBoostScore"] = 0.0
        unit["pageShapePriorityScore"] = round(number_value(unit.get("sourcePreviewPriorityMax")), 4)
        return unit
    telemetry = sentence_page_shape_metrics(compact_text(unit.get("sourceSentence")), policy)
    unit["pageShapeCategory"] = telemetry.get("category")
    unit["pageShapeSuppressed"] = bool(telemetry.get("suppressed"))
    unit["pageShapePenaltyScore"] = number_value(telemetry.get("penaltyScore"))
    unit["pageShapeNarrativeBoostScore"] = number_value(telemetry.get("boostScore"))
    unit["pageShapePriorityScore"] = round(
        max(
            0.0,
            number_value(unit.get("sourcePreviewPriorityMax"))
            - number_value(telemetry.get("penaltyScore"))
            + number_value(telemetry.get("boostScore")),
        ),
        4,
    )
    unit["pageShapeTelemetry"] = {
        "structuralSignals": telemetry.get("structuralSignals") or [],
        "negativeKeywordHits": telemetry.get("negativeKeywordHits") or [],
        "narrativeKeywordHits": telemetry.get("narrativeKeywordHits") or [],
        "pairCueKeywordHits": telemetry.get("pairCueKeywordHits") or [],
        "metrics": object_map(telemetry.get("metrics")),
        "canonicalWrites": False,
    }
    return unit


def build_review_units(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    name_map: dict[str, str],
    alias_map: dict[str, list[str]],
    scoped_ambiguous_alias_map: dict[str, list[str]],
    review_mode: str,
) -> list[dict[str, Any]]:
    semantic_policy = object_map(policy.get("semanticReview"))
    prompt_version = str(semantic_policy.get("promptVersion") or "relationship-semantic-review.v1")
    stages = set(string_list(semantic_policy.get("candidateStages")))
    max_candidates = max(1, int(number_value(semantic_policy.get("maxCandidatesPerSentence"), 12.0)))
    units_by_id: dict[str, dict[str, Any]] = {}
    candidate_seen_by_sentence: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if stages and row_stage(row) not in stages:
            continue
        trust_key = str(row.get("trustKey") or "").strip()
        if not trust_key:
            continue
        for preview in support_previews(row):
            sentence = compact_text(preview.get("quote") or preview.get("originalSentence"))
            base_unit_id = cache_unit_id(prompt_version, sentence)
            quality_score = sentence_quality_score(sentence)
            if trust_key in candidate_seen_by_sentence[base_unit_id]:
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
                        "sentenceHash": "sha256:" + hashlib.sha256(normalized_sentence(sentence).encode("utf-8")).hexdigest(),
                        "sourceSentence": sentence,
                        "sentenceQualityScore": quality_score,
                        "sourceRefs": [],
                        "sourcePreviewPriorityMax": 0.0,
                        "candidates": [],
                        "reviewMode": review_mode,
                        "canonicalWrites": False,
                    },
                )
                if len(unit["candidates"]) < max_candidates:
                    break
                chunk_index += 1
            source_ref = {
                "sourceId": preview.get("sourceId"),
                "sourceFamily": preview.get("sourceFamily"),
                "sourceLayer": preview.get("sourceLayer"),
                "confidenceSignals": string_list(preview.get("confidenceSignals")),
                "locator": preview.get("locator"),
                "url": preview.get("url"),
                "evidenceRefs": preview.get("evidenceRefs") or [],
                "canonicalWrites": False,
            }
            if source_ref not in unit["sourceRefs"]:
                unit["sourceRefs"].append(source_ref)
            unit["sourcePreviewPriorityMax"] = round(
                max(number_value(unit.get("sourcePreviewPriorityMax")), number_value(preview.get("previewPriorityScore"))),
                4,
            )
            unit["candidates"].append(candidate_payload(row, name_map))
            candidate_seen_by_sentence[base_unit_id].add(trust_key)
    for unit in units_by_id.values():
        scores = [
            number_value(candidate.get("scoreBeforeSemanticReview"))
            for candidate in unit.get("candidates") or []
            if isinstance(candidate, dict)
        ]
        focus_priorities = [
            number_value(candidate.get("focusQueuePriority"))
            for candidate in unit.get("candidates") or []
            if isinstance(candidate, dict) and candidate.get("focusQueuePriority") is not None
        ]
        unit["candidateMaxScoreBeforeSemanticReview"] = round(max(scores) if scores else 0.0, 3)
        unit["candidateCount"] = len(unit.get("candidates") or [])
        unit["focusQueuePriority"] = round(max(focus_priorities) if focus_priorities else 0.0, 3)
        unit["allowedEntities"] = allowed_entities_from_candidates(
            unit.get("candidates") or [],
            alias_map,
            scoped_ambiguous_alias_map,
        )
        unit["allowedRelationshipTypes"] = sorted(
            {
                str(candidate.get("relationshipType") or "").strip()
                for candidate in unit.get("candidates") or []
                if isinstance(candidate, dict) and str(candidate.get("relationshipType") or "").strip()
            }
        )
        annotate_page_shape(unit, policy)
    return sorted(units_by_id.values(), key=lambda item: semantic_queue_sort_key(item, policy))


def cached_candidate_keys(cache_row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for relation in cache_row.get("relationships") or []:
        if isinstance(relation, dict) and str(relation.get("trustKey") or "").strip():
            keys.add(str(relation.get("trustKey")).strip())
    for key in string_list(cache_row.get("reviewedCandidateKeys")):
        keys.add(key)
    return keys


def unit_needs_review(unit: dict[str, Any], cache: dict[str, dict[str, Any]]) -> bool:
    cached = cache.get(str(unit.get("semanticReviewUnitId") or ""))
    if not cached:
        return True
    candidate_keys = {str(item.get("trustKey") or "").strip() for item in unit.get("candidates") or [] if isinstance(item, dict)}
    return not candidate_keys.issubset(cached_candidate_keys(cached))


def system_prompt(policy: dict[str, Any]) -> str:
    semantic_policy = object_map(policy.get("semanticReview"))
    configured = str(semantic_policy.get("systemPromptZhTw") or "").strip()
    base = configured or "\n".join(
        [
            "你是三國人物關係的句級語意審查員。",
            "任務：只根據輸入的 sourceSentence 與候選 relationships，判斷該句是否支持每一個 pair-key 關係。",
            "規則：deterministic extractor 只負責縮小候選範圍，不代表語意為真；你必須保留完整句意，不可只看關鍵字。",
            "規則：如果句子只表示派遣、命令、推薦、攻打、見面、稱呼，不能直接推成君臣、親子、夫妻或結義。",
            "規則：如果方向不明、主詞不明、年代不明或只能推測，請給 uncertain 或 not_enough_context。",
            "輸出必須是 JSON，格式為：{\"relationships\":[...]}。",
            "每個 relationships[] 必須包含 trustKey、verdict、semanticTrustScore、confidence、evidenceSentence、rationaleZhTw。",
            "verdict 只能是 supported、contradicted、uncertain、not_enough_context。",
            "semanticTrustScore 是 0 到 100 的句級語意信任分數；90 以上代表這一句本身非常明確支持該關係。",
            "confidence 是 0 到 1 的模型把握度；它不是最終關係信任區分數。",
            "這一關只產生 semantic-precheck，不得宣告 canonicalWrites，也不得把候選直接升成白名單。",
        ]
    )
    strict_tail = "\n".join(
        [
            "嚴格補充：你不是判斷兩人是否有任何關係，而是判斷該 sourceSentence 是否精確支持候選 trustKey 的關係類型與方向。",
            "嚴格補充：每個候選都帶有 strictDirectionCheckZhTw，必須依該說明逐字檢查主語、受詞、關係類型與方向。",
            "嚴格補充：supported 只能用在 typeMatched=true 且 directionMatched=true；只要方向相反、關係套錯人、同句其他人的關係被誤套，verdict 必須是 contradicted 或 uncertain。",
            "嚴格補充：君臣或主從必須是明確效力、投奔、歸降、隸屬、任官上下級；共同列名、同朝任官、被同一皇帝任命、派遣作戰，不能直接判為君臣。",
            "嚴格補充：親子必須分清父母方與子女方；義父義子必須使用 adoptive_parent_child，不能用 parent_child。",
            "輸出 JSON 中每個 relationships[] 除既有欄位外，必須包含 typeMatched、directionMatched、matchedFromNameZhTw、matchedToNameZhTw、mismatchReasonZhTw。",
        ]
    )
    return f"{base}\n{strict_tail}"


def sentence_extraction_system_prompt() -> str:
    return "\n".join(
        [
            "You extract stable relationship facts from one preserved Chinese source sentence.",
            "Read sourceSentence first. Candidate windows are hints only and may be wrong.",
            "Only use entity ids from allowedEntities.",
            "Each allowed entity may include accepted aliases; treat those aliases as the same person, but keep the copied evidence span itself clean and minimal.",
            "Each allowed entity may include accepted aliases; treat those aliases as the same person, but keep the person span itself clean and minimal.",
            "Extract only explicit, stable relationship facts. Do not infer from co-occurrence alone.",
            "Do not infer from co-occurrence, attack targets, peer deployment, recommendations, introductions, or temporary alignment alone.",
            "For ruler_subject, you may extract a stable relation when the sentence explicitly names one controller or lord who orders, dispatches, appoints, retains, or commands one named officer or subject.",
            "Do not promote the attacked target, peer officer, or battlefield opponent into ruler_subject.",
            "Relationship ontology and direction:",
            "- ruler_subject: fromId is ruler/lord/controller, toId is subject/subordinate/officer.",
            "- spouse: marriage or consort relation; bidirectional.",
            "- parent_child: fromId is parent, toId is child.",
            "- adoptive_parent_child: fromId is adoptive or foster parent / guardian, toId is adopted or foster child.",
            "- sibling: biological siblings.",
            "- sworn_sibling: oath-bond siblings, not biological.",
            "- faction_membership: fromId is faction/camp/polity, toId is the member person; use only if explicit.",
            "If a stable relationship is not explicit, return no extracted relationship for it.",
            "Every extracted item must be grounded with exact substrings copied from sourceSentence.",
            "Use cueCategory values such as: lordship, subordination, service, allegiance, office-under, marriage, consort, kinship, parent-child, adoption, foster-kinship, sibling-kinship, oath-sibling, membership, affiliation, event-order, recommendation, appointment-only, hypothetical, negative.",
            "Use polarity values: affirmative, negative, hypothetical, uncertain.",
            "Grounding rules:",
            "- fromEvidenceSpanZhTw and toEvidenceSpanZhTw should isolate the person mention or accepted alias, not a long action phrase or third-party descriptor.",
            "- Do not use spans such as '某人妻', '某人之子', '見待次於某人', or '後獻與某人' as direct person spans for a stable pair.",
            "- For spouse, both endpoints must be the married pair themselves, not a marriage plan, transfer, or someone else's spouse.",
            "- For ruler_subject, do not reverse direction from ranking, comparison, courtesy mention, or temporary event-order language.",
            "Examples:",
            "- Positive alias-grounded lordship: sourceSentence='孔明令魏延自回本寨把守' => you may extract ruler_subject(zhuge-liang, wei-yan) when the grounded person spans are the aliases '孔明' and '魏延'.",
            "- Positive ruler_subject: sourceSentence='孫權以程普為盪寇將軍' => extract ruler_subject with fromId=sun-quan, toId=cheng-pu, cueCategory=office-under, polarity=affirmative.",
            "- Positive parent_child: sourceSentence='馬騰之子馬超' => extract parent_child with fromId=ma-teng, toId=ma-chao, cueCategory=parent-child, polarity=affirmative.",
            "- Negative event-order: sourceSentence='督徐晃等破劉備別將高詳於陽平' => extract nothing because this is a military event command, not a stable ruler_subject relation.",
            "- Negative hypothetical: sourceSentence='若劉備能抵抗曹操，那麼劉備就不再是將軍的臣下' => extract nothing because the sentence is hypothetical/negative rather than stable affirmative relation.",
            "Return JSON only with shape: {\"extractedRelationships\":[...]}",
            "Each extracted item must include relationshipType, fromId, toId, fromNameZhTw, toNameZhTw, fromEvidenceSpanZhTw, toEvidenceSpanZhTw, relationshipCueSpanZhTw, cueCategory, polarity, normalizedClaimZhTw, stableRelation, semanticTrustScore, confidence, evidenceSentence, rationaleZhTw.",
            "Use zh-TW for normalizedClaimZhTw and rationaleZhTw.",
        ]
    )


def sentence_extraction_system_prompt_v2() -> str:
    return "\n".join(
        [
            "You extract stable relationship facts from one preserved Chinese source sentence.",
            "Read sourceSentence first. Candidate windows are hints only and may be wrong.",
            "Only use entity ids from allowedEntities.",
            "allowedEntities may include aliasesZhTw plus scopedAmbiguousAliasesZhTw; scoped ambiguous aliases are usable only when they uniquely point to one allowed entity in this sentence.",
            "Extract only explicit, stable relationship facts. Do not infer from co-occurrence alone.",
            "Do not infer from co-occurrence, attack targets, peer deployment, recommendations, introductions, or temporary alignment alone.",
            "For ruler_subject, you may extract a stable relation when the sentence explicitly names one controller or lord who orders, dispatches, appoints, retains, or commands one named officer or subject.",
            "Do not promote the attacked target, peer officer, or battlefield opponent into ruler_subject.",
            "Relationship ontology and direction:",
            "- ruler_subject: fromId is ruler/lord/controller, toId is subject/subordinate/officer.",
            "- spouse: marriage or consort relation; bidirectional.",
            "- parent_child: fromId is parent, toId is child.",
            "- adoptive_parent_child: fromId is adoptive or foster parent / guardian, toId is adopted or foster child.",
            "- sibling: biological siblings.",
            "- sworn_sibling: oath-bond siblings, not biological.",
            "- faction_membership: fromId is faction/camp/polity, toId is the member person; use only if explicit.",
            "If a stable relationship is not explicit, return no extracted relationship for it.",
            "Every extracted item must be grounded with exact substrings copied from sourceSentence.",
            "Use cueCategory values such as: lordship, subordination, service, allegiance, office-under, marriage, consort, kinship, parent-child, adoption, foster-kinship, sibling-kinship, oath-sibling, membership, affiliation, event-order, recommendation, appointment-only, hypothetical, negative.",
            "Use polarity values: affirmative, negative, hypothetical, uncertain.",
            "Keep person spans minimal and entity-like. Do not use role-tailed spans such as '某人妻', '某人之子', '見待次於某人', or '後獻與某人' as person spans.",
            "For spouse, do not extract from third-party marriage plans, matchmaking, or references to someone else's wife or consort.",
            "For ruler_subject, do not reverse direction from ranking, comparison, praise, target-of-action, or event-order wording alone.",
            "Examples:",
            "- Positive alias-grounded lordship: sourceSentence='孔明令魏延自回本寨把守' => extract ruler_subject with fromId=zhuge-liang, toId=wei-yan, fromEvidenceSpanZhTw='孔明', toEvidenceSpanZhTw='魏延', cueCategory=lordship, polarity=affirmative.",
            "- Positive ruler_subject: sourceSentence='曹操遣將夏侯淵討宋建' => extract ruler_subject with fromId=cao-cao, toId=xiahou-yuan, cueCategory=service or office-under, polarity=affirmative.",
            "- Positive parent_child: sourceSentence='馬騰之子馬超' => extract parent_child with fromId=ma-teng, toId=ma-chao, cueCategory=parent-child, polarity=affirmative.",
            "- Negative event-order: sourceSentence='督徐晃等破劉備別將高詳於陽平' => extract nothing for liu-bei|xu-huang because the sentence names Xu Huang's action target, not a stable lord-subject pair.",
            "- Negative third-party spouse: sourceSentence='先將汝許嫁呂布，後獻與董卓' => extract nothing for spouse(dong-zhuo, lu-bu) because this is a marriage plan involving a third party.",
            "- Negative reversed lordship: sourceSentence='統遂附劉備，見待次於諸葛亮' => extract nothing for ruler_subject(zhuge-liang, liu-bei) because the sentence does not state that 劉備 serves under 諸葛亮.",
            "- Negative hypothetical: sourceSentence='若劉備能抵抗曹操，那麼劉備就不再是將軍的臣下' => extract nothing because the sentence is hypothetical or negative rather than stable affirmative relation.",
            "Return JSON only with shape: {\"extractedRelationships\":[...]}",
            "Each extracted item must include relationshipType, fromId, toId, fromNameZhTw, toNameZhTw, fromEvidenceSpanZhTw, toEvidenceSpanZhTw, relationshipCueSpanZhTw, cueCategory, polarity, normalizedClaimZhTw, stableRelation, semanticTrustScore, confidence, evidenceSentence, rationaleZhTw.",
            "Use zh-TW for normalizedClaimZhTw and rationaleZhTw.",
        ]
    )


def unique_scoped_ambiguous_alias_owners(allowed_entities: list[dict[str, Any]]) -> dict[str, str]:
    owners_by_alias: dict[str, set[str]] = defaultdict(set)
    for entity in allowed_entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entityId") or "").strip()
        if not entity_id:
            continue
        for alias in string_list(entity.get("scopedAmbiguousAliasesZhTw")):
            cleaned = compact_text(alias)
            if cleaned:
                owners_by_alias[cleaned].add(entity_id)
    return {
        alias: next(iter(entity_ids))
        for alias, entity_ids in owners_by_alias.items()
        if len(entity_ids) == 1
    }


def resolve_allowed_entity_id(name_or_id: Any, allowed_entities: list[dict[str, Any]]) -> str:
    token = compact_text(name_or_id)
    if not token:
        return ""
    lowered = token.casefold()
    for entity in allowed_entities:
        entity_id = str(entity.get("entityId") or "").strip()
        entity_name = compact_text(entity.get("nameZhTw"))
        if token == entity_id or lowered == entity_id.casefold():
            return entity_id
        if entity_name and (token == entity_name or lowered == entity_name.casefold()):
            return entity_id
        for alias in string_list(entity.get("aliasesZhTw")):
            normalized_alias = compact_text(alias)
            if normalized_alias and (token == normalized_alias or lowered == normalized_alias.casefold()):
                return entity_id
    unique_ambiguous_owners = unique_scoped_ambiguous_alias_owners(allowed_entities)
    for alias, entity_id in unique_ambiguous_owners.items():
        if token == alias or lowered == alias.casefold():
            return entity_id
    return ""


def allowed_entity_name_map(allowed_entities: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entity in allowed_entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entityId") or "").strip()
        entity_name = compact_text(entity.get("nameZhTw"))
        if entity_id and entity_name:
            mapping[entity_id] = entity_name
    return mapping


def allowed_entity_alias_map(allowed_entities: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    unique_ambiguous_owners = unique_scoped_ambiguous_alias_owners(allowed_entities)
    for entity in allowed_entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entityId") or "").strip()
        aliases = [compact_text(alias) for alias in string_list(entity.get("aliasesZhTw")) if compact_text(alias)]
        scoped_ambiguous = [
            compact_text(alias)
            for alias in string_list(entity.get("scopedAmbiguousAliasesZhTw"))
            if compact_text(alias) and unique_ambiguous_owners.get(compact_text(alias)) == entity_id
        ]
        aliases.extend(scoped_ambiguous)
        if entity_id and aliases:
            deduped = list(dict.fromkeys(aliases))
            mapping[entity_id] = sorted(deduped, key=lambda item: (-len(item), item))
    return mapping


def exact_sentence_span_exists(sentence: str, span: Any) -> bool:
    token = compact_text(span)
    return bool(token) and token in sentence


def plausible_entity_span(span: Any) -> bool:
    token = compact_text(span)
    if not token:
        return False
    if len(token) > 8:
        return False
    if any(char in token for char in "，。；：！？,.!?;:()[]{} "):
        return False
    return contains_cjk_char(token)


def plausible_cue_span(span: Any) -> bool:
    token = compact_text(span)
    if not token:
        return False
    if len(token) > 16:
        return False
    return contains_cjk_char(token)


def stable_cue_categories_for_relationship(relationship_type: str) -> set[str]:
    mapping = {
        "ruler_subject": {"lordship", "subordination", "service", "allegiance", "office-under"},
        "spouse": {"marriage", "consort"},
        "parent_child": {"kinship", "parent-child"},
        "adoptive_parent_child": {"adoption", "foster-kinship"},
        "sibling": {"sibling-kinship"},
        "sworn_sibling": {"oath-sibling"},
        "faction_membership": {"membership", "affiliation"},
    }
    return mapping.get(relationship_type, set())


def semantic_structure_gate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    semantic_policy = object_map(policy.get("semanticReview"))
    return object_map(semantic_policy.get("structureGate"))


def source_ref_layers(source_refs: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("sourceLayer") or "").strip()
        for item in source_refs
        if isinstance(item, dict) and str(item.get("sourceLayer") or "").strip()
    }


def source_ref_confidence_signals(source_refs: list[dict[str, Any]]) -> set[str]:
    signals: set[str] = set()
    for item in source_refs:
        if not isinstance(item, dict):
            continue
        for signal in string_list(item.get("confidenceSignals")):
            if signal:
                signals.add(signal)
    return signals


def semantic_structure_window_limit(
    relationship_type: str,
    source_refs: list[dict[str, Any]],
    policy: dict[str, Any],
) -> int:
    structure_policy = semantic_structure_gate_policy(policy)
    default_limit = int(number_value(structure_policy.get("defaultWindowLimit"), 24.0))
    max_window_limit = int(number_value(structure_policy.get("maxWindowLimit"), max(default_limit, 24)))
    type_limits = object_map(structure_policy.get("relationshipTypeWindowLimits"))
    source_layer_limits = object_map(structure_policy.get("sourceLayerWindowLimits"))
    confidence_signal_bonuses = object_map(structure_policy.get("confidenceSignalWindowBonuses"))
    limit = int(number_value(type_limits.get(relationship_type), default_limit))
    for source_layer in source_ref_layers(source_refs):
        limit = max(limit, int(number_value(source_layer_limits.get(source_layer), limit)))
    for signal in source_ref_confidence_signals(source_refs):
        limit += int(number_value(confidence_signal_bonuses.get(signal), 0.0))
    limit = max(limit, default_limit)
    return min(limit, max_window_limit)


def relationship_entity_span_mode(relationship_type: str, endpoint: str) -> str:
    mapping = {
        "ruler_subject": {"from": "prefix", "to": "exact"},
        "spouse": {"from": "exact", "to": "exact"},
        "parent_child": {"from": "prefix", "to": "suffix"},
        "adoptive_parent_child": {"from": "prefix", "to": "suffix"},
        "sibling": {"from": "exact", "to": "exact"},
        "sworn_sibling": {"from": "exact", "to": "exact"},
        "faction_membership": {"from": "exact", "to": "exact"},
    }
    return mapping.get(relationship_type, {}).get(endpoint, "exact")


def entity_alias_anchor(span: Any, aliases: list[str], mode: str) -> str:
    token = compact_text(span)
    if not token:
        return ""
    normalized_aliases = [compact_text(alias) for alias in aliases if compact_text(alias)]
    ordered_aliases = sorted(dict.fromkeys(normalized_aliases), key=lambda item: (-len(item), item))
    for alias in ordered_aliases:
        if mode == "exact" and token == alias:
            return alias
        if mode == "prefix" and token.startswith(alias):
            return alias
        if mode == "suffix" and token.endswith(alias):
            return alias
    return ""


def first_span_start(sentence: str, span: Any) -> int:
    token = compact_text(span)
    return sentence.find(token) if token else -1


def relation_structure_gate(
    relation: dict[str, Any],
    source_sentence: str,
    allowed_entities: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[bool, str]:
    relationship_type = str(relation.get("relationshipType") or "").strip()
    from_id = str(relation.get("fromId") or "").strip()
    to_id = str(relation.get("toId") or "").strip()
    from_span = compact_text(relation.get("fromEvidenceSpanZhTw"))
    to_span = compact_text(relation.get("toEvidenceSpanZhTw"))
    cue_span = compact_text(relation.get("relationshipCueSpanZhTw"))
    name_map = allowed_entity_name_map(allowed_entities)
    alias_map = allowed_entity_alias_map(allowed_entities)
    from_aliases = alias_map.get(from_id) or ([name_map.get(from_id, "")] if name_map.get(from_id, "") else [])
    to_aliases = alias_map.get(to_id) or ([name_map.get(to_id, "")] if name_map.get(to_id, "") else [])
    from_anchor = entity_alias_anchor(from_span, from_aliases, relationship_entity_span_mode(relationship_type, "from"))
    to_anchor = entity_alias_anchor(to_span, to_aliases, relationship_entity_span_mode(relationship_type, "to"))
    if not from_anchor:
        return False, "from-span-missing-accepted-alias-anchor"
    if not to_anchor:
        return False, "to-span-missing-accepted-alias-anchor"
    if cue_span:
        from_start = first_span_start(source_sentence, from_anchor)
        to_start = first_span_start(source_sentence, to_anchor)
        cue_start = first_span_start(source_sentence, cue_span)
        if min(from_start, to_start, cue_start) < 0:
            return False, "span-not-found-in-source-sentence"
        cover_start = min(from_start, to_start, cue_start)
        cover_end = max(from_start + len(from_anchor), to_start + len(to_anchor), cue_start + len(cue_span))
        structure_policy = semantic_structure_gate_policy(policy)
        reason_labels = object_map(structure_policy.get("reasonLabels"))
        max_window = semantic_structure_window_limit(relationship_type, source_refs, policy)
        if cover_end - cover_start > max_window:
            return False, str(reason_labels.get("pairCueWindowTooWide") or "pair-cue-window-too-wide")
    return True, ""


def extracted_relation_is_grounded(
    extracted: dict[str, Any],
    source_sentence: str,
    allowed_entities: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    policy: dict[str, Any],
) -> bool:
    relationship_type = str(extracted.get("relationshipType") or "").strip()
    cue_category = str(extracted.get("cueCategory") or "").strip().lower()
    polarity = str(extracted.get("polarity") or "").strip().lower()
    if optional_bool(extracted.get("stableRelation")) is not True:
        return False
    if polarity not in {"affirmative", ""}:
        return False
    if not plausible_entity_span(extracted.get("fromEvidenceSpanZhTw")):
        return False
    if not plausible_entity_span(extracted.get("toEvidenceSpanZhTw")):
        return False
    if not plausible_cue_span(extracted.get("relationshipCueSpanZhTw")):
        return False
    if not exact_sentence_span_exists(source_sentence, extracted.get("fromEvidenceSpanZhTw")):
        return False
    if not exact_sentence_span_exists(source_sentence, extracted.get("toEvidenceSpanZhTw")):
        return False
    if not exact_sentence_span_exists(source_sentence, extracted.get("relationshipCueSpanZhTw")):
        return False
    allowed_categories = stable_cue_categories_for_relationship(relationship_type)
    if allowed_categories and cue_category not in allowed_categories:
        return False
    structure_passed, _ = relation_structure_gate(extracted, source_sentence, allowed_entities, source_refs, policy)
    if not structure_passed:
        return False
    return True


def extraction_matches_for_candidate(
    candidate: dict[str, Any],
    extracted_rows: list[dict[str, Any]],
    bidirectional_types: set[str],
) -> tuple[str, dict[str, Any] | None]:
    relationship_type = str(candidate.get("relationshipType") or "").strip()
    from_id = str(candidate.get("fromId") or "").strip()
    to_id = str(candidate.get("toId") or "").strip()
    for extracted in extracted_rows:
        if relationship_type != str(extracted.get("relationshipType") or "").strip():
            continue
        extracted_from = str(extracted.get("fromId") or "").strip()
        extracted_to = str(extracted.get("toId") or "").strip()
        if extracted_from == from_id and extracted_to == to_id:
            return "supported", extracted
        if relationship_type in bidirectional_types and extracted_from == to_id and extracted_to == from_id:
            return "supported", extracted
        if relationship_type not in bidirectional_types and extracted_from == to_id and extracted_to == from_id:
            return "contradicted", extracted
    return "not_enough_context", None


def extraction_result_to_relationships(
    unit: dict[str, Any],
    parsed: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_entities = object_list(unit.get("allowedEntities"))
    source_refs = object_list(unit.get("sourceRefs"))
    raw_rows = parsed.get("extractedRelationships")
    if not isinstance(raw_rows, list):
        raw_rows = []
    extracted_rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        relationship_type = str(raw_row.get("relationshipType") or "").strip()
        from_id = resolve_allowed_entity_id(raw_row.get("fromId") or raw_row.get("fromNameZhTw"), allowed_entities)
        to_id = resolve_allowed_entity_id(raw_row.get("toId") or raw_row.get("toNameZhTw"), allowed_entities)
        if not relationship_type or not from_id or not to_id:
            continue
        score, confidence = semantic_score_pair(raw_row)
        extracted_row = {
            "relationshipType": relationship_type,
            "fromId": from_id,
            "toId": to_id,
            "fromNameZhTw": compact_text(raw_row.get("fromNameZhTw")) or from_id,
            "toNameZhTw": compact_text(raw_row.get("toNameZhTw")) or to_id,
            "fromEvidenceSpanZhTw": compact_text(raw_row.get("fromEvidenceSpanZhTw")),
            "toEvidenceSpanZhTw": compact_text(raw_row.get("toEvidenceSpanZhTw")),
            "relationshipCueSpanZhTw": compact_text(raw_row.get("relationshipCueSpanZhTw")),
            "cueCategory": compact_text(raw_row.get("cueCategory")).lower(),
            "polarity": compact_text(raw_row.get("polarity")).lower(),
            "normalizedClaimZhTw": compact_text(raw_row.get("normalizedClaimZhTw")),
            "stableRelation": optional_bool(raw_row.get("stableRelation")),
            "semanticTrustScore": round(score, 2),
            "confidence": round(confidence, 4),
            "evidenceSentence": compact_text(raw_row.get("evidenceSentence")) or compact_text(unit.get("sourceSentence")),
            "rationaleZhTw": compact_text(raw_row.get("rationaleZhTw")),
            "canonicalWrites": False,
        }
        if not extracted_relation_is_grounded(
            extracted_row,
            compact_text(unit.get("sourceSentence")),
            allowed_entities,
            source_refs,
            policy,
        ):
            continue
        extracted_rows.append(extracted_row)

    bidirectional_types = bidirectional_relationship_types(policy)
    normalized_relationships: list[dict[str, Any]] = []
    for candidate in unit.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        verdict, extracted = extraction_matches_for_candidate(candidate, extracted_rows, bidirectional_types)
        if verdict == "supported" and extracted:
            normalized_relationships.append(
                {
                    "trustKey": candidate.get("trustKey"),
                    "relationshipType": candidate.get("relationshipType"),
                    "fromId": candidate.get("fromId"),
                    "toId": candidate.get("toId"),
                    "verdict": "supported",
                    "semanticTrustScore": extracted.get("semanticTrustScore"),
                    "semanticScoreScale": "0-100",
                    "semanticScoreSemantics": "Sentence-level extraction confidence after reading the full source sentence first, then mapping extracted facts back to candidate trust keys.",
                    "confidence": extracted.get("confidence"),
                    "typeMatched": True,
                    "directionMatched": True,
                    "matchedFromNameZhTw": extracted.get("fromNameZhTw"),
                    "matchedToNameZhTw": extracted.get("toNameZhTw"),
                    "fromEvidenceSpanZhTw": extracted.get("fromEvidenceSpanZhTw"),
                    "toEvidenceSpanZhTw": extracted.get("toEvidenceSpanZhTw"),
                    "relationshipCueSpanZhTw": extracted.get("relationshipCueSpanZhTw"),
                    "cueCategory": extracted.get("cueCategory"),
                    "polarity": extracted.get("polarity"),
                    "mismatchReasonZhTw": "",
                    "evidenceSentence": extracted.get("evidenceSentence") or unit.get("sourceSentence"),
                    "rationaleZhTw": extracted.get("rationaleZhTw") or extracted.get("normalizedClaimZhTw") or "",
                    "normalizedClaimZhTw": extracted.get("normalizedClaimZhTw") or "",
                    "stableRelation": extracted.get("stableRelation"),
                    "reviewMode": "sentence-relation-extraction",
                    "canonicalWrites": False,
                }
            )
            continue
        if verdict == "contradicted" and extracted:
            normalized_relationships.append(
                {
                    "trustKey": candidate.get("trustKey"),
                    "relationshipType": candidate.get("relationshipType"),
                    "fromId": candidate.get("fromId"),
                    "toId": candidate.get("toId"),
                    "verdict": "contradicted",
                    "semanticTrustScore": extracted.get("semanticTrustScore"),
                    "semanticScoreScale": "0-100",
                    "semanticScoreSemantics": "Sentence-level extraction confidence after reading the full source sentence first, then mapping extracted facts back to candidate trust keys.",
                    "confidence": extracted.get("confidence"),
                    "typeMatched": True,
                    "directionMatched": False,
                    "matchedFromNameZhTw": extracted.get("fromNameZhTw"),
                    "matchedToNameZhTw": extracted.get("toNameZhTw"),
                    "fromEvidenceSpanZhTw": extracted.get("fromEvidenceSpanZhTw"),
                    "toEvidenceSpanZhTw": extracted.get("toEvidenceSpanZhTw"),
                    "relationshipCueSpanZhTw": extracted.get("relationshipCueSpanZhTw"),
                    "cueCategory": extracted.get("cueCategory"),
                    "polarity": extracted.get("polarity"),
                    "mismatchReasonZhTw": "句子有明確關係，但方向與候選 trustKey 相反。",
                    "evidenceSentence": extracted.get("evidenceSentence") or unit.get("sourceSentence"),
                    "rationaleZhTw": extracted.get("rationaleZhTw") or "",
                    "normalizedClaimZhTw": extracted.get("normalizedClaimZhTw") or "",
                    "stableRelation": extracted.get("stableRelation"),
                    "reviewMode": "sentence-relation-extraction",
                    "canonicalWrites": False,
                }
            )
            continue
        normalized_relationships.append(
            {
                "trustKey": candidate.get("trustKey"),
                "relationshipType": candidate.get("relationshipType"),
                "fromId": candidate.get("fromId"),
                "toId": candidate.get("toId"),
                "verdict": "not_enough_context",
                "semanticTrustScore": 0.0,
                "semanticScoreScale": "0-100",
                "semanticScoreSemantics": "Sentence-level extraction confidence after reading the full source sentence first, then mapping extracted facts back to candidate trust keys.",
                "confidence": 0.0,
                "typeMatched": False,
                "directionMatched": False,
                "matchedFromNameZhTw": candidate.get("fromNameZhTw"),
                "matchedToNameZhTw": candidate.get("toNameZhTw"),
                "fromEvidenceSpanZhTw": "",
                "toEvidenceSpanZhTw": "",
                "relationshipCueSpanZhTw": "",
                "cueCategory": "",
                "polarity": "",
                "mismatchReasonZhTw": "句子沒有抽出可穩定支持此候選的明確關係。",
                "evidenceSentence": compact_text(unit.get("sourceSentence")),
                "rationaleZhTw": "原句未明確表達這條穩定關係，保留為資訊不足。",
                "normalizedClaimZhTw": "",
                "stableRelation": False,
                "reviewMode": "sentence-relation-extraction",
                "canonicalWrites": False,
            }
        )
    return normalized_relationships, extracted_rows


def pair_validation_result_to_relationships(parsed: dict[str, Any], semantic_policy: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = parsed.get("relationships")
    if not isinstance(relationships, list):
        relationships = []
    normalized_relationships: list[dict[str, Any]] = []
    for item in relationships:
        if not isinstance(item, dict):
            continue
        relation = dict(item)
        semantic_score, confidence = semantic_score_pair(relation)
        verdict = str(relation.get("verdict") or "").strip()
        type_matched = optional_bool(relation.get("typeMatched"))
        direction_matched = optional_bool(relation.get("directionMatched"))
        require_strict_pair_check = bool_value(semantic_policy.get("requireStrictPairCheck"), True)
        if require_strict_pair_check and verdict == "supported" and (type_matched is not True or direction_matched is not True):
            relation["originalVerdictBeforeStrictPairCheck"] = verdict
            relation["strictPairCheckBlocked"] = True
            verdict = "uncertain"
            relation["verdict"] = verdict
            semantic_score = min(semantic_score, 60.0)
            confidence = min(confidence, 0.6)
        relation["semanticTrustScore"] = round(semantic_score, 2)
        relation["semanticScoreScale"] = "0-100"
        relation["semanticScoreSemantics"] = "Sentence-level LLM confidence that the preserved source sentence supports the candidate pair-key relationship."
        relation["confidence"] = round(confidence, 4)
        relation["typeMatched"] = type_matched
        relation["directionMatched"] = direction_matched
        normalized_relationships.append(relation)
    return normalized_relationships


def llm_user_payload(unit: dict[str, Any], review_mode: str) -> dict[str, Any]:
    if review_mode != "sentence-relation-extraction":
        return unit
    return {
        "semanticReviewUnitId": unit.get("semanticReviewUnitId"),
        "sentenceHash": unit.get("sentenceHash"),
        "sourceSentence": unit.get("sourceSentence"),
        "sentenceQualityScore": unit.get("sentenceQualityScore"),
        "allowedEntities": unit.get("allowedEntities") or [],
        "allowedRelationshipTypes": unit.get("allowedRelationshipTypes") or [],
        "sourceRefs": unit.get("sourceRefs") or [],
        "reviewMode": review_mode,
        "canonicalWrites": False,
    }


def review_unit_with_llm(unit: dict[str, Any], policy: dict[str, Any], args: argparse.Namespace, review_mode: str) -> dict[str, Any]:
    semantic_policy = semantic_runner_policy(policy, getattr(args, "runner_name", "primary"))
    adapter = resolve_reviewer_adapter(
        preset=args.reviewer_preset or semantic_policy.get("reviewerPreset"),
        provider=args.reviewer_provider or semantic_policy.get("reviewerProvider"),
        api_url=args.api_url or semantic_policy.get("apiUrl"),
        model=args.model or semantic_policy.get("model"),
        timeout_ms=args.timeout_ms if args.timeout_ms is not None else int(number_value(semantic_policy.get("timeoutMs"), 0.0)) or None,
        num_ctx=args.num_ctx if args.num_ctx is not None else int(number_value(semantic_policy.get("numCtx"), 0.0)) or None,
        num_predict=args.num_predict if args.num_predict is not None else int(number_value(semantic_policy.get("numPredict"), 0.0)) or None,
    )
    prompt_text = sentence_extraction_system_prompt_v2() if review_mode == "sentence-relation-extraction" else system_prompt(policy)
    result = adapter.request_json(system_prompt=prompt_text, user_payload=unit)
    parsed = object_map(result.parsedJson)
    relationships = parsed.get("relationships")
    if not isinstance(relationships, list):
        relationships = []
    normalized_relationships: list[dict[str, Any]] = []
    for item in relationships:
        if not isinstance(item, dict):
            continue
        relation = dict(item)
        semantic_score, confidence = semantic_score_pair(relation)
        verdict = str(relation.get("verdict") or "").strip()
        type_matched = optional_bool(relation.get("typeMatched"))
        direction_matched = optional_bool(relation.get("directionMatched"))
        require_strict_pair_check = bool_value(semantic_policy.get("requireStrictPairCheck"), True)
        if require_strict_pair_check and verdict == "supported" and (type_matched is not True or direction_matched is not True):
            relation["originalVerdictBeforeStrictPairCheck"] = verdict
            relation["strictPairCheckBlocked"] = True
            verdict = "uncertain"
            relation["verdict"] = verdict
            semantic_score = min(semantic_score, 60.0)
            confidence = min(confidence, 0.6)
        relation["semanticTrustScore"] = round(semantic_score, 2)
        relation["semanticScoreScale"] = "0-100"
        relation["semanticScoreSemantics"] = (
            "句級語意信任分數，只代表這一句對該 pair-key 關係的支持程度；"
            "不得直接改寫最終信任區 score。"
        )
        relation["confidence"] = round(confidence, 4)
        relation["typeMatched"] = type_matched
        relation["directionMatched"] = direction_matched
        normalized_relationships.append(relation)
    reviewed_keys = [str(item.get("trustKey") or "").strip() for item in unit.get("candidates") or [] if isinstance(item, dict)]
    return {
        **unit,
        "reviewedAt": utc_now(),
        "reviewer": adapter.describe(),
        "reviewMode": "local-llm-semantic-precheck",
        "semanticReviewPerformed": True,
        "reviewedCandidateKeys": reviewed_keys,
        "relationships": normalized_relationships,
        "rawReviewerSummary": result.payloadSummary,
        "canonicalWrites": False,
    }


def review_unit_with_llm_v2(unit: dict[str, Any], policy: dict[str, Any], args: argparse.Namespace, review_mode: str) -> dict[str, Any]:
    semantic_policy = semantic_runner_policy(policy, getattr(args, "runner_name", "primary"))
    adapter = resolve_reviewer_adapter(
        preset=args.reviewer_preset or semantic_policy.get("reviewerPreset"),
        provider=args.reviewer_provider or semantic_policy.get("reviewerProvider"),
        api_url=args.api_url or semantic_policy.get("apiUrl"),
        model=args.model or semantic_policy.get("model"),
        timeout_ms=args.timeout_ms if args.timeout_ms is not None else int(number_value(semantic_policy.get("timeoutMs"), 0.0)) or None,
        num_ctx=args.num_ctx if args.num_ctx is not None else int(number_value(semantic_policy.get("numCtx"), 0.0)) or None,
        num_predict=args.num_predict if args.num_predict is not None else int(number_value(semantic_policy.get("numPredict"), 0.0)) or None,
    )
    prompt_text = sentence_extraction_system_prompt_v2() if review_mode == "sentence-relation-extraction" else system_prompt(policy)
    result = adapter.request_json(system_prompt=prompt_text, user_payload=llm_user_payload(unit, review_mode))
    parsed = object_map(result.parsedJson)
    extracted_rows: list[dict[str, Any]] = []
    if review_mode == "sentence-relation-extraction":
        normalized_relationships, extracted_rows = extraction_result_to_relationships(unit, parsed, policy)
    else:
        normalized_relationships = pair_validation_result_to_relationships(parsed, semantic_policy)
    reviewed_keys = [str(item.get("trustKey") or "").strip() for item in unit.get("candidates") or [] if isinstance(item, dict)]
    return {
        **unit,
        "reviewedAt": utc_now(),
        "reviewer": adapter.describe(),
        "reviewMode": review_mode,
        "semanticReviewPerformed": True,
        "reviewedCandidateKeys": reviewed_keys,
        "relationships": normalized_relationships,
        "extractedRelationships": extracted_rows,
        "rawReviewerSummary": result.payloadSummary,
        "canonicalWrites": False,
    }


def evidence_packets_from_cache(cache_rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    semantic_policy = object_map(policy.get("semanticReview"))
    prompt_version = str(semantic_policy.get("promptVersion") or "relationship-semantic-review.v1")
    min_confidence = number_value(semantic_policy.get("minSupportedConfidence"), 0.78)
    min_trust_score = number_value(semantic_policy.get("minSupportedTrustScore"), min_confidence * 100.0)
    supported_verdicts = set(string_list(semantic_policy.get("supportedVerdicts")) or ["supported"])
    packets: list[dict[str, Any]] = []
    for cache_row in cache_rows:
        if str(cache_row.get("promptVersion") or "") != prompt_version:
            continue
        first_source_ref = first_object(cache_row.get("sourceRefs"))
        allowed_entities = object_list(cache_row.get("allowedEntities"))
        source_refs = object_list(cache_row.get("sourceRefs"))
        source_sentence = compact_text(cache_row.get("sourceSentence"))
        for relation in cache_row.get("relationships") or []:
            if not isinstance(relation, dict):
                continue
            trust_key = str(relation.get("trustKey") or "").strip()
            verdict = str(relation.get("verdict") or "").strip()
            semantic_score, confidence = semantic_score_pair(relation)
            structure_passed, structure_reason = relation_structure_gate(
                relation,
                source_sentence,
                allowed_entities,
                source_refs,
                policy,
            )
            supported = (
                verdict in supported_verdicts
                and confidence >= min_confidence
                and semantic_score >= min_trust_score
                and structure_passed
            )
            if not trust_key:
                continue
            evidence_sentence = relation.get("evidenceSentence") or cache_row.get("sourceSentence")
            effective_verdict = "supported" if supported else ("not_enough_context" if verdict in supported_verdicts else verdict)
            packet = {
                "trustKey": trust_key,
                "reviewStage": "semantic-precheck",
                "reviewVerdict": verdict,
                "verdict": effective_verdict,
                "semanticReviewPerformed": True,
                "externalLookupPerformed": False,
                "checkedExternalSources": False,
                "confidence": round(confidence, 4),
                "semanticTrustScore": round(semantic_score, 2),
                "semanticScoreScale": "0-100",
                "semanticScoreThreshold": min_trust_score,
                "semanticScoreSemantics": "句級語意信任分數；僅供後續 skill review 排序、快取與閘門使用，不直接寫入最終信任區 score。",
                "typeMatched": relation.get("typeMatched"),
                "directionMatched": relation.get("directionMatched"),
                "matchedFromNameZhTw": relation.get("matchedFromNameZhTw"),
                "matchedToNameZhTw": relation.get("matchedToNameZhTw"),
                "mismatchReasonZhTw": relation.get("mismatchReasonZhTw"),
                "fromEvidenceSpanZhTw": relation.get("fromEvidenceSpanZhTw"),
                "toEvidenceSpanZhTw": relation.get("toEvidenceSpanZhTw"),
                "relationshipCueSpanZhTw": relation.get("relationshipCueSpanZhTw"),
                "cueCategory": relation.get("cueCategory"),
                "polarity": relation.get("polarity"),
                "strictPairCheckBlocked": bool_value(relation.get("strictPairCheckBlocked")),
                "semanticStructureGatePassed": structure_passed,
                "semanticStructureGateReason": structure_reason,
                "semanticReviewUnitId": cache_row.get("semanticReviewUnitId"),
                "sentenceHash": cache_row.get("sentenceHash"),
                "reviewer": cache_row.get("reviewer"),
                "evidenceBasis": [
                    {
                        "basisVerdict": effective_verdict,
                        "basisVerdictZhTw": relation.get("rationaleZhTw") or "",
                        "semanticTrustScore": round(semantic_score, 2),
                        "confidence": round(confidence, 4),
                        "sourceId": first_source_ref.get("sourceId"),
                        "sourceFamily": first_source_ref.get("sourceFamily"),
                        "sourceLayer": first_source_ref.get("sourceLayer"),
                        "locator": first_source_ref.get("locator"),
                        "url": first_source_ref.get("url"),
                        "evidenceRefs": first_source_ref.get("evidenceRefs") or [],
                        "originalSentence": evidence_sentence,
                        "typeMatched": relation.get("typeMatched"),
                        "directionMatched": relation.get("directionMatched"),
                        "matchedFromNameZhTw": relation.get("matchedFromNameZhTw"),
                        "matchedToNameZhTw": relation.get("matchedToNameZhTw"),
                        "mismatchReasonZhTw": relation.get("mismatchReasonZhTw"),
                        "fromEvidenceSpanZhTw": relation.get("fromEvidenceSpanZhTw"),
                        "toEvidenceSpanZhTw": relation.get("toEvidenceSpanZhTw"),
                        "relationshipCueSpanZhTw": relation.get("relationshipCueSpanZhTw"),
                        "cueCategory": relation.get("cueCategory"),
                        "polarity": relation.get("polarity"),
                        "strictPairCheckBlocked": bool_value(relation.get("strictPairCheckBlocked")),
                        "semanticStructureGatePassed": structure_passed,
                        "semanticStructureGateReason": structure_reason,
                        "semanticReviewPerformed": True,
                        "externalLookupPerformed": False,
                        "canonicalWrites": False,
                    }
                ],
                "canonicalWrites": False,
            }
            packets.append(packet)
    return packets


def render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# 關係語意預審快取摘要",
        "",
        f"- 產生時間：`{summary['generatedAt']}`",
        f"- 模式：`{summary['mode']}`",
        f"- 候選句單元：`{summary['candidateUnitCount']}`",
        f"- 已命中快取：`{summary['cacheHitUnitCount']}`",
        f"- 待審句單元：`{summary['queuedUnitCount']}`",
        f"- 本輪新增快取：`{summary['newCacheRowCount']}`",
        f"- 輸出 evidence packet：`{summary['evidencePacketCount']}`",
        f"- 語意分數分布：`{summary.get('semanticScoreBandCounts', {})}`",
        "- 原則：deterministic 只縮小範圍；語意預審結果仍為 proposal-only，canonicalWrites=false。",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cached local-LLM semantic prechecks for relationship review units.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--runner-name", default="primary", help="Semantic runner profile name: primary or secondary.")
    parser.add_argument("--fact-check", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--cache", default="")
    parser.add_argument("--queue-out", default="")
    parser.add_argument("--evidence-out", default="")
    parser.add_argument("--summary-out", default="")
    parser.add_argument("--focus-general-ids-file", default="", help="Optional UTF-8 text file with one generalId per line.")
    parser.add_argument("--execute", action="store_true", help="Call the configured local reviewer for uncached units.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum uncached units to execute or queue. 0 means no limit.")
    parser.add_argument("--reviewer-preset", default="")
    parser.add_argument("--reviewer-provider", default="")
    parser.add_argument("--review-mode", default="", help="Override semantic review mode: pair-validation or sentence-relation-extraction.")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--flush-every", type=int, default=1, help="Flush cache every N reviewed units during --execute. 0 flushes only at the end.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy)
    policy = read_json(policy_path)
    inputs = object_map(policy.get("inputs"))
    outputs = object_map(policy.get("outputs"))
    semantic_policy = semantic_runner_policy(policy, args.runner_name)
    review_mode = semantic_review_mode(policy, args)
    output_root = resolve_path(args.output_root or str(outputs.get("outputRoot") or ""))
    output_root_overridden = bool(str(args.output_root or "").strip())
    fact_check_path = resolve_path(args.fact_check or str(output_root / str(outputs.get("factCheckFileName") or "")))
    cache_path = resolve_path(args.cache or str(output_root / str(semantic_policy.get("cacheFileName") or "relationship-trust-zone.semantic-review-cache.jsonl")))
    queue_path = resolve_path(args.queue_out or str(output_root / str(semantic_policy.get("queueFileName") or "relationship-trust-zone.semantic-review-queue.jsonl")))
    default_evidence_path = output_root / str(semantic_policy.get("evidenceFileName") or "relationship-trust-zone.semantic-review-evidence.jsonl")
    input_evidence_path_text = str(inputs.get("semanticReviewEvidencePath") or "").strip()
    if str(args.evidence_out or "").strip():
        evidence_path = resolve_path(args.evidence_out)
    elif output_root_overridden:
        evidence_path = resolve_path(default_evidence_path)
    else:
        use_input_evidence_path = str(semantic_policy.get("runnerName") or "primary").strip().lower() != "secondary" and bool(input_evidence_path_text)
        evidence_path = resolve_path(input_evidence_path_text) if use_input_evidence_path else resolve_path(default_evidence_path)
    summary_path = resolve_path(args.summary_out or str(output_root / str(semantic_policy.get("summaryFileName") or "relationship-trust-zone.semantic-review-summary.json")))
    summary_md_path = summary_path.with_suffix(".md")
    stable_bootstrap_path_text = str(inputs.get("stableBootstrapPath") or "").strip()
    stable_bootstrap_path = resolve_path(stable_bootstrap_path_text) if stable_bootstrap_path_text else Path("")
    formal_mention_map_path_text = str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json").strip()
    formal_mention_map_path = resolve_path(formal_mention_map_path_text) if formal_mention_map_path_text else Path("")
    general_alias_records_path_text = str(inputs.get("generalAliasRecordsPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json").strip()
    general_alias_records_path = resolve_path(general_alias_records_path_text) if general_alias_records_path_text else Path("")
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    general_alias_records = read_json(general_alias_records_path) if general_alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, general_alias_records)
    scoped_ambiguous_alias_map = build_general_scoped_ambiguous_alias_map(general_alias_records)

    rows = read_jsonl(fact_check_path)
    focus_general_ids_path = resolve_path(args.focus_general_ids_file) if str(args.focus_general_ids_file).strip() else Path("")
    focus_general_ids = load_focus_general_ids(focus_general_ids_path) if focus_general_ids_path else set()
    human_decisions_path_text = str(inputs.get("humanReviewDecisionsPath") or "").strip()
    human_decisions_path = resolve_path(human_decisions_path_text) if human_decisions_path_text else Path("")
    human_sets = read_human_decision_sets(human_decisions_path, policy) if human_decisions_path_text else {"whitelist": set(), "blacklist": set(), "removed": set()}
    source_row_count = len(rows)
    if focus_general_ids:
        rows = [row for row in rows if row_matches_focus_general_ids(row, focus_general_ids)]
        rows, focus_gap_index = apply_focus_gap_filter(rows, policy, focus_general_ids, human_sets)
    else:
        focus_gap_index = {}
    units = build_review_units(
        rows,
        policy,
        name_map=name_map,
        alias_map=alias_map,
        scoped_ambiguous_alias_map=scoped_ambiguous_alias_map,
        review_mode=review_mode,
    )
    cache = load_cache(cache_path)
    queued = [unit for unit in units if unit_needs_review(unit, cache)]
    page_shape_policy = semantic_page_shape_policy(policy)
    suppressed_units = [unit for unit in queued if bool_value(unit.get("pageShapeSuppressed"))]
    if bool_value(page_shape_policy.get("excludeSuppressedFromQueue"), False):
        queued = [unit for unit in queued if not bool_value(unit.get("pageShapeSuppressed"))]
    if args.limit > 0:
        queued = queued[: args.limit]

    new_cache_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    retry_units: list[dict[str, Any]] = []
    flush_every = max(0, int(args.flush_every))
    if args.execute:
        for unit in queued:
            try:
                reviewed = review_unit_with_llm_v2(unit, policy, args, review_mode)
            except Exception as exc:  # noqa: BLE001 - review queue must survive local model outages.
                retry_unit = dict(unit)
                retry_unit["lastSemanticReviewError"] = str(exc)[:500]
                retry_unit["lastSemanticReviewErrorAt"] = utc_now()
                retry_unit["retryRecommended"] = True
                retry_units.append(retry_unit)
                errors.append(
                    {
                        "semanticReviewUnitId": unit.get("semanticReviewUnitId"),
                        "error": str(exc)[:500],
                        "canonicalWrites": False,
                    }
                )
                continue
            cache[str(reviewed.get("semanticReviewUnitId"))] = reviewed
            new_cache_rows.append(reviewed)
            if flush_every and len(new_cache_rows) % flush_every == 0:
                write_jsonl(cache_path, sorted(cache.values(), key=lambda item: str(item.get("semanticReviewUnitId") or "")))

    queue_rows = retry_units if args.execute else queued
    write_jsonl(queue_path, queue_rows)
    write_jsonl(cache_path, sorted(cache.values(), key=lambda item: str(item.get("semanticReviewUnitId") or "")))
    evidence_packets = evidence_packets_from_cache(list(cache.values()), policy)
    write_jsonl(evidence_path, evidence_packets)

    candidate_type_counts = Counter()
    for unit in units:
        for candidate in unit.get("candidates") or []:
            if isinstance(candidate, dict):
                candidate_type_counts[str(candidate.get("relationshipType") or "")] += 1
    semantic_score_band_counts = Counter()
    for packet in evidence_packets:
        semantic_score_band_counts[semantic_score_band(number_value(packet.get("semanticTrustScore")))] += 1
    page_shape_category_counts = Counter(str(unit.get("pageShapeCategory") or "") for unit in units)
    queued_page_shape_category_counts = Counter(str(unit.get("pageShapeCategory") or "") for unit in queue_rows)
    queued_quality_scores = [number_value(unit.get("sentenceQualityScore")) for unit in queue_rows]
    summary = {
        "mode": "execute-local-llm" if args.execute else "queue-only",
        "runnerName": str(args.runner_name or "primary").strip().lower(),
        "reviewMode": review_mode,
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "factCheckPath": repo_relative(fact_check_path),
        "focusGeneralIdsFile": repo_relative(focus_general_ids_path) if focus_general_ids_path.exists() else "",
        "focusGeneralIdCount": len(focus_general_ids),
        "sourceFactCheckRowCount": source_row_count,
        "scopedFactCheckRowCount": len(rows),
        "focusGapTrustKeyCount": len(focus_gap_index),
        "generalAliasRecordsPath": repo_relative(general_alias_records_path) if general_alias_records_path.exists() else "",
        "scopedAmbiguousAliasGeneralCount": len(scoped_ambiguous_alias_map),
        "cachePath": repo_relative(cache_path),
        "queuePath": repo_relative(queue_path),
        "evidencePath": repo_relative(evidence_path),
        "candidateUnitCount": len(units),
        "cacheHitUnitCount": len([unit for unit in units if not unit_needs_review(unit, cache)]),
        "queuedUnitCount": len(queue_rows),
        "newCacheRowCount": len(new_cache_rows),
        "evidencePacketCount": len(evidence_packets),
        "candidateRelationshipTypeCounts": dict(sorted(candidate_type_counts.items())),
        "pageShapeCategoryCounts": dict(sorted(page_shape_category_counts.items())),
        "queuedPageShapeCategoryCounts": dict(sorted(queued_page_shape_category_counts.items())),
        "pageShapeSuppressedCandidateCount": len([unit for unit in units if bool_value(unit.get("pageShapeSuppressed"))]),
        "pageShapeSuppressedQueuedCount": len(suppressed_units),
        "semanticScoreBandCounts": dict(sorted(semantic_score_band_counts.items())),
        "queuedSentenceQualityMin": round(min(queued_quality_scores), 3) if queued_quality_scores else None,
        "queuedSentenceQualityMax": round(max(queued_quality_scores), 3) if queued_quality_scores else None,
        "errorCount": len(errors),
        "errors": errors[:20],
        "reviewerProfile": resolve_reviewer_adapter(
            preset=args.reviewer_preset or semantic_policy.get("reviewerPreset"),
            provider=args.reviewer_provider or semantic_policy.get("reviewerProvider"),
            api_url=args.api_url or semantic_policy.get("apiUrl"),
            model=args.model or semantic_policy.get("model"),
            timeout_ms=args.timeout_ms if args.timeout_ms is not None else int(number_value(semantic_policy.get("timeoutMs"), 0.0)) or None,
            num_ctx=args.num_ctx if args.num_ctx is not None else int(number_value(semantic_policy.get("numCtx"), 0.0)) or None,
            num_predict=args.num_predict if args.num_predict is not None else int(number_value(semantic_policy.get("numPredict"), 0.0)) or None,
        ).describe(),
    }
    write_json(summary_path, summary)
    summary_md_path.write_text(render_summary(summary), encoding="utf-8")
    print(
        "[run_relationship_semantic_review_cache] "
        f"mode={summary['mode']} units={len(units)} queue={len(queue_rows)} "
        f"cache={len(cache)} evidence={len(evidence_packets)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
