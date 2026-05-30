"""
Sanguo autopilot marathon controller.

This script is an orchestration layer over existing Sanguo-RAG tools. It does
not rewrite extractors and it does not mutate canonical data. The controller
creates resumable marathon state, progress history, and stop evidence while
delegating per-round classification to run_data_pipeline_autopilot.py.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_QUEUE_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/runtime-projection-upstream-feedback/"
    "p1-source-ref-refill/queue-base-events/runtime-projection-upstream-feedback-queue.jsonl"
)
DEFAULT_SOURCE_EVENT_PACKETS_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/source-event-packets/source-event-packets.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path("local/sanguo-autopilot-marathon")
DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_PATH = DEFAULT_OUTPUT_ROOT / "source-event-packets-extended.jsonl"
DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_SUMMARY_PATH = DEFAULT_OUTPUT_ROOT / "source-event-packets-extended-summary.json"
DEFAULT_CORE_COMPLETION_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/current.json"
)
DEFAULT_AUTOPILOT_SCRIPT = Path(__file__).with_name("run_data_pipeline_autopilot.py")
DEFAULT_COMPLETION_REFRESH_SUBDIR = "core-person-progress"
DEFAULT_FOCUS_QUEUE_SUBDIR = "queue-focus"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a resumable Sanguo data-pipeline marathon until top-N readiness "
            "or source exhaustion gates stop it."
        )
    )
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE_PATH))
    parser.add_argument("--source-event-packets", default=str(DEFAULT_SOURCE_EVENT_PACKETS_PATH))
    parser.add_argument("--completion", default=str(DEFAULT_CORE_COMPLETION_PATH))
    parser.add_argument("--autopilot-script", default=str(DEFAULT_AUTOPILOT_SCRIPT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--round-prefix", default="marathon-round")
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--target-completeness", type=float, default=95.0)
    parser.add_argument("--top-source-refs", type=int, default=25)
    parser.add_argument("--source-ref-rank-offset", type=int, default=0)
    parser.add_argument(
        "--advance-source-ref-window",
        action="store_true",
        help="Advance source-ref rank offset by --top-source-refs after each round.",
    )
    parser.add_argument("--source-exhaustion-patience", type=int, default=5)
    parser.add_argument("--no-progress-patience", type=int, default=3)
    parser.add_argument("--queue-epsilon", type=int, default=0)
    parser.add_argument("--new-source-ref-epsilon", type=int, default=0)
    parser.add_argument("--allow-apply", action="store_true")
    parser.add_argument("--apply-bucket", choices=["fast-lane", "propose-lane"], default="propose-lane")
    parser.add_argument("--include-alias-mixed", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Force read-only autopilot mode and canonicalWrites=false.")
    parser.add_argument("--plan-only", action="store_true", help="Write marathon plan artifacts without invoking subprocesses.")
    parser.add_argument("--resume", action="store_true", help="Continue from output-root/marathon-state.json when present.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_target(value: float) -> float:
    return value * 100.0 if value <= 1.0 else value


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()

    candidates = [
        (REPO_ROOT / path).resolve(),
        (REPO_ROOT.parent / path).resolve(),
        (REPO_ROOT.parent / "3KLife" / path).resolve(),
        (REPO_ROOT.parent / "3klife-npc-brain" / path).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        rows.append(json.loads(text))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_ready_events_from_rows(rows: list[dict[str, Any]], output_path: Path) -> dict[str, Any] | None:
    ready_events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        target_general_id = str(row.get("targetGeneralId") or "").strip()
        source_ref = str(row.get("sourceRef") or "").strip()
        edge_type = str(row.get("edgeType") or "").strip()
        general_ids = [value for value in [general_id, target_general_id] if value]
        if not general_ids:
            continue
        pair_key = (general_id, target_general_id, source_ref)
        if pair_key in seen:
            continue
        seen.add(pair_key)
        ready_events.append(
            {
                "generalId": general_id,
                "targetGeneralId": target_general_id,
                "generalIds": general_ids,
                "sourceRef": source_ref,
                "sourceRefs": [source_ref] if source_ref else [],
                "edgeType": edge_type,
                "sourceType": str(row.get("sourceType") or "").strip(),
                "traceSources": list(row.get("traceSources") or []),
                "originRoundId": str(row.get("originRoundId") or "").strip(),
                "reviewStatus": "ready",
                "canonicalWrites": False,
            }
        )

    if not ready_events:
        return None

    write_jsonl(output_path, ready_events)
    return {
        "readyEventPath": str(output_path),
        "readyEventCount": len(ready_events),
        "readyEventSourceRowCount": len(rows),
    }


def materialize_ready_events_from_artifacts(artifact_paths: list[Path], output_path: Path) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    input_paths: list[str] = []
    for path in artifact_paths:
        if not path.exists():
            continue
        rows.extend(read_jsonl(path))
        input_paths.append(str(path))

    summary = build_ready_events_from_rows(rows, output_path)
    if not summary:
        return None
    summary["inputPaths"] = input_paths
    summary["inputRowCount"] = len(rows)
    return summary


def focus_general_ids_from_history(history: list[dict[str, Any]], target: float, limit: int = 10) -> list[str]:
    for item in reversed(history):
        readiness = item.get("topReadiness")
        if not isinstance(readiness, dict):
            continue
        blockers = readiness.get("lowestBlockers") or []
        focus_ids: list[str] = []
        seen: set[str] = set()
        for blocker in blockers:
            if not isinstance(blocker, dict):
                continue
            completion_percent = float(blocker.get("completionPercent") or 0.0)
            if completion_percent >= target:
                continue
            general_id = str(blocker.get("generalId") or "").strip()
            if not general_id or general_id in seen:
                continue
            seen.add(general_id)
            focus_ids.append(general_id)
            if len(focus_ids) >= max(1, limit):
                return focus_ids
        if focus_ids:
            return focus_ids
    return []


def materialize_focused_queue_from_low_blockers(
    queue_path: Path,
    output_path: Path,
    focus_general_ids: list[str],
    source_ref_limit: int,
) -> dict[str, Any] | None:
    if not focus_general_ids:
        return None

    focus_set = {str(general_id).strip() for general_id in focus_general_ids if str(general_id).strip()}
    if not focus_set:
        return None

    rows = read_jsonl(queue_path)
    source_ref_counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("generalId") or "").strip()
        target_general_id = str(row.get("targetGeneralId") or "").strip()
        source_ref = str(row.get("sourceRef") or "").strip()
        if not source_ref:
            continue
        if general_id in focus_set or target_general_id in focus_set:
            source_ref_counts[source_ref] = source_ref_counts.get(source_ref, 0) + 1

    if not source_ref_counts:
        return None

    ranked_source_refs = [
        source_ref
        for source_ref, _ in sorted(source_ref_counts.items(), key=lambda item: (-item[1], item[0]))[: max(1, source_ref_limit)]
    ]
    ranked_source_ref_set = set(ranked_source_refs)
    focused_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("sourceRef") or "").strip() in ranked_source_ref_set
    ]

    if not focused_rows:
        return None

    write_jsonl(output_path, focused_rows)
    return {
        "focusGeneralIds": focus_general_ids,
        "focusSourceRefs": ranked_source_refs,
        "focusSourceRefCount": len(ranked_source_refs),
        "focusQueueRowCount": len(focused_rows),
        "queuePath": str(output_path),
    }


def refresh_completion_from_round(
    *,
    round_id: str,
    round_dir: Path,
    source_event_packets_path: Path,
    autopilot_summary: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any] | None:
    outputs = autopilot_summary.get("outputs") if isinstance(autopilot_summary.get("outputs"), dict) else {}
    artifact_paths: list[Path] = []
    for key in ("skillReviewPairs", "reviewHandoff"):
        artifact_text = str(outputs.get(key) or "").strip()
        if not artifact_text:
            continue
        artifact_paths.append(resolve_path(artifact_text))

    artifact_paths = [path for path in artifact_paths if path.exists()]
    if not artifact_paths:
        return None

    completion_refresh_root = round_dir / DEFAULT_COMPLETION_REFRESH_SUBDIR
    ready_events_path = completion_refresh_root / f"{round_id}-skill-review-ready-events.jsonl"
    ready_events_summary = materialize_ready_events_from_artifacts(artifact_paths, ready_events_path)
    if not ready_events_summary:
        return None

    estimate_script = resolve_path(__file__).with_name("estimate_core_person_completion.py")
    estimate_command = [
        sys.executable,
        str(estimate_script),
        "--round-id",
        round_id,
        "--source-event-packets",
        str(source_event_packets_path),
        "--ready-events",
        str(ready_events_path),
        "--output-root",
        str(completion_refresh_root),
        "--overwrite",
    ]
    if dry_run:
        return {
            "invoked": False,
            "reason": "dry-run",
            "command": estimate_command,
            "readyEvents": ready_events_summary,
            "completionPath": str(completion_refresh_root / f"{round_id}.json"),
        }

    completed = subprocess.run(
        estimate_command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    completion_path = completion_refresh_root / f"{round_id}.json"
    return {
        "invoked": True,
        "command": estimate_command,
        "exitCode": completed.returncode,
        "stdoutTail": (completed.stdout or "")[-2000:],
        "stderrTail": (completed.stderr or "")[-2000:],
        "readyEvents": ready_events_summary,
        "completionPath": str(completion_path),
        "completionExists": completion_path.exists(),
    }


def discover_source_event_packet_inputs(base_path: Path) -> list[Path]:
    inputs = [base_path]
    smoke_root = REPO_ROOT / "local" / "codex-smoke" / "knowledge-growth"
    if smoke_root.exists():
        for candidate in sorted(smoke_root.glob("**/source-event-packets/source-event-packets.jsonl")):
            if candidate.exists() and candidate.resolve() != base_path.resolve():
                inputs.append(candidate)
    return inputs


def materialize_extended_source_event_packets(base_path: Path) -> Path:
    target_path = DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_PATH
    inputs = discover_source_event_packet_inputs(base_path)
    seen: set[str] = set()
    merged_lines: list[str] = []
    input_summaries: list[dict[str, Any]] = []

    for input_path in inputs:
        if not input_path.exists():
            continue
        lines = [line.strip() for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        accepted_count = 0
        for line in lines:
            if line in seen:
                continue
            seen.add(line)
            merged_lines.append(line)
            accepted_count += 1
        input_summaries.append({
            "path": str(input_path),
            "lineCount": len(lines),
            "acceptedLineCount": accepted_count,
        })

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(merged_lines) + "\n", encoding="utf-8")
    write_json(DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_SUMMARY_PATH, {
        "schemaId": "sanguo-autopilot-marathon-source-event-packets-extended.v1",
        "generatedAt": utc_now(),
        "baseSourceEventPacketsPath": str(base_path),
        "sourceEventPacketInputs": input_summaries,
        "mergedLineCount": len(merged_lines),
        "inputCount": len(input_summaries),
        "canonicalWrites": False,
    })
    return target_path


def resolve_marathon_source_event_packets_path(source_event_packets_path: Path, plan_only: bool) -> Path:
    default_source_path = resolve_path(DEFAULT_SOURCE_EVENT_PACKETS_PATH)
    if source_event_packets_path.resolve() != default_source_path.resolve():
        return source_event_packets_path
    if plan_only:
        if DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_PATH.exists():
            return DEFAULT_EXTENDED_SOURCE_EVENT_PACKETS_PATH
        return source_event_packets_path
    return materialize_extended_source_event_packets(default_source_path)


def write_progress_markdown(path: Path, state: dict[str, Any], round_record: dict[str, Any]) -> None:
    readiness = round_record.get("topReadiness") or {}
    autopilot = round_record.get("autopilot") or {}
    queue_shape = autopilot.get("summary", {}).get("queueShape") or {}
    decisions = autopilot.get("summary", {}).get("decisionBuckets") or {}
    window = round_record.get("sourceRefWindow") or {}
    lines = [
        "# Sanguo Autopilot Marathon Progress",
        "",
        f"- generatedAt: {utc_now()}",
        f"- runId: {state.get('runId')}",
        f"- currentRound: {state.get('currentRound')}",
        f"- stopReason: {state.get('stopReason')}",
        f"- canonicalWrites: {state.get('canonicalWrites')}",
        "",
        "## Latest Round",
        "",
        f"- roundId: {round_record.get('roundId')}",
        f"- status: {round_record.get('status')}",
        f"- autopilotExitCode: {autopilot.get('exitCode')}",
        f"- queueRowCount: {queue_shape.get('queueRowCount')}",
        f"- eligibleRowCount: {queue_shape.get('eligibleRowCount')}",
        f"- decisionBuckets: {json.dumps(decisions, ensure_ascii=False, sort_keys=True)}",
        f"- sourceRefWindowMode: {window.get('mode')}",
        f"- sourceRefWindowRankOffset: {window.get('rankOffset')}",
        f"- sourceRefWindowStep: {window.get('step')}",
        f"- sourceRefWindowBudget: {window.get('windowBudget')}",
        "",
        "## Top Readiness",
        "",
        f"- topN: {readiness.get('topN')}",
        f"- targetCompleteness: {readiness.get('targetCompleteness')}",
        f"- readyCount: {readiness.get('readyCount')}",
        f"- minCompletionPercent: {readiness.get('minCompletionPercent')}",
        f"- avgCompletionPercent: {readiness.get('avgCompletionPercent')}",
        "",
        "## Lowest Blockers",
        "",
    ]
    blockers = readiness.get("lowestBlockers") or []
    if blockers:
        for blocker in blockers[:10]:
            lines.append(
                f"- {blocker.get('rank')}. {blocker.get('generalId')} "
                f"{blocker.get('name')} = {blocker.get('completionPercent')}"
            )
    else:
        lines.append("- none")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def top_readiness(completion_path: Path, top_n: int, target: float) -> dict[str, Any]:
    payload = read_json(completion_path, {})
    people = payload.get("people") if isinstance(payload, dict) else None
    if not isinstance(people, list):
        return {
            "available": False,
            "path": str(completion_path),
            "topN": top_n,
            "targetCompleteness": target,
            "ready": False,
            "reason": "completion-file-missing-or-invalid",
        }

    ordered = sorted(
        (person for person in people if isinstance(person, dict)),
        key=lambda person: int(person.get("rank") or 999999),
    )[:top_n]
    scores = [float(person.get("completionPercent") or 0.0) for person in ordered]
    ready_people = [score for score in scores if score >= target]
    lowest = sorted(
        [
            {
                "rank": person.get("rank"),
                "generalId": person.get("generalId"),
                "name": person.get("name"),
                "completionPercent": float(person.get("completionPercent") or 0.0),
                "recommendedActions": person.get("recommendedActions") or [],
            }
            for person in ordered
        ],
        key=lambda item: (item["completionPercent"], item.get("rank") or 999999),
    )
    return {
        "available": True,
        "path": str(completion_path),
        "generatedAt": payload.get("generatedAt"),
        "topN": top_n,
        "targetCompleteness": target,
        "personCount": len(ordered),
        "ready": bool(ordered) and len(ready_people) == len(ordered),
        "readyCount": len(ready_people),
        "minCompletionPercent": min(scores) if scores else 0.0,
        "avgCompletionPercent": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "lowestBlockers": lowest[:10],
    }


def planned_autopilot_command(
    args: argparse.Namespace,
    round_id: str,
    round_dir: Path,
    rank_offset: int,
    queue_path: Path,
    source_event_packets_path: Path,
) -> list[str]:
    script = resolve_path(args.autopilot_script)
    cmd = [
        sys.executable,
        str(script),
        "--queue",
        str(queue_path),
        "--source-event-packets",
        str(source_event_packets_path),
        "--output-root",
        str(round_dir / "autopilot"),
        "--round-id",
        round_id,
        "--top-source-refs",
        str(max(0, int(args.top_source_refs or 0))),
        "--source-ref-rank-offset",
        str(max(0, int(rank_offset or 0))),
        "--overwrite",
    ]
    if args.include_alias_mixed:
        cmd.append("--include-alias-mixed")
    if args.allow_apply and not args.dry_run:
        cmd.extend(["--allow-apply", "--apply-bucket", args.apply_bucket])
    return cmd


def run_autopilot(
    args: argparse.Namespace,
    round_id: str,
    round_dir: Path,
    rank_offset: int,
    queue_path: Path,
    source_event_packets_path: Path,
) -> dict[str, Any]:
    cmd = planned_autopilot_command(args, round_id, round_dir, rank_offset, queue_path, source_event_packets_path)
    summary_path = round_dir / "autopilot" / round_id / "autopilot-summary.json"
    if args.plan_only:
        return {
            "invoked": False,
            "reason": "plan-only",
            "command": cmd,
            "exitCode": None,
            "summaryPath": str(summary_path),
            "summary": {},
        }

    completed = subprocess.run(  # noqa: S603 - controlled repo-local script invocation
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    summary = read_json(summary_path, {})
    return {
        "invoked": True,
        "command": cmd,
        "exitCode": completed.returncode,
        "stdoutTail": (completed.stdout or "")[-2000:],
        "stderrTail": (completed.stderr or "")[-2000:],
        "summaryPath": str(summary_path),
        "summary": summary if isinstance(summary, dict) else {},
    }


def source_ref_signature(summary: dict[str, Any]) -> dict[str, Any]:
    queue_shape = summary.get("queueShape") or {}
    decisions = summary.get("decisionBuckets") or {}
    top_refs = summary.get("topSourceRefs") or []
    source_refs = sorted(str(item.get("sourceRef")) for item in top_refs if isinstance(item, dict) and item.get("sourceRef"))
    return {
        "sourceRefEligibleCount": queue_shape.get("sourceRefEligibleCount"),
        "selectedSourceRefCount": queue_shape.get("selectedSourceRefCount"),
        "eligibleRowCount": queue_shape.get("eligibleRowCount"),
        "decisionBuckets": decisions,
        "topSourceRefs": source_refs,
    }


def choose_source_ref_window(history: list[dict[str, Any]], round_number: int, args: argparse.Namespace) -> dict[str, Any]:
    base_offset = max(0, int(args.source_ref_rank_offset or 0))
    top_source_refs = max(1, int(args.top_source_refs or 0))
    if not args.advance_source_ref_window:
        return {
            "mode": "fixed",
            "baseOffset": base_offset,
            "rankOffset": base_offset,
            "step": 0,
            "windowBudget": None,
            "eligibleCount": None,
            "selectedSourceRefCount": None,
            "reason": "advance-source-ref-window-disabled",
        }

    last_signature: dict[str, Any] | None = None
    for item in reversed(history):
        signature = item.get("sourceRefSignature") or {}
        if signature.get("sourceRefEligibleCount") is not None:
            last_signature = signature
            break
        autopilot_summary = (item.get("autopilot") or {}).get("summary") or {}
        legacy_queue_shape = autopilot_summary.get("queueShape") or {}
        if legacy_queue_shape.get("sourceRefEligibleCount") is not None:
            last_signature = {
                "sourceRefEligibleCount": legacy_queue_shape.get("sourceRefEligibleCount"),
                "selectedSourceRefCount": legacy_queue_shape.get("selectedSourceRefCount"),
            }
            break

    eligible_count = int((last_signature or {}).get("sourceRefEligibleCount") or 0)
    selected_count = int((last_signature or {}).get("selectedSourceRefCount") or 0)

    if eligible_count <= 0:
        rank_offset = base_offset + (round_number - 1) * top_source_refs
        return {
            "mode": "fallback-fixed",
            "baseOffset": base_offset,
            "rankOffset": rank_offset,
            "step": top_source_refs,
            "windowBudget": None,
            "eligibleCount": eligible_count,
            "selectedSourceRefCount": selected_count,
            "reason": "missing-eligible-count",
        }

    window_budget = eligible_count - top_source_refs + 1
    if window_budget <= 1:
        return {
            "mode": "clamped",
            "baseOffset": base_offset,
            "rankOffset": 0,
            "step": 0,
            "windowBudget": max(1, window_budget),
            "eligibleCount": eligible_count,
            "selectedSourceRefCount": selected_count,
            "reason": "window-budget-too-small",
        }

    step = max(1, min(top_source_refs, window_budget - 1))
    while math.gcd(step, window_budget) != 1 and step < window_budget:
        step += 1
    if step >= window_budget:
        step = 1

    rank_offset = (base_offset + (round_number - 1) * step) % window_budget
    return {
        "mode": "adaptive-cyclic",
        "baseOffset": base_offset,
        "rankOffset": rank_offset,
        "step": step,
        "windowBudget": window_budget,
        "eligibleCount": eligible_count,
        "selectedSourceRefCount": selected_count,
        "reason": "cyclic-window-with-gcd-step",
    }


def source_exhaustion(history: list[dict[str, Any]], patience: int) -> dict[str, Any]:
    if patience <= 0 or len(history) < patience:
        return {"candidate": False, "reason": "insufficient-history", "requiredRounds": patience, "actualRounds": len(history)}
    window = history[-patience:]
    signatures = [item.get("sourceRefSignature") for item in window]
    first = signatures[0]
    repeated = bool(first) and all(signature == first for signature in signatures[1:])
    return {
        "candidate": repeated,
        "reason": "repeated-source-ref-signature" if repeated else "source-ref-signature-still-changing",
        "requiredRounds": patience,
        "actualRounds": len(history),
        "windowRoundIds": [item.get("roundId") for item in window],
    }


def decide_stop(
    readiness: dict[str, Any],
    exhaustion: dict[str, Any],
    round_number: int,
    max_rounds: int,
    autopilot: dict[str, Any],
    stop_on_error: bool,
) -> str | None:
    if readiness.get("ready"):
        return "top-n-ready"
    if exhaustion.get("candidate"):
        return "sources-exhausted"
    if stop_on_error and autopilot.get("exitCode") not in (0, None):
        return "autopilot-error"
    if round_number >= max_rounds:
        return "max-rounds"
    return None


def load_or_init_state(
    args: argparse.Namespace,
    output_root: Path,
    source_event_packets_path: Path | None = None,
) -> dict[str, Any]:
    state_path = output_root / "marathon-state.json"
    if args.resume and state_path.exists():
        state = read_json(state_path, {})
        if isinstance(state, dict):
            return state
    return {
        "schemaId": "sanguo-autopilot-marathon-state.v1",
        "runId": args.run_id.strip() or f"sanguo-marathon-{utc_stamp()}",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "currentRound": 0,
        "stopReason": None,
        "canonicalWrites": False,
        "config": {
            "topN": args.top_n,
            "targetCompleteness": normalize_target(args.target_completeness),
            "maxRounds": args.max_rounds,
            "topSourceRefs": args.top_source_refs,
            "sourceEventPackets": str(source_event_packets_path or args.source_event_packets),
            "advanceSourceRefWindow": bool(args.advance_source_ref_window),
            "dryRun": bool(args.dry_run),
            "planOnly": bool(args.plan_only),
        },
        "history": [],
    }


def sync_state_config(
    state: dict[str, Any],
    args: argparse.Namespace,
    source_event_packets_path: Path,
) -> None:
    config = state.setdefault("config", {})
    if isinstance(config, dict):
        config.update({
            "topN": args.top_n,
            "targetCompleteness": normalize_target(args.target_completeness),
            "maxRounds": args.max_rounds,
            "topSourceRefs": args.top_source_refs,
            "sourceEventPackets": str(source_event_packets_path),
            "advanceSourceRefWindow": bool(args.advance_source_ref_window),
            "dryRun": bool(args.dry_run),
            "planOnly": bool(args.plan_only),
        })


def main() -> int:
    args = parse_args()
    target = normalize_target(args.target_completeness)
    output_root = resolve_path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    source_event_packets_path = resolve_marathon_source_event_packets_path(resolve_path(args.source_event_packets), bool(args.plan_only))

    state_path = output_root / "marathon-state.json"
    history_path = output_root / "progress-history.jsonl"
    progress_path = output_root / "progress-current.md"
    stop_path = output_root / "stop-evidence.json"

    state = load_or_init_state(args, output_root, source_event_packets_path)
    sync_state_config(state, args, source_event_packets_path)
    if state.get("stopReason") and not args.overwrite:
        raise SystemExit(f"Marathon already stopped with {state.get('stopReason')}. Use --overwrite to rerun.")

    start_round = int(state.get("currentRound") or 0) + 1 if args.resume else 1
    max_rounds = max(1, int(args.max_rounds or 1))
    history = list(state.get("history") or [])
    final_record: dict[str, Any] | None = None

    for round_number in range(start_round, max_rounds + 1):
        round_id = f"{args.round_prefix}-{round_number:03d}"
        round_dir = output_root / "rounds" / round_id
        round_dir.mkdir(parents=True, exist_ok=True)
        window_plan = choose_source_ref_window(history, round_number, args)
        rank_offset = int(window_plan.get("rankOffset") or 0)
        queue_path = resolve_path(args.queue)
        queue_focus = None
        focus_general_ids = focus_general_ids_from_history(history, target, limit=min(max(3, args.top_n // 5), 10))
        if focus_general_ids:
            focus_queue_path = round_dir / DEFAULT_FOCUS_QUEUE_SUBDIR / f"{round_id}-low-blocker-queue.jsonl"
            queue_focus = materialize_focused_queue_from_low_blockers(
                queue_path,
                focus_queue_path,
                focus_general_ids,
                source_ref_limit=max(1, args.top_source_refs * 2),
            )
            if queue_focus:
                queue_path = resolve_path(queue_focus["queuePath"])

        autopilot = run_autopilot(args, round_id, round_dir, rank_offset, queue_path, source_event_packets_path)
        completion_refresh = None
        if autopilot.get("invoked") and int(autopilot.get("exitCode") or 0) == 0:
            completion_refresh = refresh_completion_from_round(
                round_id=round_id,
                round_dir=round_dir,
                source_event_packets_path=source_event_packets_path,
                autopilot_summary=autopilot.get("summary") or {},
                dry_run=bool(args.dry_run),
            )
        completion_path_text = (
            str(completion_refresh.get("completionPath"))
            if isinstance(completion_refresh, dict) and completion_refresh.get("invoked")
            else args.completion
        )
        readiness = top_readiness(resolve_path(completion_path_text), args.top_n, target)
        signature = source_ref_signature(autopilot.get("summary") or {})
        round_record = {
            "schemaId": "sanguo-autopilot-marathon-round.v1",
            "generatedAt": utc_now(),
            "roundNumber": round_number,
            "roundId": round_id,
            "status": "planned" if args.plan_only else "completed",
            "roundDir": str(round_dir),
            "canonicalWrites": False,
            "sourceRefRankOffset": rank_offset,
            "queuePath": str(queue_path),
            "queueFocus": queue_focus,
            "sourceEventPacketsPath": str(source_event_packets_path),
            "sourceRefWindow": window_plan,
            "autopilot": autopilot,
            "completionRefresh": completion_refresh,
            "topReadiness": readiness,
            "sourceRefSignature": signature,
        }
        history.append(round_record)
        exhaustion = source_exhaustion(history, int(args.source_exhaustion_patience or 0))
        stop_reason = decide_stop(readiness, exhaustion, round_number, max_rounds, autopilot, args.stop_on_error)
        round_record["sourceExhaustion"] = exhaustion
        round_record["stopCandidate"] = stop_reason

        state.update({
            "updatedAt": utc_now(),
            "currentRound": round_number,
            "stopReason": stop_reason,
            "history": history,
            "latestRound": round_record,
            "latestCompletionPath": completion_path_text,
            "latestQueuePath": str(queue_path),
            "latestQueueFocus": queue_focus,
        })
        write_json(round_dir / "round-record.json", round_record)
        append_jsonl(history_path, round_record)
        write_json(state_path, state)
        write_progress_markdown(progress_path, state, round_record)
        final_record = round_record

        if stop_reason:
            break

    stop_evidence = {
        "schemaId": "sanguo-autopilot-marathon-stop-evidence.v1",
        "generatedAt": utc_now(),
        "runId": state.get("runId"),
        "stopReason": state.get("stopReason") or "not-stopped",
        "canonicalWrites": False,
        "finalRound": final_record,
        "outputs": {
            "state": str(state_path),
            "progress": str(progress_path),
            "history": str(history_path),
            "stopEvidence": str(stop_path),
        },
    }
    write_json(stop_path, stop_evidence)

    print(json.dumps({
        "ok": True,
        "runId": state.get("runId"),
        "stopReason": stop_evidence["stopReason"],
        "currentRound": state.get("currentRound"),
        "canonicalWrites": False,
        "outputs": stop_evidence["outputs"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())