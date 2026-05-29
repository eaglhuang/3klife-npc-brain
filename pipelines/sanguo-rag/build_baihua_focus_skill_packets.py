from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import relationship_claim_pair_cues as pair_cues
import relationship_type_refinement as relationship_types
from build_relationship_claim_graph import pair_relation_terms_for_type
from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_alias_map as semantic_build_alias_map
from run_relationship_semantic_review_cache import build_name_map as semantic_build_name_map


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_BUNDLES_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-passage-bundles.jsonl"
DEFAULT_STABLE_KNOWLEDGE_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "pipelines/sanguo-rag/baihua-focus-skill-contract.zh-TW.md"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build person-centered baihua skill packets for semantic relationship extraction.")
    parser.add_argument("--bundles-path", default=str(DEFAULT_BUNDLES_PATH))
    parser.add_argument("--stable-knowledge-path", default=str(DEFAULT_STABLE_KNOWLEDGE_PATH))
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--relationship-policy-path", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--contract-path", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-file-name", default="top50-focus-skill-packets.jsonl")
    parser.add_argument("--summary-file-name", default="top50-focus-skill-packets-summary.json")
    parser.add_argument("--packet-dir-name", default="focus-skill-packets")
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


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact_text(value: Any) -> str:
    return "".join(str(value or "").strip().split())


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


def first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


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


def build_identity_maps(
    stable_path: Path,
    *,
    relationship_policy_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
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

    if relationship_policy_path and relationship_policy_path.exists():
        relationship_policy = read_json(relationship_policy_path)
        inputs = relationship_policy.get("inputs") if isinstance(relationship_policy.get("inputs"), dict) else {}
        formal_mention_map_path = resolve_path(
            str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
        )
        alias_records_path = resolve_path(
            str(inputs.get("generalAliasRecordsPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json")
        )
        formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
        alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
        name_map = semantic_build_name_map(payload, formal_mention_map)
        semantic_alias_map = semantic_build_alias_map(name_map, formal_mention_map, alias_records)
        for general_id, name in name_map.items():
            ingest_identity_value(
                id_to_name,
                name_to_ids,
                alias_index,
                general_id=general_id,
                name=name,
            )
        for general_id, aliases in semantic_alias_map.items():
            for alias in aliases:
                ingest_identity_value(
                    id_to_name,
                    name_to_ids,
                    alias_index,
                    general_id=general_id,
                    name=alias,
                )

    for general_id, aliases in list(alias_index.items()):
        alias_index[general_id] = sorted(set(aliases), key=lambda item: (-len(compact_text(item)), item))
    return id_to_name, name_to_ids, dict(alias_index)


def ingest_bundle_focus_aliases(
    bundle_rows: list[dict[str, Any]],
    *,
    id_to_name: dict[str, str],
    name_to_ids: dict[str, list[str]],
    alias_index: dict[str, list[str]],
) -> None:
    for row in bundle_rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("focusGeneralId") or "").strip()
        focus_name = str(row.get("focusNameZhTw") or "").strip()
        if not general_id or not focus_name:
            continue
        ingest_identity_value(
            id_to_name,
            name_to_ids,
            alias_index,
            general_id=general_id,
            name=focus_name,
        )
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
        payload = read_json(bundle_path)
        merged = dict(row)
        merged["passages"] = payload.get("passages") or []
        merged["candidateCounterpartIds"] = payload.get("candidateCounterpartIds") or row.get("candidateCounterpartIds") or []
        merged["waveId"] = str(payload.get("waveId") or row.get("waveId") or "").strip()
        hydrated.append(merged)
    return hydrated


def top_counterpart_hits(
    passages: list[dict[str, Any]],
    counterpart_ids: set[str],
) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    chapters: dict[str, list[str]] = defaultdict(list)
    sample_quotes: dict[str, list[str]] = defaultdict(list)

    for passage in passages:
        person_ids = {str(item).strip() for item in passage.get("personIds") or [] if str(item or "").strip()}
        chapter_ref = str(passage.get("chapterRef") or "").strip()
        quote = str(passage.get("normalizedText") or "").strip()
        for counterpart_id in sorted(counterpart_ids.intersection(person_ids)):
            counter[counterpart_id] += 1
            if chapter_ref and chapter_ref not in chapters[counterpart_id]:
                chapters[counterpart_id].append(chapter_ref)
            if quote and len(sample_quotes[counterpart_id]) < 3:
                sample_quotes[counterpart_id].append(quote)

    rows: list[dict[str, Any]] = []
    for counterpart_id, hit_count in counter.most_common():
        rows.append(
            {
                "counterpartId": counterpart_id,
                "hitCount": hit_count,
                "chapterRefs": chapters[counterpart_id][:12],
                "sampleQuotes": sample_quotes[counterpart_id][:3],
            }
        )
    return rows


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


def text_mentions_aliases(text: str, aliases: list[str]) -> bool:
    compact = compact_text(text)
    if not compact:
        return False
    for alias in aliases:
        token = compact_text(alias)
        if len(token) >= 2 and token in compact:
            return True
    return False


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


def focus_fallback_policy(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    return {}


def passage_windows_for_focus(
    passage: dict[str, Any],
    *,
    focus_id: str,
    counterpart_ids: set[str],
    alias_index: dict[str, list[str]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    candidate_pool = set(counterpart_ids)
    candidate_pool.add(focus_id)
    focus_aliases = alias_index.get(focus_id) or []
    passage_fallback = focus_fallback_policy(config, "passageScopedFocusFallback")
    empty_focus_passage_fallback = focus_fallback_policy(config, "emptyFocusPassageFallback")
    base_person_ids = passage_person_ids(
        passage,
        alias_index,
        candidate_pool=candidate_pool,
        max_ids=int(config["maxPersonIdsPerWindow"]),
    )
    passage_has_focus = focus_id in base_person_ids
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
            counterpart_hits = [item for item in window_person_ids if item in counterpart_ids and item != focus_id]
            focus_match_mode = "direct-alias"
            focus_direct_match = text_mentions_aliases(unit, focus_aliases)
            if not focus_direct_match:
                fallback_enabled = bool(passage_fallback.get("enabled", False))
                requires_passage_person = bool(passage_fallback.get("requiresPassagePersonId", True))
                requires_counterpart = bool(passage_fallback.get("requiresCounterpartMention", True))
                if not fallback_enabled:
                    continue
                if requires_passage_person and not passage_has_focus:
                    continue
                if requires_counterpart and not counterpart_hits:
                    continue
                focus_match_mode = "passage-scoped-fallback"
            if focus_id not in window_person_ids and (
                focus_direct_match or bool(passage_fallback.get("injectFocusPersonId", True))
            ):
                window_person_ids = unique_strings([focus_id, *window_person_ids])
            if focus_id not in window_person_ids:
                continue
            windows.append(
                {
                    "locator": f"{passage.get('locator')};sentence={index}",
                    "chapterRef": str(passage.get("chapterRef") or ""),
                    "normalizedText": unit,
                    "personIds": unique_strings(window_person_ids),
                    "contextPersonIds": base_person_ids,
                    "windowType": "sentence",
                    "focusMatchMode": focus_match_mode,
                    "sourcePath": passage.get("sourcePath"),
                }
            )

    if not windows and bool(empty_focus_passage_fallback.get("enabled", False)):
        requires_passage_person = bool(empty_focus_passage_fallback.get("requiresPassagePersonId", True))
        requires_counterpart = bool(empty_focus_passage_fallback.get("requiresCounterpartMention", True))
        counterpart_hits = [item for item in base_person_ids if item in counterpart_ids and item != focus_id]
        if (not requires_passage_person or passage_has_focus) and (not requires_counterpart or counterpart_hits):
            fallback_person_ids = unique_strings([focus_id, *base_person_ids]) if passage_has_focus else unique_strings(base_person_ids)
            windows.append(
                {
                    "locator": str(passage.get("locator") or ""),
                    "chapterRef": str(passage.get("chapterRef") or ""),
                    "normalizedText": str(passage.get("normalizedText") or ""),
                    "personIds": fallback_person_ids,
                    "contextPersonIds": base_person_ids,
                    "windowType": "passage-fallback",
                    "focusMatchMode": "empty-focus-passage-fallback",
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


def candidate_relationship_types(text: str, allowed_types: list[str]) -> list[str]:
    return list(matched_cue_terms_by_type(text, allowed_types).keys())


def matched_cue_terms_by_type(text: str, allowed_types: list[str]) -> dict[str, list[str]]:
    compact = compact_text(text)
    if not compact:
        return {}
    rows: dict[str, list[str]] = {}
    for relationship_type in allowed_types:
        if relationship_type == "faction_membership":
            continue
        terms = pair_relation_terms_for_type(relationship_type)
        matched = [term for term in terms if term and term in compact]
        if matched:
            rows[relationship_type] = matched
    return rows


def select_packet_passages(
    passages: list[dict[str, Any]],
    focus_id: str,
    counterpart_ids: set[str],
    alias_index: dict[str, list[str]],
    assembly: dict[str, Any],
    allowed_types: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    max_per_focus = max(int(assembly.get("maxPassagesPerFocus") or 120), 1)
    max_per_chapter = max(int(assembly.get("maxPassagesPerChapter") or 8), 1)
    max_quotes_per_counterpart = max(int(assembly.get("maxQuotesPerCounterpart") or 6), 1)
    prefer_sentence = bool(assembly.get("preferSentenceWindows", True))
    prioritize_focus_mentions = bool(assembly.get("prioritizeDirectFocusMentions", True))

    chapter_counts: Counter[str] = Counter()
    counterpart_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    window_type_counts: Counter[str] = Counter()

    enriched_rows: list[dict[str, Any]] = []
    for row in passages:
        if not isinstance(row, dict):
            continue
        for window in passage_windows_for_focus(
            row,
            focus_id=focus_id,
            counterpart_ids=counterpart_ids,
            alias_index=alias_index,
            config=assembly,
        ):
            enriched = dict(window)
            context_person_ids = unique_strings([str(item) for item in enriched.get("contextPersonIds") or []])
            counterpart_hits = [
                item for item in unique_strings([str(item) for item in window.get("personIds") or []])
                if item in counterpart_ids and item != focus_id
            ]
            enriched["counterpartHits"] = counterpart_hits
            enriched["contextPersonIds"] = context_person_ids
            enriched["contextCounterpartHits"] = [
                item for item in context_person_ids if item in counterpart_ids and item != focus_id
            ]
            cue_terms_by_type = matched_cue_terms_by_type(str(window.get("normalizedText") or ""), allowed_types)
            enriched["cueTermsByType"] = cue_terms_by_type
            enriched["candidateRelationshipTypes"] = list(cue_terms_by_type.keys())
            enriched_rows.append(enriched)

    ranked = sorted(
        enriched_rows,
        key=lambda row: (
            -len(string_list(row.get("candidateRelationshipTypes"))),
            -len([item for item in row.get("counterpartHits") or [] if str(item or "").strip() in counterpart_ids]),
            len([str(item).strip() for item in row.get("personIds") or [] if str(item or "").strip()]),
            0 if (prefer_sentence and str(row.get("windowType") or "") == "sentence") else 1,
            0 if (prioritize_focus_mentions and row.get("personIds")) else 1,
            str(row.get("chapterRef") or ""),
            str(row.get("locator") or ""),
        ),
    )

    for row in ranked:
        if len(selected) >= max_per_focus:
            break
        chapter_ref = str(row.get("chapterRef") or "").strip() or "unknown"
        if chapter_counts[chapter_ref] >= max_per_chapter:
            continue
        counterpart_hits = [str(item).strip() for item in row.get("counterpartHits") or [] if str(item or "").strip() in counterpart_ids]
        if counterpart_hits:
            limited_hits: list[str] = []
            for counterpart_id in counterpart_hits:
                if counterpart_counts[counterpart_id] >= max_quotes_per_counterpart:
                    continue
                counterpart_counts[counterpart_id] += 1
                limited_hits.append(counterpart_id)
            if not limited_hits:
                continue
            counterpart_hits = limited_hits

        chapter_counts[chapter_ref] += 1
        window_type_counts[str(row.get("windowType") or "unknown")] += 1
        selected.append(
            {
                "locator": str(row.get("locator") or ""),
                "chapterRef": chapter_ref,
                "normalizedText": str(row.get("normalizedText") or ""),
                "personIds": unique_strings([str(item) for item in row.get("personIds") or []]),
                "counterpartHits": counterpart_hits,
                "contextPersonIds": unique_strings([str(item) for item in row.get("contextPersonIds") or []]),
                "contextCounterpartHits": unique_strings([str(item) for item in row.get("contextCounterpartHits") or []]),
                "cueTermsByType": row.get("cueTermsByType") if isinstance(row.get("cueTermsByType"), dict) else {},
                "candidateRelationshipTypes": string_list(row.get("candidateRelationshipTypes")),
                "windowType": str(row.get("windowType") or ""),
                "focusMatchMode": str(row.get("focusMatchMode") or ""),
                "sourcePath": str(row.get("sourcePath") or ""),
            }
        )

    stats = {
        "selectedPassageCount": len(selected),
        "selectedChapterCount": len([key for key, value in chapter_counts.items() if value > 0]),
        "selectedCounterpartCount": len([key for key, value in counterpart_counts.items() if value > 0]),
        "selectedWindowTypeCounts": dict(window_type_counts),
        "candidateWindowCount": len(enriched_rows),
    }
    return selected, stats


def main() -> int:
    args = parse_args()
    bundles_path = Path(args.bundles_path).resolve()
    stable_knowledge_path = Path(args.stable_knowledge_path).resolve()
    policy_path = Path(args.policy_path).resolve()
    relationship_policy_path = Path(args.relationship_policy_path).resolve()
    contract_path = Path(args.contract_path).resolve()
    output_root = Path(args.output_root).resolve()
    output_path = output_root / args.output_file_name
    summary_path = output_root / args.summary_file_name
    packet_root = output_root / args.packet_dir_name

    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    policy = read_json(policy_path)
    prompt_policy = policy.get("focusSkillPrompt") if isinstance(policy.get("focusSkillPrompt"), dict) else {}
    relationship_policy = policy.get("relationshipTypes") if isinstance(policy.get("relationshipTypes"), dict) else {}
    assembly = prompt_policy.get("packetAssembly") if isinstance(prompt_policy.get("packetAssembly"), dict) else {}
    focus_config = policy.get("focusSentenceExtraction") if isinstance(policy.get("focusSentenceExtraction"), dict) else {}
    default_allowed_types = string_list(relationship_policy.get("allowed"))
    packet_config = {
        "includeFullPassageWindow": bool(focus_config.get("includeFullPassageWindow", False)),
        "includeSentenceWindows": bool(focus_config.get("includeSentenceWindows", True)),
        "maxWindowChars": max(int(focus_config.get("maxWindowChars") or 220), 40),
        "maxPersonIdsPerWindow": max(int(focus_config.get("maxPersonIdsPerWindow") or 8), 2),
        **assembly,
    }
    bundle_manifest_rows = read_jsonl(bundles_path)
    bundle_rows = hydrate_bundle_rows(bundle_manifest_rows)
    contract_text = contract_path.read_text(encoding="utf-8-sig")
    id_to_name, name_to_ids, alias_index = build_identity_maps(
        stable_knowledge_path,
        relationship_policy_path=relationship_policy_path,
    )
    ingest_bundle_focus_aliases(
        bundle_rows,
        id_to_name=id_to_name,
        name_to_ids=name_to_ids,
        alias_index=alias_index,
    )

    packet_rows: list[dict[str, Any]] = []
    total_selected_passages = 0
    total_candidate_windows = 0
    zero_packet_focus_ids: list[str] = []
    counterpart_histogram: Counter[str] = Counter()
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    relationship_types.ensure_relationship_type_refinement_rules_loaded()

    for bundle in bundle_rows:
        focus_id = str(bundle.get("focusGeneralId") or "").strip()
        if not focus_id:
            continue
        focus_name = str(bundle.get("focusNameZhTw") or focus_id).strip()
        candidate_ids = {str(item).strip() for item in bundle.get("candidateCounterpartIds") or [] if str(item or "").strip()}
        passages = bundle.get("passages") if isinstance(bundle.get("passages"), list) else []
        allowed_types = string_list(bundle.get("allowedRelationshipTypes")) or default_allowed_types
        selected_passages, stats = select_packet_passages(passages, focus_id, candidate_ids, alias_index, packet_config, allowed_types)
        if not selected_passages:
            zero_packet_focus_ids.append(focus_id)
        total_selected_passages += stats["selectedPassageCount"]
        total_candidate_windows += int(stats["candidateWindowCount"])

        counterpart_rows = top_counterpart_hits(passages, candidate_ids)
        for row in counterpart_rows[:12]:
            counterpart_histogram[row["counterpartId"]] += 1

        packet = {
            "packetId": f"baihua-focus-skill-packet:{bundle.get('waveId') or 'wave'}:{focus_id}",
            "waveId": str(bundle.get("waveId") or "").strip(),
            "focusGeneralId": focus_id,
            "focusNameZhTw": focus_name,
            "sourceCorpusId": str(bundle.get("sourceCorpusId") or "").strip(),
            "promptVersion": str(prompt_policy.get("version") or "baihua-focus-skill.v1"),
            "goalZhTw": str(prompt_policy.get("goalZhTw") or ""),
            "allowedRelationshipTypes": allowed_types,
            "relationshipPriority": string_list(prompt_policy.get("relationshipPriority")),
            "primaryTasksZhTw": string_list(prompt_policy.get("primaryTasksZhTw")),
            "extractionStepsZhTw": string_list(prompt_policy.get("extractionStepsZhTw")),
            "negativeGuardsZhTw": string_list(prompt_policy.get("negativeGuardsZhTw")),
            "relationDefinitionsZhTw": prompt_policy.get("relationDefinitionsZhTw") if isinstance(prompt_policy.get("relationDefinitionsZhTw"), dict) else {},
            "outputRequirementsZhTw": string_list(prompt_policy.get("outputRequirementsZhTw")),
            "counterpartRanking": counterpart_rows,
            "selectedPassages": selected_passages,
            "skillContractPath": str(contract_path),
            "skillContractExcerptZhTw": contract_text[:2000],
            "canonicalWrites": False,
        }

        packet_path = (packet_root / f"{focus_id}.skill-packet.json").resolve()
        write_json(packet_path, packet)
        packet_rows.append(
            {
                "packetId": packet["packetId"],
                "focusGeneralId": focus_id,
                "focusNameZhTw": focus_name,
                "packetPath": str(packet_path),
                "selectedPassageCount": stats["selectedPassageCount"],
                "selectedChapterCount": stats["selectedChapterCount"],
                "selectedCounterpartCount": stats["selectedCounterpartCount"],
                "selectedWindowTypeCounts": stats["selectedWindowTypeCounts"],
                "candidateWindowCount": stats["candidateWindowCount"],
                "topCounterpartIds": [row["counterpartId"] for row in counterpart_rows[:10]],
                "canonicalWrites": False,
            }
        )

    write_jsonl(output_path, packet_rows)
    summary = {
        "mode": "baihua-focus-skill-packet-builder",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "bundlesPath": str(bundles_path),
            "policyPath": str(policy_path),
            "contractPath": str(contract_path),
        },
        "outputs": {
            "packetManifestPath": str(output_path),
            "summaryPath": str(summary_path),
            "packetRoot": str(packet_root),
            "packetCount": len(packet_rows),
            "totalSelectedPassageCount": total_selected_passages,
            "totalCandidateWindowCount": total_candidate_windows,
            "zeroPacketFocusCount": len(zero_packet_focus_ids),
            "zeroPacketFocusIds": sorted(zero_packet_focus_ids),
            "topCounterpartIds": [item[0] for item in counterpart_histogram.most_common(20)],
        },
    }
    write_json(summary_path, summary)
    print(f"[build_baihua_focus_skill_packets] wrote {output_path}")
    print(f"[build_baihua_focus_skill_packets] wrote {summary_path}")
    print(
        "[build_baihua_focus_skill_packets] "
        f"packets={len(packet_rows)} selectedPassages={total_selected_passages} "
        f"zeroPacketFocus={len(zero_packet_focus_ids)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
