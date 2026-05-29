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
DEFAULT_TAG = "top50-effective-gap-report"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an effective Top50 gap report that folds second-layer reviewed coverage back into the gap view."
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", default=DEFAULT_TAG)
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def build_top50_name_map(job_rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("focusGeneralId") or ""): str(row.get("focusNameZhTw") or row.get("focusGeneralId") or "")
        for row in job_rows
        if str(row.get("focusGeneralId") or "").strip()
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Top50 有效缺口報告",
        "",
        "- 這份報告會把 second-layer anchor 與 second-layer reviewed-cache 反算回主線覆蓋，不再只看 primary baihua packet。",
        "- 目的是把「其實已被 second-layer 補回的人物」與「真的還沒被有效覆蓋的人物」分開。",
        "",
        f"- Top50 人數：`{summary['top50Count']}`",
        f"- 有效覆蓋人數：`{summary['effectiveCoveredCount']}`",
        f"- 真正零覆蓋：`{summary['effectiveZeroCoverageCount']}`",
        f"- 低覆蓋（有效 passage 總數 <= 2）：`{summary['effectiveLowCoverageCount']}`",
        "",
        "## 真正零覆蓋",
        "",
    ]
    if summary["effectiveZeroCoverageFocuses"]:
        for item in summary["effectiveZeroCoverageFocuses"]:
            lines.append(f"- {item['focusNameZhTw']}（`{item['focusGeneralId']}`）")
    else:
        lines.append("- 無")

    lines.extend(
        [
            "",
            "## 低覆蓋人物",
            "",
            "| 人物 | primary passage | second-layer passage | second-layer supported | Top50 對手 supported |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summary["effectiveLowCoverageFocuses"]:
        lines.append(
            "| {name} | {primary} | {secondary} | {supported} | {top50} |".format(
                name=item["focusNameZhTw"],
                primary=item["primarySelectedPassageCount"],
                secondary=item["secondLayerSelectedPassageCount"],
                supported=item["secondLayerSupportedCount"],
                top50=item["supportedTop50CounterpartCount"],
            )
        )

    lines.extend(
        [
            "",
            "## 已由 Second-layer 補回的人物",
            "",
        ]
    )
    if summary["secondLayerRecoveredFocuses"]:
        for item in summary["secondLayerRecoveredFocuses"]:
            lines.append(
                "- {name}：second-layer passage=`{selected}`、supported=`{supported}`、Top50 對手 supported=`{top50}`".format(
                    name=item["focusNameZhTw"],
                    selected=item["secondLayerSelectedPassageCount"],
                    supported=item["secondLayerSupportedCount"],
                    top50=item["supportedTop50CounterpartCount"],
                )
            )
    else:
        lines.append("- 無")

    lines.extend(
        [
            "",
            "## 下一步建議",
            "",
        ]
    )
    if summary["nextActionHintsZhTw"]:
        for item in summary["nextActionHintsZhTw"]:
            lines.append(f"- {item}")
    else:
        lines.append("- 目前沒有額外建議。")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = resolve_path(args.output_root)
    json_path = output_root / f"{args.tag}.json"
    md_path = output_root / f"{args.tag}.zh-TW.md"
    if not args.overwrite and (json_path.exists() or md_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {json_path}")

    jobs_path = output_root / "top50-bootstrap-jobs.jsonl"
    primary_packets_path = output_root / "top50-focus-skill-packets.jsonl"
    second_packets_path = output_root / "top50-focus-skill-packets.second-layer-anchor.jsonl"
    second_reviewed_path = output_root / "codex-skill-review/top50-second-layer-focus-reviewed-cache.jsonl"

    job_rows = read_jsonl(jobs_path)
    primary_packet_rows = read_jsonl(primary_packets_path)
    second_packet_rows = read_jsonl(second_packets_path)
    second_reviewed_rows = read_jsonl(second_reviewed_path)

    focus_names = build_top50_name_map(job_rows)
    top50_ids = list(focus_names.keys())

    primary_counts = {str(row.get("focusGeneralId") or ""): int(row.get("selectedPassageCount") or 0) for row in primary_packet_rows}
    second_counts = {str(row.get("focusGeneralId") or ""): int(row.get("selectedPassageCount") or 0) for row in second_packet_rows}

    second_supported_counts = {focus_id: 0 for focus_id in top50_ids}
    second_supported_top50_counts = {focus_id: 0 for focus_id in top50_ids}
    for row in second_reviewed_rows:
        focus_id = str(row.get("focusGeneralId") or "")
        relationships = row.get("relationships") or []
        if focus_id not in second_supported_counts:
            continue
        supported = [rel for rel in relationships if str(rel.get("verdict") or "") == "supported"]
        second_supported_counts[focus_id] = len(supported)
        top50_counterparts = set()
        for rel in supported:
            from_id = str(rel.get("fromId") or "")
            to_id = str(rel.get("toId") or "")
            other_id = to_id if from_id == focus_id else from_id
            if other_id in focus_names:
                top50_counterparts.add(other_id)
        second_supported_top50_counts[focus_id] = len(top50_counterparts)

    effective_zero_coverage: list[dict[str, Any]] = []
    effective_low_coverage: list[dict[str, Any]] = []
    second_layer_recovered: list[dict[str, Any]] = []
    for focus_id in top50_ids:
        primary = int(primary_counts.get(focus_id, 0))
        secondary = int(second_counts.get(focus_id, 0))
        supported = int(second_supported_counts.get(focus_id, 0))
        top50_supported = int(second_supported_top50_counts.get(focus_id, 0))
        total_effective = primary + secondary
        item = {
            "focusGeneralId": focus_id,
            "focusNameZhTw": focus_names[focus_id],
            "primarySelectedPassageCount": primary,
            "secondLayerSelectedPassageCount": secondary,
            "secondLayerSupportedCount": supported,
            "supportedTop50CounterpartCount": top50_supported,
        }
        if total_effective == 0 and supported == 0:
            effective_zero_coverage.append(item)
        if total_effective <= 2:
            effective_low_coverage.append(item)
        if primary == 0 and (secondary > 0 or supported > 0):
            second_layer_recovered.append(item)

    effective_zero_coverage.sort(key=lambda row: row["focusGeneralId"])
    effective_low_coverage.sort(
        key=lambda row: (row["primarySelectedPassageCount"] + row["secondLayerSelectedPassageCount"], row["focusGeneralId"])
    )
    second_layer_recovered.sort(
        key=lambda row: (-row["supportedTop50CounterpartCount"], -row["secondLayerSupportedCount"], row["focusGeneralId"])
    )

    next_hints: list[str] = []
    if effective_zero_coverage:
        names = "、".join(item["focusNameZhTw"] for item in effective_zero_coverage[:4])
        next_hints.append(f"先補真正零覆蓋人物的來源或 anchor passage：{names}。")
    if effective_low_coverage:
        names = "、".join(item["focusNameZhTw"] for item in effective_low_coverage[:5])
        next_hints.append(f"再補低覆蓋人物的句窗或 second-layer passage：{names}。")
    recovered_top50 = [item for item in second_layer_recovered if item["supportedTop50CounterpartCount"] > 0]
    if recovered_top50:
        names = "、".join(item["focusNameZhTw"] for item in recovered_top50[:4])
        next_hints.append(f"已被 second-layer 補出 Top50 對手支持的人物，可優先回推主線審核：{names}。")

    version_metadata = build_version_metadata(
        schema_version="top50-effective-gap-report.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-effective-gap-report",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "jobsPath": repo_relative(jobs_path),
            "primaryPacketPath": repo_relative(primary_packets_path),
            "secondLayerPacketPath": repo_relative(second_packets_path),
            "secondLayerReviewedPath": repo_relative(second_reviewed_path),
        },
        "top50Count": len(top50_ids),
        "effectiveCoveredCount": len(top50_ids) - len(effective_zero_coverage),
        "effectiveZeroCoverageCount": len(effective_zero_coverage),
        "effectiveZeroCoverageFocuses": effective_zero_coverage,
        "effectiveLowCoverageCount": len(effective_low_coverage),
        "effectiveLowCoverageFocuses": effective_low_coverage,
        "secondLayerRecoveredCount": len(second_layer_recovered),
        "secondLayerRecoveredFocuses": second_layer_recovered,
        "nextActionHintsZhTw": next_hints,
    }
    write_json(json_path, summary)
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(
        "[build_top50_effective_gap_report] "
        f"effectiveCovered={summary['effectiveCoveredCount']} "
        f"effectiveZero={summary['effectiveZeroCoverageCount']} "
        f"effectiveLow={summary['effectiveLowCoverageCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
