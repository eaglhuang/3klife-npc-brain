from __future__ import annotations

import argparse
import json
import itertools
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import relationship_claim_pair_cues as pair_cues
import relationship_type_refinement as relationship_types
from build_relationship_claim_graph import compact_text, edge_pair_relation_cue_evidence, pair_relation_terms_for_type
from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_BUNDLES_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-passage-bundles.jsonl"
DEFAULT_STABLE_KNOWLEDGE_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
DEFAULT_HARD_SPEC_PATH = REPO_ROOT / "data/sanguo/catalogs/catalog-hard-relationship-specs.jsonl"
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focusGeneralId-centered relationship extraction on baihua passage bundles.")
    parser.add_argument("--bundles-path", default=str(DEFAULT_BUNDLES_PATH))
    parser.add_argument("--stable-knowledge-path", default=str(DEFAULT_STABLE_KNOWLEDGE_PATH))
    parser.add_argument("--hard-spec-path", default=str(DEFAULT_HARD_SPEC_PATH))
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-file-name", default="top50-focus-skill-output.jsonl")
    parser.add_argument("--summary-file-name", default="top50-focus-skill-output-summary.json")
    parser.add_argument("--quote-max-chars", type=int, default=180)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be object: {path}:{line_no}")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def ingest_identity_value(
    id_to_name: dict[str, str],
    name_to_ids: dict[str, list[str]],
    alias_index: dict[str, list[str]],
    *,
    general_id: str,
    name: str,
) -> None:
    general_id = general_id.strip()
    name = name.strip()
    if not general_id or len(name) < 2:
        return
    if general_id not in id_to_name:
        id_to_name[general_id] = name
    values = name_to_ids[name]
    if general_id not in values:
        values.append(general_id)
    aliases = alias_index.setdefault(general_id, [])
    if name not in aliases:
        aliases.append(name)


def build_identity_maps(stable_path: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    payload = read_json(stable_path)
    seeds = payload.get("identitySeeds")
    id_to_name: dict[str, str] = {}
    name_to_ids: dict[str, list[str]] = defaultdict(list)
    alias_index: dict[str, list[str]] = defaultdict(list)
    if not isinstance(seeds, list):
        seeds = []

    for row in seeds:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        name = first_non_empty_text(row.get("name"), row.get("title"), general_id)
        ingest_identity_value(id_to_name, name_to_ids, alias_index, general_id=general_id, name=name)
        for alias in string_list(row.get("aliases")):
            ingest_identity_value(id_to_name, name_to_ids, alias_index, general_id=general_id, name=alias)

    for section_name in ["relationshipEdges", "plainRelationshipProposals", "reviewPendingRelationships"]:
        rows = payload.get(section_name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            ingest_identity_value(
                id_to_name,
                name_to_ids,
                alias_index,
                general_id=str(row.get("fromId") or ""),
                name=str(row.get("fromName") or ""),
            )
            ingest_identity_value(
                id_to_name,
                name_to_ids,
                alias_index,
                general_id=str(row.get("toId") or ""),
                name=str(row.get("toName") or ""),
            )

    for general_id, aliases in list(alias_index.items()):
        alias_index[general_id] = sorted(set(aliases), key=lambda item: (-len(compact_text(item)), item))
    return id_to_name, name_to_ids, dict(alias_index)


def parse_time_scope(row: dict[str, Any], fallback: str) -> str:
    start = int(row.get("validFromChapter") or 0)
    end = int(row.get("validToChapter") or 0)
    if start > 0 and end > 0:
        return f"第{start:03d}回至第{end:03d}回"
    if start > 0:
        return f"第{start:03d}回起"
    if end > 0:
        return f"至第{end:03d}回"
    return fallback


def resolve_name_to_id(name: str, name_to_ids: dict[str, list[str]]) -> str:
    candidates = name_to_ids.get(name) or []
    return candidates[0] if candidates else ""


def spec_to_edges(
    spec_rows: list[dict[str, Any]],
    *,
    allowed_types: set[str],
    symmetric_types: set[str],
    name_to_ids: dict[str, list[str]],
    time_scope_fallback: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in spec_rows:
        relationship_type = str(row.get("type") or "").strip()
        if relationship_type not in allowed_types:
            continue
        confidence = float(row.get("confidence") or 0.85)
        time_scope = parse_time_scope(row, time_scope_fallback)
        source_spec_id = str(row.get("id") or "").strip()
        source_refs = row.get("sourceRefs") if isinstance(row.get("sourceRefs"), list) else []

        names = row.get("names")
        if isinstance(names, list) and len(names) >= 2:
            resolved_ids: list[str] = []
            unresolved_names: list[str] = []
            for name in names:
                name_text = str(name or "").strip()
                if not name_text:
                    continue
                general_id = resolve_name_to_id(name_text, name_to_ids)
                if general_id:
                    resolved_ids.append(general_id)
                else:
                    unresolved_names.append(name_text)
            if unresolved_names:
                unresolved.append(
                    {
                        "specId": source_spec_id,
                        "relationshipType": relationship_type,
                        "unresolvedNames": unresolved_names,
                    }
                )
                continue
            for left_id, right_id in itertools.combinations(sorted(set(resolved_ids)), 2):
                from_id, to_id = (sorted([left_id, right_id]) if relationship_type in symmetric_types else (left_id, right_id))
                edges.append(
                    {
                        "fromId": from_id,
                        "toId": to_id,
                        "relationshipType": relationship_type,
                        "confidence": confidence,
                        "timeScopeZhTw": time_scope,
                        "sourceSpecId": source_spec_id,
                        "sourceRefs": source_refs,
                    }
                )
            continue

        from_name = str(row.get("fromName") or "").strip()
        to_name = str(row.get("toName") or "").strip()
        from_id = resolve_name_to_id(from_name, name_to_ids)
        to_id = resolve_name_to_id(to_name, name_to_ids)
        if not from_id or not to_id:
            unresolved.append(
                {
                    "specId": source_spec_id,
                    "relationshipType": relationship_type,
                    "fromName": from_name,
                    "toName": to_name,
                }
            )
            continue
        if relationship_type in symmetric_types:
            from_id, to_id = sorted([from_id, to_id])
        edges.append(
            {
                "fromId": from_id,
                "toId": to_id,
                "relationshipType": relationship_type,
                "confidence": confidence,
                "timeScopeZhTw": time_scope,
                "sourceSpecId": source_spec_id,
                "sourceRefs": source_refs,
            }
        )

    unique_edges: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for edge in edges:
        key = (edge["fromId"], edge["toId"], edge["relationshipType"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_edges.append(edge)
    return unique_edges, unresolved


def trim_text(text: str, max_chars: int) -> str:
    value = text.strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def load_focus_config(policy: dict[str, Any]) -> dict[str, Any]:
    configured = policy.get("focusSentenceExtraction")
    if not isinstance(configured, dict):
        configured = {}
    confidence_by_type = configured.get("confidenceByRelationshipType")
    if not isinstance(confidence_by_type, dict):
        confidence_by_type = {}
    return {
        "enabled": bool(configured.get("enabled", True)),
        "includeFullPassageWindow": bool(configured.get("includeFullPassageWindow", True)),
        "includeSentenceWindows": bool(configured.get("includeSentenceWindows", True)),
        "maxWindowChars": max(int(configured.get("maxWindowChars") or 220), 40),
        "maxPersonIdsPerWindow": max(int(configured.get("maxPersonIdsPerWindow") or 8), 2),
        "maxRelationshipsPerFocus": max(int(configured.get("maxRelationshipsPerFocus") or 80), 1),
        "timeScopeFallbackZhTw": str(configured.get("timeScopeFallbackZhTw") or "未限定時段"),
        "includeHardSpecFallback": bool(configured.get("includeHardSpecFallback", True)),
        "defaultConfidence": float(configured.get("defaultConfidence") or 0.88),
        "confidenceByRelationshipType": {
            str(key): float(value)
            for key, value in confidence_by_type.items()
            if str(key).strip()
        },
    }


def augment_identity_maps_from_bundles(
    bundle_rows: list[dict[str, Any]],
    id_to_name: dict[str, str],
    name_to_ids: dict[str, list[str]],
    alias_index: dict[str, list[str]],
) -> None:
    for row in bundle_rows:
        focus_id = str(row.get("focusGeneralId") or "").strip()
        focus_name = str(row.get("focusNameZhTw") or "").strip()
        if focus_id and focus_name:
            ingest_identity_value(id_to_name, name_to_ids, alias_index, general_id=focus_id, name=focus_name)
    for general_id, aliases in list(alias_index.items()):
        alias_index[general_id] = sorted(set(aliases), key=lambda item: (-len(compact_text(item)), item))


def hydrate_bundle_rows(bundle_manifest_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for row in bundle_manifest_rows:
        if not isinstance(row, dict):
            continue
        passages = row.get("passages")
        counterparts = row.get("candidateCounterpartIds")
        if isinstance(passages, list) and isinstance(counterparts, list):
            hydrated.append(row)
            continue
        bundle_path = Path(str(row.get("bundlePath") or "")).resolve()
        if not bundle_path.exists():
            hydrated.append(row)
            continue
        bundle_payload = read_json(bundle_path)
        merged = dict(row)
        merged["candidateCounterpartIds"] = bundle_payload.get("candidateCounterpartIds") or row.get("candidateCounterpartIds") or []
        merged["passages"] = bundle_payload.get("passages") or []
        merged["waveId"] = first_non_empty_text(row.get("waveId"), bundle_payload.get("waveId"))
        hydrated.append(merged)
    return hydrated


def sentence_boundaries() -> set[str]:
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    return set(pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES)


def split_sentence_units(text: str, max_chars: int) -> list[str]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    boundaries = sentence_boundaries()
    if not boundaries:
        return [stripped]
    units: list[str] = []
    current: list[str] = []
    for char in stripped:
        current.append(char)
        if char in boundaries:
            unit = "".join(current).strip()
            if unit:
                units.append(unit)
            current = []
    tail = "".join(current).strip()
    if tail:
        units.append(tail)

    normalized: list[str] = []
    for unit in units:
        if len(unit) <= max_chars:
            normalized.append(unit)
            continue
        start = 0
        while start < len(unit):
            chunk = unit[start : start + max_chars].strip()
            if chunk:
                normalized.append(chunk)
            start += max_chars
    return normalized


def infer_person_ids_from_text(
    text: str,
    alias_index: dict[str, list[str]],
    *,
    candidate_pool: set[str],
    max_ids: int,
) -> list[str]:
    compact = compact_text(text)
    if not compact:
        return []
    found: list[str] = []
    for general_id in sorted(candidate_pool):
        aliases = alias_index.get(general_id) or []
        if any(compact_text(alias) in compact for alias in aliases if len(compact_text(alias)) >= 2):
            found.append(general_id)
            if len(found) >= max_ids:
                break
    return found


def passage_person_ids(
    passage: dict[str, Any],
    alias_index: dict[str, list[str]],
    *,
    candidate_pool: set[str],
    max_ids: int,
) -> list[str]:
    raw_ids = [str(item or "").strip() for item in passage.get("personIds") or [] if str(item or "").strip()]
    filtered = [item for item in raw_ids if item in candidate_pool]
    if filtered:
        return sorted(dict.fromkeys(filtered))[:max_ids]
    return infer_person_ids_from_text(
        str(passage.get("normalizedText") or ""),
        alias_index,
        candidate_pool=candidate_pool,
        max_ids=max_ids,
    )


def passage_windows_for_focus(
    passage: dict[str, Any],
    *,
    focus_id: str,
    candidate_ids: set[str],
    alias_index: dict[str, list[str]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    candidate_pool = set(candidate_ids)
    candidate_pool.add(focus_id)
    base_person_ids = passage_person_ids(
        passage,
        alias_index,
        candidate_pool=candidate_pool,
        max_ids=int(config["maxPersonIdsPerWindow"]),
    )
    if bool(config["includeFullPassageWindow"]):
        full = dict(passage)
        full["personIds"] = base_person_ids
        full["windowType"] = "passage"
        windows.append(full)

    if bool(config["includeSentenceWindows"]):
        text = str(passage.get("normalizedText") or "")
        for index, unit in enumerate(split_sentence_units(text, int(config["maxWindowChars"])), 1):
            window_person_ids = infer_person_ids_from_text(
                unit,
                alias_index,
                candidate_pool=candidate_pool,
                max_ids=int(config["maxPersonIdsPerWindow"]),
            )
            if focus_id not in window_person_ids and focus_id not in base_person_ids:
                continue
            windows.append(
                {
                    "locator": f"{passage.get('locator')};sentence={index}",
                    "chapterRef": str(passage.get("chapterRef") or ""),
                    "normalizedText": unit,
                    "personIds": window_person_ids or base_person_ids,
                    "windowType": "sentence",
                    "sourcePath": passage.get("sourcePath"),
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for window in windows:
        locator = str(window.get("locator") or "")
        text = compact_text(window.get("normalizedText") or "")
        key = (locator, text)
        if not text or key in seen:
            continue
        seen.add(key)
        if focus_id not in [str(item or "").strip() for item in window.get("personIds") or []]:
            continue
        deduped.append(window)
    return deduped


def candidate_relationship_types(text: str, allowed_types: set[str]) -> list[str]:
    compact = compact_text(text)
    if not compact:
        return []
    result: list[str] = []
    for relationship_type in sorted(allowed_types):
        if relationship_type == "faction_membership":
            continue
        terms = pair_relation_terms_for_type(relationship_type)
        if terms and any(term in compact for term in terms):
            result.append(relationship_type)
    return result


def pair_orders_for_focus(
    focus_id: str,
    counterpart_id: str,
    relationship_type: str,
    symmetric_types: set[str],
    orientation_index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    if relationship_type in symmetric_types:
        left, right = sorted([focus_id, counterpart_id])
        return [(left, right)]
    known_orders = orientation_index.get((relationship_type, frozenset((focus_id, counterpart_id)))) or []
    if known_orders:
        return list(known_orders)
    return [(focus_id, counterpart_id), (counterpart_id, focus_id)]


def relationship_confidence(
    relationship_type: str,
    *,
    window_type: str,
    cue_payload: dict[str, Any],
    config: dict[str, Any],
) -> float:
    by_type = config.get("confidenceByRelationshipType") if isinstance(config.get("confidenceByRelationshipType"), dict) else {}
    confidence = float(by_type.get(relationship_type) or config["defaultConfidence"])
    if window_type == "sentence":
        confidence += 0.03
    binding = str(cue_payload.get("binding") or "")
    if "sentence" in binding or "ordered" in binding or "possessive" in binding:
        confidence += 0.02
    return round(clamp(confidence, 0.70, 0.99), 4)


def relationship_reason(relationship_type: str, cue_payload: dict[str, Any], source_kind: str) -> str:
    cue_term = str(cue_payload.get("cueTerm") or "").strip()
    binding = str(cue_payload.get("binding") or "").strip()
    if source_kind == "focus-sentence":
        return f"人物中心白話句窗抽取：依「{cue_term}」與 {binding} 支持 {relationship_type}"
    return f"既有硬關係規格補證據：依 {source_kind} 補到對應白話段落"


def relationship_row(
    *,
    from_id: str,
    to_id: str,
    relationship_type: str,
    symmetric_types: set[str],
    time_scope: str,
    evidence_quote: str,
    chapter_ref: str,
    source_passage_ref: str,
    confidence: float,
    reason: str,
    cue_payload: dict[str, Any] | None,
    source_kind: str,
) -> dict[str, Any]:
    row = {
        "fromId": from_id,
        "toId": to_id,
        "relationshipType": relationship_type,
        "relationshipDirection": "bidirectional" if relationship_type in symmetric_types else "directed",
        "timeScopeZhTw": time_scope,
        "evidenceQuoteZhTw": evidence_quote,
        "chapterRef": chapter_ref,
        "sourcePassageRef": source_passage_ref,
        "confidence": confidence,
        "reasonZhTw": reason,
        "sourceKind": source_kind,
        "canonicalWrites": False,
    }
    if cue_payload:
        row["cuePayload"] = cue_payload
    return row


def orientation_index_from_stable(stable_path: Path) -> dict[tuple[str, frozenset[str]], list[tuple[str, str]]]:
    payload = read_json(stable_path)
    rows = payload.get("relationshipEdges")
    if not isinstance(rows, list):
        return {}
    index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        relationship_type = str(row.get("type") or row.get("relationshipType") or "").strip()
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not relationship_type or not from_id or not to_id or from_id == to_id:
            continue
        key = (relationship_type, frozenset((from_id, to_id)))
        index.setdefault(key, [])
        if (from_id, to_id) not in index[key]:
            index[key].append((from_id, to_id))
    return index


def find_supporting_passage(
    passages: list[dict[str, Any]],
    *,
    focus_id: str,
    focus_name: str,
    counterpart_id: str,
    counterpart_name: str,
) -> dict[str, Any] | None:
    for passage in passages:
        person_ids = {str(item).strip() for item in passage.get("personIds") or [] if str(item or "").strip()}
        if focus_id in person_ids and counterpart_id in person_ids:
            return passage
    for passage in passages:
        text = str(passage.get("normalizedText") or "")
        if focus_name and counterpart_name and focus_name in text and counterpart_name in text:
            return passage
    return None


def extract_focus_sentence_relationships(
    bundle: dict[str, Any],
    *,
    allowed_types: set[str],
    symmetric_types: set[str],
    alias_index: dict[str, list[str]],
    orientation_index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]],
    config: dict[str, Any],
    quote_max_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not bool(config["enabled"]):
        return [], {"windowCount": 0, "candidateWindowCount": 0}

    focus_id = str(bundle.get("focusGeneralId") or "").strip()
    candidate_ids = {str(item).strip() for item in bundle.get("candidateCounterpartIds") or [] if str(item or "").strip()}
    passages = bundle.get("passages")
    if not isinstance(passages, list):
        passages = []

    output_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    window_count = 0
    candidate_window_count = 0

    for passage in passages:
        if not isinstance(passage, dict):
            continue
        windows = passage_windows_for_focus(
            passage,
            focus_id=focus_id,
            candidate_ids=candidate_ids,
            alias_index=alias_index,
            config=config,
        )
        window_count += len(windows)
        for window in windows:
            text = str(window.get("normalizedText") or "").strip()
            window_person_ids = {
                str(item).strip()
                for item in window.get("personIds") or []
                if str(item or "").strip()
            }
            if focus_id not in window_person_ids:
                continue
            counterpart_ids = sorted(candidate_ids.intersection(window_person_ids))
            if not counterpart_ids:
                continue
            relationship_types = candidate_relationship_types(text, allowed_types)
            if not relationship_types:
                continue
            candidate_window_count += 1
            for counterpart_id in counterpart_ids:
                for relationship_type in relationship_types:
                    for from_id, to_id in pair_orders_for_focus(
                        focus_id,
                        counterpart_id,
                        relationship_type,
                        symmetric_types,
                        orientation_index,
                    ):
                        probe = {
                            "fromId": from_id,
                            "toId": to_id,
                            "type": relationship_type,
                            "sourceQuote": text,
                            "sourceClaimType": "relationship",
                            "sourceClaimScopes": ["relationship"],
                        }
                        cue_payload = edge_pair_relation_cue_evidence(probe, alias_index, relationship_type)
                        if not cue_payload:
                            continue
                        key = (from_id, to_id, relationship_type, str(window.get("locator") or ""))
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        output_rows.append(
                            relationship_row(
                                from_id=from_id,
                                to_id=to_id,
                                relationship_type=relationship_type,
                                symmetric_types=symmetric_types,
                                time_scope=str(window.get("chapterRef") or config["timeScopeFallbackZhTw"]),
                                evidence_quote=trim_text(text, quote_max_chars),
                                chapter_ref=str(window.get("chapterRef") or ""),
                                source_passage_ref=str(window.get("locator") or ""),
                                confidence=relationship_confidence(
                                    relationship_type,
                                    window_type=str(window.get("windowType") or "passage"),
                                    cue_payload=cue_payload,
                                    config=config,
                                ),
                                reason=relationship_reason(relationship_type, cue_payload, "focus-sentence"),
                                cue_payload=cue_payload,
                                source_kind="focus-sentence",
                            )
                        )
                        if len(output_rows) >= int(config["maxRelationshipsPerFocus"]):
                            return output_rows, {
                                "windowCount": window_count,
                                "candidateWindowCount": candidate_window_count,
                            }
    return output_rows, {
        "windowCount": window_count,
        "candidateWindowCount": candidate_window_count,
    }


def hard_spec_fallback_rows(
    bundle: dict[str, Any],
    *,
    edge_rows: list[dict[str, Any]],
    id_to_name: dict[str, str],
    symmetric_types: set[str],
    quote_max_chars: int,
    existing_keys: set[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    focus_id = str(bundle.get("focusGeneralId") or "").strip()
    focus_name = first_non_empty_text(bundle.get("focusNameZhTw"), id_to_name.get(focus_id), focus_id)
    candidate_ids = {str(item).strip() for item in bundle.get("candidateCounterpartIds") or [] if str(item or "").strip()}
    passages = bundle.get("passages")
    if not isinstance(passages, list):
        passages = []

    relationships: list[dict[str, Any]] = []
    for edge in edge_rows:
        from_id = str(edge.get("fromId") or "").strip()
        to_id = str(edge.get("toId") or "").strip()
        relationship_type = str(edge.get("relationshipType") or "").strip()
        if focus_id not in {from_id, to_id}:
            continue
        counterpart_id = to_id if from_id == focus_id else from_id
        if counterpart_id not in candidate_ids:
            continue
        dedupe_key = (from_id, to_id, relationship_type)
        if dedupe_key in existing_keys:
            continue
        counterpart_name = first_non_empty_text(id_to_name.get(counterpart_id), counterpart_id)
        supporting = find_supporting_passage(
            passages,
            focus_id=focus_id,
            focus_name=focus_name,
            counterpart_id=counterpart_id,
            counterpart_name=counterpart_name,
        )
        if supporting is None:
            continue
        relationships.append(
            relationship_row(
                from_id=from_id,
                to_id=to_id,
                relationship_type=relationship_type,
                symmetric_types=symmetric_types,
                time_scope=str(edge.get("timeScopeZhTw") or ""),
                evidence_quote=trim_text(str(supporting.get("normalizedText") or ""), quote_max_chars),
                chapter_ref=str(supporting.get("chapterRef") or ""),
                source_passage_ref=str(supporting.get("locator") or ""),
                confidence=round(float(edge.get("confidence") or 0.85), 4),
                reason=relationship_reason(relationship_type, {}, str(edge.get("sourceSpecId") or "hard-spec")),
                cue_payload=None,
                source_kind="hard-spec-fallback",
            )
        )
    return relationships


def run_focus_rows(
    bundle_rows: list[dict[str, Any]],
    *,
    edge_rows: list[dict[str, Any]],
    id_to_name: dict[str, str],
    alias_index: dict[str, list[str]],
    allowed_types: set[str],
    symmetric_types: set[str],
    orientation_index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]],
    config: dict[str, Any],
    quote_max_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_rows: list[dict[str, Any]] = []
    relationship_counter: Counter[str] = Counter()
    source_kind_counter: Counter[str] = Counter()
    zero_focus_ids: list[str] = []
    total_windows = 0
    candidate_windows = 0

    for bundle in bundle_rows:
        focus_id = str(bundle.get("focusGeneralId") or "").strip()
        focus_name = first_non_empty_text(bundle.get("focusNameZhTw"), id_to_name.get(focus_id), focus_id)
        extracted_rows, extraction_stats = extract_focus_sentence_relationships(
            bundle,
            allowed_types=allowed_types,
            symmetric_types=symmetric_types,
            alias_index=alias_index,
            orientation_index=orientation_index,
            config=config,
            quote_max_chars=quote_max_chars,
        )
        total_windows += int(extraction_stats.get("windowCount") or 0)
        candidate_windows += int(extraction_stats.get("candidateWindowCount") or 0)

        existing_keys = {
            (
                str(row.get("fromId") or "").strip(),
                str(row.get("toId") or "").strip(),
                str(row.get("relationshipType") or "").strip(),
            )
            for row in extracted_rows
        }
        fallback_rows: list[dict[str, Any]] = []
        if bool(config["includeHardSpecFallback"]):
            fallback_rows = hard_spec_fallback_rows(
                bundle,
                edge_rows=edge_rows,
                id_to_name=id_to_name,
                symmetric_types=symmetric_types,
                quote_max_chars=quote_max_chars,
                existing_keys=existing_keys,
            )

        relationships = [*extracted_rows, *fallback_rows]
        relationships.sort(
            key=lambda row: (
                str(row.get("relationshipType") or ""),
                str(row.get("fromId") or ""),
                str(row.get("toId") or ""),
                str(row.get("sourcePassageRef") or ""),
            )
        )
        if not relationships:
            zero_focus_ids.append(focus_id)

        for row in relationships:
            relationship_counter[str(row.get("relationshipType") or "")] += 1
            source_kind_counter[str(row.get("sourceKind") or "")] += 1

        output_rows.append(
            {
                "focusGeneralId": focus_id,
                "focusNameZhTw": focus_name,
                "relationships": relationships,
                "canonicalWrites": False,
            }
        )

    summary = {
        "focusCount": len(output_rows),
        "relationshipCount": sum(len(row.get("relationships") or []) for row in output_rows),
        "relationshipTypeCounts": dict(sorted(relationship_counter.items())),
        "sourceKindCounts": dict(sorted(source_kind_counter.items())),
        "zeroRelationshipFocusCount": len(zero_focus_ids),
        "zeroRelationshipFocusIds": sorted(zero_focus_ids),
        "windowCount": total_windows,
        "candidateWindowCount": candidate_windows,
    }
    return output_rows, summary


def main() -> int:
    args = parse_args()
    bundles_path = Path(args.bundles_path).resolve()
    stable_path = Path(args.stable_knowledge_path).resolve()
    hard_spec_path = Path(args.hard_spec_path).resolve()
    policy_path = Path(args.policy_path).resolve()
    output_root = Path(args.output_root).resolve()
    output_path = output_root / args.output_file_name
    summary_path = output_root / args.summary_file_name

    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    bundle_manifest_rows = read_jsonl(bundles_path)
    bundle_rows = hydrate_bundle_rows(bundle_manifest_rows)
    id_to_name, name_to_ids, alias_index = build_identity_maps(stable_path)
    augment_identity_maps_from_bundles(bundle_rows, id_to_name, name_to_ids, alias_index)

    policy = read_json(policy_path)
    relation_policy = policy.get("relationshipTypes") if isinstance(policy.get("relationshipTypes"), dict) else {}
    allowed_types = {str(item).strip() for item in relation_policy.get("allowed") or [] if str(item or "").strip()}
    symmetric_types = {str(item).strip() for item in relation_policy.get("symmetric") or [] if str(item or "").strip()}
    if not allowed_types:
        raise ValueError(f"policy relationshipTypes.allowed missing: {policy_path}")

    focus_config = load_focus_config(policy)
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    relationship_types.ensure_relationship_type_refinement_rules_loaded()
    orientation_index = orientation_index_from_stable(stable_path)

    spec_rows = read_jsonl(hard_spec_path)
    edge_rows, unresolved_specs = spec_to_edges(
        spec_rows,
        allowed_types=allowed_types,
        symmetric_types=symmetric_types,
        name_to_ids=name_to_ids,
        time_scope_fallback=str(focus_config["timeScopeFallbackZhTw"]),
    )

    output_rows, extraction_summary = run_focus_rows(
        bundle_rows,
        edge_rows=edge_rows,
        id_to_name=id_to_name,
        alias_index=alias_index,
        allowed_types=allowed_types,
        symmetric_types=symmetric_types,
        orientation_index=orientation_index,
        config=focus_config,
        quote_max_chars=args.quote_max_chars,
    )

    write_jsonl(output_path, output_rows)
    summary = {
        "mode": "baihua-focus-relationship-runner",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "bundlesPath": str(bundles_path),
            "stableKnowledgePath": str(stable_path),
            "hardSpecPath": str(hard_spec_path),
            "policyPath": str(policy_path),
        },
        "outputs": {
            "skillOutputPath": str(output_path),
            "summaryPath": str(summary_path),
            **extraction_summary,
        },
        "focusSentenceExtraction": focus_config,
        "specNormalization": {
            "edgeCount": len(edge_rows),
            "unresolvedSpecCount": len(unresolved_specs),
            "unresolvedSpecs": unresolved_specs[:200],
        },
    }
    write_json(summary_path, summary)
    print(f"[run_baihua_focus_relationship_runner] wrote {output_path}")
    print(f"[run_baihua_focus_relationship_runner] wrote {summary_path}")
    print(
        "[run_baihua_focus_relationship_runner] "
        f"focus={summary['outputs']['focusCount']} "
        f"relationships={summary['outputs']['relationshipCount']} "
        f"zeroFocus={summary['outputs']['zeroRelationshipFocusCount']} "
        "canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
