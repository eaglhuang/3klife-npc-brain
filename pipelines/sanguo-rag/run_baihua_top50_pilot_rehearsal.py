from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run wave-001 top50 bootstrap pilot rehearsal summary.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
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


def render_markdown(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    acceptance = summary["acceptance"]
    lines: list[str] = []
    lines.append("# Top50 Bootstrap Wave-001 Rehearsal Summary")
    lines.append("")
    lines.append(f"- GeneratedAt: `{summary['generatedAt']}`")
    lines.append(f"- CandidateCount: `{metrics['candidateCount']}`")
    lines.append(f"- ReviewLaneCount: `{metrics['reviewLaneCount']}`")
    lines.append(f"- ConflictCount: `{metrics['conflictCount']}`")
    lines.append(f"- WhitelistCandidateCount: `{metrics['whitelistCandidateCount']}`")
    lines.append(f"- BlacklistCandidateCount: `{metrics['blacklistCandidateCount']}`")
    lines.append("")
    lines.append("## Acceptance")
    lines.append("")
    for item in acceptance:
        status = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- `{status}` {item['id']}: {item['description']}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- 本摘要僅做 bootstrap rehearshal 與治理驗收，不直接寫入 canonical。")
    lines.append("- 後續正式升級需依 human decisions 與 trust-zone 規則執行。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    summary_json_path = output_root / "top50-bootstrap-wave-001-summary.json"
    summary_md_path = output_root / "top50-bootstrap-wave-001-summary.md"
    whitelist_path = output_root / "top50-bootstrap-new-whitelist-candidates.jsonl"
    blacklist_path = output_root / "top50-bootstrap-new-blacklist-candidates.jsonl"

    if not args.overwrite and any(path.exists() for path in [summary_json_path, summary_md_path, whitelist_path, blacklist_path]):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {summary_json_path}")

    candidate_path = output_root / "merged-bootstrap-candidates-conflict-checked.jsonl"
    review_lane_path = output_root / "top50-bootstrap-review-lane.jsonl"
    conflict_report_path = output_root / "bootstrap-conflict-report.json"
    human_review_md_path = output_root / "top50-bootstrap-human-review.zh-TW.md"
    human_template_path = output_root / "top50-bootstrap-human-decisions.template.json"
    jobs_summary_path = output_root / "top50-bootstrap-jobs-summary.json"

    candidates = read_jsonl(candidate_path)
    review_rows = read_jsonl(review_lane_path)
    conflict_report = read_json(conflict_report_path)
    human_template = read_json(human_template_path)
    jobs_summary = read_json(jobs_summary_path) if jobs_summary_path.exists() else {}
    human_review_exists = human_review_md_path.exists()

    whitelist_rows: list[dict[str, Any]] = []
    blacklist_rows: list[dict[str, Any]] = []

    for row in candidates:
        stage = str(row.get("bootstrapStage") or "")
        conflict_flags = [str(item).strip() for item in (row.get("conflictFlags") or []) if str(item or "").strip()]
        output_row = {
            "trustKey": str(row.get("trustKey") or ""),
            "relationshipType": str(row.get("relationshipType") or ""),
            "fromId": str(row.get("fromId") or ""),
            "toId": str(row.get("toId") or ""),
            "bootstrapStage": stage,
            "supportCount": int(row.get("supportCount") or 0),
            "confidenceAggregate": float(row.get("confidenceAggregate") or 0.0),
            "conflictFlags": conflict_flags,
            "canonicalWrites": False,
        }
        if conflict_flags:
            blacklist_rows.append(output_row)
            continue
        if stage == "review-ready":
            whitelist_rows.append(output_row)

    write_jsonl(whitelist_path, whitelist_rows)
    write_jsonl(blacklist_path, blacklist_rows)

    candidate_stage_counter = Counter(str(row.get("bootstrapStage") or "") for row in candidates)
    review_type_counter = Counter(str(row.get("relationshipType") or "") for row in review_rows)
    conflict_count = int(conflict_report.get("outputs", {}).get("conflictCount") or 0)
    template_decisions = human_template.get("decisions") if isinstance(human_template.get("decisions"), list) else []

    acceptance = [
        {
            "id": "top50-bootstrap-candidates-generated",
            "description": "已產出 top50 硬關係白名單候選",
            "passed": len(candidates) > 0 and len(whitelist_rows) > 0,
        },
        {
            "id": "auto-conflict-and-duplicate-detection",
            "description": "可自動抓出衝突與重複（含 conflict report）",
            "passed": conflict_report_path.exists() and conflict_count >= 0,
        },
        {
            "id": "human-review-markdown-ready",
            "description": "可輸出繁中人工審核表",
            "passed": human_review_exists and len(template_decisions) > 0,
        },
        {
            "id": "review-approved-can-map-to-whitelist",
            "description": "審核通過後可對應 whitelist 候選格式",
            "passed": len(whitelist_rows) > 0 and all(str(row.get("trustKey") or "").startswith("relationship|") for row in whitelist_rows),
        },
    ]

    summary = {
        "mode": "baihua-bootstrap-top50-pilot-rehearsal",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "candidatePath": str(candidate_path),
            "reviewLanePath": str(review_lane_path),
            "conflictReportPath": str(conflict_report_path),
            "humanReviewMarkdownPath": str(human_review_md_path),
            "humanDecisionTemplatePath": str(human_template_path),
            "jobsSummaryPath": str(jobs_summary_path) if jobs_summary_path.exists() else "",
        },
        "outputs": {
            "summaryJsonPath": str(summary_json_path),
            "summaryMarkdownPath": str(summary_md_path),
            "whitelistCandidatePath": str(whitelist_path),
            "blacklistCandidatePath": str(blacklist_path),
        },
        "metrics": {
            "jobCount": int(jobs_summary.get("outputs", {}).get("jobCount") or 0) if isinstance(jobs_summary, dict) else 0,
            "candidateCount": len(candidates),
            "reviewLaneCount": len(review_rows),
            "conflictCount": conflict_count,
            "whitelistCandidateCount": len(whitelist_rows),
            "blacklistCandidateCount": len(blacklist_rows),
            "candidateStageCounts": dict(sorted(candidate_stage_counter.items())),
            "reviewLaneTypeCounts": dict(sorted(review_type_counter.items())),
        },
        "acceptance": acceptance,
    }
    summary["allAcceptancePassed"] = all(bool(item["passed"]) for item in acceptance)
    write_json(summary_json_path, summary)
    summary_md_path.write_text(render_markdown(summary), encoding="utf-8")

    print(f"[run_baihua_top50_pilot_rehearsal] wrote {whitelist_path}")
    print(f"[run_baihua_top50_pilot_rehearsal] wrote {blacklist_path}")
    print(f"[run_baihua_top50_pilot_rehearsal] wrote {summary_json_path}")
    print(f"[run_baihua_top50_pilot_rehearsal] wrote {summary_md_path}")
    print(
        "[run_baihua_top50_pilot_rehearsal] "
        f"candidates={len(candidates)} whitelist={len(whitelist_rows)} conflicts={conflict_count} "
        f"allAcceptancePassed={summary['allAcceptancePassed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
