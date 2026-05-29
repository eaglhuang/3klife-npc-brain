from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a prioritized romance-variant source campaign from policy-defined steps and local inventory."
    )
    parser.add_argument("--policy-path", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    return (REPO_ROOT / path).resolve()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
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
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['targetNameZhTw']} Variant 補源批次計畫",
        "",
        "- 這份計畫只排定 romance-variant 正式來源候選的補源順序，不混回白話主線。",
        "- 本地若只有結構痕跡或 persona 痕跡，仍視為『未達 quote-ready』。",
        "",
        "| 優先序 | 任務 | 對手 | 本地線索數 | 狀態 | 下一步 |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {priority} | {label} | {counterparts} | {trace_count} | {status} | {next_action} |".format(
                priority=int(row["priority"]),
                label=row["demandLabelZhTw"],
                counterparts="、".join(row["preferredCounterpartNamesZhTw"]) or "無",
                trace_count=int(row["matchedInventoryCount"]),
                status=row["statusZhTw"],
                next_action=row["suggestedNextActionZhTw"].replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy_path)
    output_root = resolve_path(args.output_root)
    jsonl_path = output_root / f"{args.tag}.jsonl"
    summary_path = output_root / f"{args.tag}.summary.json"
    markdown_path = output_root / f"{args.tag}.zh-TW.md"
    if not args.overwrite and any(path.exists() for path in (jsonl_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {jsonl_path}")

    policy = read_json(policy_path)
    inputs = policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}
    inventory_rows = read_jsonl(resolve_path(str(inputs.get("localInventoryPath") or "")))
    source_demand_rows = read_jsonl(resolve_path(str(inputs.get("sourceDemandPath") or "")))
    target_general_id = str(policy.get("targetGeneralId") or "").strip()
    target_name = str(policy.get("targetNameZhTw") or target_general_id)

    rows: list[dict[str, Any]] = []
    for step in policy.get("campaignSteps") or []:
        if not isinstance(step, dict):
            continue
        demand_type = str(step.get("demandType") or "")
        counterpart_names = string_list(step.get("preferredCounterpartNamesZhTw"))
        matched_inventory = []
        for row in inventory_rows:
            if str(row.get("targetGeneralId") or "") != target_general_id:
                continue
            inventory_counterparts = string_list(row.get("counterpartDisplayNamesZhTw"))
            if demand_type == "entity-existence-and-quote-ready-anchor":
                if row.get("traceKind") in {"runtime-gap", "bundle-gap", "persona-note"}:
                    matched_inventory.append(row)
            elif set(counterpart_names) & set(inventory_counterparts):
                matched_inventory.append(row)

        matched_source_demands = []
        for row in source_demand_rows:
            if str(row.get("targetGeneralId") or "") != target_general_id:
                continue
            if str(row.get("demandType") or "") != demand_type:
                continue
            demand_counterparts = string_list(row.get("preferredCounterpartDisplayNamesZhTw"))
            if demand_type == "entity-existence-and-quote-ready-anchor" or set(counterpart_names) & set(demand_counterparts):
                matched_source_demands.append(row)

        trace_counts = Counter(str(row.get("traceKind") or "") for row in matched_inventory)
        status = "待補正式來源"
        if matched_inventory:
            status = "已有本地線索，但未達 quote-ready"
        rows.append(
            {
                "campaignId": f"{target_general_id}-variant-source:{step.get('stepId')}",
                "targetGeneralId": target_general_id,
                "targetNameZhTw": target_name,
                "priority": int(step.get("priority") or 99),
                "demandType": demand_type,
                "demandLabelZhTw": str(step.get("demandLabelZhTw") or demand_type),
                "preferredCounterpartIds": string_list(step.get("preferredCounterpartIds")),
                "preferredCounterpartNamesZhTw": counterpart_names,
                "matchedInventoryCount": len(matched_inventory),
                "matchedInventoryTraceKinds": dict(sorted(trace_counts.items())),
                "matchedDemandCount": len(matched_source_demands),
                "statusZhTw": status,
                "suggestedNextActionZhTw": str(step.get("reasonZhTw") or ""),
                "canonicalWrites": False,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), row["demandType"]))
    version_metadata = build_version_metadata(
        schema_version="top50-variant-source-campaign.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-variant-source-campaign",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "targetGeneralId": target_general_id,
        "targetNameZhTw": target_name,
        "policyPath": repo_relative(policy_path),
        "campaignStepCount": len(rows),
        "inventoryCount": len(inventory_rows),
        "demandTypeCounts": dict(sorted(Counter(row["demandType"] for row in rows).items())),
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
    }

    write_jsonl(jsonl_path, rows)
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, rows), encoding="utf-8")
    print(
        "[build_top50_variant_source_campaign] "
        f"target={target_general_id} steps={len(rows)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
