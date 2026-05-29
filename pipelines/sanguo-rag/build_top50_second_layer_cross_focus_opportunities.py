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
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-second-layer-cross-focus-lane.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
DEFAULT_TAG = "top50-second-layer-cross-focus-opportunities"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan second-layer and source-grounded sentences for Top50 cross-focus hard relation opportunities."
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


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


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


def infer_mentions(text: str, alias_map: dict[str, list[str]]) -> tuple[list[str], dict[str, str]]:
    compact = compact_text(text)
    hits: list[tuple[int, str, str]] = []
    matched_alias: dict[str, str] = {}
    if not compact:
        return [], matched_alias
    for general_id, aliases in alias_map.items():
        best_alias = ""
        best_len = 0
        for alias in aliases:
            alias_text = compact_text(alias)
            if len(alias_text) < 2:
                continue
            if alias_text in compact and len(alias_text) > best_len:
                best_alias = alias_text
                best_len = len(alias_text)
        if best_alias:
            matched_alias[general_id] = best_alias
            hits.append((best_len, general_id, best_alias))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [general_id for _, general_id, _ in hits], matched_alias


def min_distance(text: str, left: str, right: str) -> int | None:
    if not left or not right:
        return None
    left_positions = [index for index in range(len(text)) if text.startswith(left, index)]
    right_positions = [index for index in range(len(text)) if text.startswith(right, index)]
    if not left_positions or not right_positions:
        return None
    return min(abs(left_pos - right_pos) for left_pos in left_positions for right_pos in right_positions)


def cue_hits(text: str, cue_families: dict[str, list[str]]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for relationship_type, terms in cue_families.items():
        matched = [term for term in terms if term and term in text]
        if matched:
            hits[relationship_type] = matched
    return hits


def load_jobs(path: Path) -> dict[str, dict[str, Any]]:
    jobs = {}
    for row in read_jsonl(path):
        focus_general_id = str(row.get("focusGeneralId") or "").strip()
        if focus_general_id:
            jobs[focus_general_id] = row
    return jobs


def source_rows_for_focus(
    *,
    focus_id: str,
    source_spec: dict[str, Any],
    source_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    row_type = str(source_spec.get("rowType") or "").strip()
    source_file = repo_relative(resolve_path(source_spec.get("path") or ""))
    results: list[dict[str, str]] = []
    for row in source_rows:
        if row_type == "second-layer-anchor-proposals":
            if str(row.get("targetGeneralId") or "").strip() != focus_id:
                continue
            quote = compact_text(row.get("sourceQuoteZhTw"))
            if not quote:
                continue
            results.append(
                {
                    "sourceType": row_type,
                    "sourceLayer": compact_text(row.get("sourceLayer") or "second-layer-anchor"),
                    "sourceStrength": compact_text(row.get("anchorSuitability") or "unknown"),
                    "sourceFile": source_file,
                    "locator": compact_text(row.get("locator")),
                    "quote": quote,
                }
            )
        elif row_type == "source-event-packets":
            if focus_id not in string_list(row.get("generalIds")):
                continue
            for quote in [compact_text(example) for example in row.get("examples") or []]:
                if not quote:
                    continue
                results.append(
                    {
                        "sourceType": row_type,
                        "sourceLayer": compact_text(row.get("reviewStatus") or "source-grounded-event-packet"),
                        "sourceStrength": "source-grounded",
                        "sourceFile": source_file,
                        "locator": compact_text(row.get("sourceRef") or row.get("packetId")),
                        "quote": quote,
                    }
                )
        elif row_type == "event-question-seeds":
            if str(row.get("generalId") or "").strip() != focus_id:
                continue
            for example in row.get("examples") or []:
                if not isinstance(example, dict):
                    continue
                quote = compact_text(example.get("text"))
                if not quote:
                    continue
                results.append(
                    {
                        "sourceType": row_type,
                        "sourceLayer": compact_text(row.get("reviewStatus") or "source-grounded-seed"),
                        "sourceStrength": "source-grounded",
                        "sourceFile": source_file,
                        "locator": compact_text(example.get("sourceRef") or row.get("seedId")),
                        "quote": quote,
                    }
                )
        elif row_type == "second-layer-reviewed-cache":
            if str(row.get("focusGeneralId") or "").strip() != focus_id:
                continue
            for review in row.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                quote = compact_text(review.get("sentenceTextZhTw") or review.get("sentenceText") or review.get("sourceQuoteZhTw"))
                if not quote:
                    continue
                results.append(
                    {
                        "sourceType": row_type,
                        "sourceLayer": compact_text(review.get("verdict") or "reviewed-cache"),
                        "sourceStrength": "reviewed-cache",
                        "sourceFile": source_file,
                        "locator": compact_text(review.get("sourcePassageRef") or review.get("locator")),
                        "quote": quote,
                    }
                )
    return results


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Top50 Second-layer Cross-focus Opportunity 掃描",
        "",
        "- 這份輸出只做 `proposal-only` 掃描，不直接回寫主線。",
        "- 目標是找出第二層 anchor / source-grounded 句子中，是否存在可回到 Top50 主線的硬關係候選。",
        "",
        f"- 掃描 focus 數：`{int(summary.get('focusCount') or 0)}`",
        f"- 掃描句子數：`{int(summary.get('quoteCount') or 0)}`",
        f"- 命中 Top50 對手句數：`{int(summary.get('top50CrossHitCount') or 0)}`",
        f"- 可信 Top50 對手句數：`{int(summary.get('trustedTop50CrossHitCount') or 0)}`",
        f"- 可疑硬關係候選數：`{int(summary.get('hardRelationCandidateCount') or 0)}`",
        "",
        "| 焦點人物 | stage | 對手 | 可能關係 | 來源 | 句子 | 判定理由 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {focus} | {stage} | {counterpart} | {types} | `{source}` | {quote} | {reason} |".format(
                focus=row.get("focusNameZhTw") or row.get("focusGeneralId") or "",
                stage=row.get("opportunityStage") or "",
                counterpart="、".join(string_list(row.get("matchedTop50CounterpartNamesZhTw"))) or "無",
                types="、".join(string_list(row.get("supportedHardRelationTypes"))) or "無",
                source=f"{row.get('sourceType')}@{row.get('locator') or '?'}",
                quote=str(row.get("sourceQuoteZhTw") or "").replace("|", "\\|"),
                reason=str(row.get("reasonZhTw") or "").replace("|", "\\|"),
            )
        )
    if not rows:
        lines.extend(
            [
                "| 無 | 無 | 無 | 無 | 無 | 無 | 目前沒有任何命中 Top50 對手且可疑的 second-layer 句子。 |",
            ]
        )
    lines.append("")
    focus_summaries = summary.get("focuses") if isinstance(summary.get("focuses"), list) else []
    if focus_summaries:
        lines.append("## Focus 摘要")
        lines.append("")
        for row in focus_summaries:
            lines.append(
                "- {focus}：掃描 `{quotes}` 句，命中 Top50 對手 `{hits}` 句，硬關係候選 `{candidates}` 句。".format(
                    focus=row.get("focusNameZhTw") or row.get("focusGeneralId") or "",
                    quotes=int(row.get("quoteCount") or 0),
                    hits=int(row.get("top50CrossHitCount") or 0),
                    candidates=int(row.get("hardRelationCandidateCount") or 0),
                )
            )
            next_action = compact_text(row.get("suggestedNextActionZhTw"))
            if next_action:
                lines.append(f"  建議：{next_action}")
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
    relationship_policy_path = resolve_path(
        str(
            (policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}).get("relationshipPolicyPath")
            or "data/sanguo/policies/policy-relationship-trust-zone.json"
        )
    )
    relationship_policy = read_json(relationship_policy_path)
    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)

    inputs = policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}
    jobs_path = resolve_path(str(inputs.get("top50JobsPath") or ""))
    jobs = load_jobs(jobs_path)
    cue_families = {
        str(key): [term for term in string_list(value)]
        for key, value in (policy.get("cueFamilies") if isinstance(policy.get("cueFamilies"), dict) else {}).items()
    }
    pair_window_chars = int(policy.get("pairWindowChars") or 24)
    target_general_ids = string_list(policy.get("targetGeneralIds"))
    source_specs = [row for row in inputs.get("sourceSpecs") or [] if isinstance(row, dict)]
    source_cache = {
        str(spec.get("id") or ""): read_jsonl(resolve_path(spec.get("path") or ""))
        for spec in source_specs
        if resolve_path(spec.get("path") or "").exists()
    }

    output_rows: list[dict[str, Any]] = []
    focus_summaries: list[dict[str, Any]] = []

    for focus_general_id in target_general_ids:
        job = jobs.get(focus_general_id) or {}
        focus_name = str(job.get("focusNameZhTw") or name_map.get(focus_general_id) or focus_general_id).strip()
        candidate_counterpart_ids = string_list(job.get("candidateCounterpartIds"))
        quote_rows: list[dict[str, str]] = []
        for source_spec in source_specs:
            source_id = str(source_spec.get("id") or "")
            source_rows = source_cache.get(source_id) or []
            quote_rows.extend(
                source_rows_for_focus(
                    focus_id=focus_general_id,
                    source_spec=source_spec,
                    source_rows=source_rows,
                )
            )

        seen_quotes: set[tuple[str, str, str]] = set()
        quote_count = 0
        top50_cross_hit_count = 0
        trusted_top50_cross_hit_count = 0
        hard_relation_candidate_count = 0

        for quote_row in quote_rows:
            dedupe_key = (quote_row["sourceFile"], quote_row["locator"], quote_row["quote"])
            if dedupe_key in seen_quotes:
                continue
            seen_quotes.add(dedupe_key)
            quote_count += 1
            quote = quote_row["quote"]
            mention_ids, matched_alias = infer_mentions(quote, alias_map)
            mention_ids = unique_strings(mention_ids)
            matched_top50_counterpart_ids = [general_id for general_id in candidate_counterpart_ids if general_id in mention_ids]
            matched_top50_counterpart_names = [str(name_map.get(general_id) or general_id) for general_id in matched_top50_counterpart_ids]
            other_mention_ids = [general_id for general_id in mention_ids if general_id not in {focus_general_id, *matched_top50_counterpart_ids}]
            relation_cues = cue_hits(quote, cue_families)
            pair_window_supported = False
            nearest_counterpart = ""
            min_pair_distance: int | None = None
            focus_alias = matched_alias.get(focus_general_id, focus_name)
            for counterpart_id in matched_top50_counterpart_ids:
                counterpart_alias = matched_alias.get(counterpart_id, str(name_map.get(counterpart_id) or counterpart_id))
                distance = min_distance(quote, focus_alias, counterpart_alias)
                if distance is None:
                    continue
                if min_pair_distance is None or distance < min_pair_distance:
                    min_pair_distance = distance
                    nearest_counterpart = counterpart_id
                if distance <= pair_window_chars:
                    pair_window_supported = True

            opportunity_stage = "no-top50-cross-hit"
            supported_hard_relation_types: list[str] = []
            blocker_codes: list[str] = []
            reason = "句中沒有命中 Top50 對手人物。"
            if matched_top50_counterpart_ids:
                top50_cross_hit_count += 1
                if str(quote_row.get("sourceStrength") or "") in {"ready", "source-grounded", "reviewed-cache"}:
                    trusted_top50_cross_hit_count += 1
                opportunity_stage = "cross-focus-trace-only"
                if relation_cues:
                    supported_hard_relation_types = sorted(relation_cues.keys())
                if not relation_cues:
                    blocker_codes.append("no-hard-relation-cue")
                if not pair_window_supported:
                    blocker_codes.append("pair-window-too-wide")
                if other_mention_ids and relation_cues:
                    blocker_codes.append("cue-binds-non-top50-entity")
                if supported_hard_relation_types and pair_window_supported and not other_mention_ids:
                    opportunity_stage = "hard-relation-candidate"
                    hard_relation_candidate_count += 1
                    reason = "句中同時命中 focus 與 Top50 對手，且硬關係 cue 沒被第三人搶走。"
                else:
                    reason_parts = []
                    if supported_hard_relation_types:
                        reason_parts.append(f"命中 cue={','.join(supported_hard_relation_types)}")
                    if other_mention_ids:
                        reason_parts.append(
                            "句中另有非 Top50 人物="
                            + ",".join(str(name_map.get(general_id) or general_id) for general_id in other_mention_ids[:4])
                        )
                    if not pair_window_supported:
                        reason_parts.append("focus 與 Top50 對手沒有形成足夠近的句內綁定")
                    if not reason_parts:
                        reason_parts.append("只形成 cross-focus trace，還不能升成硬關係候選")
                    reason = "；".join(reason_parts)

            output_rows.append(
                {
                    "proposalId": f"second-layer-cross-focus:{focus_general_id}:{len(output_rows) + 1:04d}",
                    "proposalType": "second-layer-cross-focus-opportunity",
                    "focusGeneralId": focus_general_id,
                    "focusNameZhTw": focus_name,
                    "matchedTop50CounterpartIds": matched_top50_counterpart_ids,
                    "matchedTop50CounterpartNamesZhTw": matched_top50_counterpart_names,
                    "otherMentionIds": other_mention_ids,
                    "otherMentionNamesZhTw": [str(name_map.get(general_id) or general_id) for general_id in other_mention_ids],
                    "supportedHardRelationTypes": supported_hard_relation_types,
                    "cueTermsByRelationshipType": relation_cues,
                    "pairWindowSupported": pair_window_supported,
                    "nearestCounterpartId": nearest_counterpart,
                    "pairDistance": min_pair_distance,
                    "opportunityStage": opportunity_stage,
                    "blockerCodes": blocker_codes,
                    "sourceType": quote_row["sourceType"],
                    "sourceLayer": quote_row["sourceLayer"],
                    "sourceStrength": quote_row.get("sourceStrength") or "",
                    "sourceFile": quote_row["sourceFile"],
                    "locator": quote_row["locator"],
                    "sourceQuoteZhTw": quote,
                    "reasonZhTw": reason,
                    "proposalOnly": True,
                    "canonicalWrites": False,
                }
            )

        suggested_next_action = (
            "目前第二層句子雖然碰到曹操等 Top50 人物，但硬關係 cue 仍被蔡邕、衛仲道、董祀等非 Top50 對象綁走；下一步應補『蔡琰與 Top50 人物同句、且關係直接落在兩人身上』的第二層句子。"
            if hard_relation_candidate_count == 0
            else "已有可疑硬關係候選，可直接排入下一輪 focused semantic review。"
        )
        focus_summaries.append(
            {
                "focusGeneralId": focus_general_id,
                "focusNameZhTw": focus_name,
                "quoteCount": quote_count,
                "top50CrossHitCount": top50_cross_hit_count,
                "trustedTop50CrossHitCount": trusted_top50_cross_hit_count,
                "hardRelationCandidateCount": hard_relation_candidate_count,
                "suggestedNextActionZhTw": suggested_next_action,
            }
        )

    output_rows.sort(
        key=lambda row: (
            str(row.get("focusGeneralId") or ""),
            {"hard-relation-candidate": 0, "cross-focus-trace-only": 1, "no-top50-cross-hit": 2}.get(
                str(row.get("opportunityStage") or ""),
                9,
            ),
            -len(string_list(row.get("supportedHardRelationTypes"))),
            str(row.get("sourceFile") or ""),
            str(row.get("locator") or ""),
        )
    )
    version_metadata = build_version_metadata(
        schema_version="top50-second-layer-cross-focus-opportunities.v1",
        artifact_paths=[policy_path, jobs_path],
        repo_root=REPO_ROOT,
    )
    for row in output_rows:
        row.update(version_metadata)
    write_jsonl(jsonl_path, output_rows)
    summary = {
        "mode": "top50-second-layer-cross-focus-opportunities",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "jobsPath": repo_relative(jobs_path),
        "focusCount": len(target_general_ids),
        "quoteCount": len(output_rows),
        "top50CrossHitCount": sum(1 for row in output_rows if string_list(row.get("matchedTop50CounterpartIds"))),
        "trustedTop50CrossHitCount": sum(
            1
            for row in output_rows
            if string_list(row.get("matchedTop50CounterpartIds"))
            and str(row.get("sourceStrength") or "") in {"ready", "source-grounded", "reviewed-cache"}
        ),
        "hardRelationCandidateCount": sum(1 for row in output_rows if str(row.get("opportunityStage")) == "hard-relation-candidate"),
        "opportunityStageCounts": dict(sorted(Counter(str(row.get("opportunityStage") or "") for row in output_rows).items())),
        "supportedRelationshipTypeCounts": dict(
            sorted(
                Counter(
                    relationship_type
                    for row in output_rows
                    for relationship_type in string_list(row.get("supportedHardRelationTypes"))
                ).items()
            )
        ),
        "top50CounterpartCounts": dict(
            sorted(
                Counter(
                    counterpart_id
                    for row in output_rows
                    for counterpart_id in string_list(row.get("matchedTop50CounterpartIds"))
                ).items()
            )
        ),
        "focuses": focus_summaries,
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
    }
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, output_rows), encoding="utf-8")
    print(
        "[build_top50_second_layer_cross_focus_opportunities] "
        f"rows={len(output_rows)} hardCandidates={summary['hardRelationCandidateCount']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
