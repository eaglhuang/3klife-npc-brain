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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a focus-specific low-coverage scan that explains why a Top50 hard relationship has not yet stabilized."
    )
    parser.add_argument("--focus-id", required=True)
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def build_top50_name_map(output_root: Path) -> dict[str, str]:
    rows = read_jsonl(output_root / "top50-bootstrap-jobs.jsonl")
    return {
        str(row.get("focusGeneralId") or ""): str(row.get("focusNameZhTw") or row.get("focusGeneralId") or "")
        for row in rows
        if str(row.get("focusGeneralId") or "").strip()
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['focusNameZhTw']} 低覆蓋掃描",
        "",
        f"- focus：`{summary['focusGeneralId']}`",
        f"- primary passage：`{summary['primaryPassageCount']}`",
        f"- second-layer passage：`{summary['secondLayerPassageCount']}`",
        f"- reviewed supported：`{summary['reviewedSupportedCount']}`",
        "",
        "## 目前最完整的段落",
        "",
        f"- 章回：`{summary['bundlePassage']['chapterRef']}`",
        f"- locator：`{summary['bundlePassage']['locator']}`",
        f"- 段落全文：{summary['bundlePassage']['normalizedText']}",
        "",
        "## 目前進 queue 的句窗",
        "",
        f"- 章回：`{summary['selectedPassage']['chapterRef']}`",
        f"- locator：`{summary['selectedPassage']['locator']}`",
        f"- 句窗：{summary['selectedPassage']['normalizedText']}",
        f"- 句內對手：{'、'.join(summary['selectedPassage']['counterpartDisplayNamesZhTw']) or '—'}",
        f"- 上下文對手：{'、'.join(summary['selectedPassage']['contextCounterpartDisplayNamesZhTw']) or '—'}",
        f"- cue 類型：{'、'.join(summary['selectedPassage']['candidateRelationshipTypes']) or '—'}",
        "",
        "## 為何目前還不能進硬關係白名單",
        "",
    ]
    for reason in summary["blockingReasonsZhTw"]:
        lines.append(f"- {reason}")

    lines.extend(["", "## 下一步需求", ""])
    for demand in summary["demandsZhTw"]:
        lines.append(f"- {demand}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = resolve_path(args.output_root)
    focus_id = str(args.focus_id or "").strip()
    if not focus_id:
        raise ValueError("--focus-id is required")

    tag = f"top50-focus-low-coverage-scan.{focus_id}"
    json_path = output_root / f"{tag}.json"
    md_path = output_root / f"{tag}.zh-TW.md"
    if not args.overwrite and (json_path.exists() or md_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {json_path}")

    name_map = build_top50_name_map(output_root)
    bundle = read_json(output_root / "focus-bundles-second-layer-anchor" / f"{focus_id}.bundle.json")
    packet = read_json(output_root / "focus-skill-packets-second-layer-anchor" / f"{focus_id}.skill-packet.json")
    reviewed_path = output_root / "codex-skill-review/top50-low-coverage-focus-targeted-r2-reviewed-cache.jsonl"
    reviewed_rows = [row for row in read_jsonl(reviewed_path) if str(row.get("focusGeneralId") or "") == focus_id]
    if not reviewed_rows:
        reviewed_path = output_root / "codex-skill-review/top50-low-coverage-focus-reviewed-cache.jsonl"
        reviewed_rows = [row for row in read_jsonl(reviewed_path) if str(row.get("focusGeneralId") or "") == focus_id]

    bundle_passage = (bundle.get("passages") or [{}])[0]
    selected_passage = (packet.get("selectedPassages") or [{}])[0]
    focus_name = str(packet.get("focusNameZhTw") or bundle.get("focusNameZhTw") or focus_id)

    reviewed_relationships: list[dict[str, Any]] = []
    for row in reviewed_rows:
        reviewed_relationships.extend([rel for rel in row.get("relationships") or [] if isinstance(rel, dict)])
    supported_relationships = [rel for rel in reviewed_relationships if str(rel.get("verdict") or "") == "supported"]

    display = lambda ids: [str(name_map.get(item) or item) for item in unique_strings([str(x) for x in ids])]
    selected_counterparts = unique_strings([str(x) for x in selected_passage.get("counterpartHits") or []])
    context_counterparts = unique_strings([str(x) for x in selected_passage.get("contextCounterpartHits") or []])

    blocking_reasons = []
    if not selected_counterparts and context_counterparts:
        blocking_reasons.append("目前只有上下文對手，沒有句內直接對手，因此 pair 綁定無法穩定成立。")
    if "sibling" in [str(x) for x in selected_passage.get("candidateRelationshipTypes") or []] and not selected_counterparts:
        blocking_reasons.append("雖然句中出現親屬 cue，但沒有把關係另一端的人名一起帶進句窗。")
    if not supported_relationships:
        blocking_reasons.append("現有 reviewed-cache 尚未形成任何 supported 硬關係。")

    demands = []
    if context_counterparts:
        demands.append(
            "補一段把「{focus}」與「{counterparts}」同時放進句內的引用句，而不是只留在上下文。".format(
                focus=focus_name,
                counterparts="、".join(display(context_counterparts)),
            )
        )
    if "sibling" in [str(x) for x in selected_passage.get("candidateRelationshipTypes") or []]:
        demands.append("若要成立親屬關係，需補到『具名對手 + 親屬稱謂』同句的 quote-ready 句子。")
    if not demands:
        demands.append("目前先維持觀察，等待新的 second-layer 或主文本來源進來。")

    version_metadata = build_version_metadata(
        schema_version="top50-focus-low-coverage-scan.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-focus-low-coverage-scan",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "focusGeneralId": focus_id,
        "focusNameZhTw": focus_name,
        "primaryPassageCount": 1 if (resolve_path(output_root / "focus-bundles" / f"{focus_id}.bundle.json")).exists() else 0,
        "secondLayerPassageCount": int(bundle.get("passageCount") or len(bundle.get("passages") or [])),
        "reviewedSupportedCount": len(supported_relationships),
        "bundlePassage": {
            "chapterRef": str(bundle_passage.get("chapterRef") or ""),
            "locator": str(bundle_passage.get("locator") or ""),
            "normalizedText": str(bundle_passage.get("normalizedText") or ""),
        },
        "selectedPassage": {
            "chapterRef": str(selected_passage.get("chapterRef") or ""),
            "locator": str(selected_passage.get("locator") or ""),
            "normalizedText": str(selected_passage.get("normalizedText") or ""),
            "counterpartIds": selected_counterparts,
            "counterpartDisplayNamesZhTw": display(selected_counterparts),
            "contextCounterpartIds": context_counterparts,
            "contextCounterpartDisplayNamesZhTw": display(context_counterparts),
            "candidateRelationshipTypes": [str(x) for x in selected_passage.get("candidateRelationshipTypes") or []],
        },
        "blockingReasonsZhTw": blocking_reasons,
        "demandsZhTw": demands,
        "inputs": {
            "bundlePath": repo_relative(output_root / "focus-bundles-second-layer-anchor" / f"{focus_id}.bundle.json"),
            "packetPath": repo_relative(output_root / "focus-skill-packets-second-layer-anchor" / f"{focus_id}.skill-packet.json"),
            "reviewedPath": repo_relative(reviewed_path),
        },
    }
    write_json(json_path, summary)
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(
        "[build_top50_focus_low_coverage_scan] "
        f"focus={focus_id} reviewedSupportedCount={len(supported_relationships)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
