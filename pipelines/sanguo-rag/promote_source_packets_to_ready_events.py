from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from primary_canon_inputs import choose_primary_or_fallback, latest_primary_canon_artifact_paths, primary_canon_metadata
from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
CALLER_CWD = Path.cwd()

DEFAULT_CORE_REPORT_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/top50-runtime-fill-r1.json")
DEFAULT_BASE_READY_EVENTS_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/"
    "full-roster-highway-r1-continue-r5-r1-precision-a3-rerun1-merged-staged-ready-events.jsonl"
)
DEFAULT_SOURCE_EVENT_PACKETS_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/source-event-packets/source-event-packets.jsonl")
DEFAULT_RELATIONSHIP_EVIDENCE_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/"
    "full-roster-highway-r1-continue-r5-r1-precision-a3-rerun1-merged-staged-relationship-evidence.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress")
DEFAULT_ROUND_ID = "prog-2-0019"

PACKET_STRENGTH_RANK = {"strong": 3, "rich": 2, "thin": 1}
PROMOTABLE_STRENGTHS = {"strong", "rich"}
BLOCKED_REVIEW_STATUSES = {"alias-only", "relationship_external", "missing-pair-relation-cue"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote source-event packets into upstream staged ready-event candidates.")
    parser.add_argument("--general-id", action="append", default=[], help="Target generalId. Can be repeated.")
    parser.add_argument("--core-report", default=str(DEFAULT_CORE_REPORT_PATH))
    parser.add_argument("--base-ready-events", default=str(DEFAULT_BASE_READY_EVENTS_PATH))
    parser.add_argument("--source-event-packets", default="")
    parser.add_argument("--relationship-evidence", default="")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--round-id", default=DEFAULT_ROUND_ID)
    parser.add_argument("--limit-per-general", type=int, default=3)
    parser.add_argument("--max-ready-events", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-primary-canon-defaults", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_cli_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else CALLER_CWD / path


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {path}. Re-run with --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {path}. Re-run with --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def merge_ready_events(base_rows: list[dict[str, Any]], promoted_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for row in [*base_rows, *promoted_rows]:
        event_id = str(row.get("eventId") or "").strip()
        if not event_id:
            event_id = slug("|".join(str(ref) for ref in (row.get("sourceRefs") or [])) + str(row.get("summary") or ""))
        if event_id not in merged:
            ordered.append(event_id)
        merged[event_id] = row
    return [merged[event_id] for event_id in ordered]


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "")).strip("-").lower()
    return cleaned or hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def text_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(value or "").strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def choose_targets(args: argparse.Namespace, core_report: dict[str, Any]) -> list[str]:
    if args.general_id:
        return unique(args.general_id)
    targets: list[str] = []
    for person in core_report.get("people") or []:
        general_id = str(person.get("generalId") or "").strip()
        if not general_id:
            continue
        ready_count = int(((person.get("observedCounts") or {}).get("readyEventCount") or 0))
        if args.max_ready_events and ready_count > args.max_ready_events:
            continue
        targets.append(general_id)
    return unique(targets)


def resolve_input_paths(args: argparse.Namespace) -> dict[str, Any]:
    run_root, primary_paths = (None, {})
    if not args.no_primary_canon_defaults:
        run_root, primary_paths = latest_primary_canon_artifact_paths()
    source_event_packets = (
        resolve_cli_path(args.source_event_packets)
        if args.source_event_packets
        else resolve_cli_path(choose_primary_or_fallback("sourceEventPackets", DEFAULT_SOURCE_EVENT_PACKETS_PATH, primary_paths))
    )
    relationship_evidence = (
        resolve_cli_path(args.relationship_evidence)
        if args.relationship_evidence
        else resolve_cli_path(choose_primary_or_fallback("relationshipEvidence", DEFAULT_RELATIONSHIP_EVIDENCE_PATH, primary_paths))
    )
    return {
        "sourceEventPackets": source_event_packets,
        "relationshipEvidence": relationship_evidence,
        "primaryCanonDefaults": primary_canon_metadata(run_root, primary_paths),
    }


def index_relationships(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for ref in row.get("evidenceRefs") or []:
            by_ref[str(ref)].append(row)
    return by_ref


def edge_allowed(edge: dict[str, Any], general_id: str) -> bool:
    if edge.get("fromId") != general_id and edge.get("toId") != general_id:
        return False
    review_status = str(edge.get("reviewStatus") or "").strip()
    source_layer = str(edge.get("sourceLayer") or "").strip()
    promotion_trace = {str(item) for item in edge.get("promotionTrace") or []}
    if review_status in BLOCKED_REVIEW_STATUSES or source_layer == "relationship_external":
        return False
    if "missing-pair-relation-cue" in promotion_trace:
        return False
    return True


def edge_projection(edge: dict[str, Any], source_ref: str) -> dict[str, Any]:
    return {
        "fromId": edge.get("fromId"),
        "toId": edge.get("toId"),
        "type": edge.get("type") or edge.get("originalType") or "relationship",
        "evidenceRefs": list(edge.get("evidenceRefs") or [source_ref]),
        "edgeConfidence": edge.get("edgeConfidence") or edge.get("confidence") or 0.66,
        "edgeStrength": edge.get("edgeStrength"),
        "sourceLayer": edge.get("sourceLayer"),
        "trustTier": edge.get("trustTier"),
        "claimGrade": edge.get("claimGrade"),
        "directPairSignal": bool(edge.get("directPairSignal")),
        "pairRelationSignal": bool(edge.get("pairRelationSignal")),
        "promotionTrace": list(edge.get("promotionTrace") or []),
    }


def packet_summary(packet: dict[str, Any]) -> str:
    examples = [str(item).strip() for item in packet.get("examples") or [] if str(item).strip()]
    if not examples:
        return f"sourceRef {packet.get('sourceRef')} has source-grounded event packet signals."
    return " / ".join(examples[:2])[:220]


def packet_source_quote(packet: dict[str, Any]) -> str | None:
    examples = [str(item).strip() for item in packet.get("examples") or [] if str(item).strip()]
    return examples[0] if examples else None


def packet_score(packet: dict[str, Any], edges: list[dict[str, Any]]) -> tuple[int, int, int]:
    strength = PACKET_STRENGTH_RANK.get(str(packet.get("packetStrength") or ""), 0)
    return (strength, len(edges), len(packet.get("angleFamilies") or []))


def promote_packet(packet: dict[str, Any], general_id: str, edges: list[dict[str, Any]], round_id: str) -> dict[str, Any]:
    source_ref = str(packet.get("sourceRef") or "").strip()
    source_quote = packet_source_quote(packet)
    promotion_trace = unique([
        "source-event-packet-promotion",
        f"packet-strength:{packet.get('packetStrength')}",
        *[f"angle:{angle}" for angle in packet.get("angleFamilies") or []],
        *(["relationship-evidence-pass"] if edges else []),
    ])
    return {
        "eventId": f"romance.{round_id}.{slug(general_id + '.' + source_ref)}",
        "chapterNo": packet.get("chapterNo"),
        "eventKey": f"{round_id}.{general_id}.{slug(source_ref)}",
        "eventType": "source_packet_promotion",
        "subtype": "primary_canon_ready_event_candidate",
        "generalIds": list(packet.get("generalIds") or []),
        "location": None,
        "summary": packet_summary(packet),
        "sourceQuote": source_quote,
        "relationshipEdges": [edge_projection(edge, source_ref) for edge in edges],
        "moodTags": ["source-packet-promotion"],
        "affectTags": [angle for angle in packet.get("angleFamilies") or [] if angle == "affect_story"],
        "aptitudeTags": [angle for angle in packet.get("angleFamilies") or [] if angle in {"battle", "aptitude_talent"}],
        "roleActivityTags": [angle for angle in packet.get("angleFamilies") or [] if angle in {"work_role", "activity_seed"}],
        "activitySeedHints": list(packet.get("angleFamilies") or []),
        "choiceWeightHints": [],
        "decisionWeightHints": [angle for angle in packet.get("angleFamilies") or [] if angle == "decision_weight"],
        "itemRefs": [],
        "confidence": 0.74 if edges else 0.68,
        "sourceRefs": [source_ref],
        "unresolvedParticipants": [],
        "extractionMode": "source-packet-promotion-v1",
        "reviewStatus": "accepted-review-candidate",
        "canonicalWrites": False,
        "sourceLayer": packet.get("sourceLayer") or "primary-canon-source-event-packet",
        "sourceFamily": packet.get("sourceFamily") or "sanguoyanyi",
        "trustTier": packet.get("trustTier") or "primary-text-transcription",
        "claimGrade": "source-packet-candidate",
        "directPairSignal": any(bool(edge.get("directPairSignal")) for edge in edges),
        "pairRelationSignal": any(bool(edge.get("pairRelationSignal")) for edge in edges),
        "promotionTrace": promotion_trace,
        "textHash": text_hash("\n".join([source_ref, source_quote or "", packet_summary(packet)])),
        "sourcePacketId": packet.get("packetId"),
        "angleFamilies": list(packet.get("angleFamilies") or []),
    }


def main() -> None:
    args = parse_args()
    core_report_path = resolve_cli_path(args.core_report)
    base_ready_events_path = resolve_cli_path(args.base_ready_events)
    output_root = resolve_cli_path(args.output_root)
    inputs = resolve_input_paths(args)
    required = [core_report_path, base_ready_events_path, inputs["sourceEventPackets"], inputs["relationshipEvidence"]]
    missing = [repo_relative(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing input files: {missing}")

    core_report = read_json(core_report_path)
    targets = choose_targets(args, core_report)
    target_set = set(targets)
    base_ready_events = read_jsonl(base_ready_events_path)
    packets = read_jsonl(inputs["sourceEventPackets"])
    relationships_by_ref = index_relationships(read_jsonl(inputs["relationshipEvidence"]))
    candidates_by_general: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []

    for packet in packets:
        source_ref = str(packet.get("sourceRef") or "").strip()
        packet_generals = [str(item) for item in packet.get("generalIds") or []]
        packet_targets = [general_id for general_id in packet_generals if general_id in target_set]
        if not packet_targets:
            continue
        strength = str(packet.get("packetStrength") or "").strip()
        if strength not in PROMOTABLE_STRENGTHS:
            for general_id in packet_targets:
                rejected.append({"generalId": general_id, "sourceRef": source_ref, "reason": f"packetStrength:{strength or 'missing'}"})
            continue
        if not source_ref or not packet.get("examples"):
            for general_id in packet_targets:
                rejected.append({"generalId": general_id, "sourceRef": source_ref, "reason": "missing-sourceRef-or-examples"})
            continue
        for general_id in packet_targets:
            allowed_edges = [edge for edge in relationships_by_ref.get(source_ref, []) if edge_allowed(edge, general_id)]
            candidates_by_general[general_id].append((packet, allowed_edges))

    ready_events: list[dict[str, Any]] = []
    promotion_queue: list[dict[str, Any]] = []
    per_general: dict[str, dict[str, Any]] = {}
    for general_id in targets:
        rows = sorted(
            candidates_by_general.get(general_id, []),
            key=lambda item: packet_score(item[0], item[1]),
            reverse=True,
        )
        if args.limit_per_general > 0:
            rows = rows[: args.limit_per_general]
        for packet, edges in rows:
            event = promote_packet(packet, general_id, edges, args.round_id)
            ready_events.append(event)
            promotion_queue.append({
                "generalId": general_id,
                "sourceRef": packet.get("sourceRef"),
                "sourcePacketId": packet.get("packetId"),
                "packetStrength": packet.get("packetStrength"),
                "angleFamilies": packet.get("angleFamilies") or [],
                "relationshipEdgeCount": len(edges),
                "promotionStatus": "staged-ready-event-candidate",
                "canonicalWrites": False,
                "promotionTrace": event["promotionTrace"],
                "textHash": event["textHash"],
            })
        per_general[general_id] = {
            "candidatePacketCount": len(candidates_by_general.get(general_id, [])),
            "promotedReadyEventCount": len(rows),
            "rejectedCount": sum(1 for item in rejected if item.get("generalId") == general_id),
        }

    ready_events_path = output_root / f"{args.round_id}-promoted-ready-events.jsonl"
    merged_ready_events_path = output_root / f"{args.round_id}-merged-ready-events.jsonl"
    queue_path = output_root / f"{args.round_id}-source-packet-promotion-queue.jsonl"
    report_path = output_root / f"{args.round_id}-ready-event-promotion-report.json"
    review_path = output_root / f"{args.round_id}-ready-event-promotion.md"
    merged_ready_events = merge_ready_events(base_ready_events, ready_events)
    write_jsonl(ready_events_path, ready_events, overwrite=args.overwrite)
    write_jsonl(merged_ready_events_path, merged_ready_events, overwrite=args.overwrite)
    write_jsonl(queue_path, promotion_queue, overwrite=args.overwrite)
    summary = {
        "targetCount": len(targets),
        "baseReadyEventCount": len(base_ready_events),
        "promotedReadyEventCount": len(ready_events),
        "mergedReadyEventCount": len(merged_ready_events),
        "promotionQueueCount": len(promotion_queue),
        "rejectedCount": len(rejected),
        "canonicalWrites": False,
        "statusCounts": dict(Counter(row.get("promotionStatus") for row in promotion_queue)),
    }
    report = {
        "schemaId": "sanguo.sourcePacketReadyEventPromotion.v1",
        "taskId": "PROG-2-0019",
        "generatedAt": utc_now(),
        "roundId": args.round_id,
        "inputs": {
            "coreReport": repo_relative(core_report_path),
            "baseReadyEvents": repo_relative(base_ready_events_path),
            "sourceEventPackets": repo_relative(inputs["sourceEventPackets"]),
            "relationshipEvidence": repo_relative(inputs["relationshipEvidence"]),
            "primaryCanonDefaults": inputs["primaryCanonDefaults"],
        },
        "outputs": {
            "promotedReadyEvents": repo_relative(ready_events_path),
            "mergedReadyEvents": repo_relative(merged_ready_events_path),
            "promotionQueue": repo_relative(queue_path),
            "review": repo_relative(review_path),
        },
        "settings": {
            "limitPerGeneral": args.limit_per_general,
            "primaryCanonDefaultsEnabled": not args.no_primary_canon_defaults,
            "canonicalWrites": False,
        },
        "summary": summary,
        "perGeneral": per_general,
        "rejected": rejected[:200],
    }
    write_json(report_path, report, overwrite=args.overwrite)
    review_lines = [
        "# PROG-2-0019 Ready Event Promotion",
        "",
        f"Generated at: {report['generatedAt']}",
        "",
        "## Summary",
        "",
        f"- Targets: `{summary['targetCount']}`",
        f"- Base ready-events: `{summary['baseReadyEventCount']}`",
        f"- Promoted ready-event candidates: `{summary['promotedReadyEventCount']}`",
        f"- Merged ready-events: `{summary['mergedReadyEventCount']}`",
        f"- Promotion queue rows: `{summary['promotionQueueCount']}`",
        f"- Rejected rows: `{summary['rejectedCount']}`",
        f"- Canonical writes: `{str(summary['canonicalWrites']).lower()}`",
        "",
        "## Per General",
        "",
        "| generalId | candidates | promoted | rejected |",
        "| --- | ---: | ---: | ---: |",
    ]
    for general_id, row in per_general.items():
        review_lines.append(
            f"| {general_id} | {row['candidatePacketCount']} | {row['promotedReadyEventCount']} | {row['rejectedCount']} |"
        )
    review_lines.extend([
        "",
        "## Guardrails",
        "",
        "- Outputs are upstream staged artifacts only; downstream scene eligibility is not decided here.",
        "- Every promoted row keeps `canonicalWrites=false` and provenance fields for review.",
        "- Alias-only / external relationship cues are not treated as primary ready events.",
    ])
    if review_path.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists: {review_path}. Re-run with --overwrite.")
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    print(f"[promote_source_packets_to_ready_events] wrote {repo_relative(report_path)}")
    print(f"[promote_source_packets_to_ready_events] promoted={len(ready_events)} canonicalWrites=false")


if __name__ == "__main__":
    main()