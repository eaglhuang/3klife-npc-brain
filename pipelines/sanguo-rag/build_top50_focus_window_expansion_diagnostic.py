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

SENTENCE_ENDINGS = set("。！？!?")
CLOSING_QUOTES = set('”」』》）】)"\'')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a proposal-only sentence-window expansion diagnostic for a low-coverage Top50 focus."
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


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


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


def build_top50_name_map(output_root: Path) -> dict[str, str]:
    jobs_path = output_root / "top50-bootstrap-jobs.jsonl"
    if not jobs_path.exists():
        return {}
    rows = read_jsonl(jobs_path)
    return {
        str(row.get("focusGeneralId") or "").strip(): str(row.get("focusNameZhTw") or row.get("focusGeneralId") or "").strip()
        for row in rows
        if str(row.get("focusGeneralId") or "").strip()
    }


def split_sentences(text: str) -> list[str]:
    output: list[str] = []
    buffer: list[str] = []
    idx = 0
    while idx < len(text):
        ch = text[idx]
        buffer.append(ch)
        if ch in SENTENCE_ENDINGS:
            lookahead = idx + 1
            while lookahead < len(text) and text[lookahead] in CLOSING_QUOTES:
                buffer.append(text[lookahead])
                lookahead += 1
            sentence = "".join(buffer).strip()
            if sentence:
                output.append(sentence)
            buffer = []
            idx = lookahead
            continue
        idx += 1
    tail = "".join(buffer).strip()
    if tail:
        output.append(tail)
    return output


def parse_locator_sentence_index(locator: str) -> int | None:
    marker = "sentence="
    if marker not in locator:
        return None
    try:
        return max(int(locator.split(marker, 1)[1].split(";", 1)[0].strip()) - 1, 0)
    except ValueError:
        return None


def find_focus_sentence_index(sentences: list[str], selected_text: str, locator: str) -> int:
    locator_index = parse_locator_sentence_index(locator)
    if locator_index is not None and 0 <= locator_index < len(sentences):
        return locator_index
    compact_selected = " ".join(selected_text.split())
    for idx, sentence in enumerate(sentences):
        if compact_selected and compact_selected in " ".join(sentence.split()):
            return idx
    return 0


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['focusNameZhTw']} 句窗擴張診斷",
        "",
        f"- 人物：`{summary['focusGeneralId']}`",
        f"- 原始段落定位：`{summary['bundlePassage']['locator']}`",
        f"- 原始候選句定位：`{summary['selectedPassage']['locator']}`",
        f"- 原始候選型別：{ '、'.join(summary['selectedPassage']['candidateRelationshipTypes']) or '無' }",
        "",
        "## 原始判斷",
        "",
        f"- 目前主 queue 只切到：{summary['selectedPassage']['normalizedText']}",
        f"- 句內對手：{ '、'.join(summary['selectedPassage']['counterpartDisplayNamesZhTw']) or '無' }",
        f"- 上下文對手：{ '、'.join(summary['selectedPassage']['contextCounterpartDisplayNamesZhTw']) or '無' }",
        "",
        "## 擴張句窗候選",
        "",
        "| 視窗 | 句子範圍 | 命中對手 | 可能狀態 | 說明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in summary["windowCandidates"]:
        lines.append(
            "| {label} | {sentence_range} | {counterparts} | {status} | {note} |".format(
                label=row["windowLabelZhTw"],
                sentence_range=row["sentenceRangeZhTw"],
                counterparts="、".join(row["counterpartDisplayNamesZhTw"]) or "無",
                status=row["diagnosticStatusZhTw"],
                note=row["noteZhTw"].replace("|", "\\|"),
            )
        )

    lines.extend(
        [
            "",
            "## 判斷結論",
            "",
        ]
    )
    for line in summary["conclusionsZhTw"]:
        lines.append(f"- {line}")

    lines.extend(["", "## 下一步建議", ""])
    for line in summary["demandsZhTw"]:
        lines.append(f"- {line}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_root = resolve_path(args.output_root)
    focus_id = str(args.focus_id or "").strip()
    if not focus_id:
        raise ValueError("--focus-id is required")

    tag = f"top50-focus-window-expansion.{focus_id}"
    json_path = output_root / f"{tag}.json"
    md_path = output_root / f"{tag}.zh-TW.md"
    if not args.overwrite and (json_path.exists() or md_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {json_path}")

    bundle = read_json(output_root / "focus-bundles-second-layer-anchor" / f"{focus_id}.bundle.json")
    packet = read_json(output_root / "focus-skill-packets-second-layer-anchor" / f"{focus_id}.skill-packet.json")
    name_map = build_top50_name_map(output_root)

    passage = (bundle.get("passages") or [{}])[0]
    selected = (packet.get("selectedPassages") or [{}])[0]
    focus_name = str(packet.get("focusNameZhTw") or bundle.get("focusNameZhTw") or focus_id)
    full_text = str(passage.get("normalizedText") or "")
    selected_text = str(selected.get("normalizedText") or "")
    sentences = split_sentences(full_text)
    focus_index = find_focus_sentence_index(sentences, selected_text, str(selected.get("locator") or ""))

    candidate_counterpart_ids = unique_strings(
        [str(x) for x in selected.get("counterpartHits") or []] + [str(x) for x in selected.get("contextCounterpartHits") or []]
    )
    candidate_counterpart_names = {
        counterpart_id: str(name_map.get(counterpart_id) or counterpart_id)
        for counterpart_id in candidate_counterpart_ids
    }
    candidate_types = unique_strings([str(x) for x in selected.get("candidateRelationshipTypes") or []])

    specs = [
        ("原始單句", 0, 0),
        ("前一後零", -1, 0),
        ("前二後零", -2, 0),
        ("前零後一", 0, 1),
        ("前一後一", -1, 1),
        ("前二後一", -2, 1),
        ("前二後二", -2, 2),
        ("整段", -focus_index, len(sentences) - focus_index - 1),
    ]
    seen_ranges: set[tuple[int, int]] = set()
    window_rows: list[dict[str, Any]] = []
    for label, before, after in specs:
        start = max(focus_index + before, 0)
        end = min(focus_index + after, len(sentences) - 1)
        key = (start, end)
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        window_text = "".join(sentences[start : end + 1]).strip()
        present_counterparts = [
            display_name
            for counterpart_id, display_name in candidate_counterpart_names.items()
            if display_name and display_name in window_text
        ]
        contains_focus = focus_name in window_text
        note = "句窗內已同時看到人物與候選對手。"
        status = "仍需人工判讀"
        if not contains_focus:
            status = "不可用"
            note = "這個視窗沒有保住焦點人物本身。"
        elif not present_counterparts:
            status = "仍不足"
            note = "雖然保住焦點人物，但仍沒有把上下文對手帶進同句窗。"
        elif "sibling" in candidate_types:
            status = "只適合診斷"
            note = "雖然把對手帶進來，但當前 cue 是親屬稱謂，且對手像是政治上下文人物，不適合直接升硬關係。"
        window_rows.append(
            {
                "windowLabelZhTw": label,
                "sentenceRange": [start + 1, end + 1],
                "sentenceRangeZhTw": f"第 {start + 1} 至 {end + 1} 句",
                "windowTextZhTw": window_text,
                "counterpartDisplayNamesZhTw": present_counterparts,
                "diagnosticStatusZhTw": status,
                "noteZhTw": note,
            }
        )

    conclusions = [
        "目前的主問題不是完全沒段落，而是句窗切得太窄，只留下一句「問姐姐辛憲英」。",
        "把句窗擴到前兩句後，雖然能把「司馬懿」帶進來，但這仍像政治事件上下文，不是穩定的硬關係句。",
        "這段更可能支撐的是辛憲英與非 Top50 人物的親屬線，而不是 Top50 主線硬關係。",
    ]
    demands = [
        "若要讓辛憲英回到 Top50 主線，需補到「辛憲英 + Top50 對手 + 關係語意」同時落在同一引用窗的句子。",
        "若目標是保留她的親屬線，應另開 non-top50 baseline lane，容納像辛敞這類非 Top50 親屬。",
    ]

    version_metadata = build_version_metadata(
        schema_version="top50-focus-window-expansion-diagnostic.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-focus-window-expansion-diagnostic",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "focusGeneralId": focus_id,
        "focusNameZhTw": focus_name,
        "bundlePassage": {
            "chapterRef": str(passage.get("chapterRef") or ""),
            "locator": str(passage.get("locator") or ""),
            "normalizedText": full_text,
        },
        "selectedPassage": {
            "chapterRef": str(selected.get("chapterRef") or ""),
            "locator": str(selected.get("locator") or ""),
            "normalizedText": selected_text,
            "counterpartIds": unique_strings([str(x) for x in selected.get("counterpartHits") or []]),
            "counterpartDisplayNamesZhTw": [candidate_counterpart_names.get(x, x) for x in selected.get("counterpartHits") or []],
            "contextCounterpartIds": unique_strings([str(x) for x in selected.get("contextCounterpartHits") or []]),
            "contextCounterpartDisplayNamesZhTw": [candidate_counterpart_names.get(x, x) for x in selected.get("contextCounterpartHits") or []],
            "candidateRelationshipTypes": candidate_types,
        },
        "sentenceCount": len(sentences),
        "focusSentenceIndex": focus_index + 1,
        "windowCandidates": window_rows,
        "conclusionsZhTw": conclusions,
        "demandsZhTw": demands,
        "inputs": {
            "bundlePath": repo_relative(output_root / "focus-bundles-second-layer-anchor" / f"{focus_id}.bundle.json"),
            "packetPath": repo_relative(output_root / "focus-skill-packets-second-layer-anchor" / f"{focus_id}.skill-packet.json"),
        },
    }
    write_json(json_path, summary)
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(
        "[build_top50_focus_window_expansion_diagnostic] "
        f"focus={focus_id} windows={len(window_rows)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
