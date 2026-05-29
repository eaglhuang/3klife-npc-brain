from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_name_map, read_json
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan local artifacts and propose second-layer anchor sources for Top50 members missing baihua passages."
    )
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument(
        "--general-ids",
        nargs="*",
        default=["cai-yan", "zhang-chun-hua", "wang-yi", "ma-yun-lu"],
    )
    parser.add_argument(
        "--source-path",
        action="append",
        default=[],
        help="Repeatable local JSON/JSONL source path. Defaults to curated local artifact sources.",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", default="top50-second-layer-anchor-source-proposals")
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def stable_bootstrap_name_map(policy_path: Path) -> dict[str, str]:
    policy = read_json(policy_path)
    inputs = object_map(policy.get("inputs"))
    stable_bootstrap_path = resolve_path(
        str(inputs.get("stableBootstrapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap.json")
    )
    formal_mention_map_path = resolve_path(
        str(inputs.get("formalMentionMapPath") or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
    )
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    return build_name_map(stable_bootstrap, formal_mention_map)


def default_source_paths() -> list[Path]:
    return [
        resolve_path("local/codex-smoke/knowledge-growth/anchor-first-multiround-r4-thick/anchor-first-multiround-r4-thick-r8/source-event-packets/source-event-packets.jsonl"),
        resolve_path("local/tmp/claim-graph-pair-gate-check/relationship-claims.jsonl"),
        resolve_path("artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/loc-blitz-140-r1-a1-merged-staged-relationship-evidence.jsonl"),
    ]


def quote_candidates(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("sourceQuote", "evidenceText", "quote", "seedText", "manualQuoteText"):
        raw = row.get(key)
        text = compact_text(html.unescape(str(raw or "")))
        if text:
            values.append(text)
    for item in row.get("examples") or []:
        text = compact_text(html.unescape(str(item or "")))
        if text:
            values.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def row_matches_general(row: dict[str, Any], general_id: str, display_name: str) -> bool:
    if general_id in string_list(row.get("generalIds")):
        return True
    if general_id == str(row.get("generalId") or "").strip():
        return True
    if general_id in {str(row.get("fromId") or "").strip(), str(row.get("toId") or "").strip()}:
        return True
    blob = json.dumps(row, ensure_ascii=False)
    return general_id in blob or display_name in blob


def source_locator(row: dict[str, Any]) -> str:
    for key in ("locator", "sourceRef", "packetId", "sourceEvidenceId", "edgeId", "seedId"):
        value = compact_text(row.get(key))
        if value:
            return value
    return ""


def suitability_for(row: dict[str, Any], quote: str) -> tuple[str, str]:
    lower_quote = quote.lower()
    if not quote:
        return "unusable", "缺少可引用句子。"
    if lower_quote.startswith("manual_quote target:"):
        return "weak", "只有 target-only seed，尚未形成可直接引用 passage。"
    if "source-grounded" in compact_text(row.get("reviewStatus")).lower():
        return "ready", "本地 artifact 已有 quote 與來源定位，可作 second-layer anchor 候選。"
    if "妻" in quote or "夫人" in quote or "其女" in quote or "配與" in quote:
        return "ready", "句子含有人物關係語意，可作 second-layer anchor 候選。"
    return "weak", "有本地來源命中，但仍需後續 lane 再做關係抽取或人工確認。"


def quote_priority(quote: str, target_name: str, row: dict[str, Any]) -> tuple[int, int, int, int]:
    relationship_terms = ["妻", "夫人", "其女", "之妻", "配與", "還漢", "贖之", "父女"]
    target_hit = 1 if target_name and target_name in quote else 0
    relationship_hit = sum(1 for term in relationship_terms if term in quote)
    review_status = compact_text(row.get("reviewStatus")).lower()
    source_layer = compact_text(row.get("sourceLayer") or row.get("sourceLayerRaw")).lower()
    grounded_bonus = 1 if "source-grounded" in review_status else 0
    internal_bonus = 1 if "event-packet" in review_status or "history" in source_layer else 0
    return (target_hit, relationship_hit, grounded_bonus, internal_bonus)


def scan_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def render_markdown(rows: list[dict[str, Any]], missing_general_ids: list[str]) -> str:
    lines = [
        "# Top50 第二層 Anchor 來源提案",
        "",
        "- 目的：補足白話《三國演義》沒有 passage 的 Top50 人物，讓後續 lane 有可引用的本地第二層來源。",
        "- 這份清單只整理本地 artifacts 已存在的 quote / locator，不直接改 canonical 資料。",
        "",
        "| 目標人物 | 適用性 | 來源檔案 | 定位 | 來源層 | 建議引用句 | 說明 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {target} | {suitability} | `{source_file}` | `{locator}` | {layer} | {quote} | {reason} |".format(
                target=row["targetNameZhTw"],
                suitability=row["anchorSuitability"],
                source_file=row["sourceFile"],
                locator=row["locator"],
                layer=row["sourceLayer"],
                quote=str(row["sourceQuoteZhTw"]).replace("|", "\\|"),
                reason=str(row["reasonZhTw"]).replace("|", "\\|"),
            )
        )
    lines.append("")
    if missing_general_ids:
        lines.append("## 仍無可用本地第二層來源")
        lines.append("")
        for general_id in missing_general_ids:
            lines.append(f"- `{general_id}`：目前在既有本地 artifacts 仍找不到可引用 quote。")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy)
    output_root = resolve_path(args.output_root)
    jsonl_path = output_root / f"{args.tag}.jsonl"
    md_path = output_root / f"{args.tag}.zh-TW.md"
    summary_path = output_root / f"{args.tag}.summary.json"
    if not args.overwrite and any(path.exists() for path in (jsonl_path, md_path, summary_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {jsonl_path}")

    name_map = stable_bootstrap_name_map(policy_path)
    target_ids = [general_id for general_id in string_list(args.general_ids)]
    source_paths = [resolve_path(path) for path in args.source_path] if args.source_path else default_source_paths()
    proposals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for source_path in source_paths:
        for row in scan_rows(source_path):
            source_quotes = quote_candidates(row)
            for general_id in target_ids:
                target_name = str(name_map.get(general_id) or general_id)
                if not row_matches_general(row, general_id, target_name):
                    continue
                ranked_quotes = sorted(
                    source_quotes,
                    key=lambda quote: quote_priority(quote, target_name, row),
                    reverse=True,
                )
                for quote in ranked_quotes[:3]:
                    suitability, reason = suitability_for(row, quote)
                    key = (general_id, source_locator(row), quote)
                    if key in seen:
                        continue
                    seen.add(key)
                    proposals.append(
                        {
                            "proposalId": f"anchor-source:{general_id}:{len(seen):04d}",
                            "proposalType": "anchor-source",
                            "targetGeneralId": general_id,
                            "targetNameZhTw": target_name,
                            "sourceFile": repo_relative(source_path),
                            "sourceLayer": compact_text(row.get("sourceLayer") or row.get("sourceLayerRaw") or row.get("reviewStatus") or "local-artifact"),
                            "sourceFamily": compact_text(row.get("sourceFamily")),
                            "sourcePolicyId": compact_text(row.get("sourcePolicyId")),
                            "locator": source_locator(row),
                            "sourceQuoteZhTw": quote,
                            "anchorSuitability": suitability,
                            "reasonZhTw": reason,
                            "canonicalWrites": False,
                        }
                    )
                    break

    proposals.sort(
        key=lambda item: (
            item.get("targetGeneralId") or "",
            {"ready": 0, "weak": 1, "unusable": 2}.get(str(item.get("anchorSuitability") or ""), 9),
            -quote_priority(
                str(item.get("sourceQuoteZhTw") or ""),
                str(item.get("targetNameZhTw") or ""),
                {"reviewStatus": item.get("sourceLayer"), "sourceLayer": item.get("sourceLayer")},
            )[0],
            -quote_priority(
                str(item.get("sourceQuoteZhTw") or ""),
                str(item.get("targetNameZhTw") or ""),
                {"reviewStatus": item.get("sourceLayer"), "sourceLayer": item.get("sourceLayer")},
            )[1],
            item.get("sourceFile") or "",
        )
    )
    version_metadata = build_version_metadata(
        schema_version="top50-anchor-source-gap-proposals.v1",
        artifact_paths=[policy_path, *source_paths],
        repo_root=REPO_ROOT,
    )
    for row in proposals:
        row.update(version_metadata)
    write_jsonl(jsonl_path, proposals)
    ready_by_general = {row["targetGeneralId"] for row in proposals if row.get("anchorSuitability") == "ready"}
    missing_general_ids = [general_id for general_id in target_ids if general_id not in ready_by_general]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(proposals, missing_general_ids), encoding="utf-8")

    summary = {
        "mode": "top50-second-layer-anchor-source-proposals",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "sourcePaths": [repo_relative(path) for path in source_paths],
        "targetGeneralIds": target_ids,
        "proposalCount": len(proposals),
        "anchorSuitabilityCounts": dict(sorted(Counter(str(row.get("anchorSuitability") or "") for row in proposals).items())),
        "targetsWithReadyAnchor": sorted(ready_by_general),
        "targetsStillMissingReadyAnchor": missing_general_ids,
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "markdownPath": repo_relative(md_path),
            "summaryPath": repo_relative(summary_path),
        },
    }
    write_json(summary_path, summary)
    print(
        "[build_top50_anchor_source_gap_proposals] "
        f"proposalCount={len(proposals)} readyTargets={len(ready_by_general)} missingTargets={len(missing_general_ids)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
