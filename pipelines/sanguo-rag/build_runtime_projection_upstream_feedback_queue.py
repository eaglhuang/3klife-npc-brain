from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_BATCH_REPORT = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles/top50-runtime-fill-r1-export-report.json"
)
DEFAULT_PROFILE_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles")
DEFAULT_POLICY_PATH = Path("data/sanguo/policies/policy-runtime-general-profile-export.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/runtime-projection-upstream-feedback")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build proposal-only upstream refill queue from runtime projection gaps."
    )
    parser.add_argument("--batch-report", default=str(DEFAULT_BATCH_REPORT))
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--general-id", action="append", default=[])
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--queue-file-name", default="runtime-projection-upstream-feedback-queue.jsonl")
    parser.add_argument("--summary-file-name", default="runtime-projection-upstream-feedback-summary.json")
    parser.add_argument("--markdown-file-name", default="runtime-projection-upstream-feedback-summary.md")
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def object_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def compact_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def stable_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def queue_policy(policy: dict[str, Any]) -> dict[str, Any]:
    value = object_map(policy.get("upstreamFeedbackQueuePolicy"))
    if not value:
        raise ValueError("policy-runtime-general-profile-export missing upstreamFeedbackQueuePolicy")
    return value


def selected_general_ids(args: argparse.Namespace, batch_report: dict[str, Any]) -> list[str]:
    explicit = string_list(args.general_id)
    if explicit:
        return explicit
    return string_list(batch_report.get("generalIds"))


def persona_paths(profile_root: Path, general_ids: list[str]) -> list[Path]:
    if general_ids:
        return [profile_root / general_id / f"{general_id}.persona.json" for general_id in general_ids]
    return sorted(profile_root.glob("*/*.persona.json"))


def iter_runtime_sources(persona: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sources: list[tuple[str, dict[str, Any]]] = []
    for row in object_rows(persona.get("storyBeats")):
        sources.append(("storyBeat", row))
    for row in object_rows(persona.get("sourceHighlights")):
        sources.append(("sourceHighlight", row))
    return sources


def source_ref_for(source: dict[str, Any]) -> str:
    source_ref = str(source.get("sourceRef") or "").strip()
    if source_ref:
        return source_ref
    refs = string_list(source.get("sourceRefs"))
    return refs[0] if refs else ""


def projection_gap_fields(projection: dict[str, Any], gap_policy: dict[str, Any]) -> list[str]:
    fields_by_trace = object_map(gap_policy.get("missingFieldsByTraceSource"))
    default_fields = string_list(gap_policy.get("defaultMissingFields"))
    fields: list[str] = []
    for trace_source in string_list(projection.get("traceSources")):
        fields.extend(string_list(fields_by_trace.get(trace_source)))
    if not fields:
        fields.extend(default_fields)
    return sorted(set(fields))


def projection_actions(projection: dict[str, Any], gap_policy: dict[str, Any]) -> list[str]:
    action_by_trace = object_map(gap_policy.get("recommendedActionByTraceSource"))
    fallback_action = str(gap_policy.get("fallbackRecommendedAction") or "").strip()
    actions = [str(action_by_trace.get(trace_source) or "").strip() for trace_source in string_list(projection.get("traceSources"))]
    actions = [action for action in actions if action]
    if not actions and fallback_action:
        actions.append(fallback_action)
    return sorted(set(actions))


def missing_stable_proposals(
    *,
    batch_report: dict[str, Any],
    selected_ids: set[str],
    policy_config: dict[str, Any],
) -> list[dict[str, Any]]:
    proposal_config = object_map(policy_config.get("missingStableInput"))
    proposal_type = str(proposal_config.get("proposalType") or "missing-stable-input")
    proposals: list[dict[str, Any]] = []
    for gap in object_rows(object_map(batch_report.get("upstreamGaps")).get("missingStableInputs")):
        general_id = str(gap.get("generalId") or "").strip()
        if not general_id or (selected_ids and general_id not in selected_ids):
            continue
        missing_fields = string_list(gap.get("missingFields"))
        identity = {"proposalType": proposal_type, "generalId": general_id, "missingFields": missing_fields}
        proposals.append(
            {
                "proposalId": f"runtime-projection-feedback:{stable_hash(identity)}",
                "proposalType": proposal_type,
                "generalId": general_id,
                "targetGeneralId": general_id,
                "sourceDataStatus": str(proposal_config.get("sourceDataStatus") or "missing-stable-input"),
                "missingFields": missing_fields,
                "requiredUpstreamData": string_list(proposal_config.get("requiredUpstreamData")),
                "recommendedActions": string_list(proposal_config.get("recommendedActions"))
                or string_list(gap.get("recommendedAction")),
                "reason": str(proposal_config.get("reason") or gap.get("recommendedAction") or "").strip(),
                "evidence": {"batchReportStatus": "missing-stable-input"},
            }
        )
    return proposals


def projection_gap_proposals(
    *,
    profile_root: Path,
    general_ids: list[str],
    focus_policy: dict[str, Any],
    policy_config: dict[str, Any],
) -> list[dict[str, Any]]:
    gap_policy = object_map(policy_config.get("projectionSourceGap"))
    proposal_type = str(gap_policy.get("proposalType") or "projection-source-gap")
    missing_status = str(focus_policy.get("missingDataStatus") or "insufficient_source_data")
    proposals: list[dict[str, Any]] = []
    for path in persona_paths(profile_root, general_ids):
        if not path.exists():
            continue
        persona = read_json(path)
        general_id = str(persona.get("generalId") or path.parent.name).strip()
        for source_type, source in iter_runtime_sources(persona):
            source_ref = source_ref_for(source)
            for projection in object_rows(source.get("targetProjections")):
                status = str(projection.get("sourceDataStatus") or "").strip()
                if status != missing_status and not projection.get("upstreamFeedback"):
                    continue
                target_id = str(projection.get("targetId") or "").strip()
                if not target_id:
                    continue
                missing_fields = projection_gap_fields(projection, gap_policy)
                identity = {
                    "proposalType": proposal_type,
                    "generalId": general_id,
                    "targetId": target_id,
                    "sourceType": source_type,
                    "sourceRef": source_ref,
                    "traceSources": string_list(projection.get("traceSources")),
                }
                proposals.append(
                    {
                        "proposalId": f"runtime-projection-feedback:{stable_hash(identity)}",
                        "proposalType": proposal_type,
                        "generalId": general_id,
                        "targetGeneralId": target_id,
                        "sourceType": source_type,
                        "sourceRef": source_ref,
                        "sourceTitle": source.get("title") or source.get("label"),
                        "sourceQuote": compact_text(
                            source.get("sourceQuote")
                            or source.get("example")
                            or source.get("quote")
                            or source.get("text")
                            or source.get("summary")
                        ),
                        "traceSources": string_list(projection.get("traceSources")),
                        "linkAuthority": projection.get("linkAuthority"),
                        "sourceDataStatus": status or missing_status,
                        "sceneEligible": bool(projection.get("sceneEligible")),
                        "missingFields": missing_fields,
                        "requiredUpstreamData": string_list(gap_policy.get("requiredUpstreamData")),
                        "recommendedActions": projection_actions(projection, gap_policy),
                        "reason": str(projection.get("upstreamFeedback") or gap_policy.get("reason") or "").strip(),
                        "evidence": {"personaPath": repo_relative(path), "projectionGate": focus_policy.get("version")},
                    }
                )
    return proposals


def finalize_proposals(rows: list[dict[str, Any]], policy_config: dict[str, Any]) -> list[dict[str, Any]]:
    proposal_status = str(policy_config.get("proposalStatus") or "proposal-open")
    review_gate = str(policy_config.get("reviewGate") or "upstream-source-review")
    canonical_writes = bool(policy_config.get("canonicalWrites", False))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = {
            "proposalStatus": proposal_status,
            "reviewGate": review_gate,
            "canonicalWrites": canonical_writes,
            **row,
        }
        deduped.setdefault(str(row.get("proposalId") or stable_hash(row)), row)
    return sorted(
        deduped.values(),
        key=lambda item: (
            str(item.get("proposalType") or ""),
            str(item.get("generalId") or ""),
            str(item.get("targetGeneralId") or ""),
            str(item.get("sourceRef") or ""),
        ),
    )


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Runtime Projection Upstream Feedback Queue",
        "",
        f"- Generated At: `{summary['generatedAt']}`",
        f"- Canonical Writes: `{summary['canonicalWrites']}`",
        f"- Proposal Count: `{summary['proposalCount']}`",
        "",
        "| Type | General | Target | Missing Fields | Recommended Actions | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:80]:
        lines.append(
            "| {proposal_type} | `{general}` | `{target}` | {fields} | {actions} | {reason} |".format(
                proposal_type=str(row.get("proposalType") or ""),
                general=str(row.get("generalId") or ""),
                target=str(row.get("targetGeneralId") or ""),
                fields=", ".join(string_list(row.get("missingFields"))) or "-",
                actions=", ".join(string_list(row.get("recommendedActions"))) or "-",
                reason=compact_text(row.get("reason"), 120).replace("|", "\\|"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    batch_report_path = resolve_path(args.batch_report)
    profile_root = resolve_path(args.profile_root)
    policy_path = resolve_path(args.policy)
    output_root = resolve_path(args.output_root)
    queue_path = output_root / str(args.queue_file_name)
    summary_path = output_root / str(args.summary_file_name)
    markdown_path = output_root / str(args.markdown_file_name)
    if not args.overwrite and any(path.exists() for path in (queue_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {queue_path}")

    batch_report = read_json(batch_report_path)
    policy = read_json(policy_path)
    policy_config = queue_policy(policy)
    focus_policy = object_map(policy.get("focusProjectionPolicy"))
    general_ids = selected_general_ids(args, batch_report)
    selected_ids = set(general_ids)
    proposals = finalize_proposals(
        [
            *missing_stable_proposals(
                batch_report=batch_report,
                selected_ids=selected_ids,
                policy_config=policy_config,
            ),
            *projection_gap_proposals(
                profile_root=profile_root,
                general_ids=general_ids,
                focus_policy=focus_policy,
                policy_config=policy_config,
            ),
        ],
        policy_config,
    )
    type_counts = Counter(str(row.get("proposalType") or "") for row in proposals)
    general_counts = Counter(str(row.get("generalId") or "") for row in proposals)
    summary = {
        "schemaVersion": str(policy_config.get("schemaVersion") or "runtime-projection-upstream-feedback-queue.v1"),
        "generatedAt": utc_now(),
        "mode": "runtime-projection-upstream-feedback-queue-builder",
        "canonicalWrites": bool(policy_config.get("canonicalWrites", False)),
        "inputs": {
            "batchReportPath": repo_relative(batch_report_path),
            "profileRoot": repo_relative(profile_root),
            "policyPath": repo_relative(policy_path),
            "generalIds": general_ids,
        },
        "outputs": {
            "queuePath": repo_relative(queue_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
        "proposalCount": len(proposals),
        "proposalTypeCounts": dict(sorted(type_counts.items())),
        "topGeneralProposalCounts": dict(general_counts.most_common(20)),
    }
    write_jsonl(queue_path, proposals)
    write_json(summary_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(summary, proposals), encoding="utf-8")
    print(
        "[build_runtime_projection_upstream_feedback_queue] "
        f"proposals={len(proposals)} canonicalWrites={summary['canonicalWrites']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())