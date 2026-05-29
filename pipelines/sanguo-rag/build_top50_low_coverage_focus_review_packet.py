from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_PACKET_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/focus-skill-packets"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a low-coverage person-centered review packet from baihua focus skill packets."
    )
    parser.add_argument("--packet-root", default=str(DEFAULT_PACKET_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", default="top50-low-coverage-focus")
    parser.add_argument(
        "--focus-ids",
        nargs="*",
        default=["yu-jin", "guan-yin-ping", "xin-xian-ying", "xiao-qiao", "da-qiao"],
    )
    parser.add_argument("--max-passages-per-focus", type=int, default=6)
    parser.add_argument("--max-counterparts-per-focus", type=int, default=10)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
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


def counterpart_ids_from_passages(passages: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for passage in passages:
        values.extend([str(item) for item in passage.get("counterpartHits") or []])
        values.extend([str(item) for item in passage.get("contextCounterpartHits") or []])
    return unique_strings(values)


def trimmed_passages(passages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for passage in passages[:limit]:
        rows.append(
            {
                "locator": str(passage.get("locator") or ""),
                "chapterRef": str(passage.get("chapterRef") or ""),
                "windowType": str(passage.get("windowType") or ""),
                "focusMatchMode": str(passage.get("focusMatchMode") or ""),
                "candidateRelationshipTypes": [str(item) for item in passage.get("candidateRelationshipTypes") or []],
                "cueTermsByType": passage.get("cueTermsByType") if isinstance(passage.get("cueTermsByType"), dict) else {},
                "counterpartHits": [str(item) for item in passage.get("counterpartHits") or []],
                "contextCounterpartHits": [str(item) for item in passage.get("contextCounterpartHits") or []],
                "normalizedText": str(passage.get("normalizedText") or ""),
                "sourcePath": str(passage.get("sourcePath") or ""),
                "canonicalWrites": False,
            }
        )
    return rows


def render_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Top50 低覆蓋人物中心補審 Packet",
        "",
        f"- 產生時間：`{packet['generatedAt']}`",
        f"- focus 數量：`{packet['entryCount']}`",
        "- 這份 packet 直接給 skill / Codex 做人物中心補審，不依賴既有一般 sentence queue 是否已成功綁出 counterpart。",
        "- 目標是補出低覆蓋人物的硬關係草案，仍維持 `canonicalWrites=false`。",
        "",
    ]
    for index, entry in enumerate(packet.get("entries") or [], 1):
        lines.extend(
            [
                f"## {index}. {entry.get('focusNameZhTw')}（`{entry.get('focusGeneralId')}`）",
                "",
                f"- 已選句窗：`{entry.get('selectedPassageCount')}`",
                f"- 候選對象：{ '、'.join(entry.get('topCounterpartNamesZhTw') or []) or '無' }",
                "",
                "### 補審指引",
                "- 先以這個人物為中心讀句子，不要被 deterministic counterpart 綁死。",
                "- 若句子能直接看出硬關係，請產出正確 pair 與關係型別。",
                "- 若只有語意線索但對象不夠明確，標成 `not_enough_context`。",
                "",
                "### 句窗",
                "",
            ]
        )
        for sub_index, passage in enumerate(entry.get("selectedPassages") or [], 1):
            lines.extend(
                [
                    f"{sub_index}. `{passage.get('chapterRef')}` / `{passage.get('locator')}`",
                    f"   - 推定型別：{ '、'.join(passage.get('candidateRelationshipTypes') or []) or '未命中' }",
                    f"   - 對象命中：{ '、'.join(passage.get('counterpartHits') or []) or '無' }",
                    f"   - 上下文對象：{ '、'.join(passage.get('contextCounterpartHits') or []) or '無' }",
                    f"   - 原文：{passage.get('normalizedText')}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    packet_root = Path(args.packet_root).resolve()
    output_root = Path(args.output_root).resolve()
    json_path = output_root / f"{args.tag}.json"
    md_path = output_root / f"{args.tag}.zh-TW.md"
    summary_path = output_root / f"{args.tag}.summary.json"
    if not args.overwrite and (json_path.exists() or md_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {json_path}")

    entries: list[dict[str, Any]] = []
    missing_focus_ids: list[str] = []

    for focus_id in unique_strings([str(item) for item in args.focus_ids]):
        packet_path = packet_root / f"{focus_id}.skill-packet.json"
        if not packet_path.exists():
            missing_focus_ids.append(focus_id)
            continue
        payload = read_json(packet_path)
        selected_passages = trimmed_passages(
            [row for row in payload.get("selectedPassages") or [] if isinstance(row, dict)],
            max(1, int(args.max_passages_per_focus)),
        )
        counterpart_ranking = [row for row in payload.get("counterpartRanking") or [] if isinstance(row, dict)]
        top_counterparts = counterpart_ranking[: max(1, int(args.max_counterparts_per_focus))]
        if not top_counterparts:
            fallback_ids = counterpart_ids_from_passages(selected_passages)
            top_counterparts = [{"counterpartId": counterpart_id, "counterpartNameZhTw": counterpart_id} for counterpart_id in fallback_ids]

        entries.append(
            {
                "focusGeneralId": focus_id,
                "focusNameZhTw": str(payload.get("focusNameZhTw") or focus_id),
                "selectedPassageCount": len(selected_passages),
                "selectedPassages": selected_passages,
                "topCounterpartIds": [str(row.get("counterpartId") or "") for row in top_counterparts if str(row.get("counterpartId") or "").strip()],
                "topCounterpartNamesZhTw": [
                    str(row.get("counterpartNameZhTw") or row.get("counterpartId") or "")
                    for row in top_counterparts
                    if str(row.get("counterpartId") or "").strip()
                ],
                "canonicalWrites": False,
            }
        )

    packet = {
        "schemaVersion": "top50-low-coverage-focus-review-packet.v1",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "entryCount": len(entries),
        "missingFocusIds": missing_focus_ids,
        "entries": entries,
    }
    version_metadata = build_version_metadata(
        schema_version="top50-low-coverage-focus-review-packet.v1",
        artifact_paths=[packet_root],
        repo_root=REPO_ROOT,
    )
    packet.update(version_metadata)
    summary = {
        "generatedAt": packet["generatedAt"],
        **version_metadata,
        "canonicalWrites": False,
        "packetPath": str(json_path),
        "markdownPath": str(md_path),
        "entryCount": len(entries),
        "missingFocusIds": missing_focus_ids,
    }
    write_json(json_path, packet)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(packet), encoding="utf-8")
    write_json(summary_path, summary)
    print(f"[build_top50_low_coverage_focus_review_packet] wrote {json_path}")
    print(f"[build_top50_low_coverage_focus_review_packet] wrote {md_path}")
    print(f"[build_top50_low_coverage_focus_review_packet] wrote {summary_path}")
    print(
        "[build_top50_low_coverage_focus_review_packet] "
        f"entries={len(entries)} missing={len(missing_focus_ids)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
