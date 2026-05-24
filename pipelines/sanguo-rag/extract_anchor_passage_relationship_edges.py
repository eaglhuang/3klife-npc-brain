from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations, permutations
from pathlib import Path
from typing import Any

import relationship_claim_pair_cues as pair_cues
import relationship_type_refinement as relationship_types
from build_relationship_claim_graph import (
    build_alias_index,
    compact_text,
    edge_pair_relation_cue_evidence,
    pair_relation_terms_for_type,
)
from repo_layout import resolve_repo_root
from sanguo_governance_loader import default_governance_root, load_relationship_runtime_canon_policy


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_ANCHOR_INDEX_ROOT = Path("artifacts/data-pipeline/sanguo-rag/anchor-index")
DEFAULT_SOURCE_CONFIG_PATH = Path("pipelines/sanguo-rag/config/anchor-index-build-sources.json")
DEFAULT_ALIAS_MAP_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
DEFAULT_GENERALS_PATH = Path("assets/resources/data/generals.json")
DEFAULT_STABLE_BOOTSTRAP_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
)
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/anchor-passage-relationship-evidence")
DEFAULT_GOVERNANCE_ROOT = default_governance_root()
OUTPUT_JSONL_NAME = "source-grounded-relationship-edges.anchor-passages.jsonl"
PROPOSAL_JSONL_NAME = "source-grounded-relationship-edges.anchor-passages.proposals.jsonl"
RESIDUAL_JSONL_NAME = "anchor-passage-relationship-residual-windows.jsonl"
SUMMARY_JSON_NAME = "anchor-passage-relationship-evidence-summary.json"


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


def int_config(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, minimum)


def extraction_config(source_payload: dict[str, Any]) -> dict[str, Any]:
    config = source_payload.get("anchorPassageRelationshipExtraction")
    if not isinstance(config, dict):
        config = {}
    windowed_config = config.get("windowedExtraction")
    if not isinstance(windowed_config, dict):
        windowed_config = {}
    residual_config = config.get("residualWindowTelemetry")
    if not isinstance(residual_config, dict):
        residual_config = {}
    return {
        "enabled": bool(config.get("enabled", True)),
        "corpusIds": string_list(config.get("corpusIds")),
        "minPersonIds": max(int(config.get("minPersonIds") or 2), 2),
        "maxPersonIdsPerPassage": max(int(config.get("maxPersonIdsPerPassage") or 8), 2),
        "maxEdgesPerPassage": max(int(config.get("maxEdgesPerPassage") or 12), 1),
        "includeShadowPeople": bool(config.get("includeShadowPeople", False)),
        "bidirectionalRelationshipTypes": string_list(config.get("bidirectionalRelationshipTypes")),
        "knownOrientationRequiredTypes": string_list(config.get("knownOrientationRequiredTypes")),
        "proposalOnlyRelationshipTypes": string_list(config.get("proposalOnlyRelationshipTypes")),
        "maxPairCueEndpointDistance": max(int(config.get("maxPairCueEndpointDistance") or 0), 0),
        "windowedExtraction": {
            "enabled": bool(windowed_config.get("enabled", False)),
            "boundarySource": str(windowed_config.get("boundarySource") or "relationshipClaimPairCues"),
            "includeClauseBoundaries": bool(windowed_config.get("includeClauseBoundaries", False)),
            "minWindowChars": int_config(windowed_config.get("minWindowChars"), 12, 1),
            "maxWindowChars": int_config(windowed_config.get("maxWindowChars"), 260, 1),
            "windowBefore": int_config(windowed_config.get("windowBefore"), 0, 0),
            "windowAfter": int_config(windowed_config.get("windowAfter"), 0, 0),
            "maxPersonIdsPerWindow": int_config(
                windowed_config.get("maxPersonIdsPerWindow"),
                int_config(config.get("maxPersonIdsPerPassage"), 8, 2),
                2,
            ),
        },
        "residualWindowTelemetry": {
            "enabled": bool(residual_config.get("enabled", False)),
            "maxRows": int_config(residual_config.get("maxRows"), 0, 0),
            "maxQuoteChars": int_config(residual_config.get("maxQuoteChars"), 180, 1),
            "includeNoCandidateWindows": bool(residual_config.get("includeNoCandidateWindows", False)),
        },
        "sourceLayer": str(config.get("sourceLayer") or "anchor-passages"),
        "sourceLayerRaw": str(config.get("sourceLayerRaw") or "romance"),
        "sourceClass": str(config.get("sourceClass") or "internal-primary-text"),
        "sourceClaimScopes": string_list(config.get("sourceClaimScopes")) or ["relationship"],
        "confidenceSignals": string_list(config.get("confidenceSignals")) or ["anchor-passage-pair-cue"],
        "canonicalWrites": False,
    }


def stable_alias_values(row: dict[str, Any]) -> list[str]:
    values = [row.get("name"), row.get("displayName"), row.get("canonicalName")]
    values.extend(string_list(row.get("aliases")))
    values.extend(string_list(row.get("alias")))
    return [str(value or "").strip() for value in values if len(str(value or "").strip()) >= 2]


def extend_alias_index_from_stable(alias_index: dict[str, list[str]], stable_path: Path) -> dict[str, list[str]]:
    payload = read_json(stable_path)
    if not isinstance(payload, dict):
        return alias_index
    for key in ("identitySeeds", "basicProfileSeeds", "femalePriorityProfiles"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            general_id = str(row.get("generalId") or row.get("id") or "").strip()
            if not general_id:
                continue
            existing = set(alias_index.get(general_id, []))
            for alias in stable_alias_values(row):
                existing.add(alias)
            alias_index[general_id] = sorted(existing, key=lambda item: (-len(item), item))
    return alias_index


def alias_index_from_inputs(generals_path: Path, alias_map_path: Path, stable_path: Path | None) -> dict[str, list[str]]:
    alias_index = build_alias_index(generals_path, alias_map_path)
    if stable_path is not None and stable_path.exists():
        alias_index = extend_alias_index_from_stable(alias_index, stable_path)
    return alias_index


def relationship_orientation_index(stable_path: Path | None) -> dict[tuple[str, frozenset[str]], list[tuple[str, str]]]:
    if stable_path is None or not stable_path.exists():
        return {}
    payload = read_json(stable_path)
    rows = payload.get("relationshipEdges") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}
    index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rel_type = str(row.get("type") or "").strip()
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not rel_type or not from_id or not to_id or from_id == to_id:
            continue
        key = (rel_type, frozenset((from_id, to_id)))
        index.setdefault(key, [])
        if (from_id, to_id) not in index[key]:
            index[key].append((from_id, to_id))
    return index


def infer_person_ids_from_text(text: str, alias_index: dict[str, list[str]], max_ids: int) -> list[str]:
    compact = compact_text(text)
    found: list[str] = []
    for general_id, aliases in sorted(alias_index.items()):
        if any(compact_text(alias) in compact for alias in aliases if len(compact_text(alias)) >= 2):
            found.append(general_id)
            if len(found) >= max_ids:
                break
    return found


def stable_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def window_boundary_chars(window_config: dict[str, Any]) -> set[str]:
    if str(window_config.get("boundarySource") or "") != "relationshipClaimPairCues":
        return set(string_list(window_config.get("boundaryChars")))
    boundaries = set(pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES)
    if bool(window_config.get("includeClauseBoundaries")):
        boundaries.update(pair_cues.PAIR_CUE_CLAUSE_BOUNDARIES)
    return boundaries


def split_text_units(text: str, window_config: dict[str, Any]) -> list[str]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    boundaries = window_boundary_chars(window_config)
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
    return units


def bounded_window_text(
    units: list[str],
    index: int,
    *,
    window_before: int,
    window_after: int,
    max_window_chars: int,
) -> str:
    start = max(0, index - window_before)
    end = min(len(units), index + window_after + 1)
    while end - start > 1 and len("".join(units[start:end])) > max_window_chars:
        if end - 1 > index:
            end -= 1
            continue
        if start < index:
            start += 1
            continue
        break
    return "".join(units[start:end]).strip()


def passage_windows(
    passage: dict[str, Any],
    *,
    alias_index: dict[str, list[str]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    window_config = config.get("windowedExtraction") if isinstance(config.get("windowedExtraction"), dict) else {}
    if not bool(window_config.get("enabled")):
        return [passage]
    text = str(passage.get("normalizedText") or "")
    units = split_text_units(text, window_config)
    if not units:
        return []
    min_chars = int(window_config.get("minWindowChars") or 1)
    max_chars = int(window_config.get("maxWindowChars") or 1)
    window_before = int(window_config.get("windowBefore") or 0)
    window_after = int(window_config.get("windowAfter") or 0)
    max_ids = int(window_config.get("maxPersonIdsPerWindow") or config["maxPersonIdsPerPassage"])
    base_locator = str(passage.get("locator") or "")
    base_text_hash = str(passage.get("textHash") or "")
    windows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, _unit in enumerate(units):
        window_text = bounded_window_text(
            units,
            index,
            window_before=window_before,
            window_after=window_after,
            max_window_chars=max_chars,
        )
        if len(window_text) < min_chars:
            continue
        if window_text in seen:
            continue
        seen.add(window_text)
        person_ids = infer_person_ids_from_text(window_text, alias_index, max_ids)
        if not person_ids:
            continue
        locator = f"{base_locator};window={index + 1}" if base_locator else f"window={index + 1}"
        child = dict(passage)
        child.update(
            {
                "normalizedText": window_text,
                "locator": locator,
                "textHash": stable_text_hash(window_text),
                "personIds": person_ids,
                "parentLocator": base_locator,
                "parentTextHash": base_text_hash,
                "windowIndex": index + 1,
                "windowUnitCount": len(units),
            }
        )
        windows.append(child)
    return windows


def passage_person_ids(
    passage: dict[str, Any],
    alias_index: dict[str, list[str]],
    config: dict[str, Any],
) -> list[str]:
    max_ids = int(config["maxPersonIdsPerPassage"])
    raw_ids = [str(item or "").strip() for item in passage.get("personIds") or [] if str(item or "").strip()]
    if not bool(config.get("includeShadowPeople")):
        raw_ids = [item for item in raw_ids if not item.startswith("shadow:")]
    if raw_ids:
        return sorted(dict.fromkeys(raw_ids))[:max_ids]
    return infer_person_ids_from_text(str(passage.get("normalizedText") or ""), alias_index, max_ids)


def passage_paths(anchor_index_root: Path, corpus_ids: list[str]) -> list[Path]:
    if corpus_ids:
        return [anchor_index_root / f"{corpus_id}-passages.jsonl" for corpus_id in corpus_ids]
    return sorted(anchor_index_root.glob("*-passages.jsonl"), key=lambda item: str(item).lower())


def candidate_relationship_types(text: str, relationship_types_to_check: list[str]) -> list[str]:
    compact = compact_text(text)
    result: list[str] = []
    for rel_type in relationship_types_to_check:
        terms = pair_relation_terms_for_type(rel_type)
        if terms and any(term in compact for term in terms):
            result.append(rel_type)
    return result


def pair_orders_for_relationship(
    person_ids: list[str],
    rel_type: str,
    orientation_index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]],
    config: dict[str, Any],
) -> list[tuple[str, str]]:
    if rel_type in set(config["bidirectionalRelationshipTypes"]):
        return [(left, right) for left, right in combinations(sorted(person_ids), 2)]
    if rel_type in set(config["knownOrientationRequiredTypes"]):
        orders: list[tuple[str, str]] = []
        for left, right in combinations(sorted(person_ids), 2):
            orders.extend(orientation_index.get((rel_type, frozenset((left, right))), []))
        return orders
    return list(permutations(person_ids, 2))


def cue_endpoint_distance_ok(cue_payload: dict[str, Any], config: dict[str, Any]) -> bool:
    max_distance = int(config.get("maxPairCueEndpointDistance") or 0)
    if max_distance <= 0:
        return True
    cue_span = cue_payload.get("cueSpan") or []
    from_span = cue_payload.get("fromAliasSpan") or []
    to_span = cue_payload.get("toAliasSpan") or []
    if len(cue_span) != 2 or len(from_span) != 2 or len(to_span) != 2:
        return True
    cue_mid = (int(cue_span[0]) + int(cue_span[1])) / 2.0
    from_mid = (int(from_span[0]) + int(from_span[1])) / 2.0
    to_mid = (int(to_span[0]) + int(to_span[1])) / 2.0
    return max(abs(cue_mid - from_mid), abs(cue_mid - to_mid)) <= max_distance


def edge_for_pair(
    *,
    passage: dict[str, Any],
    from_id: str,
    to_id: str,
    rel_type: str,
    cue_payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    corpus_id = str(passage.get("corpusId") or "")
    locator = str(passage.get("locator") or "")
    text_hash = str(passage.get("textHash") or "")
    quote = str(passage.get("normalizedText") or "").strip()
    source_evidence_id = "anchor-passage:" + stable_hash(corpus_id, locator, text_hash)
    edge_id = "anchorrel." + stable_hash(corpus_id, locator, from_id, to_id, rel_type, text_hash)
    anchor_evidence = {
        "corpusId": corpus_id,
        "locator": locator,
        "textHash": text_hash,
        "sourcePath": passage.get("sourcePath"),
        "passagePersonIds": list(passage.get("personIds") or []),
        "pairRelationCue": cue_payload,
    }
    if passage.get("parentLocator") or passage.get("parentTextHash"):
        anchor_evidence.update(
            {
                "parentLocator": passage.get("parentLocator"),
                "parentTextHash": passage.get("parentTextHash"),
                "windowIndex": passage.get("windowIndex"),
                "windowUnitCount": passage.get("windowUnitCount"),
            }
        )
    return {
        "edgeId": edge_id,
        "fromId": from_id,
        "toId": to_id,
        "type": rel_type,
        "originalType": rel_type,
        "candidateRelationshipType": rel_type,
        "relationshipTypeLocked": False,
        "relationshipTypeLockReason": "deterministic-candidate-only",
        "semanticReviewRequired": True,
        "semanticReviewReason": "anchor-passage-deterministic-candidate-narrowing",
        "pattern": "external-relationship-card-gate",
        "sourceQuote": quote,
        "quote": quote,
        "locator": locator,
        "textHash": text_hash,
        "evidenceRefs": [locator] if locator else [],
        "sourcePolicyId": corpus_id,
        "sourceId": corpus_id,
        "sourceEvidenceId": source_evidence_id,
        "sourceFamily": str(passage.get("sourceFamily") or ""),
        "sourceLayer": config["sourceLayer"],
        "sourceLayerRaw": config["sourceLayerRaw"],
        "sourceClass": config["sourceClass"],
        "trustTier": str(passage.get("trustTier") or ""),
        "sourceClaimType": "relationship",
        "sourceClaimScopes": list(config["sourceClaimScopes"]),
        "confidenceSignals": list(config["confidenceSignals"]),
        "chapterNo": str(locator).split("#", 2)[1] if "#" in locator else None,
        "anchorEvidence": anchor_evidence,
        "canonicalWrites": False,
    }


def extract_edges_from_passage(
    passage: dict[str, Any],
    *,
    alias_index: dict[str, list[str]],
    relationship_types_to_check: list[str],
    orientation_index: dict[tuple[str, frozenset[str]], list[tuple[str, str]]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = str(passage.get("normalizedText") or "")
    person_ids = passage_person_ids(passage, alias_index, config)
    stats = {
        "personCount": len(person_ids),
        "candidateTypeCount": 0,
        "candidateTypes": [],
        "pairCandidateCount": 0,
        "cueMatchedCount": 0,
    }
    if len(person_ids) < int(config["minPersonIds"]):
        return [], stats
    candidates = candidate_relationship_types(text, relationship_types_to_check)
    stats["candidateTypeCount"] = len(candidates)
    stats["candidateTypes"] = candidates
    if not candidates:
        return [], stats
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    max_edges = int(config["maxEdgesPerPassage"])
    for rel_type in candidates:
        pair_orders = pair_orders_for_relationship(person_ids, rel_type, orientation_index, config)
        for from_id, to_id in pair_orders:
            stats["pairCandidateCount"] += 1
            probe = {
                "fromId": from_id,
                "toId": to_id,
                "type": rel_type,
                "sourceQuote": text,
                "sourceClaimType": "relationship",
                "sourceClaimScopes": list(config["sourceClaimScopes"]),
            }
            cue_payload = edge_pair_relation_cue_evidence(probe, alias_index, rel_type)
            if not cue_payload:
                continue
            if not cue_endpoint_distance_ok(cue_payload, config):
                continue
            edge_key = (rel_type, from_id, to_id)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            stats["cueMatchedCount"] += 1
            edges.append(
                edge_for_pair(
                    passage=passage,
                    from_id=from_id,
                    to_id=to_id,
                    rel_type=rel_type,
                    cue_payload=cue_payload,
                    config=config,
                )
            )
            if len(edges) >= max_edges:
                return edges, stats
    return edges, stats


def residual_row_for_passage(
    passage: dict[str, Any],
    stats: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    residual_config = (
        config.get("residualWindowTelemetry") if isinstance(config.get("residualWindowTelemetry"), dict) else {}
    )
    if not bool(residual_config.get("enabled")):
        return None
    candidate_types = [str(item or "").strip() for item in stats.get("candidateTypes") or [] if str(item or "").strip()]
    if not candidate_types and not bool(residual_config.get("includeNoCandidateWindows")):
        return None
    person_ids = [str(item or "").strip() for item in passage.get("personIds") or [] if str(item or "").strip()]
    if len(person_ids) < int(config["minPersonIds"]):
        return None
    quote = str(passage.get("normalizedText") or "").strip()
    max_quote_chars = int(residual_config.get("maxQuoteChars") or 1)
    if len(quote) > max_quote_chars:
        quote = quote[:max_quote_chars]
    locator = str(passage.get("locator") or "")
    text_hash = str(passage.get("textHash") or "")
    corpus_id = str(passage.get("corpusId") or "")
    reason = "candidate-window-no-cue-match" if candidate_types else "eligible-window-no-candidate-type"
    return {
        "residualId": "anchorrelresidual." + stable_hash(corpus_id, locator, text_hash, ",".join(candidate_types)),
        "corpusId": corpus_id,
        "locator": locator,
        "textHash": text_hash,
        "parentLocator": passage.get("parentLocator"),
        "parentTextHash": passage.get("parentTextHash"),
        "windowIndex": passage.get("windowIndex"),
        "personIds": person_ids,
        "candidateRelationshipTypes": candidate_types,
        "personCount": int(stats.get("personCount") or 0),
        "pairCandidateCount": int(stats.get("pairCandidateCount") or 0),
        "reason": reason,
        "sourceQuote": quote,
        "proposalStatus": "telemetry-only",
        "reviewGate": "data-rule-proposal-required",
        "canonicalWrites": False,
    }


def dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for edge in edges:
        key = (
            str(edge.get("fromId") or ""),
            str(edge.get("toId") or ""),
            str(edge.get("type") or ""),
            str(edge.get("locator") or ""),
            str(edge.get("textHash") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def extract_anchor_passage_relationship_edges(
    *,
    anchor_index_root: Path,
    source_config_path: Path,
    alias_map_path: Path,
    generals_path: Path,
    stable_bootstrap_path: Path | None,
    output_root: Path,
    governance_root: Path,
    relationship_policy: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()) and not overwrite:
        raise FileExistsError(f"output already exists: {repo_relative(output_root)}")
    source_payload = read_json(source_config_path)
    config = extraction_config(source_payload if isinstance(source_payload, dict) else {})
    relationship_types.apply_relationship_type_refinement_rules(governance_root)
    pair_cues.apply_relationship_claim_pair_cue_rules(governance_root)
    policy = load_relationship_runtime_canon_policy(governance_root, relationship_policy=relationship_policy)
    relationship_types_to_check = string_list(policy.get("promotableRelationshipTypes"))
    alias_index = alias_index_from_inputs(generals_path, alias_map_path, stable_bootstrap_path)
    orientation_index = relationship_orientation_index(stable_bootstrap_path)

    paths = passage_paths(anchor_index_root, list(config["corpusIds"]))
    edges: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    path_stats: list[dict[str, Any]] = []
    totals = Counter()
    if not config["enabled"]:
        paths = []
    for path in paths:
        rows = read_jsonl(path)
        path_edges: list[dict[str, Any]] = []
        path_totals = Counter()
        for passage in rows:
            path_totals["passages"] += 1
            windows = passage_windows(passage, alias_index=alias_index, config=config)
            path_totals["windows"] += len(windows)
            for window in windows:
                extracted, passage_stats = extract_edges_from_passage(
                    window,
                    alias_index=alias_index,
                    relationship_types_to_check=relationship_types_to_check,
                    orientation_index=orientation_index,
                    config=config,
                )
                if passage_stats["personCount"] >= int(config["minPersonIds"]):
                    path_totals["eligiblePassages"] += 1
                if passage_stats["candidateTypeCount"]:
                    path_totals["candidatePassages"] += 1
                if extracted:
                    path_totals["cueMatchedPassages"] += 1
                residual_row = residual_row_for_passage(window, passage_stats, config) if not extracted else None
                if residual_row is not None:
                    residual_rows.append(residual_row)
                    path_totals["residualWindows"] += 1
                path_totals["pairCandidates"] += int(passage_stats["pairCandidateCount"])
                path_totals["cueMatches"] += int(passage_stats["cueMatchedCount"])
                path_edges.extend(extracted)
        path_edges = dedupe_edges(path_edges)
        edges.extend(path_edges)
        path_stats.append(
            {
                "path": repo_relative(path),
                "status": "ok" if path.exists() else "missing",
                "passageCount": int(path_totals["passages"]),
                "windowCount": int(path_totals["windows"]),
                "eligiblePassageCount": int(path_totals["eligiblePassages"]),
                "eligibleWindowCount": int(path_totals["eligiblePassages"]),
                "candidatePassageCount": int(path_totals["candidatePassages"]),
                "candidateWindowCount": int(path_totals["candidatePassages"]),
                "cueMatchedPassageCount": int(path_totals["cueMatchedPassages"]),
                "cueMatchedWindowCount": int(path_totals["cueMatchedPassages"]),
                "residualWindowCount": int(path_totals["residualWindows"]),
                "pairCandidateCount": int(path_totals["pairCandidates"]),
                "cueMatchCount": int(path_totals["cueMatches"]),
                "edgeCount": len(path_edges),
            }
        )
        totals.update(path_totals)

    edges = dedupe_edges(edges)
    proposal_only_types = set(config["proposalOnlyRelationshipTypes"])
    proposal_edges = [dict(edge, proposalStatus="sandbox-proposed", canonicalWrites=False) for edge in edges if edge.get("type") in proposal_only_types]
    consumable_edges = [edge for edge in edges if edge.get("type") not in proposal_only_types]
    by_type = Counter(str(edge.get("type") or "") for edge in consumable_edges)
    proposal_by_type = Counter(str(edge.get("type") or "") for edge in proposal_edges)
    by_corpus = Counter(
        str((edge.get("anchorEvidence") or {}).get("corpusId") or edge.get("sourceId") or "") for edge in consumable_edges
    )
    output_root.mkdir(parents=True, exist_ok=True)
    edges_path = output_root / OUTPUT_JSONL_NAME
    proposal_path = output_root / PROPOSAL_JSONL_NAME
    residual_path = output_root / RESIDUAL_JSONL_NAME
    summary_path = output_root / SUMMARY_JSON_NAME
    write_jsonl(edges_path, consumable_edges)
    write_jsonl(proposal_path, proposal_edges)
    residual_limit = int(config["residualWindowTelemetry"].get("maxRows") or 0)
    residual_output_rows = residual_rows[:residual_limit] if residual_limit > 0 else residual_rows
    write_jsonl(residual_path, residual_output_rows)
    summary = {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "anchor-passage-relationship-extraction",
        "canonicalWrites": False,
        "inputs": {
            "anchorIndexRoot": repo_relative(anchor_index_root),
            "sourceConfigPath": repo_relative(source_config_path),
            "aliasMapPath": repo_relative(alias_map_path),
            "generalsPath": repo_relative(generals_path),
            "stableBootstrapPath": repo_relative(stable_bootstrap_path) if stable_bootstrap_path else None,
            "passagePaths": [repo_relative(path) for path in paths],
        },
        "outputs": {
            "relationshipEdges": repo_relative(edges_path),
            "proposalOnlyRelationshipEdges": repo_relative(proposal_path),
            "residualWindowTelemetry": repo_relative(residual_path),
            "summary": repo_relative(summary_path),
        },
        "config": config,
        "metrics": {
            "passageCount": int(totals["passages"]),
            "windowCount": int(totals["windows"]),
            "eligiblePassageCount": int(totals["eligiblePassages"]),
            "eligibleWindowCount": int(totals["eligiblePassages"]),
            "candidatePassageCount": int(totals["candidatePassages"]),
            "candidateWindowCount": int(totals["candidatePassages"]),
            "cueMatchedPassageCount": int(totals["cueMatchedPassages"]),
            "cueMatchedWindowCount": int(totals["cueMatchedPassages"]),
            "residualWindowCount": int(totals["residualWindows"]),
            "residualWindowOutputCount": len(residual_output_rows),
            "pairCandidateCount": int(totals["pairCandidates"]),
            "cueMatchCount": int(totals["cueMatches"]),
            "edgeCount": len(consumable_edges),
            "proposalOnlyEdgeCount": len(proposal_edges),
            "relationshipTypeCounts": dict(sorted(by_type.items())),
            "proposalOnlyRelationshipTypeCounts": dict(sorted(proposal_by_type.items())),
            "corpusCounts": dict(sorted(by_corpus.items())),
            "aliasGeneralCount": len(alias_index),
            "knownOrientationPairCount": len(orientation_index),
            "relationshipTypeCount": len(relationship_types_to_check),
        },
        "passageInputs": path_stats,
    }
    write_json(summary_path, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract relationship evidence from local anchor passages.")
    parser.add_argument("--anchor-index-root", default=str(DEFAULT_ANCHOR_INDEX_ROOT))
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG_PATH))
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP_PATH))
    parser.add_argument("--generals", default=str(DEFAULT_GENERALS_PATH))
    parser.add_argument("--stable-knowledge", default=str(DEFAULT_STABLE_BOOTSTRAP_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--governance-root", default=str(DEFAULT_GOVERNANCE_ROOT))
    parser.add_argument("--relationship-policy", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stable_path = resolve_path(args.stable_knowledge) if args.stable_knowledge else None
    summary = extract_anchor_passage_relationship_edges(
        anchor_index_root=resolve_path(args.anchor_index_root),
        source_config_path=resolve_path(args.source_config),
        alias_map_path=resolve_path(args.alias_map),
        generals_path=resolve_path(args.generals),
        stable_bootstrap_path=stable_path,
        output_root=resolve_path(args.output_root),
        governance_root=resolve_path(args.governance_root),
        relationship_policy=args.relationship_policy,
        overwrite=bool(args.overwrite),
    )
    print(
        "[extract_anchor_passage_relationship_edges] "
        f"edges={summary['metrics']['edgeCount']} passages={summary['metrics']['passageCount']} "
        f"eligible={summary['metrics']['eligiblePassageCount']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
