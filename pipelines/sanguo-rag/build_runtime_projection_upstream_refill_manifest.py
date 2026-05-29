from __future__ import annotations

import argparse
import ast
import hashlib
import json
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
DEFAULT_QUEUE_SUMMARY_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/runtime-projection-upstream-feedback/"
    "runtime-projection-upstream-feedback-summary.json"
)
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/runtime-projection-upstream-feedback")

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Condense runtime projection upstream feedback queue into actionable refill manifest."
    )
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE_PATH))
    parser.add_argument("--queue-summary", default=str(DEFAULT_QUEUE_SUMMARY_PATH))
    parser.add_argument("--batch-report", default="")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--work-items-file-name", default="runtime-projection-upstream-refill-work-items.jsonl")
    parser.add_argument("--summary-file-name", default="runtime-projection-upstream-refill-manifest.json")
    parser.add_argument("--markdown-file-name", default="runtime-projection-upstream-refill-summary.md")
    parser.add_argument("--top-n", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


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


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def stable_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def parse_reason_text(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        payload = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [text]
    if isinstance(payload, list):
        result: list[str] = []
        for row in payload:
            if isinstance(row, dict):
                reason = str(row.get("reason") or "").strip()
                if reason:
                    result.append(reason)
        return unique(result) or [text]
    return [text]


def batch_report_path(args: argparse.Namespace, queue_summary: dict[str, Any]) -> Path | None:
    if args.batch_report:
        return resolve_path(args.batch_report)
    path_text = str(object_map(queue_summary.get("inputs")).get("batchReportPath") or "").strip()
    if not path_text:
        return None
    return resolve_path(path_text)


def priority_for(row: dict[str, Any]) -> str:
    proposal_type = str(row.get("proposalType") or "").strip()
    trace_sources = set(string_list(row.get("traceSources")))
    if proposal_type == "missing-stable-input":
        return "P0"
    if trace_sources == {"declaredGeneralIds"}:
        return "P1"
    if "declaredGeneralIds" in trace_sources:
        return "P2"
    if trace_sources == {"aliasMatch"}:
        return "P3"
    return "P2"


def work_type_for(row: dict[str, Any]) -> str:
    proposal_type = str(row.get("proposalType") or "").strip()
    trace_sources = set(string_list(row.get("traceSources")))
    if proposal_type == "missing-stable-input":
        return "stable-seed-backfill"
    if trace_sources == {"declaredGeneralIds"}:
        return "relationship-authority-backfill"
    if "declaredGeneralIds" in trace_sources and "aliasMatch" in trace_sources:
        return "participant-plus-relationship-backfill"
    if trace_sources == {"aliasMatch"}:
        return "source-grounding-required"
    return "projection-source-backfill"


def blocker_reason_for(row: dict[str, Any]) -> str:
    trace_sources = set(string_list(row.get("traceSources")))
    if trace_sources == {"aliasMatch"}:
        return "alias-only cannot become runtime scene authority; source-grounded participant evidence is required first"
    if "aliasMatch" in trace_sources and "declaredGeneralIds" in trace_sources:
        return "mixed alias/participant trace still needs source-grounded relationship authority before scene promotion"
    return ""


def upstream_owners_for(row: dict[str, Any]) -> list[str]:
    proposal_type = str(row.get("proposalType") or "").strip()
    missing_fields = set(string_list(row.get("missingFields")))
    owners: list[str] = []
    if proposal_type == "missing-stable-input":
        owners.append("stable-knowledge-bootstrap enrichment")
    if {"relationshipEdges", "sourceRefs"} & missing_fields:
        owners.append("source-event-packets refinement")
    if "relationshipRefs" in missing_fields:
        owners.append("relationship-evidence refinement")
    if {"readyEvent", "sceneAuthority"} & missing_fields:
        owners.append("ready-event promotion bridge")
    return unique(owners)


def suggested_source_files_for(row: dict[str, Any], batch_inputs: dict[str, Any]) -> list[str]:
    proposal_type = str(row.get("proposalType") or "").strip()
    missing_fields = set(string_list(row.get("missingFields")))
    suggestions: list[str] = []
    if proposal_type == "missing-stable-input":
        suggestions.extend(string_list(batch_inputs.get("stableKnowledge")))
    if {"relationshipEdges", "sourceRefs"} & missing_fields:
        suggestions.extend(string_list(batch_inputs.get("sourceEventPackets")))
    if "relationshipRefs" in missing_fields:
        suggestions.extend(string_list(batch_inputs.get("relationshipEvidence")))
    if {"readyEvent", "sceneAuthority"} & missing_fields:
        suggestions.extend(string_list(batch_inputs.get("events")))
    return unique(suggestions)


def next_pipeline_steps_for(row: dict[str, Any]) -> list[str]:
    proposal_type = str(row.get("proposalType") or "").strip()
    trace_sources = set(string_list(row.get("traceSources")))
    if proposal_type == "missing-stable-input":
        return [
            "fill stable-knowledge-bootstrap.identitySeeds",
            "fill stable-knowledge-bootstrap.basicProfileSeeds",
            "rerun runtime profile export",
        ]
    if trace_sources == {"aliasMatch"}:
        return [
            "replace alias-only mention with source-grounded participant/sourceRefs in source-event-packets",
            "only after source grounding exists, consider relationship evidence refinement",
            "rerun runtime profile export and projection queue",
        ]
    return [
        "refine source-event-packets to add relationshipEdges and explicit participants",
        "refine relationship-evidence to add relationshipRefs for the same pair",
        "if grounded event authority exists, stage ready-event sceneAuthority via promotion bridge",
        "rerun runtime profile export and projection queue",
    ]


def summarize_rows(rows: list[dict[str, Any]], batch_inputs: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        priority = priority_for(row)
        work_type = work_type_for(row)
        trace_sources = tuple(string_list(row.get("traceSources")))
        missing_fields = tuple(sorted(set(string_list(row.get("missingFields")))))
        key = (
            priority,
            work_type,
            str(row.get("proposalType") or ""),
            str(row.get("generalId") or ""),
            str(row.get("targetGeneralId") or ""),
            trace_sources,
            missing_fields,
        )
        bucket = grouped.get(key)
        if not bucket:
            identity = {
                "priority": priority,
                "workType": work_type,
                "proposalType": str(row.get("proposalType") or ""),
                "generalId": str(row.get("generalId") or ""),
                "targetGeneralId": str(row.get("targetGeneralId") or ""),
                "traceSources": list(trace_sources),
                "missingFields": list(missing_fields),
            }
            bucket = {
                "manifestItemId": f"runtime-projection-refill:{stable_hash(identity)}",
                **identity,
                "occurrenceCount": 0,
                "sourceRefs": [],
                "sourceTypes": [],
                "sampleProposalId": str(row.get("proposalId") or ""),
                "sampleSourceQuote": str(row.get("sourceQuote") or "").strip(),
                "samplePersonaPath": str(object_map(row.get("evidence")).get("personaPath") or "").strip(),
                "recommendedActions": [],
                "requiredUpstreamData": [],
                "upstreamOwners": upstream_owners_for(row),
                "suggestedSourceFiles": suggested_source_files_for(row, batch_inputs),
                "blocker": False,
                "blockerReasons": [],
                "reasonHints": [],
                "nextPipelineSteps": next_pipeline_steps_for(row),
            }
            grouped[key] = bucket
        bucket["occurrenceCount"] += 1
        source_ref = str(row.get("sourceRef") or "").strip()
        if source_ref:
            bucket["sourceRefs"].append(source_ref)
        source_type = str(row.get("sourceType") or "").strip()
        if source_type:
            bucket["sourceTypes"].append(source_type)
        bucket["recommendedActions"].extend(string_list(row.get("recommendedActions")))
        bucket["requiredUpstreamData"].extend(string_list(row.get("requiredUpstreamData")))
        bucket["reasonHints"].extend(parse_reason_text(row.get("reason")))
        blocker_reason = blocker_reason_for(row)
        if blocker_reason:
            bucket["blocker"] = True
            bucket["blockerReasons"].append(blocker_reason)

    work_items: list[dict[str, Any]] = []
    for bucket in grouped.values():
        bucket["sourceRefs"] = unique(bucket["sourceRefs"])
        bucket["sourceRefCount"] = len(bucket["sourceRefs"])
        bucket["sourceTypes"] = unique(bucket["sourceTypes"])
        bucket["recommendedActions"] = unique(bucket["recommendedActions"])
        bucket["requiredUpstreamData"] = unique(bucket["requiredUpstreamData"])
        bucket["upstreamOwners"] = unique(bucket["upstreamOwners"])
        bucket["suggestedSourceFiles"] = unique(bucket["suggestedSourceFiles"])
        bucket["blockerReasons"] = unique(bucket["blockerReasons"])
        bucket["reasonHints"] = unique(bucket["reasonHints"])
        bucket["sceneAuthorityDeferred"] = (
            "ready-event promotion bridge" in bucket["upstreamOwners"] and not bucket["blocker"]
        )
        work_items.append(bucket)

    work_items.sort(
        key=lambda item: (
            PRIORITY_RANK.get(str(item.get("priority") or "P9"), 9),
            -int(item.get("occurrenceCount") or 0),
            str(item.get("generalId") or ""),
            str(item.get("targetGeneralId") or ""),
        )
    )
    return work_items


def render_markdown(summary: dict[str, Any], work_items: list[dict[str, Any]], top_n: int) -> str:
    lines = [
        "# Runtime Projection Upstream Refill Manifest",
        "",
        f"- Generated At: `{summary['generatedAt']}`",
        f"- Canonical Writes: `{summary['canonicalWrites']}`",
        f"- Queue Proposal Count: `{summary['queueProposalCount']}`",
        f"- Work Item Count: `{summary['workItemCount']}`",
        f"- Blocking Work Item Count: `{summary['blockingWorkItemCount']}`",
        "",
        "## Priority Counts",
        "",
    ]
    for key, value in summary.get("priorityCounts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Top Owners", ""])
    for key, value in summary.get("topUpstreamOwners", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Top Source Refs", ""])
    for key, value in summary.get("topSourceRefs", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if summary.get("topBlockerSourceRefs"):
        lines.extend(["", "## Top Alias-Only Blocker Source Refs", ""])
        for key, value in summary.get("topBlockerSourceRefs", {}).items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Work Items",
            "",
            "| Priority | General | Target | Work Type | Count | Missing Fields | Owners | Blocker |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in work_items[: max(top_n, 1)]:
        lines.append(
            "| {priority} | `{general}` | `{target}` | `{work_type}` | {count} | {fields} | {owners} | {blocker} |".format(
                priority=str(item.get("priority") or ""),
                general=str(item.get("generalId") or ""),
                target=str(item.get("targetGeneralId") or ""),
                work_type=str(item.get("workType") or ""),
                count=int(item.get("occurrenceCount") or 0),
                fields=", ".join(string_list(item.get("missingFields"))) or "-",
                owners=", ".join(string_list(item.get("upstreamOwners"))) or "-",
                blocker="yes" if item.get("blocker") else "no",
            )
        )
    lines.extend(["", "## Refill Policy", ""])
    lines.append("- `declaredGeneralIds` only: 補 `relationshipEdges` / `relationshipRefs`，必要時再做 `sceneAuthority` promotion。")
    lines.append("- `aliasMatch + declaredGeneralIds`: 先去 alias 化並補 source-grounded participant/sourceRefs，再補 relationship authority。")
    lines.append("- `aliasMatch` only: 維持 blocker；不可直接當 runtime scene authority。")
    lines.append("")
    return "\n".join(lines)


def build_summary(
    *,
    queue_path: Path,
    queue_summary_path: Path,
    batch_report_path_value: Path | None,
    batch_inputs: dict[str, Any],
    queue_rows: list[dict[str, Any]],
    work_items: list[dict[str, Any]],
) -> dict[str, Any]:
    priority_counts = Counter(str(item.get("priority") or "") for item in work_items)
    work_type_counts = Counter(str(item.get("workType") or "") for item in work_items)
    owner_counts = Counter(owner for item in work_items for owner in string_list(item.get("upstreamOwners")))
    general_counts = Counter(str(item.get("generalId") or "") for item in work_items)
    target_counts = Counter(str(item.get("targetGeneralId") or "") for item in work_items)
    source_ref_counts = Counter(str(row.get("sourceRef") or "").strip() for row in queue_rows if str(row.get("sourceRef") or "").strip())
    blocker_source_ref_counts = Counter(
        str(row.get("sourceRef") or "").strip()
        for row in queue_rows
        if str(row.get("sourceRef") or "").strip() and priority_for(row) == "P3"
    )
    return {
        "schemaVersion": "runtime-projection-upstream-refill-manifest.v1",
        "generatedAt": utc_now(),
        "mode": "runtime-projection-upstream-refill-manifest-builder",
        "canonicalWrites": False,
        "inputs": {
            "queuePath": repo_relative(queue_path),
            "queueSummaryPath": repo_relative(queue_summary_path),
            "batchReportPath": repo_relative(batch_report_path_value) if batch_report_path_value else "",
            "batchInputFiles": {key: str(value) for key, value in batch_inputs.items() if value},
        },
        "queueProposalCount": len(queue_rows),
        "workItemCount": len(work_items),
        "blockingWorkItemCount": len([item for item in work_items if item.get("blocker")]),
        "priorityCounts": dict(sorted(priority_counts.items(), key=lambda item: PRIORITY_RANK.get(item[0], 9))),
        "workTypeCounts": dict(sorted(work_type_counts.items())),
        "topUpstreamOwners": dict(owner_counts.most_common(12)),
        "topGeneralWorkItems": dict(general_counts.most_common(12)),
        "topTargetWorkItems": dict(target_counts.most_common(12)),
        "topSourceRefs": dict(source_ref_counts.most_common(15)),
        "topBlockerSourceRefs": dict(blocker_source_ref_counts.most_common(15)),
        "nextActionOrder": [
            "source-event-packets refinement",
            "relationship-evidence refinement",
            "ready-event promotion bridge",
            "rerun runtime profile export + projection queue",
        ],
    }


def main() -> int:
    args = parse_args()
    queue_path = resolve_path(args.queue)
    queue_summary_path = resolve_path(args.queue_summary)
    output_root = resolve_path(args.output_root)
    work_items_path = output_root / str(args.work_items_file_name)
    summary_path = output_root / str(args.summary_file_name)
    markdown_path = output_root / str(args.markdown_file_name)
    if not args.overwrite and any(path.exists() for path in (work_items_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {summary_path}")

    queue_rows = read_jsonl(queue_path)
    queue_summary = read_json(queue_summary_path)
    batch_report_path_value = batch_report_path(args, queue_summary)
    batch_inputs = object_map(read_json(batch_report_path_value).get("inputs")) if batch_report_path_value else {}
    work_items = summarize_rows(queue_rows, batch_inputs)
    summary = build_summary(
        queue_path=queue_path,
        queue_summary_path=queue_summary_path,
        batch_report_path_value=batch_report_path_value,
        batch_inputs=batch_inputs,
        queue_rows=queue_rows,
        work_items=work_items,
    )

    write_jsonl(work_items_path, work_items)
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(summary, work_items, args.top_n), encoding="utf-8")
    print(
        "[build_runtime_projection_upstream_refill_manifest] "
        f"queueRows={len(queue_rows)} workItems={len(work_items)} blockers={summary['blockingWorkItemCount']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())