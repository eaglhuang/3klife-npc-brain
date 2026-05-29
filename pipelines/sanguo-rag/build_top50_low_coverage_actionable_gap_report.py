from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
DEFAULT_TAG = "top50-low-coverage-actionable-gap-report"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Top50 low-coverage actionable report after filtering focuses already resolved by human trust-zone decisions."
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--effective-gap-tag", default="top50-effective-gap-report")
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def collect_resolved_focus_ids(applied_payload: dict[str, Any], focus_ids: set[str]) -> set[str]:
    resolved: set[str] = set()
    for command in applied_payload.get("commands") or []:
        if not isinstance(command, dict):
            continue
        trust_key = str(command.get("trustKey") or "").strip()
        if not trust_key:
            continue
        parts = trust_key.split(":")
        ids = parts[1:]
        for general_id in ids:
            if general_id in focus_ids:
                resolved.add(general_id)
    return resolved


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Top50 低覆蓋可行動缺口報告",
        "",
        "- 這份報告會從有效缺口中，排除已被主 trust-zone 或 historical-phase 決策吸收的人物。",
        "- 因此它代表的是「現在還值得繼續投入補句窗或補來源」的低覆蓋 focus。",
        "",
        f"- 可行動低覆蓋人數：`{summary['actionableLowCoverageCount']}`",
        "",
        "| 人物 | primary passage | second-layer passage | second-layer supported | Top50 對手 supported |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["actionableLowCoverageFocuses"]:
        lines.append(
            "| {name} | {primary} | {secondary} | {supported} | {top50} |".format(
                name=item["focusNameZhTw"],
                primary=item["primarySelectedPassageCount"],
                secondary=item["secondLayerSelectedPassageCount"],
                supported=item["secondLayerSupportedCount"],
                top50=item["supportedTop50CounterpartCount"],
            )
        )
    if not summary["actionableLowCoverageFocuses"]:
        lines.append("| — | 0 | 0 | 0 | 0 |")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = resolve_path(args.output_root)
    json_path = output_root / f"{DEFAULT_TAG}.json"
    md_path = output_root / f"{DEFAULT_TAG}.zh-TW.md"
    if not args.overwrite and (json_path.exists() or md_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {json_path}")

    effective_gap_path = output_root / f"{args.effective_gap_tag}.json"
    main_applied_path = resolve_path(
        "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.json"
    )
    historical_applied_path = output_root / "top50-ruler-subject-historical-phase-human-decisions.applied.json"

    effective_gap = read_json(effective_gap_path)
    main_applied = read_json(main_applied_path)
    historical_applied = read_json(historical_applied_path) if historical_applied_path.exists() else {"commands": []}

    low_coverage_rows = effective_gap.get("effectiveLowCoverageFocuses") or []
    focus_ids = {str(row.get("focusGeneralId") or "") for row in low_coverage_rows if str(row.get("focusGeneralId") or "").strip()}
    resolved_main = collect_resolved_focus_ids(main_applied, focus_ids)
    resolved_historical = collect_resolved_focus_ids(historical_applied, focus_ids)
    resolved_focus_ids = resolved_main | resolved_historical

    actionable_rows = [
        row for row in low_coverage_rows if str(row.get("focusGeneralId") or "").strip() not in resolved_focus_ids
    ]
    actionable_rows.sort(
        key=lambda row: (
            int(row.get("primarySelectedPassageCount") or 0) + int(row.get("secondLayerSelectedPassageCount") or 0),
            str(row.get("focusGeneralId") or ""),
        )
    )

    version_metadata = build_version_metadata(
        schema_version="top50-low-coverage-actionable-gap-report.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-low-coverage-actionable-gap-report",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputEffectiveGapPath": repo_relative(effective_gap_path),
        "inputMainAppliedPath": repo_relative(main_applied_path),
        "inputHistoricalAppliedPath": repo_relative(historical_applied_path),
        "actionableLowCoverageCount": len(actionable_rows),
        "actionableLowCoverageFocuses": actionable_rows,
        "resolvedByMainTrustZone": sorted(resolved_main),
        "resolvedByHistoricalPhase": sorted(resolved_historical),
    }
    write_json(json_path, summary)
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(
        "[build_top50_low_coverage_actionable_gap_report] "
        f"actionableLowCoverageCount={len(actionable_rows)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
