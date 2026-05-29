from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_alias_map, build_name_map, read_json


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_BUNDLE_MANIFEST_PATH = (
    REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-passage-bundles.jsonl"
)
DEFAULT_PROPOSALS_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/top50-second-layer-anchor-source-proposals.jsonl"
)
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"
CHAPTER_DIGITS_PATTERN = re.compile(r"(?<!\d)(\d{3})(?!\d)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adapt second-layer anchor proposals into synthetic passage bundles for Top50 focus jobs."
    )
    parser.add_argument("--bundle-manifest", default=str(DEFAULT_BUNDLE_MANIFEST_PATH))
    parser.add_argument("--proposals-path", default=str(DEFAULT_PROPOSALS_PATH))
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--bundle-jsonl-file-name",
        default="top50-passage-bundles.second-layer-anchor.jsonl",
    )
    parser.add_argument(
        "--summary-file-name",
        default="top50-passage-bundles.second-layer-anchor.summary.json",
    )
    parser.add_argument(
        "--focus-dir-name",
        default="focus-bundles-second-layer-anchor",
    )
    parser.add_argument(
        "--target-general-ids",
        nargs="*",
        default=["cai-yan", "zhang-chun-hua", "wang-yi"],
    )
    parser.add_argument(
        "--max-ready-proposals-per-focus",
        type=int,
        default=3,
    )
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


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


def hydrate_bundle_rows(bundle_manifest_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for row in bundle_manifest_rows:
        if not isinstance(row, dict):
            continue
        bundle_path = Path(str(row.get("bundlePath") or "")).resolve()
        if not bundle_path.exists():
            hydrated.append(row)
            continue
        payload = read_json(bundle_path)
        merged = dict(row)
        merged["candidateCounterpartIds"] = payload.get("candidateCounterpartIds") or row.get("candidateCounterpartIds") or []
        merged["passages"] = payload.get("passages") or []
        merged["waveId"] = str(payload.get("waveId") or row.get("waveId") or "").strip()
        merged["chapterRefs"] = payload.get("chapterRefs") or row.get("chapterRefs") or []
        hydrated.append(merged)
    return hydrated


def chapter_ref(locator: str) -> str:
    match = CHAPTER_DIGITS_PATTERN.search(locator or "")
    if not match:
        return ""
    return f"第{int(match.group(1)):03d}回"


def quote_priority(proposal: dict[str, Any]) -> tuple[int, int, int, int]:
    suitability = str(proposal.get("anchorSuitability") or "")
    quote = compact_text(proposal.get("sourceQuoteZhTw"))
    source_layer = compact_text(proposal.get("sourceLayer"))
    source_file = compact_text(proposal.get("sourceFile"))
    return (
        1 if suitability == "ready" else 0,
        1 if "source-grounded" in source_layer else 0,
        1 if "妻" in quote or "夫" in quote or "女" in quote or "子" in quote else 0,
        1 if "source-event-packets" in source_file else 0,
    )


def infer_person_ids_from_text(
    text: str,
    alias_map: dict[str, list[str]],
    *,
    max_ids: int,
) -> list[str]:
    compact = compact_text(text)
    if not compact:
        return []
    hits: list[tuple[int, str]] = []
    for general_id, aliases in alias_map.items():
        best_len = 0
        for alias in aliases:
            alias_text = compact_text(alias)
            if len(alias_text) < 2:
                continue
            if alias_text in compact and len(alias_text) > best_len:
                best_len = len(alias_text)
        if best_len > 0:
            hits.append((best_len, general_id))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [general_id for _, general_id in hits[:max_ids]]


def proposal_to_passage(
    proposal: dict[str, Any],
    *,
    focus_id: str,
    candidate_counterpart_ids: list[str],
    alias_map: dict[str, list[str]],
) -> dict[str, Any]:
    locator = str(proposal.get("locator") or "").strip()
    quote = compact_text(proposal.get("sourceQuoteZhTw"))
    person_ids = unique_strings([focus_id, *infer_person_ids_from_text(quote, alias_map, max_ids=8)])
    counterpart_set = set(candidate_counterpart_ids)
    counterpart_hits = sorted(counterpart_set.intersection(person_ids) - {focus_id})
    return {
        "locator": f"second-layer-anchor:{locator}" if locator else f"second-layer-anchor:{focus_id}",
        "chapterRef": chapter_ref(locator),
        "normalizedText": quote,
        "charCount": len(quote),
        "sourcePath": str(proposal.get("sourceFile") or "").strip(),
        "personIds": person_ids,
        "counterpartHits": counterpart_hits,
        "anchorSourceMode": "second-layer-anchor",
        "anchorProposalId": str(proposal.get("proposalId") or "").strip(),
        "anchorSuitability": str(proposal.get("anchorSuitability") or "").strip(),
        "anchorSourceLayer": str(proposal.get("sourceLayer") or "").strip(),
        "anchorSourcePolicyId": str(proposal.get("sourcePolicyId") or "").strip(),
        "canonicalWrites": False,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    outputs = summary.get("outputs") if isinstance(summary.get("outputs"), dict) else {}
    focus_rows = summary.get("focuses") if isinstance(summary.get("focuses"), list) else []
    lines = [
        "# Top50 Second-layer Anchor Bundle Adapter 摘要",
        "",
        "- 目的：把 ready second-layer anchor proposal 轉成 synthetic passage，接回人物中心白話抽取主線。",
        "- 契約：只寫新 bundle manifest 與新 focus bundle 目錄，不覆蓋原始 baihua primary bundle。",
        "- canonicalWrites：`false`",
        "",
        f"- 產出 bundle 數：`{outputs.get('bundleCount', 0)}`",
        f"- 補入 synthetic passage 數：`{outputs.get('syntheticPassageCount', 0)}`",
        f"- 原本 zero-passage 但已補入的人物數：`{outputs.get('recoveredZeroPassageFocusCount', 0)}`",
        "",
        "| 人物 | 原 passage | 新增 synthetic passage | 主要來源 |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in focus_rows:
        lines.append(
            "| {focus} | {original} | {synthetic} | {sources} |".format(
                focus=row.get("focusNameZhTw") or row.get("focusGeneralId") or "",
                original=int(row.get("originalPassageCount") or 0),
                synthetic=int(row.get("syntheticPassageCount") or 0),
                sources="、".join(string_list(row.get("sourceFiles"))[:3]) or "—",
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    bundle_manifest_path = resolve_path(args.bundle_manifest)
    proposals_path = resolve_path(args.proposals_path)
    relationship_policy_path = resolve_path(args.relationship_policy)
    output_root = resolve_path(args.output_root)
    bundle_jsonl_path = output_root / args.bundle_jsonl_file_name
    summary_path = output_root / args.summary_file_name
    focus_root = output_root / args.focus_dir_name
    markdown_path = summary_path.with_suffix(".zh-TW.md")

    if not args.overwrite and any(path.exists() for path in (bundle_jsonl_path, summary_path, markdown_path)):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {bundle_jsonl_path}")

    relationship_policy = read_json(relationship_policy_path)
    stable_bootstrap_path, formal_mention_map_path, alias_records_path = stable_inputs(relationship_policy)
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    alias_records = read_json(alias_records_path) if alias_records_path.exists() else {}
    name_map = build_name_map(stable_bootstrap, formal_mention_map)
    alias_map = build_alias_map(name_map, formal_mention_map, alias_records)

    proposals = read_jsonl(proposals_path)
    target_general_ids = set(string_list(args.target_general_ids))
    ready_proposals_by_focus: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in proposals:
        focus_id = str(row.get("targetGeneralId") or "").strip()
        if focus_id not in target_general_ids:
            continue
        if str(row.get("anchorSuitability") or "").strip() != "ready":
            continue
        ready_proposals_by_focus[focus_id].append(row)
    for focus_id, rows in list(ready_proposals_by_focus.items()):
        rows.sort(key=quote_priority, reverse=True)
        ready_proposals_by_focus[focus_id] = rows[: max(int(args.max_ready_proposals_per_focus), 1)]

    bundle_manifest_rows = read_jsonl(bundle_manifest_path)
    bundle_rows = hydrate_bundle_rows(bundle_manifest_rows)
    output_rows: list[dict[str, Any]] = []
    focus_summaries: list[dict[str, Any]] = []
    recovered_zero_passage = 0
    synthetic_passage_count = 0

    for bundle in bundle_rows:
        focus_id = str(bundle.get("focusGeneralId") or "").strip()
        focus_name = str(bundle.get("focusNameZhTw") or name_map.get(focus_id) or focus_id).strip()
        original_passages = list(bundle.get("passages") or [])
        candidate_counterpart_ids = string_list(bundle.get("candidateCounterpartIds"))
        synthetic_passages: list[dict[str, Any]] = []
        seen_locators = {str(row.get("locator") or "").strip() for row in original_passages if isinstance(row, dict)}
        for proposal in ready_proposals_by_focus.get(focus_id, []):
            passage = proposal_to_passage(
                proposal,
                focus_id=focus_id,
                candidate_counterpart_ids=candidate_counterpart_ids,
                alias_map=alias_map,
            )
            locator = str(passage.get("locator") or "").strip()
            if not locator or locator in seen_locators:
                continue
            seen_locators.add(locator)
            synthetic_passages.append(passage)

        if not original_passages and synthetic_passages:
            recovered_zero_passage += 1
        synthetic_passage_count += len(synthetic_passages)

        merged_passages = [*original_passages, *synthetic_passages]
        chapter_refs = unique_strings(
            [
                *string_list(bundle.get("chapterRefs")),
                *[str(row.get("chapterRef") or "").strip() for row in synthetic_passages if str(row.get("chapterRef") or "").strip()],
            ]
        )
        focus_bundle_path = (focus_root / f"{focus_id}.bundle.json").resolve()
        payload = {
            "bundleId": str(bundle.get("bundleId") or ""),
            "jobId": str(bundle.get("jobId") or ""),
            "focusGeneralId": focus_id,
            "focusNameZhTw": focus_name,
            "waveId": str(bundle.get("waveId") or "").strip(),
            "sourceCorpusId": str(bundle.get("sourceCorpusId") or "").strip(),
            "candidateCounterpartIds": candidate_counterpart_ids,
            "passageCount": len(merged_passages),
            "chapterRefs": chapter_refs,
            "passages": merged_passages,
            "augmentationMode": "second-layer-anchor",
            "syntheticPassageCount": len(synthetic_passages),
            "canonicalWrites": False,
        }
        write_json(focus_bundle_path, payload)

        output_rows.append(
            {
                "bundleId": str(bundle.get("bundleId") or ""),
                "jobId": str(bundle.get("jobId") or ""),
                "focusGeneralId": focus_id,
                "focusNameZhTw": focus_name,
                "sourceCorpusId": str(bundle.get("sourceCorpusId") or "").strip(),
                "bundlePath": str(focus_bundle_path),
                "passageCount": len(merged_passages),
                "chapterRefs": chapter_refs,
                "augmentationMode": "second-layer-anchor",
                "syntheticPassageCount": len(synthetic_passages),
                "canonicalWrites": False,
            }
        )
        focus_summaries.append(
            {
                "focusGeneralId": focus_id,
                "focusNameZhTw": focus_name,
                "originalPassageCount": len(original_passages),
                "syntheticPassageCount": len(synthetic_passages),
                "sourceFiles": unique_strings([str(row.get("sourceFile") or "").strip() for row in ready_proposals_by_focus.get(focus_id, [])]),
            }
        )

    write_jsonl(bundle_jsonl_path, output_rows)
    summary = {
        "mode": "top50-second-layer-anchor-bundle-adapter",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "bundleManifestPath": repo_relative(bundle_manifest_path),
            "proposalsPath": repo_relative(proposals_path),
            "relationshipPolicyPath": repo_relative(relationship_policy_path),
            "targetGeneralIds": sorted(target_general_ids),
        },
        "outputs": {
            "bundleJsonlPath": repo_relative(bundle_jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "focusBundleRoot": repo_relative(focus_root),
            "bundleCount": len(output_rows),
            "syntheticPassageCount": synthetic_passage_count,
            "recoveredZeroPassageFocusCount": recovered_zero_passage,
            "readyFocusCount": sum(1 for row in focus_summaries if int(row.get("syntheticPassageCount") or 0) > 0),
        },
        "focuses": focus_summaries,
    }
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    print(
        "[build_second_layer_anchor_bundle_adapter] "
        f"bundles={len(output_rows)} syntheticPassages={synthetic_passage_count} "
        f"recoveredZeroPassage={recovered_zero_passage} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
