from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_alias_map, build_name_map, read_json
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-variant-romance-anchor-lane.json"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
DEFAULT_TAG = "top50-variant-romance-source-demand"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a reviewable source-demand sheet for Top50 romance-variant lanes without merging into baihua primary."
    )
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
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


def name_to_general_id(name: str, alias_map: dict[str, list[str]], name_map: dict[str, str]) -> str | None:
    target = compact_text(name)
    if not target:
        return None
    for general_id, display_name in name_map.items():
        if compact_text(display_name) == target:
            return general_id
    for general_id, aliases in alias_map.items():
        if target in [compact_text(alias) for alias in aliases]:
            return general_id
    return None


def display_name_from_id(general_id: str, top50_name_map: dict[str, str], name_map: dict[str, str]) -> str:
    return str(top50_name_map.get(general_id) or name_map.get(general_id) or general_id)


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Top50 Variant Romance 來源需求表",
        "",
        "- 此表只服務 `romance-variant-only` lane，不會回灌到白話主文本主線。",
        "- 目的不是直接產生白名單，而是把目前只剩結構痕跡的候選，整理成可採證、可審核的來源需求。",
        "",
        f"- 需求筆數：`{int(summary.get('demandCount') or 0)}`",
        f"- 目標人物數：`{int(summary.get('targetCount') or 0)}`",
        "",
        "| 目標人物 | 需求類型 | 優先對手 | 痕跡筆數 | 建議補強來源 | 原因 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {target} | {demand_type} | {counterpart} | {trace_count} | {source_need} | {reason} |".format(
                target=row.get("targetNameZhTw") or row.get("targetGeneralId") or "",
                demand_type=row.get("demandTypeZhTw") or row.get("demandType") or "",
                counterpart="、".join(string_list(row.get("preferredCounterpartDisplayNamesZhTw"))) or "—",
                trace_count=int(row.get("traceCount") or 0),
                source_need=str(row.get("suggestedSourceNeedZhTw") or "").replace("|", "\\|"),
                reason=str(row.get("reasonZhTw") or "").replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def demand_type_zh_tw(demand_type: str) -> str:
    mapping = {
        "entity-existence-and-quote-ready-anchor": "人物存在與可引用錨點",
        "counterpart-anchored-quote": "對手綁定引用句",
    }
    return mapping.get(demand_type, demand_type)


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy_path)
    relationship_policy_path = resolve_path(args.relationship_policy)
    output_root = resolve_path(args.output_root)
    jsonl_path = output_root / f"{args.tag}.jsonl"
    summary_path = output_root / f"{args.tag}.summary.json"
    markdown_path = output_root / f"{args.tag}.zh-TW.md"
    if not args.overwrite and any(path.exists() for path in (jsonl_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {jsonl_path}")

    policy = read_json(policy_path)
    relationship_policy = read_json(relationship_policy_path)
    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)
    top50_name_map = build_top50_name_map(output_root)

    proposal_path = output_root / "top50-variant-romance-anchor-proposals.jsonl"
    proposals = read_jsonl(proposal_path)
    grouped_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in proposals:
        grouped_by_target[str(row.get("targetGeneralId") or "").strip()].append(row)

    rows: list[dict[str, Any]] = []
    for target_general_id in string_list(policy.get("targetGeneralIds")):
        target_name = display_name_from_id(target_general_id, top50_name_map, name_map)
        target_rows = grouped_by_target.get(target_general_id, [])
        stage_counts = Counter(str(row.get("proposalStage") or "") for row in target_rows)
        trace_counts = Counter(str(row.get("traceKind") or "") for row in target_rows)
        counterpart_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in target_rows:
            for counterpart_name in string_list(row.get("counterpartNamesZhTw")):
                counterpart_rows[counterpart_name].append(row)

        if stage_counts.get("needs-new-source", 0) > 0:
            rows.append(
                {
                    "demandId": f"variant-source-demand:{target_general_id}:identity",
                    "targetGeneralId": target_general_id,
                    "targetNameZhTw": target_name,
                    "demandType": "entity-existence-and-quote-ready-anchor",
                    "demandTypeZhTw": demand_type_zh_tw("entity-existence-and-quote-ready-anchor"),
                    "preferredCounterpartIds": [],
                    "preferredCounterpartDisplayNamesZhTw": [],
                    "traceCount": int(stage_counts.get("needs-new-source", 0)),
                    "traceKinds": sorted(trace_counts),
                    "suggestedSourceNeedZhTw": "先補一個可引用的 romance-variant 正式來源，至少要能穩定指出人物存在、出場定位與可追溯的 passage locator。",
                    "reasonZhTw": "目前 runtime/persona 只剩人物存在痕跡，缺少可以進入 review 的可引用錨點。",
                    "canonicalWrites": False,
                }
            )

        for counterpart_name, counterpart_traces in sorted(counterpart_rows.items(), key=lambda item: (-len(item[1]), item[0])):
            counterpart_id = name_to_general_id(counterpart_name, alias_map, name_map)
            counterpart_display = (
                display_name_from_id(counterpart_id, top50_name_map, name_map)
                if counterpart_id
                else counterpart_name
            )
            trace_kinds = Counter(str(row.get("traceKind") or "") for row in counterpart_traces)
            needs_new_source = any(str(row.get("proposalStage") or "") == "needs-new-source" for row in counterpart_traces)
            rows.append(
                {
                    "demandId": f"variant-source-demand:{target_general_id}:{counterpart_id or counterpart_name}",
                    "targetGeneralId": target_general_id,
                    "targetNameZhTw": target_name,
                    "demandType": "counterpart-anchored-quote",
                    "demandTypeZhTw": demand_type_zh_tw("counterpart-anchored-quote"),
                    "preferredCounterpartIds": [counterpart_id] if counterpart_id else [],
                    "preferredCounterpartDisplayNamesZhTw": [counterpart_display],
                    "traceCount": len(counterpart_traces),
                    "traceKinds": dict(sorted(trace_kinds.items())),
                    "suggestedSourceNeedZhTw": (
                        "補一段 romance-variant 來源中的明確引用句，讓關係同時落在人物本身與指定對手上，且具備 passage locator。"
                        if not needs_new_source
                        else "現有痕跡只夠證明有人物或配對傳說，仍需補一個真正可引用的 romance-variant 來源。"
                    ),
                    "reasonZhTw": (
                        "目前只有結構痕跡或平面欄位共現，還不足以直接進 review；需要 quote-ready 句子把關係穩定綁到對手。"
                    ),
                    "canonicalWrites": False,
                }
            )

    rows.sort(
        key=lambda row: (
            str(row.get("targetGeneralId") or ""),
            0 if str(row.get("demandType") or "") == "entity-existence-and-quote-ready-anchor" else 1,
            -int(row.get("traceCount") or 0),
            "、".join(string_list(row.get("preferredCounterpartDisplayNamesZhTw"))),
        )
    )

    write_jsonl(jsonl_path, rows)
    version_metadata = build_version_metadata(
        schema_version="top50-variant-romance-source-demand.v1",
        artifact_paths=[],
        repo_root=REPO_ROOT,
    )
    summary = {
        "mode": "top50-variant-romance-source-demand",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "relationshipPolicyPath": repo_relative(relationship_policy_path),
        "proposalPath": repo_relative(proposal_path),
        "targetCount": len(string_list(policy.get("targetGeneralIds"))),
        "demandCount": len(rows),
        "demandTypeCounts": dict(sorted(Counter(str(row.get("demandType") or "") for row in rows).items())),
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
    }
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, rows), encoding="utf-8")
    print(
        "[build_top50_variant_romance_source_demand] "
        f"demandCount={len(rows)} targetCount={summary['targetCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
