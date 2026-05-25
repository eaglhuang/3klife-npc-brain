from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def ingest_identity_value(id_to_name: dict[str, str], name_to_ids: dict[str, list[str]], *, general_id: str, name: str) -> None:
    general_id = general_id.strip()
    name = name.strip()
    if not general_id or not name:
        return
    if general_id not in id_to_name:
        id_to_name[general_id] = name
    values = name_to_ids[name]
    if general_id not in values:
        values.append(general_id)


def build_identity_maps(stable_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    payload = read_json(stable_path)
    seeds = payload.get("identitySeeds")
    id_to_name: dict[str, str] = {}
    name_to_ids: dict[str, list[str]] = defaultdict(list)
    if not isinstance(seeds, list):
        seeds = []

    for row in seeds:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        if not general_id:
            continue
        name = first_non_empty_text(row.get("name"), row.get("title"), general_id)
        id_to_name[general_id] = name
        aliases = [name]
        alias_values = row.get("aliases")
        if isinstance(alias_values, list):
            aliases.extend(str(item or "").strip() for item in alias_values)
        for alias in aliases:
            alias_text = alias.strip()
            if not alias_text:
                continue
            ingest_identity_value(id_to_name, name_to_ids, general_id=general_id, name=alias_text)

    # stable-knowledge 內大量 fromId/fromName 與 toId/toName 會補齊人名映射。
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
                general_id=str(row.get("fromId") or ""),
                name=str(row.get("fromName") or ""),
            )
            ingest_identity_value(
                id_to_name,
                name_to_ids,
                general_id=str(row.get("toId") or ""),
                name=str(row.get("toName") or ""),
            )
    return id_to_name, name_to_ids


def parse_time_scope(row: dict[str, Any]) -> str:
    start = int(row.get("validFromChapter") or 0)
    end = int(row.get("validToChapter") or 0)
    if start > 0 and end > 0:
        return f"第{start:03d}回至第{end:03d}回"
    if start > 0:
        return f"第{start:03d}回後"
    if end > 0:
        return f"第{end:03d}回前"
    return "時段未明"


def resolve_name_to_id(name: str, name_to_ids: dict[str, list[str]]) -> str:
    candidates = name_to_ids.get(name) or []
    return candidates[0] if candidates else ""


def spec_to_edges(
    spec_rows: list[dict[str, Any]],
    *,
    allowed_types: set[str],
    symmetric_types: set[str],
    name_to_ids: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in spec_rows:
        relationship_type = str(row.get("type") or "").strip()
        if relationship_type not in allowed_types:
            continue
        confidence = float(row.get("confidence") or 0.85)
        time_scope = parse_time_scope(row)
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
                if relationship_type in symmetric_types:
                    from_id, to_id = sorted([left_id, right_id])
                else:
                    from_id, to_id = left_id, right_id
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


def run_focus_rows(
    bundle_rows: list[dict[str, Any]],
    *,
    edge_rows: list[dict[str, Any]],
    id_to_name: dict[str, str],
    symmetric_types: set[str],
    quote_max_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    edges_by_focus: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edge_rows:
        edges_by_focus[edge["fromId"]].append(edge)
        edges_by_focus[edge["toId"]].append(edge)

    output_rows: list[dict[str, Any]] = []
    relationship_counter: Counter[str] = Counter()
    unresolved_support: list[dict[str, Any]] = []

    for bundle in bundle_rows:
        focus_id = str(bundle.get("focusGeneralId") or "").strip()
        if not focus_id:
            continue
        focus_name = first_non_empty_text(bundle.get("focusNameZhTw"), id_to_name.get(focus_id), focus_id)
        candidate_ids = {str(item).strip() for item in bundle.get("candidateCounterpartIds") or [] if str(item or "").strip()}
        passages = bundle.get("passages")
        if not isinstance(passages, list):
            passages = []

        relationships: list[dict[str, Any]] = []
        seen_relationships: set[tuple[str, str, str]] = set()
        for edge in edges_by_focus.get(focus_id, []):
            from_id = str(edge.get("fromId") or "").strip()
            to_id = str(edge.get("toId") or "").strip()
            relationship_type = str(edge.get("relationshipType") or "").strip()
            if not from_id or not to_id or not relationship_type:
                continue

            counterpart_id = to_id if from_id == focus_id else from_id
            if counterpart_id not in candidate_ids:
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
                unresolved_support.append(
                    {
                        "focusGeneralId": focus_id,
                        "counterpartGeneralId": counterpart_id,
                        "relationshipType": relationship_type,
                        "sourceSpecId": edge.get("sourceSpecId"),
                    }
                )
                continue

            key = (from_id, to_id, relationship_type)
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            relationship_counter[relationship_type] += 1
            relationships.append(
                {
                    "fromId": from_id,
                    "toId": to_id,
                    "relationshipType": relationship_type,
                    "relationshipDirection": "bidirectional" if relationship_type in symmetric_types else "directed",
                    "timeScopeZhTw": str(edge.get("timeScopeZhTw") or "時段未明"),
                    "evidenceQuoteZhTw": trim_text(str(supporting.get("normalizedText") or ""), quote_max_chars),
                    "chapterRef": str(supporting.get("chapterRef") or ""),
                    "sourcePassageRef": str(supporting.get("locator") or ""),
                    "confidence": round(float(edge.get("confidence") or 0.85), 4),
                    "reasonZhTw": f"依據硬關係規格 {edge.get('sourceSpecId')}，並在白話 passage 找到人物同段證據。",
                    "canonicalWrites": False,
                }
            )

        relationships.sort(key=lambda row: (str(row.get("relationshipType")), str(row.get("fromId")), str(row.get("toId"))))
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
        "unresolvedSupportingEvidenceCount": len(unresolved_support),
        "unresolvedSupportingEvidence": unresolved_support[:200],
    }
    return output_rows, summary


def augment_identity_maps_from_bundles(
    bundle_rows: list[dict[str, Any]],
    id_to_name: dict[str, str],
    name_to_ids: dict[str, list[str]],
) -> None:
    for row in bundle_rows:
        focus_id = str(row.get("focusGeneralId") or "").strip()
        focus_name = str(row.get("focusNameZhTw") or "").strip()
        if focus_id and focus_name:
            ingest_identity_value(id_to_name, name_to_ids, general_id=focus_id, name=focus_name)


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
        bundle_path = str(row.get("bundlePath") or "").strip()
        if bundle_path:
            path = Path(bundle_path)
            if path.exists():
                payload = read_json(path)
                hydrated.append(payload)
                continue
        hydrated.append(row)
    return hydrated


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
    hard_spec_rows = read_jsonl(hard_spec_path)
    policy = read_json(policy_path)
    relation_policy = policy.get("relationshipTypes") if isinstance(policy.get("relationshipTypes"), dict) else {}
    allowed_types = {str(item).strip() for item in relation_policy.get("allowed") or [] if str(item or "").strip()}
    symmetric_types = {str(item).strip() for item in relation_policy.get("symmetric") or [] if str(item or "").strip()}
    if not allowed_types:
        raise ValueError(f"policy relationshipTypes.allowed missing: {policy_path}")

    id_to_name, name_to_ids = build_identity_maps(stable_path)
    augment_identity_maps_from_bundles(bundle_rows, id_to_name, name_to_ids)
    edge_rows, unresolved_specs = spec_to_edges(
        hard_spec_rows,
        allowed_types=allowed_types,
        symmetric_types=symmetric_types,
        name_to_ids=name_to_ids,
    )
    output_rows, runner_summary = run_focus_rows(
        bundle_rows,
        edge_rows=edge_rows,
        id_to_name=id_to_name,
        symmetric_types=symmetric_types,
        quote_max_chars=max(40, int(args.quote_max_chars)),
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
            **runner_summary,
        },
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
        f"focus={runner_summary['focusCount']} relationships={runner_summary['relationshipCount']} "
        f"unresolvedSupportingEvidence={runner_summary['unresolvedSupportingEvidenceCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
