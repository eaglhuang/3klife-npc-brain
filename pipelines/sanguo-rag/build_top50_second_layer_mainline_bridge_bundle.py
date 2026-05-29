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
DEFAULT_TOP50_JOBS_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-bootstrap-jobs.jsonl"
)
DEFAULT_SUPPORTED_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-stable-hard-whitelist-candidates.reviewed-supported.second-layer-focus.jsonl"
)
DEFAULT_HUMAN_DECISIONS_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.json"
)
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge second-layer reviewed-supported rows back into Top50 mainline review bundles."
    )
    parser.add_argument("--top50-jobs-path", default=str(DEFAULT_TOP50_JOBS_PATH))
    parser.add_argument("--supported-path", default=str(DEFAULT_SUPPORTED_PATH))
    parser.add_argument("--human-decisions-path", action="append", default=[])
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", default="reviewed-supported.second-layer-mainline-bridge")
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


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
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


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def compact_sentence(value: Any, limit: int = 140) -> str:
    text = compact_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def relationship_label(relationship_type: str) -> str:
    labels = {
        "ruler_subject": "君臣",
        "spouse": "配偶",
        "parent_child": "親子",
        "adoptive_parent_child": "義父義子",
        "sibling": "兄弟姊妹",
        "sworn_sibling": "結義",
        "faction_membership": "陣營",
    }
    return labels.get(relationship_type, relationship_type)


def top50_name_map(path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in read_jsonl(path):
        focus_id = str(row.get("focusGeneralId") or "").strip()
        focus_name = str(row.get("focusNameZhTw") or "").strip()
        if focus_id:
            names[focus_id] = focus_name or focus_id
    return names


def resolved_human_keys(paths: list[Path]) -> set[str]:
    resolved: set[str] = set()
    for path in paths:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        for command in payload.get("commands") or []:
            if not isinstance(command, dict):
                continue
            trust_key = str(command.get("trustKey") or "").strip()
            if trust_key:
                resolved.add(trust_key)
    return resolved


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Top50 second-layer 回推主線審核表",
        "",
        "- 這份清單只保留 `second-layer reviewed-supported` 中，雙方都屬於 Top50 的硬關係。",
        "- 已經進入白黑名單的 trustKey 會先跳過，不重複送審。",
        "- 本批維持 `canonicalWrites=false`，用途是讓 second-layer 成果安全回推主線視圖。",
        "",
        "| # | trustKey | 關係 | 甲方 | 乙方 | 語意分數 | 來源定位 | 證據句 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | `{trust_key}` | {rtype} | {from_name} | {to_name} | {score} | `{locator}` | {quote} |".format(
                idx=index,
                trust_key=markdown_escape(row.get("trustKey")),
                rtype=markdown_escape(row.get("relationshipTypeZhTw")),
                from_name=markdown_escape(row.get("fromNameZhTw")),
                to_name=markdown_escape(row.get("toNameZhTw")),
                score=markdown_escape(f"{number_value(row.get('semanticTrustScore')):.1f}"),
                locator=markdown_escape(row.get("locator")),
                quote=markdown_escape(compact_sentence(row.get("evidenceSentenceZhTw"))),
            )
        )
    lines.extend(
        [
            "",
            "## 審核方式",
            "",
            "1. 正確者標記為 `approved`，後續可轉進主線白名單決策。",
            "2. 不正確者標記為 `rejected`，後續可轉進黑名單決策。",
            "3. 這份表不是 raw queue，不會混入 proposal-only 候選。",
            "",
        ]
    )
    return "\n".join(lines)


def build_decision_template(rows: list[dict[str, Any]], tag: str) -> dict[str, Any]:
    decisions = []
    for row in rows:
        decisions.append(
            {
                "trustKey": row["trustKey"],
                "decision": "pending",
                "reviewer": "human",
                "relationshipType": row["relationshipType"],
                "fromId": row["fromId"],
                "toId": row["toId"],
                "reviewNotesZhTw": f"{tag} second-layer 回推主線待審",
                "canonicalWrites": False,
            }
        )
    return {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": f"top50-stable-hard-human-decisions.{tag}.template",
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


def output_paths(output_root: Path, tag: str) -> dict[str, Path]:
    stem = f"top50-stable-hard-whitelist-candidates.{tag}"
    return {
        "jsonl": output_root / f"{stem}.jsonl",
        "markdown": output_root / f"{stem}.zh-TW.md",
        "template": output_root / f"top50-stable-hard-human-decisions.{tag}.template.json",
        "summary": output_root / f"{stem}.summary.json",
    }


def main() -> int:
    args = parse_args()
    jobs_path = resolve_path(args.top50_jobs_path)
    supported_path = resolve_path(args.supported_path)
    human_paths = [resolve_path(path_text) for path_text in args.human_decisions_path if str(path_text).strip()]
    if not human_paths:
        human_paths = [DEFAULT_HUMAN_DECISIONS_PATH]
    output_root = resolve_path(args.output_root)
    tag = str(args.tag or "reviewed-supported.second-layer-mainline-bridge").strip()
    paths = output_paths(output_root, tag)
    if not args.overwrite and any(path.exists() for path in paths.values()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {paths['jsonl']}")

    top50_names = top50_name_map(jobs_path)
    top50_ids = set(top50_names)
    resolved_keys = resolved_human_keys(human_paths)

    rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    seen_trust_keys: set[str] = set()

    for row in read_jsonl(supported_path):
        trust_key = str(row.get("trustKey") or "").strip()
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not trust_key or not from_id or not to_id:
            counters["skippedMissingIdentity"] += 1
            continue
        if trust_key in seen_trust_keys:
            counters["skippedDuplicate"] += 1
            continue
        if trust_key in resolved_keys:
            counters["skippedResolvedByHuman"] += 1
            continue
        if from_id not in top50_ids or to_id not in top50_ids:
            counters["skippedNonTop50Counterpart"] += 1
            continue
        bridge_row = {
            "trustKey": trust_key,
            "relationshipType": str(row.get("relationshipType") or "").strip(),
            "relationshipTypeZhTw": relationship_label(str(row.get("relationshipType") or "").strip()),
            "fromId": from_id,
            "toId": to_id,
            "fromNameZhTw": str(row.get("fromNameZhTw") or top50_names.get(from_id) or from_id),
            "toNameZhTw": str(row.get("toNameZhTw") or top50_names.get(to_id) or to_id),
            "semanticTrustScore": round(number_value(row.get("semanticTrustScore"), 0.0), 3),
            "confidence": round(number_value(row.get("confidence"), 0.0), 4),
            "reviewSourceZhTw": "second-layer reviewed-supported 回推主線",
            "reviewSourceType": "second-layer-mainline-bridge",
            "provider": str(row.get("provider") or "codex-skill"),
            "semanticReviewUnitId": str(row.get("semanticReviewUnitId") or ""),
            "sourceReviewedCachePath": str(row.get("sourceReviewedCachePath") or ""),
            "locator": str(row.get("locator") or ""),
            "evidenceSentenceZhTw": compact_text(row.get("evidenceSentenceZhTw")),
            "canonicalWrites": False,
        }
        rows.append(bridge_row)
        seen_trust_keys.add(trust_key)
        counters["keptMainlineBridge"] += 1

    rows.sort(
        key=lambda item: (
            -number_value(item.get("semanticTrustScore")),
            -number_value(item.get("confidence")),
            str(item.get("trustKey") or ""),
        )
    )

    version_metadata = build_version_metadata(
        schema_version="top50-second-layer-mainline-bridge-bundle.v1",
        artifact_paths=[jobs_path, supported_path, *human_paths],
        repo_root=REPO_ROOT,
    )
    for row in rows:
        row.update(version_metadata)

    paths["markdown"].parent.mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(render_markdown(rows), encoding="utf-8")
    write_jsonl(paths["jsonl"], rows)
    template = build_decision_template(rows, tag)
    template.update(version_metadata)
    write_json(paths["template"], template)
    summary = {
        "mode": "top50-second-layer-mainline-bridge-bundle",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputs": {
            "top50JobsPath": repo_relative(jobs_path),
            "supportedPath": repo_relative(supported_path),
            "humanDecisionPaths": [repo_relative(path) for path in human_paths],
        },
        "outputs": {
            "jsonlPath": repo_relative(paths["jsonl"]),
            "markdownPath": repo_relative(paths["markdown"]),
            "decisionTemplatePath": repo_relative(paths["template"]),
            "summaryPath": repo_relative(paths["summary"]),
            "rowCount": len(rows),
        },
        "counts": dict(sorted(counters.items())),
        "relationshipTypeCounts": dict(
            sorted(Counter(str(row.get("relationshipType") or "") for row in rows).items())
        ),
    }
    write_json(paths["summary"], summary)
    print(
        "[build_top50_second_layer_mainline_bridge_bundle] "
        f"rows={len(rows)} kept={counters['keptMainlineBridge']} "
        f"skippedResolved={counters['skippedResolvedByHuman']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
