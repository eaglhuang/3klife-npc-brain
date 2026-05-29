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
from versioning import build_version_metadata
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


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_PACKET_MANIFEST_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-focus-skill-packets.jsonl"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_LANE_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-hard-baseline-lane.json"
DEFAULT_RANKING_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/top50-famous-bond-r13.famous-ranking.json"
DEFAULT_CATALOG_PATH = REPO_ROOT / "data/sanguo/catalogs/catalog-faction-timeline-specs.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Top50 faction membership semantic review queue from person-centered baihua packets.")
    parser.add_argument("--packet-manifest", default=str(DEFAULT_PACKET_MANIFEST_PATH))
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--lane-policy", default=str(DEFAULT_LANE_POLICY_PATH))
    parser.add_argument("--ranking-path", default=str(DEFAULT_RANKING_PATH))
    parser.add_argument("--catalog-path", default=str(DEFAULT_CATALOG_PATH))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--queue-file-name", default="")
    parser.add_argument("--summary-file-name", default="")
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


def stable_inputs(relationship_policy: dict[str, Any]) -> tuple[Path, Path, Path]:
    inputs = object_map(relationship_policy.get("inputs"))
    stable_bootstrap = resolve_path(str(inputs.get("stableBootstrapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"))
    formal_mention_map = resolve_path(str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json"))
    alias_records = resolve_path(str(inputs.get("generalAliasRecordsPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json"))
    return stable_bootstrap, formal_mention_map, alias_records


def read_catalog_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"Catalog row must be object: {path}:{line_no}")
            rows.append(payload)
    return rows


def top50_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    rows = payload.get("ranking")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def split_alias_tokens(value: str) -> list[str]:
    if not value:
        return []
    tokens = [token.strip() for token in re.split(r"[／/,、\s]+", value) if token.strip()]
    output: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            output.append(token)
    return output


def faction_label_aliases(relationship_policy: dict[str, Any]) -> dict[str, list[str]]:
    labels = object_map(relationship_policy.get("factionLabelsZhTw"))
    alias_map: dict[str, list[str]] = {}
    for faction_id, label in labels.items():
        tokens = split_alias_tokens(str(label or "").strip())
        if not tokens and str(label or "").strip():
            tokens = [str(label).strip()]
        alias_map[str(faction_id)] = tokens
    return alias_map


def ranking_faction_map(
    ranking_rows: list[dict[str, Any]],
    lane_policy: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    faction_policy = object_map(lane_policy.get("factionReview"))
    skip = {str(item or "").strip() for item in string_list(faction_policy.get("skipRankingFactions")) if str(item or "").strip()}
    focus_to_factions: dict[str, list[str]] = defaultdict(list)
    leader_by_faction: dict[str, str] = {}
    for row in ranking_rows:
        general_id = str(row.get("generalId") or "").strip()
        faction_id = str(row.get("faction") or row.get("baseFaction") or "").strip()
        if not general_id or not faction_id or faction_id in skip:
            continue
        if faction_id not in focus_to_factions[general_id]:
            focus_to_factions[general_id].append(faction_id)
        leader_by_faction.setdefault(faction_id, general_id)
    return dict(focus_to_factions), leader_by_faction


def catalog_faction_map(
    catalog_rows: list[dict[str, Any]],
    *,
    name_map: dict[str, str],
) -> dict[str, list[str]]:
    by_general: dict[str, list[str]] = defaultdict(list)
    name_to_id = {name: general_id for general_id, name in name_map.items() if name}
    for row in catalog_rows:
        name = str(row.get("name") or "").strip()
        general_id = name_to_id.get(name, "")
        if not general_id:
            continue
        for interval in row.get("intervals") or []:
            if not isinstance(interval, dict):
                continue
            faction_id = str(interval.get("faction") or "").strip()
            if faction_id and faction_id not in by_general[general_id]:
                by_general[general_id].append(faction_id)
    return dict(by_general)


def generic_faction_aliases(
    faction_id: str,
    *,
    relationship_policy: dict[str, Any],
    lane_policy: dict[str, Any],
    leader_by_faction: dict[str, str],
    alias_map: dict[str, list[str]],
) -> list[str]:
    faction_labels = faction_label_aliases(relationship_policy)
    faction_policy = object_map(lane_policy.get("factionReview"))
    suffixes = [item for item in string_list(faction_policy.get("genericMembershipTermsZhTw")) if item]
    aliases: list[str] = []
    seen: set[str] = set()

    for token in faction_labels.get(faction_id, []):
        if token not in seen:
            seen.add(token)
            aliases.append(token)
        for suffix in suffixes:
            value = f"{token}{suffix}"
            if value not in seen:
                seen.add(value)
                aliases.append(value)

    leader_id = leader_by_faction.get(faction_id, "")
    for token in alias_map.get(leader_id, []):
        normalized = str(token or "").strip()
        if len(normalized) < 2:
            continue
        if normalized not in seen:
            seen.add(normalized)
            aliases.append(normalized)
        for suffix in suffixes:
            value = f"{normalized}{suffix}"
            if value not in seen:
                seen.add(value)
                aliases.append(value)
    return aliases


def source_ref_for_sentence(packet: dict[str, Any], passage: dict[str, Any]) -> dict[str, Any]:
    return {
        "sourceId": packet.get("sourceCorpusId") or "sanguoyanyi-baihua-zh-tw",
        "sourceFamily": "baihua-bootstrap-focus",
        "sourceLayer": "baihua-focus-packets",
        "confidenceSignals": [
            "baihua-translation-anchor",
            "focus-sentence-window",
            "faction-membership-focus",
        ],
        "locator": passage.get("locator"),
        "url": "",
        "evidenceRefs": [passage.get("chapterRef"), passage.get("locator")],
        "canonicalWrites": False,
    }


def sentence_hash(sentence: str) -> str:
    return "sha256:" + hashlib.sha256(re.sub(r"\s+", "", sentence).encode("utf-8")).hexdigest()


def faction_display_name(faction_id: str, relationship_policy: dict[str, Any]) -> str:
    labels = object_map(relationship_policy.get("factionLabelsZhTw"))
    return split_alias_tokens(str(labels.get(faction_id) or "").strip())[0] if str(labels.get(faction_id) or "").strip() else faction_id


def candidate_factions_for_focus(
    focus_id: str,
    *,
    ranking_map: dict[str, list[str]],
    catalog_map: dict[str, list[str]],
    relationship_policy: dict[str, Any],
) -> list[str]:
    faction_labels = set(object_map(relationship_policy.get("factionLabelsZhTw")).keys())
    candidates: list[str] = []
    for faction_id in ranking_map.get(focus_id, []):
        if faction_id in faction_labels and faction_id not in candidates:
            candidates.append(faction_id)
    for faction_id in catalog_map.get(focus_id, []):
        if faction_id in faction_labels and faction_id not in candidates:
            candidates.append(faction_id)
    if focus_id in faction_labels and focus_id not in candidates:
        candidates.append(focus_id)
    return candidates


def lane_score(
    *,
    sentence: str,
    matched_aliases: list[str],
    faction_aliases: list[str],
    lane_policy: dict[str, Any],
) -> float:
    faction_policy = object_map(lane_policy.get("factionReview"))
    score = max(float(faction_policy.get("baseScore") or 88.0), sentence_quality_score(sentence))
    leader_boost = float(faction_policy.get("leaderAliasBoost") or 4.0)
    label_boost = float(faction_policy.get("labelAliasBoost") or 2.0)
    label_alias_set = set(faction_aliases)
    for alias in matched_aliases:
        if alias in label_alias_set:
            score += label_boost
        else:
            score += leader_boost
    return round(min(100.0, score), 3)


def build_units(
    packet_rows: list[dict[str, Any]],
    *,
    ranking_map: dict[str, list[str]],
    catalog_map: dict[str, list[str]],
    leader_by_faction: dict[str, str],
    relationship_policy: dict[str, Any],
    lane_policy: dict[str, Any],
    name_map: dict[str, str],
    alias_map: dict[str, list[str]],
    scoped_ambiguous_alias_map: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    semantic_policy = object_map(relationship_policy.get("semanticReview"))
    prompt_version = str(semantic_policy.get("promptVersion") or "relationship-semantic-review.v1")
    faction_policy = object_map(lane_policy.get("factionReview"))
    max_candidates_per_sentence = max(1, int(faction_policy.get("maxCandidatesPerSentence") or 3))

    units_by_id: dict[str, dict[str, Any]] = {}
    seen_trust_keys: dict[str, set[str]] = defaultdict(set)
    focus_counts: Counter[str] = Counter()
    faction_counts: Counter[str] = Counter()

    for packet_row in packet_rows:
        packet_path = resolve_path(packet_row.get("packetPath") or "")
        if not packet_path.exists():
            continue
        packet = read_json(packet_path)
        focus_id = str(packet.get("focusGeneralId") or "").strip()
        focus_name = str(packet.get("focusNameZhTw") or name_map.get(focus_id) or focus_id).strip()
        candidate_factions = candidate_factions_for_focus(
            focus_id,
            ranking_map=ranking_map,
            catalog_map=catalog_map,
            relationship_policy=relationship_policy,
        )
        if not candidate_factions:
            continue

        alias_by_faction = {
            faction_id: generic_faction_aliases(
                faction_id,
                relationship_policy=relationship_policy,
                lane_policy=lane_policy,
                leader_by_faction=leader_by_faction,
                alias_map=alias_map,
            )
            for faction_id in candidate_factions
        }

        for passage in packet.get("selectedPassages") or []:
            if not isinstance(passage, dict):
                continue
            sentence = str(passage.get("normalizedText") or "").strip()
            if not sentence:
                continue
            candidate_rows: list[dict[str, Any]] = []
            for faction_id in candidate_factions:
                matched = [alias for alias in alias_by_faction.get(faction_id, []) if alias and alias in sentence]
                if not matched:
                    continue
                faction_name = faction_display_name(faction_id, relationship_policy)
                candidate_rows.append(
                    {
                        "trustKey": f"faction_membership:{faction_id}:{focus_id}",
                        "claimSentenceZhTw": f"{focus_name}屬於{faction_name}陣營",
                        "relationshipType": "faction_membership",
                        "fromId": faction_id,
                        "toId": focus_id,
                        "subjectId": focus_id,
                        "controllerId": faction_id,
                        "fromNameZhTw": faction_name,
                        "toNameZhTw": focus_name,
                        "subjectNameZhTw": focus_name,
                        "controllerNameZhTw": faction_name,
                        "strictDirectionCheckZhTw": "只有句子明確指出人物屬於該勢力、該軍、該陣營或其領袖麾下時，才可支持此方向。",
                        "scoreBeforeSemanticReview": lane_score(
                            sentence=sentence,
                            matched_aliases=matched,
                            faction_aliases=split_alias_tokens(str(object_map(relationship_policy.get("factionLabelsZhTw")).get(faction_id) or "")),
                            lane_policy=lane_policy,
                        ),
                        "focusQueuePriority": round(128.0 + min(20.0, sentence_quality_score(sentence) / 5.0), 3),
                        "counterpartSelectionMode": "focus-faction",
                        "matchedCueTerms": matched,
                        "matchedCueTermCount": len(matched),
                        "focusGapCount": 1,
                        "focusGapGeneralIds": [focus_id],
                        "canonicalWrites": False,
                    }
                )

            if not candidate_rows:
                continue
            base_unit_id = cache_unit_id(prompt_version, sentence)
            source_ref = source_ref_for_sentence(packet, passage)
            focus_counts[focus_id] += 1
            for candidate in candidate_rows[:max_candidates_per_sentence]:
                faction_counts[str(candidate.get("fromId") or "")] += 1
                trust_key_value = str(candidate.get("trustKey") or "").strip()
                if trust_key_value in seen_trust_keys[base_unit_id]:
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
                            "sentenceHash": sentence_hash(sentence),
                            "sourceSentence": sentence,
                            "sentenceQualityScore": sentence_quality_score(sentence),
                            "sourceRefs": [],
                            "sourcePreviewPriorityMax": 0.0,
                            "candidates": [],
                            "reviewMode": "sentence-relation-extraction",
                            "canonicalWrites": False,
                        },
                    )
                    if len(unit["candidates"]) < max_candidates_per_sentence:
                        break
                    chunk_index += 1

                if source_ref not in unit["sourceRefs"]:
                    unit["sourceRefs"].append(source_ref)
                unit["sourcePreviewPriorityMax"] = round(
                    max(float(unit.get("sourcePreviewPriorityMax") or 0.0), float(candidate.get("scoreBeforeSemanticReview") or 0.0)),
                    4,
                )
                unit["candidates"].append(candidate)
                seen_trust_keys[base_unit_id].add(trust_key_value)

    units: list[dict[str, Any]] = []
    for unit in units_by_id.values():
        unit["candidateMaxScoreBeforeSemanticReview"] = round(
            max(float(candidate.get("scoreBeforeSemanticReview") or 0.0) for candidate in unit.get("candidates") or []),
            3,
        )
        unit["candidateCount"] = len(unit.get("candidates") or [])
        unit["focusQueuePriority"] = round(
            max(float(candidate.get("focusQueuePriority") or 0.0) for candidate in unit.get("candidates") or []),
            3,
        )
        unit["allowedEntities"] = allowed_entities_from_candidates(unit.get("candidates") or [], alias_map, scoped_ambiguous_alias_map)
        unit["allowedRelationshipTypes"] = ["faction_membership"]
        units.append(unit)

    units = sorted(units, key=lambda item: semantic_queue_sort_key(item, relationship_policy))
    metrics = {
        "focusSentenceCounts": dict(sorted(focus_counts.items())),
        "factionCandidateCounts": dict(sorted(faction_counts.items())),
    }
    return units, metrics


def main() -> int:
    args = parse_args()
    packet_manifest_path = resolve_path(args.packet_manifest)
    relationship_policy_path = resolve_path(args.relationship_policy)
    lane_policy_path = resolve_path(args.lane_policy)
    ranking_path = resolve_path(args.ranking_path)
    catalog_path = resolve_path(args.catalog_path)

    relationship_policy = read_json(relationship_policy_path)
    lane_policy = read_json(lane_policy_path)
    output_root = resolve_path(args.output_root) if str(args.output_root).strip() else packet_manifest_path.parent

    output_names = object_map(lane_policy.get("outputs"))
    queue_path = output_root / (str(args.queue_file_name).strip() or str(output_names.get("factionQueueFileName") or "top50-faction-membership-queue.jsonl"))
    summary_path = output_root / (str(args.summary_file_name).strip() or str(output_names.get("factionSummaryFileName") or "top50-faction-membership-queue-summary.json"))
    if not args.overwrite and (queue_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)
    scoped_ambiguous_alias_map = build_general_scoped_ambiguous_alias_map(alias_records)

    ranking_rows = top50_rows(read_json(ranking_path))
    ranking_map, leader_by_faction = ranking_faction_map(ranking_rows, lane_policy)
    catalog_map = catalog_faction_map(read_catalog_rows(catalog_path), name_map=name_map)
    packet_rows = read_jsonl(packet_manifest_path)

    units, metrics = build_units(
        packet_rows,
        ranking_map=ranking_map,
        catalog_map=catalog_map,
        leader_by_faction=leader_by_faction,
        relationship_policy=relationship_policy,
        lane_policy=lane_policy,
        name_map=name_map,
        alias_map=alias_map,
        scoped_ambiguous_alias_map=scoped_ambiguous_alias_map,
    )

    version_metadata = build_version_metadata(
        schema_version="top50-faction-membership-queue.v1",
        artifact_paths=[packet_manifest_path, relationship_policy_path, lane_policy_path, ranking_path, catalog_path],
        repo_root=REPO_ROOT,
    )
    for unit in units:
        unit.update(version_metadata)

    write_jsonl(queue_path, units)
    summary = {
        "mode": "top50-faction-membership-queue-builder",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "packetManifestPath": repo_relative(packet_manifest_path),
            "relationshipPolicyPath": repo_relative(relationship_policy_path),
            "lanePolicyPath": repo_relative(lane_policy_path),
            "rankingPath": repo_relative(ranking_path),
            "catalogPath": repo_relative(catalog_path),
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "unitCount": len(units),
            "focusCount": len(metrics["focusSentenceCounts"]),
            "focusSentenceCountsTop20": dict(list(sorted(metrics["focusSentenceCounts"].items(), key=lambda item: (-item[1], item[0]))[:20])),
            "factionCandidateCounts": metrics["factionCandidateCounts"],
        },
    }
    write_json(summary_path, summary)
    print(
        "[build_top50_faction_membership_queue] "
        f"units={len(units)} focuses={len(metrics['focusSentenceCounts'])} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
