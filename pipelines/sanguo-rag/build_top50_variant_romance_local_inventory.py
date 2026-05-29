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
DEFAULT_TARGET = "ma-yun-lu"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory local romance-variant traces for a target without mixing them into baihua primary."
    )
    parser.add_argument("--target-general-id", default=DEFAULT_TARGET)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
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


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['targetNameZhTw']} 本地 Romance Variant 來源盤點",
        "",
        "- 這份盤點只整理 repo 內已存在的本地痕跡，不把任何資料直接混回白話主來源。",
        "- 目的不是直接產生白名單，而是區分哪些是身份痕跡、哪些是對手線索、哪些仍缺正式可引用來源。",
        "",
        f"- 盤點筆數：`{summary['inventoryCount']}`",
        f"- quote-ready 筆數：`{summary['quoteReadyCount']}`",
        "",
        "| 類型 | 來源 | 對手 | 可直接引用 | 下一步 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {trace_kind} | {source_file} | {counterparts} | {quote_ready} | {next_action} |".format(
                trace_kind=row["traceKindZhTw"],
                source_file=row["sourceFile"].replace("|", "\\|"),
                counterparts="、".join(row["counterpartDisplayNamesZhTw"]) or "無",
                quote_ready="是" if row["quoteReady"] else "否",
                next_action=row["suggestedNextActionZhTw"].replace("|", "\\|"),
            )
        )

    lines.extend(["", "## 結論", ""])
    for line in summary["conclusionsZhTw"]:
        lines.append(f"- {line}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = resolve_path(args.output_root)
    target_general_id = str(args.target_general_id or "").strip()
    if not target_general_id:
        raise ValueError("--target-general-id is required")

    tag = f"top50-variant-romance-local-inventory.{target_general_id}"
    jsonl_path = output_root / f"{tag}.jsonl"
    summary_path = output_root / f"{tag}.summary.json"
    markdown_path = output_root / f"{tag}.zh-TW.md"
    if not args.overwrite and any(path.exists() for path in (jsonl_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {jsonl_path}")

    proposal_rows = [
        row
        for row in read_jsonl(output_root / "top50-variant-romance-anchor-proposals.jsonl")
        if str(row.get("targetGeneralId") or "") == target_general_id
    ]
    target_name = str(proposal_rows[0].get("targetNameZhTw") if proposal_rows else target_general_id)

    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(proposal_rows, 1):
        trace_kind = str(row.get("traceKind") or "")
        trace_kind_zh = {
            "catalog-female-profile-override": "女性 profile override",
            "persona-note": "人物設定備註",
            "refactor-plain-relationship-proposal": "舊版 plain association 旁證",
            "runtime-gap": "runtime 缺口",
        }.get(trace_kind, trace_kind or "未分類")
        proposal_stage = str(row.get("proposalStage") or "")
        quote_ready = proposal_stage == "review-ready"
        next_action = "保留作為 variant 線索，等待正式 romance-variant 書籍或來源句。"
        if trace_kind == "runtime-gap":
            next_action = "先補一個能證明人物存在與出場定位的正式引用來源。"
        elif string_list(row.get("counterpartNamesZhTw")):
            next_action = "優先補能把人物與對手同時綁進引用句的正式來源。"
        rows.append(
            {
                "inventoryId": f"variant-local-inventory:{target_general_id}:{idx:04d}",
                "targetGeneralId": target_general_id,
                "targetNameZhTw": target_name,
                "traceKind": trace_kind,
                "traceKindZhTw": trace_kind_zh,
                "sourceFile": str(row.get("sourceFile") or ""),
                "sourceLocator": str(row.get("sourceLocator") or ""),
                "sourceQuoteZhTw": compact_text(row.get("sourceQuoteZhTw")),
                "counterpartDisplayNamesZhTw": string_list(row.get("counterpartNamesZhTw")),
                "proposalStage": proposal_stage,
                "quoteReady": quote_ready,
                "suggestedNextActionZhTw": next_action,
                "canonicalWrites": False,
            }
        )

    bundle = read_json(output_root / "focus-bundles" / f"{target_general_id}.bundle.json")
    rows.append(
        {
            "inventoryId": f"variant-local-inventory:{target_general_id}:bundle-gap",
            "targetGeneralId": target_general_id,
            "targetNameZhTw": target_name,
            "traceKind": "bundle-gap",
            "traceKindZhTw": "白話主線 bundle 缺口",
            "sourceFile": repo_relative(output_root / "focus-bundles" / f"{target_general_id}.bundle.json"),
            "sourceLocator": "passageCount",
            "sourceQuoteZhTw": f"目前 baihua primary passageCount={int(bundle.get('passageCount') or 0)}",
            "counterpartDisplayNamesZhTw": [],
            "proposalStage": "needs-new-source",
            "quoteReady": False,
            "suggestedNextActionZhTw": "不要再重跑白話主線；改補 romance-variant 正式來源。",
            "canonicalWrites": False,
        }
    )

    rows.sort(key=lambda item: (0 if not item["quoteReady"] else 1, item["traceKindZhTw"], item["sourceFile"]))
    version_metadata = build_version_metadata(
        schema_version="top50-variant-romance-local-inventory.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-variant-romance-local-inventory",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "targetGeneralId": target_general_id,
        "targetNameZhTw": target_name,
        "inventoryCount": len(rows),
        "quoteReadyCount": sum(1 for row in rows if row["quoteReady"]),
        "traceKindCounts": dict(sorted(Counter(row["traceKind"] for row in rows).items())),
        "conclusionsZhTw": [
            "目前 repo 內已經能證明馬雲騄作為 variant 人物的存在痕跡，但還沒有任何 quote-ready 正式來源。",
            "現有最有價值的本地線索，是她與趙雲、馬超、馬岱的對手焦點，而不是白話主線段落。",
            "下一步不該再洗 baihua primary，而應補 romance-variant 正式來源，先解決『人物存在可引用』，再解決『對手綁定可引用』。",
        ],
        "inputs": {
            "proposalPath": repo_relative(output_root / "top50-variant-romance-anchor-proposals.jsonl"),
            "focusBundlePath": repo_relative(output_root / "focus-bundles" / f"{target_general_id}.bundle.json"),
        },
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
        "[build_top50_variant_romance_local_inventory] "
        f"target={target_general_id} inventoryCount={len(rows)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
