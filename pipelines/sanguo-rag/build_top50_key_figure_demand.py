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
        description="Build a generic key-figure demand sheet for a focus person using policy-defined target figures."
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


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def collect_quote_pool(policy_inputs: dict[str, Any]) -> list[dict[str, str]]:
    pool: list[dict[str, str]] = []
    for key in ("focusBundlePath", "focusPacketPath"):
        path_text = str(policy_inputs.get(key) or "").strip()
        if not path_text:
            continue
        path = resolve_path(path_text)
        if not path.exists():
            continue
        payload = read_json(path)
        if key == "focusBundlePath":
            for row in payload.get("passages") or []:
                pool.append(
                    {
                        "sourceType": "focus-bundle",
                        "sourceFile": repo_relative(path),
                        "locator": compact_text(row.get("locator") or row.get("chapterRef")),
                        "quote": compact_text(row.get("normalizedText")),
                    }
                )
        else:
            for row in payload.get("selectedPassages") or []:
                pool.append(
                    {
                        "sourceType": "focus-packet",
                        "sourceFile": repo_relative(path),
                        "locator": compact_text(row.get("locator") or row.get("chapterRef")),
                        "quote": compact_text(row.get("normalizedText")),
                    }
                )

    for path_text in string_list(policy_inputs.get("reviewedCachePaths")):
        path = resolve_path(path_text)
        if not path.exists():
            continue
        for row in read_jsonl(path):
            for rel in row.get("relationships") or []:
                if not isinstance(rel, dict):
                    continue
                pool.append(
                    {
                        "sourceType": "reviewed-cache",
                        "sourceFile": repo_relative(path),
                        "locator": compact_text(rel.get("sourcePassageRef") or rel.get("locator")),
                        "quote": compact_text(rel.get("evidenceQuoteZhTw") or rel.get("sourceQuoteZhTw") or rel.get("sentenceTextZhTw")),
                    }
                )
            for review in row.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                pool.append(
                    {
                        "sourceType": "reviewed-cache",
                        "sourceFile": repo_relative(path),
                        "locator": compact_text(review.get("sourcePassageRef") or review.get("locator")),
                        "quote": compact_text(review.get("sentenceTextZhTw") or review.get("sentenceText")),
                    }
                )

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in pool:
        if not row["quote"]:
            continue
        key = (row["sourceType"], row["locator"], row["quote"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['targetNameZhTw']} 關鍵人物需求表",
        "",
        "- 這份表只整理上游應補的人物與關係，不直接產生白名單。",
        "- 若本地 quote pool 還沒有命中，表示下一步應補來源或補句窗，不代表關係本身被否定。",
        "",
        f"- 關鍵人物數：`{summary['keyFigureCount']}`",
        f"- 本地已有命中數：`{summary['coveredFigureCount']}`",
        "",
        "| 關鍵人物 | 關係 | lane | 本地命中 | 狀態 | 下一步 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {name} | {rel} | `{lane}` | {count} | {status} | {next_action} |".format(
                name=row["figureNameZhTw"],
                rel=row["relationshipType"],
                lane=row["lane"],
                count=int(row["matchedQuoteCount"]),
                status=row["statusZhTw"],
                next_action=row["suggestedNextActionZhTw"].replace("|", "\\|"),
            )
        )
    lines.extend(["", "## 理由", ""])
    for row in rows:
        lines.append(f"- {row['figureNameZhTw']}：{row['reasonZhTw']}")
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
    quote_pool = collect_quote_pool(policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {})

    rows: list[dict[str, Any]] = []
    for item in policy.get("keyFigures") or []:
        if not isinstance(item, dict):
            continue
        aliases = [str(item.get("figureNameZhTw") or ""), *string_list(item.get("figureAliasesZhTw"))]
        matched = [row for row in quote_pool if any(alias and alias in row["quote"] for alias in aliases)]
        status = "本地已命中" if matched else "待補來源或句窗"
        next_action = (
            "先從現有 focus bundle / reviewed-cache 抽更好的句窗。"
            if matched
            else "優先補可引用來源，或補到能同時放進人物與關係語意的句窗。"
        )
        rows.append(
            {
                "demandId": f"{target_general_id}-key-figure:{item.get('figureId')}",
                "targetGeneralId": target_general_id,
                "targetNameZhTw": target_name,
                "figureId": str(item.get("figureId") or ""),
                "figureNameZhTw": str(item.get("figureNameZhTw") or ""),
                "relationshipType": str(item.get("relationshipType") or ""),
                "priority": int(item.get("priority") or 99),
                "lane": str(item.get("lane") or ""),
                "isTop50": bool(item.get("isTop50")),
                "matchedQuoteCount": len(matched),
                "matchedSourceTypeCounts": dict(sorted(Counter(row["sourceType"] for row in matched).items())),
                "matchedQuotePreviewZhTw": [row["quote"] for row in matched[:3]],
                "statusZhTw": status,
                "suggestedNextActionZhTw": next_action,
                "reasonZhTw": str(item.get("reasonZhTw") or ""),
                "canonicalWrites": False,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), row["figureId"]))
    version_metadata = build_version_metadata(
        schema_version="top50-key-figure-demand.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-key-figure-demand",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "targetGeneralId": target_general_id,
        "targetNameZhTw": target_name,
        "policyPath": repo_relative(policy_path),
        "quotePoolCount": len(quote_pool),
        "keyFigureCount": len(rows),
        "coveredFigureCount": sum(1 for row in rows if int(row["matchedQuoteCount"]) > 0),
        "laneCounts": dict(sorted(Counter(row["lane"] for row in rows).items())),
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
        "[build_top50_key_figure_demand] "
        f"target={target_general_id} keyFigures={len(rows)} covered={summary['coveredFigureCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
