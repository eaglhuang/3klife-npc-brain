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
DEFAULT_INPUT_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/merged-bootstrap-candidates-conflict-checked.jsonl"
)
DEFAULT_PACKET_SUMMARY_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-focus-skill-packets.jsonl"
)
DEFAULT_DECISION_PATHS = [
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.json",
]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"

RELATIONSHIP_LABELS: dict[str, str] = {
    "parent_child": "親子",
    "adoptive_parent_child": "義父義子",
    "spouse": "夫妻",
    "sibling": "兄弟姊妹",
    "sworn_sibling": "結義兄弟姊妹",
    "ruler_subject": "君臣",
    "faction_membership": "陣營",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a clean Top50 merged-bootstrap hard-relation human review bundle."
    )
    parser.add_argument("--input-path", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--packet-summary-path", default=str(DEFAULT_PACKET_SUMMARY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--decision-path",
        action="append",
        default=[str(path) for path in DEFAULT_DECISION_PATHS],
        help="Applied human decision JSON path. Repeatable.",
    )
    parser.add_argument(
        "--allowed-relationship-type",
        action="append",
        default=["parent_child", "adoptive_parent_child", "spouse", "sibling", "sworn_sibling"],
        help="Allowed relationship type. Repeatable.",
    )
    parser.add_argument(
        "--allowed-bootstrap-stage",
        action="append",
        default=["review-ready", "bootstrap-candidate"],
        help="Allowed bootstrap stage. Repeatable.",
    )
    parser.add_argument("--min-confidence", type=float, default=0.9)
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--tag", default="round022.merged-bootstrap-non-ruler")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_candidate_trust_key(value: str) -> str:
    text = value.strip()
    if text.startswith("relationship|"):
        parts = text.split("|")
        if len(parts) >= 4:
            return f"{parts[1]}:{parts[2]}:{parts[3]}"
    return text


def load_resolved_trust_keys(paths: list[Path]) -> set[str]:
    resolved: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        commands = payload.get("commands")
        if not isinstance(commands, list):
            continue
        for row in commands:
            if not isinstance(row, dict):
                continue
            trust_key = str(row.get("trustKey") or "").strip()
            if trust_key:
                resolved.add(trust_key)
    return resolved


def load_name_map(packet_summary_path: Path) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for row in read_jsonl(packet_summary_path):
        general_id = str(row.get("focusGeneralId") or "").strip()
        name = str(row.get("focusNameZhTw") or "").strip()
        if general_id and name:
            name_map[general_id] = name
    return name_map


def relationship_label(relationship_type: str) -> str:
    return RELATIONSHIP_LABELS.get(relationship_type, relationship_type)


def markdown_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def trim_text(text: str, limit: int = 120) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Top50 硬關係精準審核表")
    lines.append("")
    lines.append("- 來源：白話《三國演義》人物中心 bootstrap 合併候選")
    lines.append("- 範圍：只保留非主從硬關係，且排除已進白黑名單的 trustKey")
    lines.append("- 狀態欄預設為 `pending`，可改成 `approved` 或 `rejected`")
    lines.append("")
    lines.append("| # | 狀態 | trustKey | 關係 | 人物 A | 人物 B | 信心 | 階段 | 章回 | 證據摘錄 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | pending | `{trust_key}` | {rtype} | {from_name} | {to_name} | {score} | {stage} | {chapter} | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(str(row["trustKey"])),
                rtype=markdown_escape(str(row["relationshipLabelZhTw"])),
                from_name=markdown_escape(str(row["fromNameZhTw"])),
                to_name=markdown_escape(str(row["toNameZhTw"])),
                score=markdown_escape(str(row["confidenceAggregate"])),
                stage=markdown_escape(str(row["bootstrapStage"])),
                chapter=markdown_escape(str(row["chapterRefsPreview"])),
                quote=markdown_escape(str(row["evidenceQuotePreview"])),
            )
        )
    lines.append("")
    lines.append("## 審核方式")
    lines.append("")
    lines.append("1. 到對應的 decision template 把 `decision` 改成 `approved` 或 `rejected`。")
    lines.append("2. 如需補註解，可填 `notes`。")
    lines.append("3. 套用後會轉成既有 trust-zone 可吃的白黑名單指令。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_decision_template(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for row in rows:
        decisions.append(
            {
                "trustKey": row["trustKey"],
                "relationshipType": row["relationshipType"],
                "fromId": row["fromId"],
                "toId": row["toId"],
                "fromNameZhTw": row["fromNameZhTw"],
                "toNameZhTw": row["toNameZhTw"],
                "decision": "pending",
                "reviewer": "human",
                "notes": "",
                "canonicalWrites": False,
            }
        )
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "top50-merged-bootstrap-human-decisions-template",
        "decisionField": "decision",
        "commandField": "action",
        "approvedStatuses": ["approved"],
        "rejectedStatuses": ["rejected"],
        "availableCommands": {
            "forceWhitelistActions": ["force-whitelist"],
            "forceBlacklistActions": ["force-blacklist"],
            "removeFromIndexActions": ["remove-from-index"],
        },
        "canonicalWrites": False,
        "commands": [],
        "decisions": decisions,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path).resolve()
    packet_summary_path = Path(args.packet_summary_path).resolve()
    output_root = Path(args.output_root).resolve()
    decision_paths = [Path(path).resolve() for path in args.decision_path]
    allowed_types = {item.strip() for item in args.allowed_relationship_type if item.strip()}
    allowed_stages = {item.strip() for item in args.allowed_bootstrap_stage if item.strip()}
    min_confidence = float(args.min_confidence)
    max_rows = max(1, int(args.max_rows))
    tag = str(args.tag).strip()

    jsonl_path = output_root / f"top50-merged-bootstrap-hard-whitelist-candidates.{tag}.jsonl"
    markdown_path = output_root / f"top50-merged-bootstrap-hard-whitelist-candidates.{tag}.zh-TW.md"
    template_path = output_root / f"top50-merged-bootstrap-hard-human-decisions.{tag}.template.json"
    summary_path = output_root / f"top50-merged-bootstrap-hard-whitelist-candidates.{tag}.summary.json"

    if not args.overwrite and any(path.exists() for path in [jsonl_path, markdown_path, template_path, summary_path]):
        raise FileExistsError("Output exists. Re-run with --overwrite.")

    resolved_trust_keys = load_resolved_trust_keys(decision_paths)
    name_map = load_name_map(packet_summary_path)
    rows = read_jsonl(input_path)

    kept_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()

    for row in rows:
        relationship_type = str(row.get("relationshipType") or "").strip()
        if relationship_type not in allowed_types:
            counters["skip:type"] += 1
            continue
        bootstrap_stage = str(row.get("bootstrapStage") or "").strip()
        if bootstrap_stage not in allowed_stages:
            counters["skip:stage"] += 1
            continue
        confidence = float(row.get("confidenceAggregate") or 0.0)
        if confidence < min_confidence:
            counters["skip:confidence"] += 1
            continue
        conflict_flags = row.get("conflictFlags")
        if isinstance(conflict_flags, list) and any(str(item or "").strip() for item in conflict_flags):
            counters["skip:conflict"] += 1
            continue

        normalized_trust_key = normalize_candidate_trust_key(str(row.get("trustKey") or ""))
        if not normalized_trust_key:
            counters["skip:missing-trustkey"] += 1
            continue
        if normalized_trust_key in resolved_trust_keys:
            counters["skip:resolved"] += 1
            continue

        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not from_id or not to_id:
            counters["skip:missing-pair"] += 1
            continue

        chapter_refs = row.get("chapterRefs") if isinstance(row.get("chapterRefs"), list) else []
        evidence_quotes = row.get("evidenceQuotes") if isinstance(row.get("evidenceQuotes"), list) else []

        kept_rows.append(
            {
                "trustKey": normalized_trust_key,
                "sourceTrustKey": str(row.get("trustKey") or "").strip(),
                "relationshipType": relationship_type,
                "relationshipLabelZhTw": relationship_label(relationship_type),
                "fromId": from_id,
                "toId": to_id,
                "fromNameZhTw": name_map.get(from_id, from_id),
                "toNameZhTw": name_map.get(to_id, to_id),
                "bootstrapStage": bootstrap_stage,
                "confidenceAggregate": confidence,
                "supportCount": int(row.get("supportCount") or 0),
                "timeScopeZhTw": str(row.get("timeScopeZhTw") or "").strip(),
                "chapterRefsPreview": "、".join(str(item) for item in chapter_refs[:3]) if chapter_refs else "",
                "sourcePassageRefs": row.get("sourcePassageRefs") or [],
                "evidenceQuotePreview": trim_text(str(evidence_quotes[0] or "")) if evidence_quotes else "",
                "canonicalWrites": False,
            }
        )
        counters["keep"] += 1

    kept_rows.sort(
        key=lambda item: (
            -float(item["confidenceAggregate"]),
            str(item["relationshipType"]),
            str(item["fromId"]),
            str(item["toId"]),
        )
    )
    kept_rows = kept_rows[:max_rows]

    version_metadata = build_version_metadata(
        schema_version="top50-bootstrap-merged-hard-review-bundle.v1",
        artifact_paths=[input_path, packet_summary_path, *decision_paths],
        repo_root=REPO_ROOT,
    )
    for row in kept_rows:
        row.update(version_metadata)

    write_jsonl(jsonl_path, kept_rows)
    markdown_path.write_text(render_markdown(kept_rows), encoding="utf-8")
    template = build_decision_template(kept_rows)
    template.update(version_metadata)
    write_json(template_path, template)

    summary = {
        "mode": "top50-merged-bootstrap-hard-review-bundle",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "candidatePath": str(input_path),
            "packetSummaryPath": str(packet_summary_path),
            "decisionPaths": [str(path) for path in decision_paths],
            "candidateCount": len(rows),
            "resolvedTrustKeyCount": len(resolved_trust_keys),
            "allowedRelationshipTypes": sorted(allowed_types),
            "allowedBootstrapStages": sorted(allowed_stages),
            "minConfidence": min_confidence,
        },
        "outputs": {
            "jsonlPath": str(jsonl_path),
            "markdownPath": str(markdown_path),
            "templatePath": str(template_path),
            "summaryPath": str(summary_path),
            "rowCount": len(kept_rows),
            "relationshipTypeCounts": dict(sorted(Counter(str(row["relationshipType"]) for row in kept_rows).items())),
        },
        "counters": dict(sorted(counters.items())),
    }
    write_json(summary_path, summary)

    print(f"[build_top50_bootstrap_merged_hard_review_bundle] wrote {jsonl_path}")
    print(f"[build_top50_bootstrap_merged_hard_review_bundle] wrote {markdown_path}")
    print(f"[build_top50_bootstrap_merged_hard_review_bundle] wrote {template_path}")
    print(f"[build_top50_bootstrap_merged_hard_review_bundle] wrote {summary_path}")
    print(
        "[build_top50_bootstrap_merged_hard_review_bundle] "
        f"rows={len(kept_rows)} resolvedSkipped={counters.get('skip:resolved', 0)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
