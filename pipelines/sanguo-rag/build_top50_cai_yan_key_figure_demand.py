from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_alias_map, build_name_map, read_json
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-cai-yan-key-figure-demand.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
DEFAULT_TAG = "top50-cai-yan-key-figure-demand"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a reviewable key-figure demand sheet for Cai Yan second-layer relationship search."
    )
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
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


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def stable_inputs(relationship_policy: dict[str, Any]) -> tuple[Path, Path, Path]:
    inputs = relationship_policy.get("inputs") if isinstance(relationship_policy.get("inputs"), dict) else {}
    stable_bootstrap = resolve_path(
        str(
            inputs.get("stableBootstrapPath")
            or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
        )
    )
    formal_mention_map = resolve_path(
        str(
            inputs.get("formalMentionMapPath")
            or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json"
        )
    )
    alias_records = resolve_path(
        str(
            inputs.get("generalAliasRecordsPath")
            or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json"
        )
    )
    return stable_bootstrap, formal_mention_map, alias_records


def collect_quotes(policy_inputs: dict[str, Any], target_general_id: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []

    proposal_path = resolve_path(str(policy_inputs.get("secondLayerProposalPath") or ""))
    if proposal_path.exists():
        for row in read_jsonl(proposal_path):
            if str(row.get("targetGeneralId") or "").strip() != target_general_id:
                continue
            output.append(
                {
                    "sourceType": "second-layer-proposal",
                    "sourceFile": repo_relative(proposal_path),
                    "locator": compact_text(row.get("locator")),
                    "quote": compact_text(row.get("sourceQuoteZhTw")),
                    "sourceLayer": compact_text(row.get("sourceLayer")),
                }
            )

    event_packet_path = resolve_path(str(policy_inputs.get("sourceEventPacketPath") or ""))
    if event_packet_path.exists():
        for row in read_jsonl(event_packet_path):
            if target_general_id not in string_list(row.get("generalIds")):
                continue
            for example in row.get("examples") or []:
                quote = compact_text(example)
                if quote:
                    output.append(
                        {
                            "sourceType": "source-event-packet",
                            "sourceFile": repo_relative(event_packet_path),
                            "locator": compact_text(row.get("sourceRef") or row.get("packetId")),
                            "quote": quote,
                            "sourceLayer": compact_text(row.get("reviewStatus")),
                        }
                    )

    event_seed_path = resolve_path(str(policy_inputs.get("eventQuestionSeedPath") or ""))
    if event_seed_path.exists():
        for row in read_jsonl(event_seed_path):
            if str(row.get("generalId") or "").strip() != target_general_id:
                continue
            for example in row.get("examples") or []:
                if not isinstance(example, dict):
                    continue
                quote = compact_text(example.get("text"))
                if quote:
                    output.append(
                        {
                            "sourceType": "event-question-seed",
                            "sourceFile": repo_relative(event_seed_path),
                            "locator": compact_text(example.get("sourceRef") or row.get("seedId")),
                            "quote": quote,
                            "sourceLayer": compact_text(row.get("reviewStatus")),
                        }
                    )

    reviewed_cache_path = resolve_path(str(policy_inputs.get("reviewedCachePath") or ""))
    if reviewed_cache_path.exists():
        for row in read_jsonl(reviewed_cache_path):
            if str(row.get("focusGeneralId") or "").strip() != target_general_id:
                continue
            for review in row.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                quote = compact_text(review.get("sentenceTextZhTw") or review.get("sentenceText"))
                if quote:
                    output.append(
                        {
                            "sourceType": "reviewed-cache",
                            "sourceFile": repo_relative(reviewed_cache_path),
                            "locator": compact_text(review.get("sourcePassageRef") or review.get("locator")),
                            "quote": quote,
                            "sourceLayer": compact_text(review.get("verdict")),
                        }
                    )

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in output:
        key = (row["sourceType"], row["locator"], row["quote"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 蔡琰關鍵四人來源需求表",
        "",
        "- 這份表把蔡琰最該沿著哪四條線補資料，整理成可執行的搜尋/補 source 需求。",
        "- 目的不是直接生關係，而是先把 `該找誰`、`該走哪個 lane`、`目前已經有沒有句子` 講清楚。",
        "",
        f"- 關鍵人物數：`{int(summary.get('keyFigureCount') or 0)}`",
        f"- 已有句子覆蓋的人數：`{int(summary.get('coveredFigureCount') or 0)}`",
        "",
        "| 關鍵人物 | 目標關係 | lane | 目前句子數 | 狀態 | 下一步 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {name} | {rel} | `{lane}` | {count} | {status} | {next_action} |".format(
                name=row.get("figureNameZhTw") or row.get("figureId") or "",
                rel=row.get("relationshipType") or "",
                lane=row.get("lane") or "",
                count=int(row.get("matchedQuoteCount") or 0),
                status=row.get("statusZhTw") or "",
                next_action=str(row.get("suggestedNextActionZhTw") or "").replace("|", "\\|"),
            )
        )
    lines.append("")
    lines.append("## 判讀")
    lines.append("")
    for row in rows:
        lines.append(
            "- {name}：{reason}".format(
                name=row.get("figureNameZhTw") or row.get("figureId") or "",
                reason=row.get("reasonZhTw") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


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
    policy_inputs = policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}
    relationship_policy_path = resolve_path(str(policy_inputs.get("relationshipPolicyPath") or ""))
    relationship_policy = read_json(relationship_policy_path)
    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)

    target_general_id = str(policy.get("targetGeneralId") or "").strip()
    quotes = collect_quotes(policy_inputs, target_general_id)

    rows: list[dict[str, Any]] = []
    for figure in policy.get("keyFigures") or []:
        if not isinstance(figure, dict):
            continue
        figure_id = str(figure.get("figureId") or "").strip()
        figure_name = str(figure.get("figureNameZhTw") or figure_id).strip()
        aliases = [figure_name, *string_list(figure.get("figureAliasesZhTw"))]
        matched_quotes = [
            row
            for row in quotes
            if any(alias and alias in row.get("quote", "") for alias in aliases)
        ]
        matched_source_types = Counter(row.get("sourceType", "") for row in matched_quotes)
        status = "已有可用句子" if matched_quotes else "尚缺直接句子"
        if str(figure.get("isTop50")).lower() == "true" or bool(figure.get("isTop50")):
            next_action = "優先補能直接落在蔡琰與此人身上的同句硬關係，補到後可回 Top50 主線。"
        else:
            next_action = "先把這條第二層硬關係句補齊，作為蔡琰身世線的穩定 anchor。"
        rows.append(
            {
                "demandId": f"cai-yan-key-figure:{figure_id}",
                "targetGeneralId": target_general_id,
                "targetNameZhTw": str(policy.get("targetNameZhTw") or target_general_id),
                "figureId": figure_id,
                "figureNameZhTw": figure_name,
                "figureAliasesZhTw": aliases,
                "relationshipType": str(figure.get("relationshipType") or ""),
                "priority": int(figure.get("priority") or 99),
                "isTop50": bool(figure.get("isTop50")),
                "lane": str(figure.get("lane") or ""),
                "matchedQuoteCount": len(matched_quotes),
                "matchedSourceTypeCounts": dict(sorted(matched_source_types.items())),
                "matchedQuotePreviewsZhTw": [row.get("quote", "") for row in matched_quotes[:3]],
                "statusZhTw": status,
                "suggestedNextActionZhTw": next_action,
                "reasonZhTw": str(figure.get("reasonZhTw") or ""),
                "canonicalWrites": False,
            }
        )

    rows.sort(key=lambda row: (int(row.get("priority") or 99), str(row.get("figureId") or "")))
    write_jsonl(jsonl_path, rows)
    version_metadata = build_version_metadata(
        schema_version="top50-cai-yan-key-figure-demand.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-cai-yan-key-figure-demand",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "quotePoolCount": len(quotes),
        "keyFigureCount": len(rows),
        "coveredFigureCount": sum(1 for row in rows if int(row.get("matchedQuoteCount") or 0) > 0),
        "laneCounts": dict(sorted(Counter(str(row.get("lane") or "") for row in rows).items())),
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
    }
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, rows), encoding="utf-8")
    print(
        "[build_top50_cai_yan_key_figure_demand] "
        f"keyFigures={len(rows)} covered={summary['coveredFigureCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
