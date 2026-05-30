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


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_QUEUE_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/runtime-projection-upstream-feedback/"
    "runtime-projection-upstream-feedback-queue.jsonl"
)
DEFAULT_SOURCE_EVENT_PACKETS_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/source-event-packets/source-event-packets.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path("local/codex-smoke/runtime-projection-refill-overlay")
DEFAULT_OUTPUT_FILE_NAME = "runtime-projection-refill-overlay-spec.json"
DEFAULT_SUMMARY_FILE_NAME = "runtime-projection-refill-overlay-summary.json"
ALLOWED_MISSING_FIELDS = {"relationshipEdges", "relationshipRefs"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build bulk runtime-projection refill overlay specs from queue rows that only need participant promotion."
    )
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE_PATH))
    parser.add_argument("--source-event-packets", default=str(DEFAULT_SOURCE_EVENT_PACKETS_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-file-name", default=DEFAULT_OUTPUT_FILE_NAME)
    parser.add_argument("--summary-file-name", default=DEFAULT_SUMMARY_FILE_NAME)
    parser.add_argument("--round-id", default="")
    parser.add_argument(
        "--generated-for",
        default="runtime projection upstream refill auto-generated participant bridge overlay",
    )
    parser.add_argument("--target-general-id", action="append", default=[])
    parser.add_argument("--general-id", action="append", default=[])
    parser.add_argument("--source-ref", action="append", default=[])
    parser.add_argument("--source-ref-file", default="")
    parser.add_argument(
        "--top-source-refs",
        type=int,
        default=0,
        help="Keep only the hottest sourceRefs after filtering, ordered by matching queue rows.",
    )
    parser.add_argument(
        "--include-alias-mixed",
        action="store_true",
        help="Also include aliasMatch+declaredGeneralIds rows. Default is declaredGeneralIds-only for safer bulk promotion.",
    )
    parser.add_argument("--edge-type", default="battlefield_contact")
    parser.add_argument("--pattern", default="declared-general-scene-participant-bridge")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_round_id() -> str:
    return f"runtime-projection-refill-auto-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    search_roots = [REPO_ROOT, REPO_ROOT.parent, REPO_ROOT.parent.parent]
    for root in search_roots:
        candidate = (root / path).resolve()
        if candidate.exists():
            return candidate
    return (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Any, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        cleaned = str(item or "").strip()
        if cleaned:
            result.append(cleaned)
    return result


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def safe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_chapter_no(source_ref: str) -> int | None:
    match = re.match(r"(\d{1,3})#p\d+", str(source_ref or "").strip())
    if not match:
        return None
    return int(match.group(1))


def stable_hash(*parts: Any, length: int = 16) -> str:
    body = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:length]


def load_source_refs(path_text: str) -> list[str]:
    if not path_text:
        return []
    path = resolve_path(path_text)
    if not path.exists():
        return []
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # '#' is part of sourceRef syntax (e.g. '001#p10'); only treat as comment when at line start.
        if stripped.startswith("#"):
            continue
        values.append(stripped)
    return values


def candidate_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    source_type = str(row.get("sourceType") or "").strip()
    trace_sources = set(string_list(row.get("traceSources")))
    return (
        1 if source_type == "storyBeat" else 0,
        len(normalize_text(row.get("sourceQuote"))),
        1 if trace_sources == {"declaredGeneralIds"} else 0,
    )


def select_candidate(existing: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return row
    return row if candidate_rank(row) > candidate_rank(existing) else existing


def eligible_trace_sources(row: dict[str, Any], include_alias_mixed: bool) -> bool:
    trace_sources = set(string_list(row.get("traceSources")))
    if trace_sources == {"declaredGeneralIds"}:
        return True
    return include_alias_mixed and trace_sources == {"aliasMatch", "declaredGeneralIds"}


def qualifying_reason(row: dict[str, Any], include_alias_mixed: bool) -> str | None:
    if str(row.get("proposalType") or "").strip() != "projection-source-gap":
        return "skip_non_projection_gap"
    if str(row.get("linkAuthority") or "").strip() != "source_event_participant":
        return "skip_link_authority"
    if not eligible_trace_sources(row, include_alias_mixed):
        return "skip_trace_sources"
    missing_fields = set(string_list(row.get("missingFields")))
    if not missing_fields or not missing_fields <= ALLOWED_MISSING_FIELDS:
        return "skip_missing_fields"
    if not str(row.get("sourceRef") or "").strip():
        return "skip_missing_source_ref"
    if not str(row.get("generalId") or "").strip() or not str(row.get("targetGeneralId") or "").strip():
        return "skip_missing_general_ids"
    return None


def score_for_row(row: dict[str, Any]) -> tuple[float, float]:
    trace_sources = set(string_list(row.get("traceSources")))
    source_type = str(row.get("sourceType") or "").strip()
    edge_confidence = 0.66 if source_type == "storyBeat" else 0.62
    edge_strength = 0.53 if source_type == "storyBeat" else 0.49
    if trace_sources != {"declaredGeneralIds"}:
        edge_confidence -= 0.04
        edge_strength -= 0.03
    return round(edge_confidence, 2), round(edge_strength, 2)


def queue_row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("sourceRef") or "").strip(),
        str(row.get("generalId") or "").strip(),
        str(row.get("targetGeneralId") or "").strip(),
    )


def build_edge_ids(round_slug: str, source_ref: str, general_id: str, edge_type: str, target_general_id: str) -> tuple[str, str]:
    ref_key = safe_key(source_ref.replace("#", "-"))
    pair_key = f"{general_id}.{safe_key(edge_type)}.{target_general_id}"
    packet_edge_id = f"packet-rel.{round_slug}.{ref_key}.{pair_key}"
    relationship_edge_id = f"rel.{round_slug}.{ref_key}.{pair_key}"
    return packet_edge_id, relationship_edge_id


def build_patch_reason(source_ref: str, rows: list[dict[str, Any]]) -> str:
    pairs = unique(
        [
            f"{str(row.get('generalId') or row.get('fromId') or '').strip()} -> "
            f"{str(row.get('targetGeneralId') or row.get('toId') or '').strip()}"
            for row in rows
        ]
    )
    preview = ", ".join(pairs[:4])
    if len(pairs) > 4:
        preview += f", +{len(pairs) - 4} more"
    return f"Bulk-generated participant bridge edges for {source_ref}: {preview}."


def build_overlay_rows(
    rows: list[dict[str, Any]],
    *,
    round_id: str,
    edge_type: str,
    pattern: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packet_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relationship_rows: list[dict[str, Any]] = []
    round_slug = safe_key(round_id)

    for row in rows:
        source_ref = str(row.get("sourceRef") or "").strip()
        general_id = str(row.get("generalId") or "").strip()
        target_general_id = str(row.get("targetGeneralId") or "").strip()
        chapter_no = parse_chapter_no(source_ref)
        source_quote = normalize_text(row.get("sourceQuote"))
        edge_confidence, edge_strength = score_for_row(row)
        packet_edge_id, relationship_edge_id = build_edge_ids(round_slug, source_ref, general_id, edge_type, target_general_id)
        promotion_trace = [
            round_id,
            pattern,
            "auto-generated-from-queue",
            f"sourceRef:{source_ref}",
        ]
        packet_groups[source_ref].append(
            {
                "edgeId": packet_edge_id,
                "fromId": general_id,
                "toId": target_general_id,
                "type": edge_type,
                "originalType": edge_type,
                "evidenceRefs": [source_ref],
                "sourceQuote": source_quote,
                "edgeConfidence": edge_confidence,
                "edgeStrength": edge_strength,
                "directPairSignal": True,
                "pairRelationSignal": False,
                "pattern": pattern,
                "promotionTrace": promotion_trace,
                "sourceLayer": "runtime-projection-upstream-refill-staged",
                "trustTier": "primary-text-transcription",
                "claimGrade": "source-packet-candidate",
                "reviewStatus": "source-grounded-review",
                "canonicalWrites": False,
            }
        )
        relationship_rows.append(
            {
                "chapterNo": chapter_no,
                "edgeId": relationship_edge_id,
                "fromId": general_id,
                "toId": target_general_id,
                "type": edge_type,
                "originalType": edge_type,
                "evidenceRefs": [source_ref],
                "evidenceText": source_quote,
                "sourceQuote": source_quote,
                "summary": (
                    f"Bulk-generated participant bridge for {source_ref}, promoting "
                    f"{general_id} / {target_general_id} into relationship evidence."
                ),
                "edgeConfidence": edge_confidence,
                "edgeStrength": edge_strength,
                "directPairSignal": True,
                "pairRelationSignal": False,
                "pattern": pattern,
                "promotionTrace": promotion_trace,
                "sourceLayer": "runtime-projection-upstream-refill-staged",
                "trustTier": "primary-text-transcription",
                "claimGrade": "source-packet-candidate",
                "reviewStatus": "source-grounded-review",
                "canonicalWrites": False,
            }
        )

    packet_patches = [
        {
            "sourceRef": source_ref,
            "patchReason": build_patch_reason(source_ref, source_rows),
            "relationshipEdges": source_rows,
        }
        for source_ref, source_rows in sorted(packet_groups.items())
    ]
    relationship_rows.sort(
        key=lambda row: (
            str(row.get("evidenceRefs", [""])[0]),
            str(row.get("fromId") or ""),
            str(row.get("toId") or ""),
        )
    )
    return packet_patches, relationship_rows


def main() -> int:
    args = parse_args()
    round_id = args.round_id or default_round_id()
    queue_path = resolve_path(args.queue)
    source_event_packets_path = resolve_path(args.source_event_packets)
    output_root = resolve_path(args.output_root)
    output_path = output_root / str(args.output_file_name)
    summary_path = output_root / str(args.summary_file_name)

    target_general_ids = set(unique(string_list(args.target_general_id)))
    general_ids = set(unique(string_list(args.general_id)))
    source_refs = set(unique(string_list(args.source_ref) + load_source_refs(args.source_ref_file)))

    queue_rows = read_jsonl(queue_path)
    packets = read_jsonl(source_event_packets_path)
    packets_by_ref = {
        str(row.get("sourceRef") or "").strip(): row
        for row in packets
        if str(row.get("sourceRef") or "").strip()
    }

    skip_counts: Counter[str] = Counter()
    candidate_rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_ref_queue_counts: Counter[str] = Counter()
    selected_queue_rows = 0

    for row in queue_rows:
        reason = qualifying_reason(row, args.include_alias_mixed)
        if reason:
            skip_counts[reason] += 1
            continue
        source_ref, general_id, target_general_id = queue_row_key(row)
        if target_general_ids and target_general_id not in target_general_ids:
            skip_counts["skip_target_general_filter"] += 1
            continue
        if general_ids and general_id not in general_ids:
            skip_counts["skip_general_filter"] += 1
            continue
        if source_refs and source_ref not in source_refs:
            skip_counts["skip_source_ref_filter"] += 1
            continue
        packet = packets_by_ref.get(source_ref)
        if not packet:
            skip_counts["skip_missing_packet"] += 1
            continue
        participants = set(string_list(packet.get("generalIds")))
        if general_id not in participants or target_general_id not in participants:
            skip_counts["skip_packet_missing_participants"] += 1
            continue
        selected_queue_rows += 1
        source_ref_queue_counts[source_ref] += 1
        enriched = {
            **row,
            "sourceQuote": normalize_text(
                row.get("sourceQuote")
                or row.get("sourceTitle")
                or (packet.get("examples") or [""])[0]
                or packet.get("sourceQuote")
            ),
        }
        key = (source_ref, general_id, target_general_id)
        candidate_rows_by_key[key] = select_candidate(candidate_rows_by_key.get(key), enriched)

    selected_source_refs = set(source_ref_queue_counts)
    if args.top_source_refs > 0:
        selected_source_refs = {
            source_ref
            for source_ref, _count in sorted(
                source_ref_queue_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[: args.top_source_refs]
        }
    filtered_queue_row_count = sum(source_ref_queue_counts.get(source_ref, 0) for source_ref in selected_source_refs)

    candidate_rows = [
        row
        for key, row in candidate_rows_by_key.items()
        if key[0] in selected_source_refs
    ]
    candidate_rows.sort(
        key=lambda row: (
            -source_ref_queue_counts.get(str(row.get("sourceRef") or ""), 0),
            str(row.get("sourceRef") or ""),
            str(row.get("generalId") or ""),
            str(row.get("targetGeneralId") or ""),
        )
    )

    packet_patches, relationship_rows = build_overlay_rows(
        candidate_rows,
        round_id=round_id,
        edge_type=str(args.edge_type or "battlefield_contact").strip() or "battlefield_contact",
        pattern=str(args.pattern or "declared-general-scene-participant-bridge").strip()
        or "declared-general-scene-participant-bridge",
    )

    target_counts = Counter(str(row.get("targetGeneralId") or "") for row in candidate_rows)
    source_ref_counts = Counter(str(row.get("sourceRef") or "") for row in candidate_rows)
    pair_counts = Counter(
        (str(row.get("generalId") or ""), str(row.get("targetGeneralId") or ""))
        for row in candidate_rows
    )
    summary = {
        "generatedAt": utc_now(),
        "mode": "runtime-projection-refill-overlay-spec-builder",
        "canonicalWrites": False,
        "inputs": {
            "queuePath": repo_relative(queue_path),
            "sourceEventPacketsPath": repo_relative(source_event_packets_path),
        },
        "filters": {
            "targetGeneralIds": sorted(target_general_ids),
            "generalIds": sorted(general_ids),
            "sourceRefs": sorted(source_refs),
            "topSourceRefs": int(args.top_source_refs or 0),
            "includeAliasMixed": bool(args.include_alias_mixed),
            "allowedMissingFields": sorted(ALLOWED_MISSING_FIELDS),
        },
        "counts": {
            "queueRowCount": len(queue_rows),
            "selectedQueueRowCount": filtered_queue_row_count,
            "candidateCount": len(candidate_rows),
            "packetPatchCount": len(packet_patches),
            "relationshipEvidenceAddCount": len(relationship_rows),
        },
        "skipCounts": dict(sorted(skip_counts.items())),
        "topTargetGeneralIds": dict(target_counts.most_common(20)),
        "topSourceRefs": dict(source_ref_counts.most_common(25)),
        "topGeneralTargetPairs": {
            f"{general_id}->{target_general_id}": count
            for (general_id, target_general_id), count in pair_counts.most_common(25)
        },
        "outputs": {
            "outputPath": repo_relative(output_path),
            "summaryPath": repo_relative(summary_path),
        },
    }
    spec = {
        "version": "1.0.0",
        "roundId": round_id,
        "generatedFor": str(args.generated_for or "").strip(),
        "canonicalWrites": False,
        "packetPatches": packet_patches,
        "relationshipEvidenceAdds": relationship_rows,
    }

    if not args.dry_run:
        write_json(output_path, spec, overwrite=args.overwrite)
        write_json(summary_path, summary, overwrite=args.overwrite)

    print(
        "[build_runtime_projection_refill_overlay_spec] "
        f"candidateCount={summary['counts']['candidateCount']} "
        f"packetPatchCount={summary['counts']['packetPatchCount']} "
        f"selectedQueueRowCount={summary['counts']['selectedQueueRowCount']} dryRun={args.dry_run}"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())