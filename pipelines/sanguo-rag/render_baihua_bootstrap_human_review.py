from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_INPUT_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-bootstrap-review-lane.jsonl"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render baihua bootstrap human-review markdown and decisions template.")
    parser.add_argument("--input-path", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--markdown-file-name", default="top50-bootstrap-human-review.zh-TW.md")
    parser.add_argument("--decision-template-file-name", default="top50-bootstrap-human-decisions.template.json")
    parser.add_argument("--summary-file-name", default="top50-bootstrap-human-review-summary.json")
    parser.add_argument("--max-rows", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def quote_preview(row: dict[str, Any], limit: int = 80) -> str:
    supporting = row.get("supportingEvidence")
    if not isinstance(supporting, list) or not supporting:
        return ""
    quote = str((supporting[0] or {}).get("quote") or "").strip()
    if len(quote) <= limit:
        return quote
    return quote[: limit - 1].rstrip() + "…"


def markdown_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Top50 白話關係 Bootstrap 人工審核表")
    lines.append("")
    lines.append("- 本表僅供審核與決策，不可直接視為 canonical truth。")
    lines.append("- 建議先審核 `有衝突旗標`、`分數較低`、`證據較少` 的列。")
    lines.append("- 決策代碼：`pending` / `approved` / `rejected`。")
    lines.append("")
    lines.append("| # | 決策 | trustKey | 關係型別 | from | to | 分數 | 階段 | 衝突 | 證據摘錄 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, row in enumerate(rows, 1):
        conflict_flags = [str(item).strip() for item in (row.get("conflictFlags") or []) if str(item or "").strip()]
        conflict_text = ",".join(conflict_flags) if conflict_flags else "-"
        lines.append(
            "| {idx} | pending | `{trust_key}` | `{rtype}` | `{from_id}` | `{to_id}` | `{score}` | `{stage}` | `{conflict}` | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(str(row.get("trustKey") or "")),
                rtype=markdown_escape(str(row.get("relationshipType") or "")),
                from_id=markdown_escape(str(row.get("fromId") or "")),
                to_id=markdown_escape(str(row.get("toId") or "")),
                score=markdown_escape(str(row.get("score") or "")),
                stage=markdown_escape(str(row.get("bootstrapStage") or "")),
                conflict=markdown_escape(conflict_text),
                quote=markdown_escape(quote_preview(row)),
            )
        )
    lines.append("")
    lines.append("## 回填方式")
    lines.append("")
    lines.append("1. 先編輯 `top50-bootstrap-human-decisions.template.json` 的 `decisions[]`。")
    lines.append("2. 每列至少填 `decision`，可補 `notes`。")
    lines.append("3. 若需覆蓋白名單/黑名單，填 `commands[]`。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_decision_template(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for row in rows:
        decisions.append(
            {
                "trustKey": str(row.get("trustKey") or ""),
                "decision": "pending",
                "reviewer": "human",
                "notes": "",
                "canonicalWrites": False,
            }
        )
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "baihua-bootstrap-human-decisions-template",
        "instructions": [
            "僅在你確認 pair-key 關係正確時標記 approved。",
            "若關係型別或方向不正確，標記 rejected 並補 notes。",
            "有 conflictFlags 的列優先審核。",
            "本模板只做審核決策，不可直接當 canonical 寫入。"
        ],
        "decisionField": "decision",
        "commandField": "action",
        "approvedStatuses": ["approved", "accept", "通過"],
        "rejectedStatuses": ["rejected", "reject", "駁回"],
        "availableCommands": {
            "forceWhitelistActions": ["force-whitelist", "move-to-whitelist"],
            "forceBlacklistActions": ["force-blacklist", "move-to-blacklist"],
            "removeFromIndexActions": ["remove-from-index", "delete"],
        },
        "canonicalWrites": False,
        "commands": [
            {
                "action": "",
                "trustKey": "",
                "reviewer": "human",
                "notes": "可選：覆蓋白/黑名單決策。",
                "canonicalWrites": False,
            }
        ],
        "decisions": decisions,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path).resolve()
    output_root = Path(args.output_root).resolve()
    markdown_path = output_root / args.markdown_file_name
    decision_template_path = output_root / args.decision_template_file_name
    summary_path = output_root / args.summary_file_name

    if not args.overwrite and (markdown_path.exists() or decision_template_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {markdown_path}")

    input_rows = read_jsonl(input_path)
    rows = input_rows[: max(1, int(args.max_rows))]
    markdown_text = render_markdown(rows)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_text, encoding="utf-8")

    decision_template = build_decision_template(rows)
    write_json(decision_template_path, decision_template)

    stage_counter = Counter(str(row.get("bootstrapStage") or "") for row in rows)
    type_counter = Counter(str(row.get("relationshipType") or "") for row in rows)
    conflict_count = sum(1 for row in rows if row.get("conflictFlags"))
    summary = {
        "mode": "baihua-bootstrap-human-review-render",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "reviewLanePath": str(input_path),
            "reviewLaneCount": len(input_rows),
            "maxRows": max(1, int(args.max_rows)),
        },
        "outputs": {
            "markdownPath": str(markdown_path),
            "decisionTemplatePath": str(decision_template_path),
            "summaryPath": str(summary_path),
            "renderedCount": len(rows),
            "conflictFlaggedCount": conflict_count,
            "bootstrapStageCounts": dict(sorted(stage_counter.items())),
            "relationshipTypeCounts": dict(sorted(type_counter.items())),
        },
    }
    write_json(summary_path, summary)

    print(f"[render_baihua_bootstrap_human_review] wrote {markdown_path}")
    print(f"[render_baihua_bootstrap_human_review] wrote {decision_template_path}")
    print(f"[render_baihua_bootstrap_human_review] wrote {summary_path}")
    print(
        "[render_baihua_bootstrap_human_review] "
        f"rows={len(rows)} conflicts={conflict_count} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
