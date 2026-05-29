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
        description="Render a formal source candidate sheet from a policy-defined source candidate list."
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
        f"# {summary['targetNameZhTw']} 正式來源候選表",
        "",
        "- 這份表是補來源的工作清單，不直接產生關係白名單。",
        "- `citationReady=是` 代表來源型態已足夠當正式引用入口；不代表句子已抽出。",
        "",
        "| 優先序 | 來源 | 類型 | 可支撐目標 | 可直接引用 | 下一步 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {priority} | [{title}]({url}) | {source_type} | {targets} | {citation_ready} | {next_action} |".format(
                priority=int(row["priority"]),
                title=row["titleZhTw"],
                url=row["url"],
                source_type=row["sourceType"],
                targets="、".join(row["supportsTargetsZhTw"]) or "無",
                citation_ready="是" if row["citationReady"] else "否",
                next_action=row["nextActionZhTw"].replace("|", "\\|"),
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
    target_general_id = str(policy.get("targetGeneralId") or "").strip()
    target_name = str(policy.get("targetNameZhTw") or target_general_id)

    rows: list[dict[str, Any]] = []
    for item in policy.get("candidateSources") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "candidateId": f"{target_general_id}-formal-source:{item.get('sourceId')}",
                "targetGeneralId": target_general_id,
                "targetNameZhTw": target_name,
                "priority": int(item.get("priority") or 99),
                "sourceId": str(item.get("sourceId") or ""),
                "sourceType": str(item.get("sourceType") or ""),
                "titleZhTw": str(item.get("titleZhTw") or ""),
                "url": str(item.get("url") or ""),
                "host": str(item.get("host") or ""),
                "supportsTargetsZhTw": string_list(item.get("supportsTargetsZhTw")),
                "reasonZhTw": str(item.get("reasonZhTw") or ""),
                "nextActionZhTw": str(item.get("nextActionZhTw") or ""),
                "citationReady": bool(item.get("citationReady")),
                "canonicalWrites": False,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), row["sourceId"]))
    version_metadata = build_version_metadata(
        schema_version="top50-formal-source-candidate-sheet.v1",
        artifact_paths=[policy_path],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-formal-source-candidate-sheet",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "targetGeneralId": target_general_id,
        "targetNameZhTw": target_name,
        "policyPath": repo_relative(policy_path),
        "candidateCount": len(rows),
        "sourceTypeCounts": dict(sorted(Counter(row["sourceType"] for row in rows).items())),
        "citationReadyCount": sum(1 for row in rows if row["citationReady"]),
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
        "[build_top50_formal_source_candidate_sheet] "
        f"target={target_general_id} candidates={len(rows)} citationReady={summary['citationReadyCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
