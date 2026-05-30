"""
Runtime-projection upstream-refill autopilot.

Phase 1 (default, read-only):
  Reads queue + staged packets + alias map + trust-zone skip-index, applies
  alias/blacklist/whitelist hard rules, and emits decision-ledger /
  feedback / review-handoff / skill-review-pairs + summary. canonicalWrites=false.

Phase 2 (--allow-apply):
  After Phase 1 classification, picks the chosen bucket's sourceRefs and
  invokes build_runtime_projection_refill_overlay_spec.py with
  --source-ref-file so the overlay generator produces a staged spec (also
  canonicalWrites=false). The overlay tool re-checks alias + missing-fields
  + participant gates, so this is layered defence, not gate bypass.

Hard-rule gates (in order, short-circuit):
  1. alias gate    : both endpoint generalIds must be alias-accepted
  2. blacklist gate: pair-key must not be in blacklist / decisionOnlyBlacklist /
                     removedByHumanDecision / negativeCondition / blockedRelationship
  3. whitelist gate: if pair-key already in whitelist / decisionOnlyWhitelist /
                     noRecompute / fixedAliasLike -> fast lane
  4. otherwise     : propose-lane (cold-lane if neither endpoint has trust signal)

Neither phase mutates trust-zone or canonical artifacts. Promotion to canonical
remains a separate human-reviewed step.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_ALIAS_MAP_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/alias-to-general-map.json"
)
DEFAULT_SKIP_INDEX_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/relationship-trust-zone/relationship-trust-zone.skip-index.json"
)
DEFAULT_OUTPUT_ROOT = Path("local/codex-smoke/runtime-projection-autopilot")
DEFAULT_EDGE_TYPE = "battlefield_contact"
ALLOWED_MISSING_FIELDS = {"relationshipEdges", "relationshipRefs"}
ALIAS_ACCEPTED_STATUSES = {"accepted", "auto-accepted"}
ALIAS_USABLE_ENTRY_STATUSES = {"high-confidence", "medium-confidence", "accepted", "auto-accepted"}

GATE_ALIAS = "alias"
GATE_BLACKLIST = "blacklist"
GATE_WHITELIST = "whitelist"

DECISION_DROP_ALIAS = "drop-alias"
DECISION_DROP_BLACKLIST = "drop-blacklist"
DECISION_FAST = "fast-lane"
DECISION_PROPOSE = "propose-lane"
DECISION_COLD = "cold-lane"
DECISION_SKIP_FILTER = "skip-prefilter"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 1 read-only autopilot for runtime-projection upstream refill loop. "
            "Measures the queue, applies alias + trust-zone hard rules, and emits "
            "decision / feedback / review-handoff artifacts under output-root."
        )
    )
    parser.add_argument("--queue", required=True, help="runtime-projection-upstream-feedback-queue.jsonl")
    parser.add_argument("--source-event-packets", required=True, help="staged source-event-packets.jsonl")
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP_PATH))
    parser.add_argument("--skip-index", default=str(DEFAULT_SKIP_INDEX_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--round-id", default="")
    parser.add_argument("--edge-type", default=DEFAULT_EDGE_TYPE,
                        help="Edge type used when forming pair-keys for trust-zone gating.")
    parser.add_argument("--include-alias-mixed", action="store_true",
                        help="Allow aliasMatch+declaredGeneralIds rows. Default is declaredGeneralIds-only.")
    parser.add_argument("--top-source-refs", type=int, default=0,
                        help="If > 0, retain only the hottest N sourceRefs by row count.")
    parser.add_argument("--source-ref-rank-offset", type=int, default=0,
                        help="Skip this many hottest eligible sourceRefs before applying --top-source-refs.")
    parser.add_argument("--max-feedback-rows", type=int, default=2000,
                        help="Cap feedback.jsonl size; remainder is summarized only.")
    parser.add_argument("--max-handoff-rows", type=int, default=500,
                        help="Cap review-handoff.jsonl size; remainder is summarized only.")
    parser.add_argument("--allow-apply", action="store_true",
                        help="Phase 2: invoke overlay spec builder for the chosen --apply-bucket.")
    parser.add_argument("--apply-bucket", choices=[DECISION_FAST, DECISION_PROPOSE], default=DECISION_FAST,
                        help="Bucket whose sourceRefs are fed to the overlay generator (Phase 2 only).")
    parser.add_argument("--apply-overlay-script",
                        default=str(Path(__file__).with_name("build_runtime_projection_refill_overlay_spec.py")),
                        help="Path to build_runtime_projection_refill_overlay_spec.py (Phase 2 only).")
    parser.add_argument("--apply-overlay-output-root", default="",
                        help="Output root for the generated overlay; defaults to <round-dir>/overlay.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Allow overwriting an existing round directory.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    for root in (REPO_ROOT, REPO_ROOT.parent, REPO_ROOT.parent.parent):
        candidate = (root / path).resolve()
        if candidate.exists():
            return candidate
    return (REPO_ROOT / path).resolve()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
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


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for x in value:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


# ---------- alias gate ----------

def load_alias_map(path: Path) -> tuple[dict[str, set[str]], dict[str, Any]]:
    """Return (generalId -> usable alias entry status set, meta)."""
    if not path.exists():
        return {}, {"present": False, "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}, {"present": True, "path": str(path), "entryCount": 0}
    accepted_by_general: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_status = str(entry.get("status") or "").strip().lower()
        review_map = entry.get("reviewStatusByGeneral") or {}
        for gid in entry.get("generalIds") or []:
            gid_text = str(gid or "").strip()
            if not gid_text:
                continue
            per_general_status = str((review_map or {}).get(gid_text) or "").strip().lower()
            usable = (
                entry_status in ALIAS_USABLE_ENTRY_STATUSES
                and per_general_status in ALIAS_ACCEPTED_STATUSES
            )
            if usable:
                accepted_by_general[gid_text].add(per_general_status or entry_status)
    return dict(accepted_by_general), {
        "present": True,
        "path": str(path),
        "version": payload.get("version") if isinstance(payload, dict) else None,
        "entryCount": len(entries),
        "acceptedGeneralCount": len(accepted_by_general),
    }


def alias_gate(
    general_id: str, target_general_id: str, alias_accepted: dict[str, set[str]]
) -> tuple[bool, str]:
    missing = [gid for gid in (general_id, target_general_id) if gid not in alias_accepted]
    if missing:
        return False, "alias-not-accepted:" + ",".join(missing)
    return True, "alias-accepted"


# ---------- trust-zone gate ----------

def load_skip_index(path: Path) -> tuple[dict[str, set[str]], dict[str, Any]]:
    if not path.exists():
        return {}, {"present": False, "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))

    def keys(name: str) -> set[str]:
        v = payload.get(name)
        return {str(k).strip() for k in v if str(k).strip()} if isinstance(v, list) else set()

    sets = {
        "whitelist": keys("whitelistTrustKeys"),
        "blacklist": keys("blacklistTrustKeys"),
        "decisionOnlyWhitelist": keys("decisionOnlyWhitelistTrustKeys"),
        "decisionOnlyBlacklist": keys("decisionOnlyBlacklistTrustKeys"),
        "removedByHumanDecision": keys("removedByHumanDecisionTrustKeys"),
        "noRecompute": keys("noRecomputeTrustKeys"),
        "fixedAliasLike": keys("fixedAliasLikeTrustKeys"),
        "negativeCondition": keys("negativeConditionTrustKeys"),
        "blockedRelationship": keys("blockedRelationshipTrustKeys"),
    }
    # Endpoints that appear in ANY trust key (used to distinguish cold vs propose).
    endpoints_with_signal: set[str] = set()
    for key_set in sets.values():
        for key in key_set:
            # Trust keys are 'relationship|<type>|<a>|<b>' or '<type>:<a>:<b>'.
            parts = key.replace(":", "|").split("|")
            for token in parts:
                t = token.strip()
                # Skip the leading 'relationship'/'faction' tag and known type tokens.
                if t and "-" in t:
                    endpoints_with_signal.add(t)
    sets["_endpointsWithSignal"] = endpoints_with_signal
    meta = {
        "present": True,
        "path": str(path),
        "version": payload.get("version"),
        "generatedAt": payload.get("generatedAt"),
        "counts": {name: len(s) for name, s in sets.items() if not name.startswith("_")},
        "endpointsWithSignalCount": len(endpoints_with_signal),
    }
    return sets, meta


def pair_key_candidates(
    edge_type: str, from_id: str, to_id: str
) -> list[str]:
    et = str(edge_type or "").strip() or DEFAULT_EDGE_TYPE
    a, b = str(from_id or "").strip(), str(to_id or "").strip()
    candidates: list[str] = []
    if a and b:
        for u, v in ((a, b), (b, a)):
            candidates.append(f"relationship|{et}|{u}|{v}")
            candidates.append(f"{et}:{u}:{v}")
    return candidates


def trust_gate(
    candidates: list[str], skip_sets: dict[str, set[str]]
) -> tuple[str, str, list[str]]:
    """Return (decision-marker, reason, matched-keys).

    Marker is one of: 'blacklist', 'whitelist', 'cold'.
    'whitelist' means the pair is already trusted (fast lane).
    """
    blacklist_groups = ("blacklist", "decisionOnlyBlacklist", "removedByHumanDecision",
                        "negativeCondition", "blockedRelationship")
    whitelist_groups = ("whitelist", "decisionOnlyWhitelist", "noRecompute", "fixedAliasLike")
    matched_bl: list[str] = []
    matched_wl: list[str] = []
    for cand in candidates:
        for group in blacklist_groups:
            if cand in skip_sets.get(group, set()):
                matched_bl.append(f"{group}:{cand}")
        for group in whitelist_groups:
            if cand in skip_sets.get(group, set()):
                matched_wl.append(f"{group}:{cand}")
    if matched_bl:
        return "blacklist", "blacklist-hit", matched_bl
    if matched_wl:
        return "whitelist", "whitelist-hit", matched_wl
    return "cold", "no-trust-hit", []


# ---------- prefilter ----------

def is_eligible_row(row: dict[str, Any], include_alias_mixed: bool) -> tuple[bool, str]:
    if str(row.get("proposalType") or "").strip() != "projection-source-gap":
        return False, "not-projection-gap"
    if str(row.get("linkAuthority") or "").strip() != "source_event_participant":
        return False, "not-source-event-participant"
    missing = set(string_list(row.get("missingFields")))
    if not missing or not missing.issubset(ALLOWED_MISSING_FIELDS):
        return False, "missing-fields-out-of-scope"
    trace = set(string_list(row.get("traceSources")))
    if trace == {"declaredGeneralIds"}:
        return True, "declared-only"
    if include_alias_mixed and trace == {"aliasMatch", "declaredGeneralIds"}:
        return True, "declared-plus-alias"
    return False, "trace-sources-out-of-scope"


# ---------- packet index ----------

def load_packet_general_index(path: Path) -> tuple[dict[str, set[str]], dict[str, Any]]:
    """Map sourceRef -> set of generalIds present in any packet referencing it."""
    index: dict[str, set[str]] = defaultdict(set)
    packet_count = 0
    for packet in read_jsonl(path):
        packet_count += 1
        general_ids = {str(x).strip() for x in packet.get("generalIds") or [] if str(x).strip()}
        if not general_ids:
            continue
        # Packets in this pipeline use 'sourceRef' (singular); some legacy ones use 'sourceRefs' list.
        refs: list[str] = []
        primary_ref = str(packet.get("sourceRef") or packet.get("primarySourceRef") or "").strip()
        if primary_ref:
            refs.append(primary_ref)
        for ref in packet.get("sourceRefs") or []:
            ref_text = str(ref or "").strip()
            if ref_text:
                refs.append(ref_text)
        for ref in refs:
            index[ref].update(general_ids)
    return dict(index), {"present": path.exists(), "path": str(path), "packetCount": packet_count}


# ---------- main ----------

def _apply_overlay(
    *,
    args: argparse.Namespace,
    round_dir: Path,
    bucket_rows: list[dict[str, Any]],
    bucket_source_refs: list[str],
) -> dict[str, Any]:
    """Phase 2: shell out to build_runtime_projection_refill_overlay_spec.py for the chosen bucket."""
    overlay_script = Path(args.apply_overlay_script).resolve()
    if not overlay_script.exists():
        return {
            "invoked": False,
            "reason": "overlay-script-not-found",
            "overlayScript": str(overlay_script),
        }
    if not bucket_rows or not bucket_source_refs:
        return {
            "invoked": False,
            "reason": "no-source-refs-for-bucket",
            "bucket": args.apply_bucket,
        }

    source_ref_file = round_dir / f"applied-source-refs.{args.apply_bucket}.txt"
    source_ref_file.write_text("\n".join(bucket_source_refs) + "\n", encoding="utf-8")
    filtered_queue_file = round_dir / f"applied-queue.{args.apply_bucket}.jsonl"
    write_jsonl(filtered_queue_file, bucket_rows)

    overlay_output_root = (
        resolve_path(args.apply_overlay_output_root)
        if args.apply_overlay_output_root
        else (round_dir / "overlay")
    )
    overlay_round_id = f"{round_dir.name}-overlay-{args.apply_bucket}"

    cmd = [
        sys.executable,
        str(overlay_script),
        "--queue", str(filtered_queue_file),
        "--source-event-packets", str(resolve_path(args.source_event_packets)),
        "--source-ref-file", str(source_ref_file),
        "--output-root", str(overlay_output_root),
        "--round-id", overlay_round_id,
        "--edge-type", args.edge_type,
    ]
    if args.include_alias_mixed:
        cmd.append("--include-alias-mixed")
    if args.overwrite:
        cmd.append("--overwrite")

    completed = subprocess.run(  # noqa: S603 - controlled internal script
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    overlay_summary_path = overlay_output_root / "runtime-projection-refill-overlay-summary.json"
    overlay_spec_path = overlay_output_root / "runtime-projection-refill-overlay-spec.json"
    overlay_summary_payload: Any = None
    if overlay_summary_path.exists():
        try:
            overlay_summary_payload = json.loads(overlay_summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            overlay_summary_payload = {"_parseError": True}

    return {
        "invoked": True,
        "bucket": args.apply_bucket,
        "sourceRefCount": len(bucket_source_refs),
        "sourceRefFile": str(source_ref_file),
        "filteredQueue": str(filtered_queue_file),
        "filteredQueueRowCount": len(bucket_rows),
        "overlayScript": str(overlay_script),
        "overlayOutputRoot": str(overlay_output_root),
        "overlayRoundId": overlay_round_id,
        "overlaySpec": str(overlay_spec_path) if overlay_spec_path.exists() else None,
        "overlaySummary": str(overlay_summary_path) if overlay_summary_path.exists() else None,
        "exitCode": completed.returncode,
        "stdoutTail": (completed.stdout or "")[-2000:],
        "stderrTail": (completed.stderr or "")[-2000:],
        "canonicalWrites": False,
        "overlaySummaryEcho": overlay_summary_payload,
    }


def _append_rounds_history(output_root: Path, summary: dict[str, Any]) -> Path:
    history_path = output_root / "autopilot-rounds-history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "generatedAt": summary.get("generatedAt"),
        "roundId": summary.get("roundId"),
        "phase": summary.get("phase"),
        "mode": summary.get("mode"),
        "queueShape": summary.get("queueShape"),
        "decisionBuckets": summary.get("decisionBuckets"),
        "errorDetected": summary.get("errorDetected"),
        "apply": summary.get("apply"),
        "summaryPath": summary.get("outputs", {}).get("roundDir"),
    }
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return history_path


def main() -> int:
    args = parse_args()

    queue_path = resolve_path(args.queue)
    packets_path = resolve_path(args.source_event_packets)
    alias_path = resolve_path(args.alias_map)
    skip_path = resolve_path(args.skip_index)
    output_root = resolve_path(args.output_root)

    round_id = args.round_id.strip() or f"autopilot-{utc_stamp()}"
    round_dir = output_root / round_id
    if round_dir.exists() and any(round_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"Round directory exists. Re-run with --overwrite: {round_dir}")
    round_dir.mkdir(parents=True, exist_ok=True)

    alias_accepted, alias_meta = load_alias_map(alias_path)
    skip_sets, skip_meta = load_skip_index(skip_path)
    packet_index, packet_meta = load_packet_general_index(packets_path)

    queue_rows = list(read_jsonl(queue_path))
    queue_row_count = len(queue_rows)

    # Pre-count source-ref hotness on eligible rows so --top-source-refs can prune.
    eligible_rows: list[dict[str, Any]] = []
    prefilter_reasons: Counter = Counter()
    for row in queue_rows:
        ok, reason = is_eligible_row(row, args.include_alias_mixed)
        if not ok:
            prefilter_reasons[reason] += 1
            continue
        # Both endpoints must already exist in the packet for this sourceRef.
        source_ref = str(row.get("sourceRef") or "").strip()
        general_id = str(row.get("generalId") or "").strip()
        target_general_id = str(row.get("targetGeneralId") or "").strip()
        packet_generals = packet_index.get(source_ref, set())
        if not packet_generals:
            prefilter_reasons["source-ref-missing-from-packets"] += 1
            continue
        if general_id not in packet_generals or target_general_id not in packet_generals:
            prefilter_reasons["endpoint-not-in-packet"] += 1
            continue
        eligible_rows.append(row)

    source_ref_counts: Counter = Counter()
    for row in eligible_rows:
        ref = str(row.get("sourceRef") or "").strip()
        if ref:
            source_ref_counts[ref] += 1

    selected_refs: set[str] | None = None
    source_ref_rank_offset = max(0, int(args.source_ref_rank_offset or 0))
    ranked_source_refs = sorted(source_ref_counts.items(), key=lambda item: (-item[1], item[0]))
    if args.top_source_refs and args.top_source_refs > 0:
        selected_refs = {
            ref for ref, _ in ranked_source_refs[source_ref_rank_offset:source_ref_rank_offset + args.top_source_refs]
        }

    # Apply gates.
    decisions: list[dict[str, Any]] = []
    bucket_counts: Counter = Counter()
    bucket_pair_counts: dict[str, Counter] = defaultdict(Counter)
    blocked_pair_counts: Counter = Counter()
    feedback_rows: list[dict[str, Any]] = []
    handoff_rows: list[dict[str, Any]] = []
    source_ref_decision_counts: dict[str, Counter] = defaultdict(Counter)
    bucket_queue_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in eligible_rows:
        general_id = str(row.get("generalId") or "").strip()
        target_general_id = str(row.get("targetGeneralId") or "").strip()
        source_ref = str(row.get("sourceRef") or "").strip()
        proposal_id = str(row.get("proposalId") or "").strip()

        # Top-N filter is applied as a prefilter-bucket, not a gate, so feedback still records it.
        if selected_refs is not None and source_ref not in selected_refs:
            bucket = DECISION_SKIP_FILTER
            reason = "not-in-top-source-refs"
            decisions.append({
                "proposalId": proposal_id,
                "generalId": general_id,
                "targetGeneralId": target_general_id,
                "sourceRef": source_ref,
                "gateLayer": "prefilter",
                "decision": bucket,
                "reason": reason,
                "matchedKeys": [],
            })
            bucket_counts[bucket] += 1
            source_ref_decision_counts[source_ref][bucket] += 1
            continue

        if not general_id or not target_general_id:
            bucket = DECISION_DROP_ALIAS
            reason = "missing-pair-endpoint"
        else:
            ok, alias_reason = alias_gate(general_id, target_general_id, alias_accepted)
            if not ok:
                bucket = DECISION_DROP_ALIAS
                reason = alias_reason
            else:
                candidates = pair_key_candidates(args.edge_type, general_id, target_general_id)
                trust_marker, trust_reason, matched_keys = trust_gate(candidates, skip_sets)
                if trust_marker == "blacklist":
                    bucket = DECISION_DROP_BLACKLIST
                    reason = trust_reason
                elif trust_marker == "whitelist":
                    bucket = DECISION_FAST
                    reason = trust_reason
                else:
                    endpoints_signal = skip_sets.get("_endpointsWithSignal", set())
                    side_seen = (general_id in endpoints_signal) or (target_general_id in endpoints_signal)
                    bucket = DECISION_PROPOSE if side_seen else DECISION_COLD
                    reason = trust_reason
                    matched_keys = []

        decision_row = {
            "proposalId": proposal_id,
            "generalId": general_id,
            "targetGeneralId": target_general_id,
            "sourceRef": source_ref,
            "sourceType": row.get("sourceType"),
            "edgeType": args.edge_type,
            "decision": bucket,
            "reason": reason,
            "matchedKeys": matched_keys if bucket in {DECISION_DROP_BLACKLIST, DECISION_FAST} else [],
            "traceSources": row.get("traceSources"),
        }
        decisions.append(decision_row)
        bucket_counts[bucket] += 1
        source_ref_decision_counts[source_ref][bucket] += 1
        bucket_queue_rows[bucket].append(row)

        pair_key = f"{general_id}|{target_general_id}"
        bucket_pair_counts[bucket][pair_key] += 1

        if bucket in {DECISION_DROP_ALIAS, DECISION_DROP_BLACKLIST}:
            blocked_pair_counts[pair_key] += 1
            if len(feedback_rows) < args.max_feedback_rows:
                feedback_rows.append({
                    **decision_row,
                    "queueRow": {
                        "proposalStatus": row.get("proposalStatus"),
                        "sourceQuote": row.get("sourceQuote"),
                        "evidence": row.get("evidence"),
                    },
                })
            if bucket == DECISION_DROP_BLACKLIST and len(handoff_rows) < args.max_handoff_rows:
                handoff_rows.append({
                    **decision_row,
                    "handoffReason": "blacklist-hit-needs-human-confirmation",
                })
        elif bucket == DECISION_COLD and len(handoff_rows) < args.max_handoff_rows:
            handoff_rows.append({
                **decision_row,
                "handoffReason": "no-trust-zone-signal-needs-first-evidence",
            })
        elif bucket == DECISION_PROPOSE and len(handoff_rows) < args.max_handoff_rows // 4:
            # Smaller sample for propose-lane so handoff stays focused on action items.
            handoff_rows.append({
                **decision_row,
                "handoffReason": "propose-lane-pending-skill-review",
            })

    # Write artifacts.
    decision_ledger_path = round_dir / "decision-ledger.jsonl"
    feedback_path = round_dir / "feedback.jsonl"
    handoff_path = round_dir / "review-handoff.jsonl"
    skill_review_path = round_dir / "skill-review-pairs.jsonl"
    summary_path = round_dir / "autopilot-summary.json"

    decision_count = write_jsonl(decision_ledger_path, decisions)
    feedback_count = write_jsonl(feedback_path, feedback_rows)
    handoff_count = write_jsonl(handoff_path, handoff_rows)

    # Skill-review handoff: every propose-lane row, deduped by (pair, sourceRef).
    seen_skill_keys: set[tuple[str, str, str]] = set()
    skill_review_rows: list[dict[str, Any]] = []
    for d in decisions:
        if d.get("decision") != DECISION_PROPOSE:
            continue
        key = (d.get("generalId") or "", d.get("targetGeneralId") or "", d.get("sourceRef") or "")
        if key in seen_skill_keys:
            continue
        seen_skill_keys.add(key)
        skill_review_rows.append({
            "generalId": d.get("generalId"),
            "targetGeneralId": d.get("targetGeneralId"),
            "sourceRef": d.get("sourceRef"),
            "edgeType": d.get("edgeType"),
            "sourceType": d.get("sourceType"),
            "traceSources": d.get("traceSources"),
            "originRoundId": round_id,
        })
    skill_review_count = write_jsonl(skill_review_path, skill_review_rows)

    # Phase 2: apply overlay generator for the chosen bucket, if requested.
    apply_payload: dict[str, Any] = {"invoked": False, "reason": "phase-1-read-only"}
    if args.allow_apply:
        bucket_refs_counter: Counter = Counter()
        for d in decisions:
            if d.get("decision") == args.apply_bucket:
                ref = d.get("sourceRef") or ""
                if ref:
                    bucket_refs_counter[ref] += 1
        bucket_source_refs = [ref for ref, _ in bucket_refs_counter.most_common()]
        apply_payload = _apply_overlay(
            args=args,
            round_dir=round_dir,
            bucket_source_refs=bucket_source_refs,
        )

    # Top blocked pairs for quick triage.
    top_blocked = [
        {"pair": pair, "count": count}
        for pair, count in blocked_pair_counts.most_common(25)
    ]
    top_source_refs_summary = [
        {"sourceRef": ref, "totalEligible": cnt, "decisions": dict(source_ref_decision_counts[ref])}
        for ref, cnt in source_ref_counts.most_common(25)
    ]

    summary = {
        "schemaId": "runtime-projection-autopilot-summary.v1",
        "phase": 1,
        "mode": "read-only",
        "canonicalWrites": False,
        "generatedAt": utc_now(),
        "roundId": round_id,
        "inputs": {
            "queue": str(queue_path),
            "sourceEventPackets": str(packets_path),
            "aliasMap": alias_meta,
            "skipIndex": skip_meta,
            "packetIndex": {**packet_meta, "sourceRefCoverage": len(packet_index)},
        },
        "config": {
            "edgeType": args.edge_type,
            "includeAliasMixed": bool(args.include_alias_mixed),
            "topSourceRefs": args.top_source_refs,
            "sourceRefRankOffset": source_ref_rank_offset,
        },
        "queueShape": {
            "queueRowCount": queue_row_count,
            "eligibleRowCount": len(eligible_rows),
            "prefilterReasons": dict(prefilter_reasons),
            "sourceRefEligibleCount": len(source_ref_counts),
            "selectedSourceRefCount": (len(selected_refs) if selected_refs is not None else len(source_ref_counts)),
        },
        "decisionBuckets": dict(bucket_counts),
        "topBlockedPairs": top_blocked,
        "topSourceRefs": top_source_refs_summary,
        "outputs": {
            "roundDir": str(round_dir),
            "decisionLedger": str(decision_ledger_path),
            "decisionLedgerRowCount": decision_count,
            "feedback": str(feedback_path),
            "feedbackRowCount": feedback_count,
            "reviewHandoff": str(handoff_path),
            "reviewHandoffRowCount": handoff_count,
            "skillReviewPairs": str(skill_review_path),
            "skillReviewPairsRowCount": skill_review_count,
        },
        "errorDetected": {
            "aliasMapMissing": not alias_meta.get("present"),
            "skipIndexMissing": not skip_meta.get("present"),
            "noEligibleRows": len(eligible_rows) == 0,
            "applyInvocationFailed": bool(
                args.allow_apply
                and apply_payload.get("invoked")
                    bucket_rows = bucket_queue_rows.get(args.apply_bucket, [])
                    for row in bucket_rows:
                        ref = row.get("sourceRef") or ""
        "phase": 2 if args.allow_apply else 1,
        "mode": "apply" if args.allow_apply else "read-only",
        "apply": apply_payload,
        "nextActions": _suggested_next_actions(bucket_counts, alias_meta, skip_meta, apply_payload),
    }

                        bucket_rows=bucket_rows,
    write_json(summary_path, summary)
    history_path = _append_rounds_history(output_root, summary)

    print(json.dumps({
        "ok": True,
        "roundId": round_id,
        "phase": summary["phase"],
        "mode": summary["mode"],
        "queueRowCount": queue_row_count,
        "eligibleRowCount": len(eligible_rows),
        "decisionBuckets": dict(bucket_counts),
        "apply": {
            "invoked": apply_payload.get("invoked"),
            "bucket": apply_payload.get("bucket"),
            "sourceRefCount": apply_payload.get("sourceRefCount"),
            "exitCode": apply_payload.get("exitCode"),
        },
        "summary": str(summary_path),
        "history": str(history_path),
    }, ensure_ascii=False, indent=2))
    return 0


def _suggested_next_actions(
    bucket_counts: Counter,
    alias_meta: dict[str, Any],
    skip_meta: dict[str, Any],
    apply_payload: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    apply_payload = apply_payload or {}
    if not alias_meta.get("present"):
        actions.append({
            "code": "ALIAS_MAP_MISSING",
            "action": "Rebuild alias-to-general-map.json before re-running autopilot.",
        })
    if not skip_meta.get("present"):
        actions.append({
            "code": "SKIP_INDEX_MISSING",
            "action": "Rebuild relationship-trust-zone.skip-index.json before re-running autopilot.",
        })
    if bucket_counts.get(DECISION_FAST):
        actions.append({
            "code": "FAST_LANE_READY",
            "action": "Re-run with --allow-apply --apply-bucket=fast-lane to emit a staged overlay for the trusted pairs.",
        })
    elif bucket_counts.get(DECISION_PROPOSE):
        actions.append({
            "code": "FAST_LANE_EMPTY",
            "action": (
                "Trust-zone has no whitelist hits for these pairs. Run skill review on "
                "skill-review-pairs.jsonl, or run with --allow-apply --apply-bucket=propose-lane "
                "to stage an overlay for human-reviewed promotion."
            ),
        })
    if bucket_counts.get(DECISION_PROPOSE):
        actions.append({
            "code": "PROPOSE_LANE_PENDING",
            "action": "Feed skill-review-pairs.jsonl into run_relationship_skill_review_rounds.py for trust-zone signal growth.",
        })
    if bucket_counts.get(DECISION_COLD):
        actions.append({
            "code": "COLD_LANE_NEEDS_EVIDENCE",
            "action": "Cold-lane pairs need first relationship evidence; route to source-event-packets backfill.",
        })
    if bucket_counts.get(DECISION_DROP_BLACKLIST):
        actions.append({
            "code": "BLACKLIST_REVIEW",
            "action": "Confirm blacklist hits with trust-zone reviewer; do not auto-promote.",
        })
    if bucket_counts.get(DECISION_DROP_ALIAS):
        actions.append({
            "code": "ALIAS_GAP",
            "action": "Run propose_alias_from_observed.py (or alias intake) to cover endpoints that failed alias gate.",
        })
    if apply_payload.get("invoked") and apply_payload.get("exitCode") not in (0, None):
        actions.append({
            "code": "APPLY_OVERLAY_FAILED",
            "action": f"Overlay generator exited with code {apply_payload.get('exitCode')}. Inspect apply.stderrTail in summary.",
        })
    if apply_payload.get("invoked") and apply_payload.get("exitCode") == 0:
        actions.append({
            "code": "OVERLAY_READY_FOR_REVIEW",
            "action": f"Staged overlay generated under {apply_payload.get('overlayOutputRoot')}. Promote only after human review.",
        })
    return actions


if __name__ == "__main__":
    raise SystemExit(main())
