from __future__ import annotations

import argparse
import html
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = Path("data/sanguo/policies/policy-relationship-trust-zone.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def display_utc_timestamp(iso_text: str) -> str:
    text = str(iso_text or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return f"{parsed.strftime('%Y-%m-%d %H:%M:%S')}（世界標準時間）"
    except ValueError:
        return text.replace("T", " ")


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
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


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


def stable_hash(*parts: Any, length: int = 18) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


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


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def trim_text(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 0)] + "..."


def sanitize_human_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[A-Za-z][A-Za-z0-9_-]{1,}", " ", text)
    text = re.sub(r"\b\d+[A-Za-z][A-Za-z0-9_-]*\b", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def sanitize_instruction_zh_tw(value: Any) -> str:
    text = str(value or "").strip()
    replacements = {
        "anchor": "錨點",
        "Anchor": "錨點",
        "skill review": "技能審查",
        "skill-review": "技能審查",
        "canonical": "正式主資料",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


def policy_label(policy: dict[str, Any], section: str, key: Any, fallback: str = "-") -> str:
    labels = object_map(policy.get(section))
    text = str(labels.get(str(key or "").strip()) or "").strip()
    return text or str(key or fallback or "-")


def contains_ascii_word(value: Any) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", str(value or "")))


def contains_cjk_char(value: Any) -> bool:
    text = str(value or "")
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def decode_romance_person_id(entity_id: Any) -> str:
    text = str(entity_id or "").strip()
    if not text.startswith("romance-person-"):
        return ""
    parts = text.split("-")[2:]
    if not parts:
        return ""
    chars: list[str] = []
    for part in parts:
        if not re.fullmatch(r"[0-9a-fA-F]{4,6}", part):
            return ""
        try:
            chars.append(chr(int(part, 16)))
        except ValueError:
            return ""
    candidate = "".join(chars).strip()
    return candidate if contains_cjk_char(candidate) else ""


def anonymized_entity_label(entity_id: Any) -> str:
    raw = str(entity_id or "").strip()
    if not raw:
        return "未知角色000000"
    code = int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16) % 1_000_000
    return f"未知角色{code:06d}"


def trust_code(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "審核代碼000000"
    code = int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16) % 1_000_000
    return f"審核代碼{code:06d}"


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


def build_name_map(stable_bootstrap: dict[str, Any], formal_mention_map: dict[str, Any]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for section in ("identitySeeds", "basicProfileSeeds", "femalePriorityProfiles"):
        rows = stable_bootstrap.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("generalId") or "").strip()
            name = str(row.get("name") or "").strip()
            if gid and name:
                name_map[gid] = name
    formal_names = build_formal_mention_name_map(formal_mention_map)
    for gid, name in formal_names.items():
        name_map.setdefault(gid, name)
    return name_map


def display_name(entity_id: Any, name_map: dict[str, str]) -> str:
    key = str(entity_id or "").strip()
    if not key:
        return "-"
    mapped = str(name_map.get(key) or "").strip()
    if mapped:
        return mapped
    romance_name = decode_romance_person_id(key)
    if romance_name:
        return romance_name
    return anonymized_entity_label(key)


def semantic_verdict_label(verdict: Any) -> str:
    normalized = str(verdict or "").strip().lower()
    if normalized == "supported":
        return "支持"
    if normalized == "contradicted":
        return "反證"
    if normalized == "uncertain":
        return "不確定"
    if normalized == "not_enough_context":
        return "資訊不足"
    return normalized or "-"


def claim_sentence_display(row: dict[str, Any], policy: dict[str, Any], name_map: dict[str, str]) -> str:
    rel_type = str(row.get("relationshipType") or "").strip()
    templates = object_map(policy.get("claimSentenceTemplatesZhTw"))
    template = str(templates.get(rel_type) or "").strip()
    if not template:
        return str(row.get("claimSentenceZhTw") or "").strip()
    values = {
        "fromName": display_name(row.get("fromId"), name_map),
        "toName": display_name(row.get("toId"), name_map),
        "subjectName": display_name(row.get("subjectId"), name_map),
        "controllerName": display_name(row.get("controllerId"), name_map),
    }
    try:
        return template.format(**values)
    except Exception:
        return str(row.get("claimSentenceZhTw") or "").strip()


def source_label_zh_tw(preview: dict[str, Any], policy: dict[str, Any]) -> str:
    source_id = str(preview.get("sourceId") or "").strip()
    source_family = str(preview.get("sourceFamily") or "").strip()
    source_layer = str(preview.get("sourceLayer") or "").strip()
    for key in (source_id, source_family, source_layer):
        if not key:
            continue
        label = policy_label(policy, "sourceLabelsZhTw", key, fallback="")
        if label and not contains_ascii_word(label):
            return label
    fingerprint = " ".join([source_id.lower(), source_family.lower(), source_layer.lower()])
    if any(token in fingerprint for token in ("sanguoyanyi", "romance", "baihua")):
        return "《三國演義》相關資料"
    if any(token in fingerprint for token in ("sanguozhi", "houhanshu", "zizhitongjian", "history")):
        return "正史或歷史資料"
    return "外部參考資料"


def locator_summary_zh_tw(preview: dict[str, Any]) -> str:
    raw = str(
        preview.get("locator")
        or ", ".join(string_list(preview.get("evidenceRefs")))
        or preview.get("url")
        or ""
    ).strip()
    if not raw:
        return "-"
    chapter_match = re.search(r"chapter[-_](\d+)", raw, flags=re.IGNORECASE)
    paragraph_match = re.search(r"#p(\d+)", raw, flags=re.IGNORECASE)
    sentence_match = re.search(r"sentence[=:](\d+)", raw, flags=re.IGNORECASE)
    if chapter_match or paragraph_match or sentence_match:
        parts: list[str] = []
        if chapter_match:
            parts.append(f"第{int(chapter_match.group(1))}回")
        if paragraph_match:
            parts.append(f"第{int(paragraph_match.group(1))}段")
        if sentence_match:
            parts.append(f"句位{int(sentence_match.group(1))}")
        return "、".join(parts) if parts else "已記錄定位"
    if contains_cjk_char(raw):
        return trim_text(raw, 48)
    return "定位已存於明細檔"


def decision_options(policy: dict[str, Any]) -> list[str]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    return string_list(round_policy.get("decisionOptionsZhTw")) or ["待審", "通過", "打叉", "保留待查"]


def default_decision(policy: dict[str, Any]) -> str:
    round_policy = object_map(policy.get("skillReviewRounds"))
    value = str(round_policy.get("defaultDecisionZhTw") or "").strip()
    return value or decision_options(policy)[0]


def output_path_from_template(root: Path, template: str, round_no: int) -> Path:
    return root / template.format(roundNo=f"{round_no:03d}", roundIndex=round_no)


def read_review_trust_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    candidate_files = [path] if path.is_file() else sorted(path.rglob("relationship-trust-zone-skill-review-*.jsonl"))
    keys: set[str] = set()
    for candidate_file in candidate_files:
        for row in read_jsonl(candidate_file):
            trust_key = str(row.get("trustKey") or "").strip()
            if trust_key:
                keys.add(trust_key)
    return keys


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


def relationship_dimension_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(policy.get("relationshipDimension"))


def expand_bidirectional_decision_keys(keys: set[str], policy: dict[str, Any]) -> set[str]:
    relation_policy = relationship_dimension_policy(policy)
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


def directed_relationship_types(policy: dict[str, Any]) -> set[str]:
    relation_policy = relationship_dimension_policy(policy)
    stable_types = set(string_list(relation_policy.get("stableRelationshipTypes")))
    bidirectional_types = set(string_list(relation_policy.get("bidirectionalRelationshipTypes")))
    return stable_types - bidirectional_types


def inverse_relationship_trust_key(row: dict[str, Any]) -> str:
    rel_type = str(row.get("relationshipType") or "").strip()
    from_id = str(row.get("fromId") or "").strip()
    to_id = str(row.get("toId") or "").strip()
    if not rel_type or not from_id or not to_id:
        return ""
    return f"relationship|{rel_type}|{to_id}|{from_id}"


def direction_group_key(row: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str, str] | None:
    rel_type = str(row.get("relationshipType") or "").strip()
    if rel_type not in directed_relationship_types(policy):
        return None
    from_id = str(row.get("fromId") or "").strip()
    to_id = str(row.get("toId") or "").strip()
    if not from_id or not to_id or from_id == to_id:
        return None
    first, second = sorted([from_id, to_id])
    return (rel_type, first, second)


def semantic_policy(policy: dict[str, Any]) -> dict[str, Any]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    semantic_round_policy = object_map(round_policy.get("semanticPriority"))
    semantic_root_policy = object_map(policy.get("semanticReview"))
    merged = dict(semantic_root_policy)
    merged.update(semantic_round_policy)
    return merged


def semantic_consensus_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return object_map(semantic_policy(policy).get("consensus"))


def read_semantic_packets(path: Path) -> dict[str, list[dict[str, Any]]]:
    packets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(path):
        trust_key = str(row.get("trustKey") or "").strip()
        if trust_key:
            packets[trust_key].append(row)
    return packets


def best_semantic_packet(packets: list[dict[str, Any]]) -> dict[str, Any]:
    if not packets:
        return {}

    def rank(packet: dict[str, Any]) -> tuple[int, float]:
        verdict = str(packet.get("verdict") or packet.get("reviewVerdict") or "").strip()
        score = number_value(packet.get("semanticTrustScore"))
        if verdict == "supported":
            return (3, score)
        if verdict == "contradicted":
            return (2, score)
        return (1, score)

    return max(packets, key=rank)


def semantic_profile(trust_key: str, semantic_packets: dict[str, list[dict[str, Any]]], policy: dict[str, Any]) -> dict[str, Any]:
    packet = best_semantic_packet(semantic_packets.get(trust_key, []))
    semantic = semantic_policy(policy)
    if not packet:
        return {
            "status": "not-reviewed",
            "statusZhTw": "尚未語意預審",
            "semanticTrustScore": None,
            "semanticVerdict": "",
            "semanticReviewUnitId": "",
            "rationaleZhTw": "",
            "canonicalWrites": False,
        }

    verdict = str(packet.get("verdict") or packet.get("reviewVerdict") or "").strip()
    score = number_value(packet.get("semanticTrustScore"))
    preferred_min = number_value(semantic.get("preferredMinTrustScore"), number_value(semantic.get("skillReviewPreferredMinTrustScore"), 90.0))
    supported_min = number_value(semantic.get("supportedMinTrustScore"), number_value(semantic.get("minSupportedTrustScore"), 80.0))
    residual_max = number_value(semantic.get("residualMaxTrustScore"), 60.0)
    blacklist_verdicts = set(string_list(semantic.get("blacklistCandidateVerdicts")))
    residual_verdicts = set(string_list(semantic.get("residualVerdicts")))

    if verdict in blacklist_verdicts:
        status, label = "blacklist-candidate", "黑名單候選"
    elif verdict in residual_verdicts or score <= residual_max:
        status, label = "residual-candidate", "殘留待查"
    elif verdict == "supported" and score >= preferred_min:
        status, label = "preferred-for-skill-review", "優先進入技能審查"
    elif verdict == "supported" and score >= supported_min:
        status, label = "supported-for-review", "可進入審查"
    else:
        status, label = "semantic-reviewed", "已語意預審"

    basis = packet.get("evidenceBasis")
    first_basis = basis[0] if isinstance(basis, list) and basis and isinstance(basis[0], dict) else {}
    return {
        "status": status,
        "statusZhTw": label,
        "semanticTrustScore": round(score, 3),
        "semanticVerdict": verdict,
        "semanticReviewUnitId": packet.get("semanticReviewUnitId"),
        "rationaleZhTw": first_basis.get("basisVerdictZhTw") or packet.get("rationaleZhTw") or "",
        "evidenceSentence": first_basis.get("originalSentence") or packet.get("evidenceSentence") or "",
        "sourceId": first_basis.get("sourceId"),
        "sourceFamily": first_basis.get("sourceFamily"),
        "sourceLayer": first_basis.get("sourceLayer"),
        "locator": first_basis.get("locator"),
        "url": first_basis.get("url"),
        "canonicalWrites": False,
    }


def semantic_profile_with_consensus(
    trust_key: str,
    semantic_packets: dict[str, list[dict[str, Any]]],
    secondary_semantic_packets: dict[str, list[dict[str, Any]]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    profile = semantic_profile(trust_key, semantic_packets, policy)
    consensus = semantic_consensus_policy(policy)
    if not bool_value(consensus.get("enabled"), False):
        return profile

    secondary_profile = semantic_profile(trust_key, secondary_semantic_packets, policy)
    profile["secondarySemanticReview"] = secondary_profile
    profile["consensusRequired"] = True

    primary_status = str(profile.get("status") or "")
    secondary_status = str(secondary_profile.get("status") or "")
    supported_statuses = set(
        string_list(consensus.get("supportedStatuses"))
        or ["preferred-for-skill-review", "supported-for-review"]
    )
    primary_supported = primary_status in supported_statuses
    secondary_supported = secondary_status in supported_statuses
    preferred_min = number_value(
        consensus.get("preferredMinTrustScore"),
        number_value(semantic_policy(policy).get("preferredMinTrustScore"), 90.0),
    )
    supported_min = number_value(
        consensus.get("supportedMinTrustScore"),
        number_value(semantic_policy(policy).get("supportedMinTrustScore"), 80.0),
    )
    primary_score = number_value(profile.get("semanticTrustScore"), -1.0)
    secondary_score = number_value(secondary_profile.get("semanticTrustScore"), -1.0)
    consensus_score = min(primary_score, secondary_score) if primary_score >= 0.0 and secondary_score >= 0.0 else -1.0

    if str(secondary_profile.get("semanticVerdict") or "") == "contradicted":
        profile["status"] = "blacklist-candidate"
        profile["statusZhTw"] = "黑名單候選"
        profile["consensusStatusZhTw"] = "第二模型反證"
        profile["consensusGateReasonZhTw"] = "第二模型給出反證，暫列黑名單候選。"
        return profile
    if primary_supported and secondary_supported:
        if consensus_score >= preferred_min:
            profile["status"] = "preferred-for-skill-review"
            profile["statusZhTw"] = "優先進入技能審查"
            profile["consensusStatusZhTw"] = "雙模型支持"
            profile["consensusGateReasonZhTw"] = "第一模型與第二模型都支持，允許進入 95 分人工審核表。"
        elif consensus_score >= supported_min:
            profile["status"] = "supported-for-review"
            profile["statusZhTw"] = "可進入審查"
            profile["consensusStatusZhTw"] = "雙模型支持"
            profile["consensusGateReasonZhTw"] = "兩個模型都支持，但共識分數仍低於優先門檻。"
        else:
            profile["status"] = "residual-candidate"
            profile["statusZhTw"] = "保留待查"
            profile["consensusStatusZhTw"] = "雙模型支持但分數不足"
            profile["consensusGateReasonZhTw"] = "兩個模型都支持，但共識分數不足以進入審核表。"
        return profile
    if primary_supported and not secondary_profile.get("semanticReviewUnitId"):
        profile["status"] = "residual-candidate"
        profile["statusZhTw"] = "保留待查"
        profile["consensusStatusZhTw"] = "缺少第二模型"
        profile["consensusGateReasonZhTw"] = "第一模型支持，但尚未取得第二模型共識。"
        return profile
    if primary_supported and not secondary_supported:
        profile["status"] = "residual-candidate"
        profile["statusZhTw"] = "保留待查"
        profile["consensusStatusZhTw"] = "第二模型未支持"
        profile["consensusGateReasonZhTw"] = "第一模型支持，但第二模型未支持，因此不得進入 95 分審核表。"
        return profile
    profile["consensusStatusZhTw"] = "未形成雙模型支持"
    return profile


def candidate_needs_lookup(row: dict[str, Any]) -> bool:
    fact_check = object_map(row.get("factCheck"))
    return not bool_value(fact_check.get("externalLookupPerformed")) or not bool_value(fact_check.get("verified"))


def is_residual_or_blacklist(profile: dict[str, Any]) -> bool:
    return str(profile.get("status") or "") in {"residual-candidate", "blacklist-candidate"}


def with_review_gate(row: dict[str, Any], *, status: str, label: str, reason: str) -> dict[str, Any]:
    enriched = dict(row)
    profile = dict(object_map(enriched.get("semanticReview")))
    profile.update(
        {
            "status": status,
            "statusZhTw": label,
            "gateReasonZhTw": reason,
            "canonicalWrites": False,
        }
    )
    enriched["semanticReview"] = profile
    return enriched


def apply_human_and_direction_gates(
    candidates: list[dict[str, Any]],
    residual_rows: list[dict[str, Any]],
    blacklist_rows: list[dict[str, Any]],
    policy: dict[str, Any],
    human_sets: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    whitelist_keys = set(human_sets.get("whitelist") or set()) - set(human_sets.get("removed") or set())
    blacklist_keys = set(human_sets.get("blacklist") or set()) - set(human_sets.get("removed") or set())
    kept: list[dict[str, Any]] = []
    gate_counts = Counter()

    for row in candidates:
        trust_key = str(row.get("trustKey") or "").strip()
        inverse_key = inverse_relationship_trust_key(row)
        if trust_key in whitelist_keys:
            gate_counts["human-whitelist-skip"] += 1
            continue
        if trust_key in blacklist_keys:
            blacklist_rows.append(
                with_review_gate(
                    row,
                    status="human-blacklisted",
                    label="人工黑名單已固定",
                    reason="這個內部關係鍵已被人工打叉，後續不再送審。",
                )
            )
            gate_counts["human-blacklist-block"] += 1
            continue
        if inverse_key and inverse_key in whitelist_keys:
            residual_rows.append(
                with_review_gate(
                    row,
                    status="inverse-whitelist-conflict",
                    label="反向已有白名單",
                    reason="相反方向的關係已被人工固定，此方向需先回查來源，不進入本輪審核。",
                )
            )
            gate_counts["inverse-whitelist-residual"] += 1
            continue
        kept.append(row)

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in kept:
        group_key = direction_group_key(row, policy)
        if group_key:
            groups[group_key].append(row)
    conflicted_keys: set[str] = set()
    for group_rows in groups.values():
        directions = {(str(row.get("fromId") or ""), str(row.get("toId") or "")) for row in group_rows}
        if len(directions) < 2:
            continue
        for row in group_rows:
            trust_key = str(row.get("trustKey") or "").strip()
            if trust_key:
                conflicted_keys.add(trust_key)

    if conflicted_keys:
        next_kept: list[dict[str, Any]] = []
        for row in kept:
            trust_key = str(row.get("trustKey") or "").strip()
            if trust_key in conflicted_keys:
                residual_rows.append(
                    with_review_gate(
                        row,
                        status="direction-conflict-candidate",
                        label="方向衝突待查",
                        reason="同一組人物的有方向關係同時出現正反兩邊，需先由上游語意審查釐清主客方向。",
                    )
                )
                gate_counts["direction-conflict-residual"] += 1
                continue
            next_kept.append(row)
        kept = next_kept

    return kept, residual_rows, blacklist_rows, dict(gate_counts)


def eligible_candidates(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    semantic_packets: dict[str, list[dict[str, Any]]],
    secondary_semantic_packets: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    semantic = semantic_policy(policy)
    stages = set(string_list(round_policy.get("candidateStages")))
    excluded_types = set(string_list(round_policy.get("excludeRelationshipTypes")))
    require_semantic_before_queue = bool_value(
        semantic.get("requireSemanticReviewBeforeQueue"),
        True,
    )
    candidates: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    blacklist_rows: list[dict[str, Any]] = []
    for row in rows:
        rel_type = str(row.get("relationshipType") or "").strip()
        if excluded_types and rel_type in excluded_types:
            continue
        if stages and str(row.get("zone") or "").strip() not in stages:
            continue
        if not candidate_needs_lookup(row):
            continue
        enriched = dict(row)
        profile = semantic_profile_with_consensus(
            str(row.get("trustKey") or ""),
            semantic_packets,
            secondary_semantic_packets,
            policy,
        )
        enriched["semanticReview"] = profile
        if profile.get("status") == "blacklist-candidate":
            blacklist_rows.append(enriched)
            continue
        if require_semantic_before_queue and profile.get("status") == "not-reviewed":
            residual_rows.append(enriched)
            continue
        if profile.get("status") == "residual-candidate":
            residual_rows.append(enriched)
            continue
        candidates.append(enriched)
    return candidates, residual_rows, blacklist_rows


def semantic_sort_rank(row: dict[str, Any]) -> tuple[int, float]:
    profile = object_map(row.get("semanticReview"))
    status = str(profile.get("status") or "")
    score = number_value(profile.get("semanticTrustScore"), -1.0)
    if status == "preferred-for-skill-review":
        return (0, -score)
    if status == "supported-for-review":
        return (1, -score)
    if status == "not-reviewed":
        return (2, 0.0)
    return (3, -score)


def sorted_candidates(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    preference = string_list(round_policy.get("preferRelationshipTypes"))
    priority = {rel_type: index for index, rel_type in enumerate(preference)}
    fallback = len(priority) + 100
    return sorted(
        rows,
        key=lambda row: (
            -number_value(row.get("focusQueuePriority")),
            *semantic_sort_rank(row),
            priority.get(str(row.get("relationshipType") or ""), fallback),
            -number_value(row.get("score")),
            str(row.get("trustKey") or ""),
        ),
    )


def pick_round_candidates(candidates: list[dict[str, Any]], policy: dict[str, Any], *, round_count: int, items_per_round: int) -> list[list[dict[str, Any]]]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    max_per_type = int(number_value(round_policy.get("maxPerRelationshipTypePerRoundDefault"), 0.0))
    ordered = sorted_candidates(candidates, policy)
    remaining_by_key = {str(row.get("trustKey") or stable_hash(row)): row for row in ordered}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        buckets[str(row.get("relationshipType") or "")].append(row)
    preference = string_list(round_policy.get("preferRelationshipTypes"))
    type_order = preference + sorted(set(buckets) - set(preference))

    rounds: list[list[dict[str, Any]]] = []
    for _round_no in range(1, round_count + 1):
        selected: list[dict[str, Any]] = []
        per_type_counts: Counter[str] = Counter()
        if str(round_policy.get("selectionMode") or "") == "balanced-by-relationship-type":
            progressed = True
            while progressed and len(selected) < items_per_round:
                progressed = False
                for rel_type in type_order:
                    if len(selected) >= items_per_round:
                        break
                    if max_per_type > 0 and per_type_counts[rel_type] >= max_per_type:
                        continue
                    while buckets[rel_type]:
                        row = buckets[rel_type].pop(0)
                        key = str(row.get("trustKey") or stable_hash(row))
                        if key not in remaining_by_key:
                            continue
                        selected.append(row)
                        per_type_counts[rel_type] += 1
                        remaining_by_key.pop(key, None)
                        progressed = True
                        break
        if len(selected) < items_per_round:
            for row in ordered:
                if len(selected) >= items_per_round:
                    break
                key = str(row.get("trustKey") or stable_hash(row))
                if key not in remaining_by_key:
                    continue
                selected.append(row)
                remaining_by_key.pop(key, None)
        rounds.append(selected)
    return rounds


def evidence_previews(row: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    max_items = int(number_value(round_policy.get("maxExistingEvidenceItems"), 3.0))
    max_chars = int(number_value(round_policy.get("maxQuoteChars"), 220.0))
    previews = row.get("sourceQuotePreviews")
    items: list[dict[str, Any]] = []
    if isinstance(previews, list):
        for preview in previews:
            if not isinstance(preview, dict):
                continue
            quote = sanitize_human_text(trim_text(preview.get("quote"), max_chars))
            if not quote:
                continue
            if not contains_cjk_char(quote):
                continue
            if len(quote) < 6:
                continue
            source = source_label_zh_tw(preview, policy)
            layer = policy_label(policy, "sourceLabelsZhTw", preview.get("sourceLayer"), fallback="")
            if contains_ascii_word(layer):
                layer = ""
            locator = locator_summary_zh_tw(preview)
            items.append(
                {
                    "來源": source,
                    "來源層級": layer,
                    "定位摘要": locator,
                    "原文句": quote,
                    "canonicalWrites": False,
                }
            )
            if len(items) >= max_items:
                break
    return items


def query_previews(row: dict[str, Any], policy: dict[str, Any], name_map: dict[str, str]) -> list[dict[str, Any]]:
    round_policy = object_map(policy.get("skillReviewRounds"))
    max_items = int(number_value(round_policy.get("maxQueryItems"), 4.0))
    rel_label = policy_label(policy, "relationshipLabelsZhTw", row.get("relationshipType"), fallback="關係")
    from_name = display_name(row.get("fromId"), name_map)
    to_name = display_name(row.get("toId"), name_map)
    support_query = f"{from_name} {to_name} {rel_label} 關係 證據"
    contradict_query = f"{from_name} 不是 {to_name} {rel_label} 關係 反證"
    items: list[dict[str, Any]] = [
        {"查詢方向": "支持查詢", "查詢詞": support_query, "查詢目標": ["關係資料站", "錨點語料", "既有知識"]},
        {"查詢方向": "反證查詢", "查詢詞": contradict_query, "查詢目標": ["關係資料站", "錨點語料", "既有知識"]},
    ]
    if max_items > 0:
        items = items[:max_items]
    return items


def build_review_item(row: dict[str, Any], policy: dict[str, Any], name_map: dict[str, str], *, round_no: int, item_index: int) -> dict[str, Any]:
    trust_key = str(row.get("trustKey") or "")
    review_item_id = "relationship-skill-review." + stable_hash(round_no, item_index, trust_key)
    semantic_review = object_map(row.get("semanticReview"))
    return {
        "reviewItemId": review_item_id,
        "reviewRoundNo": round_no,
        "reviewItemNo": item_index,
        "reviewCodeZhTw": trust_code(trust_key),
        "decisionZhTw": default_decision(policy),
        "decisionOptionsZhTw": decision_options(policy),
        "claimSentenceZhTw": claim_sentence_display(row, policy, name_map),
        "relationshipType": row.get("relationshipType"),
        "relationshipTypeZhTw": policy_label(policy, "relationshipLabelsZhTw", row.get("relationshipType")),
        "scoreBeforeReview": row.get("score"),
        "semanticReview": semantic_review,
        "secondarySemanticReview": semantic_review.get("secondarySemanticReview"),
        "semanticTrustScore": semantic_review.get("semanticTrustScore"),
        "semanticVerdict": semantic_review.get("semanticVerdict"),
        "semanticStatusZhTw": semantic_review.get("statusZhTw"),
        "semanticConsensusStatusZhTw": semantic_review.get("consensusStatusZhTw"),
        "trustKey": trust_key,
        "trustZoneId": row.get("trustZoneId"),
        "fromId": row.get("fromId"),
        "toId": row.get("toId"),
        "subjectId": row.get("subjectId"),
        "controllerId": row.get("controllerId"),
        "currentZone": row.get("zone"),
        "existingEvidence": evidence_previews(row, policy),
        "suggestedQueries": query_previews(row, policy, name_map),
        "humanEvidenceToFill": {field: "" for field in string_list(object_map(policy.get("skillReviewRounds")).get("humanEvidenceFieldsZhTw"))},
        "promotionGuardZhTw": "這只是審核佇列。必須有明確查證證據後，才可另寫技能審查證據封包；本檔不會升分或寫入正式主資料。",
        "canonicalWrites": False,
    }


def render_evidence_block(item: dict[str, Any]) -> str:
    evidence = item.get("existingEvidence")
    if not isinstance(evidence, list) or not evidence:
        return "尚無可用原文摘錄"
    parts: list[str] = []
    for index, row in enumerate(evidence, 1):
        if not isinstance(row, dict):
            continue
        source = row.get("來源") or "-"
        locator = row.get("定位摘要") or "-"
        sentence = row.get("原文句") or "-"
        locator_hint = f"（{locator}）" if locator and locator != "-" else ""
        parts.append(f"{index}. {source}{locator_hint}：{sentence}")
    return "；".join(markdown_cell(part) for part in parts) or "尚無可用原文摘錄"


def render_query_block(item: dict[str, Any]) -> str:
    queries = item.get("suggestedQueries")
    if not isinstance(queries, list) or not queries:
        return "尚無建議查詢"
    parts: list[str] = []
    for index, query in enumerate(queries, 1):
        if not isinstance(query, dict):
            continue
        parts.append(f"{index}. {query.get('查詢方向')}: {query.get('查詢詞')}")
    return "；".join(markdown_cell(part) for part in parts) or "尚無建議查詢"


def render_semantic_block(item: dict[str, Any]) -> str:
    semantic = object_map(item.get("semanticReview"))
    score = semantic.get("semanticTrustScore")
    verdict = semantic_verdict_label(semantic.get("semanticVerdict"))
    consensus_status = str(item.get("semanticConsensusStatusZhTw") or "").strip()
    status = semantic.get("statusZhTw") or "尚未語意預審"
    if score is None:
        return markdown_cell(consensus_status) if consensus_status else status
    pieces = [f"{status}", f"分數 {score}", f"判定 {verdict}"]
    if consensus_status:
        pieces.append(f"共識 {consensus_status}")
    return "；".join(markdown_cell(piece) for piece in pieces)


def human_fill_hint(policy: dict[str, Any]) -> str:
    fields = string_list(object_map(policy.get("skillReviewRounds")).get("humanEvidenceFieldsZhTw"))
    return "；".join(markdown_cell(f"{field}：") for field in fields)


def render_round_markdown(items: list[dict[str, Any]], policy: dict[str, Any], *, round_no: int, generated_at: str) -> str:
    round_policy = object_map(policy.get("skillReviewRounds"))
    options = " / ".join(decision_options(policy))
    generated_label = display_utc_timestamp(generated_at)
    lines = [
        f"# 關係技能審查第 {round_no} 輪",
        "",
        f"- 產生時間：`{generated_label}`",
        f"- 本輪項目數：`{len(items)}`",
        "- 本檔是人工/技能審查佇列，不會直接寫入正式主資料。",
        f"- 決策選項：{options}",
    ]
    for instruction in string_list(round_policy.get("reviewInstructionsZhTw")):
        lines.append(f"- {sanitize_instruction_zh_tw(instruction)}")
    lines.extend(
        [
            "",
            "| # | 決策 | 語意預審 | 原信任分 | 關係類型 | 命題句 | 原文或既有證據 | 建議查詢 | 人工查證欄 | 審核代碼 |",
            "|---:|---|---|---:|---|---|---|---|---|---|",
        ]
    )
    fill_hint = human_fill_hint(policy)
    for item in items:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("reviewItemNo") or ""),
                    markdown_cell(item.get("decisionZhTw")),
                    render_semantic_block(item),
                    markdown_cell(item.get("scoreBeforeReview")),
                    markdown_cell(item.get("relationshipTypeZhTw")),
                    markdown_cell(item.get("claimSentenceZhTw")),
                    render_evidence_block(item),
                    render_query_block(item),
                    fill_hint,
                    markdown_cell(item.get("reviewCodeZhTw")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_aggregate_markdown(rounds: list[list[dict[str, Any]]], policy: dict[str, Any], *, generated_at: str) -> str:
    generated_label = display_utc_timestamp(generated_at)
    lines = [
        "# 關係技能審查總表",
        "",
        f"- 產生時間：`{generated_label}`",
        f"- 總輪數：`{len(rounds)}`",
        f"- 總項目數：`{sum(len(round_items) for round_items in rounds)}`",
        "- 語意分數 90 以上會優先進入技能審查；低分或反證項目會另分流到待查清單或黑名單候選檔。",
        "- 人工只需要逐條看命題句、原文、語意預審與外部查證欄位；錯的請打叉。",
        "",
    ]
    for round_no, items in enumerate(rounds, 1):
        lines.extend(
            [
                f"## 第 {round_no} 輪",
                "",
                "| # | 決策 | 語意預審 | 原信任分 | 關係類型 | 命題句 | 原文或既有證據 | 建議查詢 | 審核代碼 |",
                "|---:|---|---|---:|---|---|---|---|---|",
            ]
        )
        for item in items:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("reviewItemNo") or ""),
                        markdown_cell(item.get("decisionZhTw")),
                        render_semantic_block(item),
                        markdown_cell(item.get("scoreBeforeReview")),
                        markdown_cell(item.get("relationshipTypeZhTw")),
                        markdown_cell(item.get("claimSentenceZhTw")),
                        render_evidence_block(item),
                        render_query_block(item),
                        markdown_cell(item.get("reviewCodeZhTw")),
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines)


def residual_item(row: dict[str, Any], policy: dict[str, Any], *, residual_type: str) -> dict[str, Any]:
    semantic_review = object_map(row.get("semanticReview"))
    semantic_status = str(semantic_review.get("status") or "")
    if semantic_status == "direction-conflict-candidate":
        recommended_action = "先回查原文與語意審查，釐清主客方向"
    elif semantic_status == "inverse-whitelist-conflict":
        recommended_action = "相反方向已在白名單，除非有新證據否則不要送審"
    elif semantic_status == "human-blacklisted":
        recommended_action = "已由人工黑名單固定，後續直接視為錯誤"
    else:
        recommended_action = "保留待查" if residual_type == "semantic-residual" else "人工確認後可加入黑名單"
    return {
        "residualType": residual_type,
        "trustKey": row.get("trustKey"),
        "claimSentenceZhTw": row.get("claimSentenceZhTw"),
        "relationshipType": row.get("relationshipType"),
        "relationshipTypeZhTw": policy_label(policy, "relationshipLabelsZhTw", row.get("relationshipType")),
        "scoreBeforeReview": row.get("score"),
        "semanticReview": semantic_review,
        "semanticTrustScore": semantic_review.get("semanticTrustScore"),
        "semanticVerdict": semantic_review.get("semanticVerdict"),
        "semanticStatusZhTw": semantic_review.get("statusZhTw"),
        "fromId": row.get("fromId"),
        "toId": row.get("toId"),
        "subjectId": row.get("subjectId"),
        "controllerId": row.get("controllerId"),
        "currentZone": row.get("zone"),
        "recommendedActionZhTw": recommended_action,
        "canonicalWrites": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build proposal-only relationship skill review rounds.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH), help="Relationship trust-zone policy JSON path.")
    parser.add_argument("--fact-check", default="", help="Override fact-check JSONL path.")
    parser.add_argument("--semantic-review-evidence", default="", help="Override semantic review evidence JSONL path.")
    parser.add_argument("--secondary-semantic-review-evidence", default="", help="Optional second-model semantic review evidence JSONL path.")
    parser.add_argument("--output-root", default="", help="Override output root.")
    parser.add_argument("--focus-general-ids-file", default="", help="Optional UTF-8 text file with one generalId per line.")
    parser.add_argument(
        "--exclude-review-root",
        action="append",
        default=[],
        help="Review queue file or directory whose trustKey rows should be skipped.",
    )
    parser.add_argument("--round-count", type=int, default=0, help="Number of review rounds to build.")
    parser.add_argument("--items-per-round", type=int, default=0, help="Items per review round.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy)
    policy = read_json(policy_path)
    inputs = object_map(policy.get("inputs"))
    outputs = object_map(policy.get("outputs"))
    round_policy = object_map(policy.get("skillReviewRounds"))
    semantic = semantic_policy(policy)
    if not bool_value(round_policy.get("enabled"), True):
        print("[run_relationship_skill_review_rounds] disabled-by-policy")
        return 0

    output_root = resolve_path(args.output_root or str(outputs.get("outputRoot") or ""))
    fact_check_path = resolve_path(args.fact_check or str(output_root / str(outputs.get("factCheckFileName") or "")))
    semantic_path = resolve_path(args.semantic_review_evidence or str(inputs.get("semanticReviewEvidencePath") or output_root / str(semantic.get("evidenceFileName") or "")))
    secondary_semantic_path_text = str(
        args.secondary_semantic_review_evidence
        or inputs.get("secondarySemanticReviewEvidencePath")
        or ""
    ).strip()
    secondary_semantic_path = resolve_path(secondary_semantic_path_text) if secondary_semantic_path_text else Path("")
    human_decisions_path_text = str(inputs.get("humanReviewDecisionsPath") or "").strip()
    human_decisions_path = resolve_path(human_decisions_path_text) if human_decisions_path_text else Path("")
    stable_bootstrap_path = resolve_path(str(inputs.get("stableBootstrapPath") or ""))
    formal_mention_map_path = resolve_path(str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json"))
    round_count = args.round_count or int(number_value(round_policy.get("roundCountDefault"), 3.0))
    items_per_round = args.items_per_round or int(number_value(round_policy.get("itemsPerRoundDefault"), 12.0))
    if round_count <= 0 or items_per_round <= 0:
        raise ValueError("round-count-and-items-per-round-must-be-positive")

    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)

    rows = read_jsonl(fact_check_path)
    focus_general_ids_path = resolve_path(args.focus_general_ids_file) if str(args.focus_general_ids_file).strip() else Path("")
    focus_general_ids = load_focus_general_ids(focus_general_ids_path) if focus_general_ids_path else set()
    source_row_count = len(rows)
    if focus_general_ids:
        rows = [row for row in rows if row_matches_focus_general_ids(row, focus_general_ids)]
    human_sets = read_human_decision_sets(human_decisions_path, policy) if str(human_decisions_path) else {"whitelist": set(), "blacklist": set(), "removed": set()}
    if focus_general_ids:
        rows, focus_gap_index = apply_focus_gap_filter(rows, policy, focus_general_ids, human_sets)
    else:
        focus_gap_index = {}
    semantic_packets = read_semantic_packets(semantic_path) if semantic_path.exists() else {}
    secondary_semantic_packets = read_semantic_packets(secondary_semantic_path) if secondary_semantic_path.exists() else {}
    if focus_general_ids:
        scoped_trust_keys = {str(row.get("trustKey") or "").strip() for row in rows if str(row.get("trustKey") or "").strip()}
        semantic_packets = {key: value for key, value in semantic_packets.items() if key in scoped_trust_keys}
        secondary_semantic_packets = {key: value for key, value in secondary_semantic_packets.items() if key in scoped_trust_keys}
    candidates, residual_candidates, blacklist_candidates = eligible_candidates(
        rows,
        policy,
        semantic_packets,
        secondary_semantic_packets,
    )
    candidates, residual_candidates, blacklist_candidates, gate_counts = apply_human_and_direction_gates(
        candidates,
        residual_candidates,
        blacklist_candidates,
        policy,
        human_sets,
    )
    excluded_trust_keys: set[str] = set()
    for exclude_root in args.exclude_review_root:
        excluded_trust_keys.update(read_review_trust_keys(resolve_path(exclude_root)))
    if excluded_trust_keys:
        candidates = [row for row in candidates if str(row.get("trustKey") or "").strip() not in excluded_trust_keys]

    selected_rounds = pick_round_candidates(candidates, policy, round_count=round_count, items_per_round=items_per_round)
    generated_at = utc_now()

    queue_template = str(round_policy.get("reviewQueueFileNameTemplate") or "relationship-trust-zone-skill-review-round-{roundNo}.jsonl")
    markdown_template = str(round_policy.get("reviewMarkdownFileNameTemplate") or "relationship-trust-zone-skill-review-round-{roundNo}.md")
    all_items: list[dict[str, Any]] = []
    rendered_rounds: list[list[dict[str, Any]]] = []
    output_files: list[str] = []
    for round_no, round_rows in enumerate(selected_rounds, 1):
        items = [build_review_item(row, policy, name_map, round_no=round_no, item_index=index) for index, row in enumerate(round_rows, 1)]
        rendered_rounds.append(items)
        all_items.extend(items)
        queue_path = output_path_from_template(output_root, queue_template, round_no)
        markdown_path = output_path_from_template(output_root, markdown_template, round_no)
        if not args.overwrite and (queue_path.exists() or markdown_path.exists()):
            raise FileExistsError(f"output-exists-use-overwrite: {queue_path} / {markdown_path}")
        write_jsonl(queue_path, items)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_round_markdown(items, policy, round_no=round_no, generated_at=generated_at), encoding="utf-8")
        output_files.extend([repo_relative(queue_path), repo_relative(markdown_path)])

    aggregate_jsonl_path = output_root / str(round_policy.get("aggregateJsonlFileName") or "relationship-trust-zone-skill-review-human-review.jsonl")
    aggregate_md_path = output_root / str(round_policy.get("aggregateMarkdownFileName") or "relationship-trust-zone-skill-review-human-review.md")
    summary_path = output_root / str(round_policy.get("summaryFileName") or "relationship-trust-zone-skill-review-rounds-summary.json")
    residual_path = output_root / str(semantic.get("residualFileName") or "relationship-trust-zone-semantic-residual-candidates.jsonl")
    blacklist_path = output_root / str(semantic.get("blacklistCandidateFileName") or "relationship-trust-zone-semantic-blacklist-candidates.jsonl")
    if not args.overwrite and any(path.exists() for path in [aggregate_jsonl_path, aggregate_md_path, summary_path, residual_path, blacklist_path]):
        raise FileExistsError("aggregate-output-exists-use-overwrite")

    write_jsonl(aggregate_jsonl_path, all_items)
    aggregate_md_path.write_text(render_aggregate_markdown(rendered_rounds, policy, generated_at=generated_at), encoding="utf-8")
    output_files.extend([repo_relative(aggregate_jsonl_path), repo_relative(aggregate_md_path)])

    residual_rows = [residual_item(row, policy, residual_type="semantic-residual") for row in sorted_candidates(residual_candidates, policy)]
    blacklist_rows = [residual_item(row, policy, residual_type="semantic-blacklist-candidate") for row in sorted_candidates(blacklist_candidates, policy)]
    write_jsonl(residual_path, residual_rows)
    write_jsonl(blacklist_path, blacklist_rows)
    output_files.extend([repo_relative(residual_path), repo_relative(blacklist_path)])

    selected_semantic_status_counts = Counter(str(object_map(item.get("semanticReview")).get("status") or "") for item in all_items)
    selected_consensus_status_counts = Counter(
        str(item.get("semanticConsensusStatusZhTw") or "").strip()
        for item in all_items
        if str(item.get("semanticConsensusStatusZhTw") or "").strip()
    )
    consensus = semantic_consensus_policy(policy)
    summary = {
        "mode": "relationship-skill-review-rounds",
        "generatedAt": generated_at,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "factCheckPath": repo_relative(fact_check_path),
        "semanticReviewEvidencePath": repo_relative(semantic_path) if semantic_path.exists() else "",
        "secondarySemanticReviewEvidencePath": repo_relative(secondary_semantic_path) if secondary_semantic_path.exists() else "",
        "focusGeneralIdsFile": repo_relative(focus_general_ids_path) if focus_general_ids_path.exists() else "",
        "focusGeneralIdCount": len(focus_general_ids),
        "sourceFactCheckRowCount": source_row_count,
        "scopedFactCheckRowCount": len(rows),
        "focusGapTrustKeyCount": len(focus_gap_index),
        "humanReviewDecisionsPath": repo_relative(human_decisions_path) if human_decisions_path.exists() else "",
        "semanticEvidenceTrustKeyCount": len(semantic_packets),
        "secondarySemanticEvidenceTrustKeyCount": len(secondary_semantic_packets),
        "semanticConsensusEnabled": bool_value(consensus.get("enabled"), False),
        "humanDecisionWhitelistCount": len(human_sets.get("whitelist") or set()),
        "humanDecisionBlacklistCount": len(human_sets.get("blacklist") or set()),
        "preSkillReviewGateCounts": gate_counts,
        "eligibleCandidateCount": len(candidates),
        "semanticResidualCandidateCount": len(residual_rows),
        "semanticBlacklistCandidateCount": len(blacklist_rows),
        "excludedReviewTrustKeyCount": len(excluded_trust_keys),
        "roundCount": round_count,
        "itemsPerRound": items_per_round,
        "selectedItemCount": len(all_items),
        "selectedRelationshipTypeCounts": dict(Counter(str(item.get("relationshipType") or "") for item in all_items)),
        "selectedSemanticStatusCounts": dict(sorted(selected_semantic_status_counts.items())),
        "selectedSemanticConsensusStatusCounts": dict(sorted(selected_consensus_status_counts.items())),
        str(semantic.get("summaryFieldName") or "semanticPrioritySummary"): {
            "preferredMinTrustScore": number_value(semantic.get("preferredMinTrustScore"), number_value(semantic.get("skillReviewPreferredMinTrustScore"), 90.0)),
            "supportedMinTrustScore": number_value(semantic.get("supportedMinTrustScore"), number_value(semantic.get("minSupportedTrustScore"), 80.0)),
            "residualMaxTrustScore": number_value(semantic.get("residualMaxTrustScore"), 60.0),
            "blacklistCandidateVerdicts": string_list(semantic.get("blacklistCandidateVerdicts")),
            "residualVerdicts": string_list(semantic.get("residualVerdicts")),
        },
        "semanticConsensus": {
            "enabled": bool_value(consensus.get("enabled"), False),
            "supportedStatuses": string_list(consensus.get("supportedStatuses")),
            "preferredMinTrustScore": number_value(consensus.get("preferredMinTrustScore"), 90.0),
            "supportedMinTrustScore": number_value(consensus.get("supportedMinTrustScore"), 80.0),
        },
        "outputFiles": output_files,
        "promotionGuard": "review queue only; write external skill-review evidence packets separately after actual lookup.",
    }
    write_json(summary_path, summary)
    print(
        "[run_relationship_skill_review_rounds] "
        f"rounds={round_count} selected={len(all_items)} eligible={len(candidates)} "
        f"semanticEvidence={len(semantic_packets)} secondarySemanticEvidence={len(secondary_semantic_packets)} residual={len(residual_rows)} "
        f"blacklistCandidates={len(blacklist_rows)} aggregate={repo_relative(aggregate_md_path)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
