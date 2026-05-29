from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import read_json
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-cai-yan-key-figure-demand.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
DEFAULT_TAG = "top50-cai-yan-focused-source-scan"

CLAUSE_BREAKS = "。！？；：\n"
PARENTSHIP_CUES = ("其女", "之女", "父女", "其父", "之父", "女蔡琰")
SPOUSE_CUES = ("配與", "為妻", "之妻", "嫁與", "娶", "納", "婚配")
MAINLINE_CUES = ("贖", "贖回", "迎還", "還漢", "歸漢", "迎回", "召還", "安置", "收留", "命")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a focused source scan and review-ready packet for Cai Yan key-figure lines."
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


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = compact_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def find_all_positions(text: str, token: str) -> list[int]:
    if not token:
        return []
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return positions
        positions.append(index)
        start = index + len(token)


def best_pair_span(text: str, left_aliases: list[str], right_aliases: list[str]) -> tuple[int | None, str | None, str | None]:
    best_length: int | None = None
    best_left: str | None = None
    best_right: str | None = None
    for left in left_aliases:
        left_positions = find_all_positions(text, left)
        if not left_positions:
            continue
        for right in right_aliases:
            right_positions = find_all_positions(text, right)
            if not right_positions:
                continue
            for left_pos in left_positions:
                for right_pos in right_positions:
                    start = min(left_pos, right_pos)
                    end = max(left_pos + len(left), right_pos + len(right))
                    span_length = end - start
                    if best_length is None or span_length < best_length:
                        best_length = span_length
                        best_left = left
                        best_right = right
    return best_length, best_left, best_right


def cue_near_pair(text: str, cues: tuple[str, ...], left_aliases: list[str], right_aliases: list[str], max_window: int) -> tuple[bool, str | None]:
    for cue in cues:
        for match in re.finditer(re.escape(cue), text):
            cue_start = match.start()
            cue_end = match.end()
            left_near = any(any(abs(pos - cue_start) <= max_window or abs(pos + len(alias) - cue_end) <= max_window for pos in find_all_positions(text, alias)) for alias in left_aliases)
            right_near = any(any(abs(pos - cue_start) <= max_window or abs(pos + len(alias) - cue_end) <= max_window for pos in find_all_positions(text, alias)) for alias in right_aliases)
            if left_near and right_near:
                return True, cue
    return False, None


def contains_any_alias(text: str, aliases: list[str]) -> bool:
    return any(alias and alias in text for alias in aliases)


def quote_hygiene_issue(text: str) -> str | None:
    ascii_digit_count = sum(1 for char in text if char.isdigit())
    if "[" in text or "]" in text:
        return "句子帶有清單或殘留標記，較像二手整理片段，不直接送審。"
    if ascii_digit_count >= 2:
        return "句子含較重的條目化數字痕跡，先降回 trace，避免把二手整理片段誤當原句。"
    return None


def looks_like_mainline_patron_sentence(text: str, figure_aliases: list[str], target_aliases: list[str]) -> tuple[bool, str | None]:
    if not contains_any_alias(text, figure_aliases) or not contains_any_alias(text, target_aliases):
        return False, None
    # 這條線只在同句真的出現「迎還 / 歸漢 / 贖 / 命」等主線行動時才放行，
    # 避免把「曹操與蔡邕相善」或泛提人物背景誤升成 ruler_subject。
    return cue_near_pair(text, MAINLINE_CUES, figure_aliases, target_aliases, max_window=14)


def score_quote(relationship_type: str, quote: str, figure_aliases: list[str], target_aliases: list[str]) -> tuple[int, str, str]:
    pair_span, _, _ = best_pair_span(quote, figure_aliases, target_aliases)
    if pair_span is None:
        return 0, "trace-only", "句內沒有同時命中蔡琰與關鍵對象，仍只是弱 trace。"

    if relationship_type == "parent_child":
        cue_ok, cue = cue_near_pair(quote, PARENTSHIP_CUES, figure_aliases, target_aliases, max_window=10)
        if cue_ok:
            score = 95 if cue in {"其女", "之女", "女蔡琰"} else 90
            hygiene_issue = quote_hygiene_issue(quote)
            if hygiene_issue:
                return 0, "trace-only", hygiene_issue
            return score, "review-ready", "句內有父女直接 cue，且 cue 與蔡琰/關鍵對象綁定緊密，可進 parent_child 審查。"
        return 0, "needs-better-parent-child-quote", "有同句共現，但缺少直接父女 cue，暫時不能送審。"

    if relationship_type == "spouse":
        cue_ok, cue = cue_near_pair(quote, SPOUSE_CUES, figure_aliases, target_aliases, max_window=12)
        if cue_ok:
            score = 96 if cue in {"配與", "為妻", "之妻"} else 90
            hygiene_issue = quote_hygiene_issue(quote)
            if hygiene_issue:
                return 0, "trace-only", hygiene_issue
            return score, "review-ready", "句內有婚配/妻室直接 cue，且關係落在蔡琰與該對象兩人身上，可進 spouse 審查。"
        return 0, "needs-better-spouse-quote", "雖然人物同句，但婚姻 cue 沒有綁到這一對人物，暫時不能送審。"

    if relationship_type == "ruler_subject":
        cue_ok, cue = looks_like_mainline_patron_sentence(quote, figure_aliases, target_aliases)
        if cue_ok:
            score = 88 if cue in {"贖", "贖回", "迎還", "還漢", "歸漢"} else 84
            hygiene_issue = quote_hygiene_issue(quote)
            if hygiene_issue:
                return 0, "trace-only", hygiene_issue
            return score, "review-ready", "句內出現歸漢/迎還/安置等主線行動，且曹操與蔡琰都緊貼 cue，可進主線審查。"
        return 0, "needs-new-mainline-quote", "目前只有背景或旁帶敘述，還沒有蔡琰與曹操直接落關係的主線句。"

    return 0, "trace-only", "關係型別尚未定義 focused scan 的送審規則。"


def chapter_ref_from_quote(quote: str) -> str:
    if "配與董祀為妻" in quote or "左賢王懼操之勢" in quote:
        return "chapter-071"
    if "其女蔡琰" in quote:
        return "chapter-071"
    return ""


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]], packet: dict[str, Any]) -> str:
    lines = [
        "# 蔡琰 Focused Source Scan",
        "",
        "- 目標：把蔡琰關鍵四人線拆成真正可審核句子，避免把弱 trace 或錯綁 pair 直接升成審核包。",
        "- 規則：只有句內同時命中蔡琰與關鍵對象，且 cue 緊貼這一對人物，才標成 `review-ready`。",
        "- 說明：`second-layer baseline` 可補非 Top50 關係，但 `曹操` 這條仍要靠更乾淨主線句才能往 Top50 主線推。",
        "",
        f"- 掃描列數：`{int(summary.get('rowCount') or 0)}`",
        f"- 可審核列數：`{int(summary.get('reviewReadyCount') or 0)}`",
        f"- 可審核人物數：`{int(summary.get('packetEntryCount') or 0)}`",
        "",
        "| 關鍵對象 | 關係型別 | 判定 | 分數 | 來源 | 句子 | 說明 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {figure} | {rel} | {status} | {score} | `{source}` | {quote} | {reason} |".format(
                figure=row.get("figureNameZhTw") or row.get("figureId") or "",
                rel=row.get("relationshipType") or "",
                status=row.get("reviewReadiness") or "",
                score=row.get("score") or 0,
                source=f"{row.get('sourceType')}@{row.get('locator') or '?'}",
                quote=str(row.get("quoteZhTw") or "").replace("|", "\\|"),
                reason=str(row.get("reasonZhTw") or "").replace("|", "\\|"),
            )
        )
    lines.append("")
    lines.append("## Review-ready Packet")
    lines.append("")
    lines.append(f"- packet entries：`{len(packet.get('entries') or [])}`")
    for entry in packet.get("entries") or []:
        lines.append(
            "- {figure}：`{count}` 句".format(
                figure=entry.get("figureNameZhTw") or entry.get("figureId") or "",
                count=len(entry.get("selectedPassages") or []),
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
    packet_path = output_root / f"{args.tag}.review-packet.json"
    if not args.overwrite and any(path.exists() for path in (jsonl_path, summary_path, markdown_path, packet_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {jsonl_path}")

    policy = read_json(policy_path)
    demand_rows = read_jsonl(output_root / "top50-cai-yan-key-figure-demand.jsonl")
    target_general_id = str(policy.get("targetGeneralId") or "cai-yan")
    target_name = str(policy.get("targetNameZhTw") or "蔡琰")
    target_aliases = dedupe_preserve_order(string_list(policy.get("targetAliasesZhTw")) or [target_name])

    output_rows: list[dict[str, Any]] = []
    packet_entries: list[dict[str, Any]] = []

    demand_by_figure = {str(row.get("figureId") or ""): row for row in demand_rows}
    for figure in policy.get("keyFigures") or []:
        if not isinstance(figure, dict):
            continue
        figure_id = str(figure.get("figureId") or "").strip()
        figure_name = str(figure.get("figureNameZhTw") or figure_id)
        figure_aliases = dedupe_preserve_order([figure_name, *string_list(figure.get("figureAliasesZhTw"))])
        demand_row = demand_by_figure.get(figure_id, {})
        matched_source_counts = demand_row.get("matchedSourceTypeCounts") or {}
        quote_previews = dedupe_preserve_order(string_list(demand_row.get("matchedQuotePreviewsZhTw")))

        ready_candidates: list[dict[str, Any]] = []
        for index, quote in enumerate(quote_previews, 1):
            score, readiness, reason = score_quote(
                str(figure.get("relationshipType") or ""),
                quote,
                figure_aliases,
                target_aliases,
            )
            row = {
                "scanId": f"cai-yan-focused-scan:{figure_id}:{index:02d}",
                "figureId": figure_id,
                "figureNameZhTw": figure_name,
                "relationshipType": str(figure.get("relationshipType") or ""),
                "lane": str(figure.get("lane") or ""),
                "isTop50": bool(figure.get("isTop50")),
                "sourceType": ",".join(sorted(matched_source_counts.keys())),
                "locator": "key-demand-preview",
                "quoteZhTw": quote,
                "score": score,
                "reviewReadiness": readiness,
                "reasonZhTw": reason,
                "canonicalWrites": False,
            }
            output_rows.append(row)
            if readiness == "review-ready":
                ready_candidates.append(row)

        ready_candidates.sort(key=lambda row: (-int(row.get("score") or 0), len(str(row.get("quoteZhTw") or ""))))
        if ready_candidates:
            best = ready_candidates[0]
            packet_entries.append(
                {
                    "focusGeneralId": target_general_id,
                    "focusNameZhTw": target_name,
                    "figureId": figure_id,
                    "figureNameZhTw": figure_name,
                    "topCounterpartIds": [figure_id] if bool(figure.get("isTop50")) else [],
                    "selectedPassages": [
                        {
                            "locator": f"cai-yan-focused:{figure_id}:01",
                            "chapterRef": chapter_ref_from_quote(str(best.get("quoteZhTw") or "")),
                            "normalizedText": str(best.get("quoteZhTw") or ""),
                            "candidateRelationshipTypes": [str(figure.get("relationshipType") or "")],
                            "personIds": [target_general_id, figure_id],
                            "counterpartHits": [figure_id],
                            "canonicalWrites": False,
                        }
                    ],
                    "canonicalWrites": False,
                }
            )

    readiness_order = {
        "review-ready": 0,
        "needs-better-parent-child-quote": 1,
        "needs-better-spouse-quote": 1,
        "needs-new-mainline-quote": 2,
        "trace-only": 3,
    }
    output_rows.sort(
        key=lambda row: (
            readiness_order.get(str(row.get("reviewReadiness") or ""), 9),
            -int(row.get("score") or 0),
            str(row.get("figureId") or ""),
        )
    )

    packet = {
        "schemaVersion": "cai-yan-focused-source-scan.v2",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "entries": packet_entries,
    }
    version_metadata = build_version_metadata(
        schema_version="top50-cai-yan-focused-source-scan.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-cai-yan-focused-source-scan",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "rowCount": len(output_rows),
        "reviewReadyCount": sum(1 for row in output_rows if str(row.get("reviewReadiness")) == "review-ready"),
        "reviewReadinessCounts": dict(sorted(Counter(str(row.get("reviewReadiness") or "") for row in output_rows).items())),
        "packetEntryCount": len(packet_entries),
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
            "packetPath": repo_relative(packet_path),
        },
    }

    write_jsonl(jsonl_path, output_rows)
    write_json(packet_path, packet)
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, output_rows, packet), encoding="utf-8")
    print(
        "[build_top50_cai_yan_focused_source_scan] "
        f"rows={len(output_rows)} reviewReady={summary['reviewReadyCount']} packetEntries={len(packet_entries)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
