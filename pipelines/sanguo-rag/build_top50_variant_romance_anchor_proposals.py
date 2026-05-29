from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import build_name_map, read_json
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-top50-variant-romance-anchor-lane.json"
DEFAULT_RELATIONSHIP_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-relationship-trust-zone.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build proposal-only romance-variant anchor lane rows for Top50 members that should not merge into baihua primary."
    )
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--relationship-policy", default=str(DEFAULT_RELATIONSHIP_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--tag", default="top50-variant-romance-anchor-proposals")
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


def stable_name_map(relationship_policy_path: Path) -> dict[str, str]:
    relationship_policy = read_json(relationship_policy_path)
    inputs = relationship_policy.get("inputs") if isinstance(relationship_policy.get("inputs"), dict) else {}
    stable_bootstrap_path = resolve_path(
        str(
            inputs.get("stableBootstrapPath")
            or "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
        )
    )
    formal_mention_map_path = resolve_path(
        str(
            inputs.get("formalMentionMapPath")
            or "artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json"
        )
    )
    stable_bootstrap = read_json(stable_bootstrap_path) if stable_bootstrap_path.exists() else {}
    formal_mention_map = read_json(formal_mention_map_path) if formal_mention_map_path.exists() else {}
    return build_name_map(stable_bootstrap, formal_mention_map)


def trace_row(
    *,
    proposal_id: str,
    target_general_id: str,
    target_name: str,
    source_file: str,
    source_locator: str,
    source_quote: str,
    proposal_stage: str,
    trace_kind: str,
    reason: str,
    counterpart_names: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "proposalId": proposal_id,
        "proposalType": "variant-romance-anchor",
        "targetGeneralId": target_general_id,
        "targetNameZhTw": target_name,
        "sourceFile": source_file,
        "sourceLocator": source_locator,
        "sourceQuoteZhTw": source_quote,
        "counterpartNamesZhTw": counterpart_names or [],
        "proposalStage": proposal_stage,
        "traceKind": trace_kind,
        "sourceScope": "romance-variant-only",
        "doNotMergeIntoBaihuaPrimary": True,
        "reasonZhTw": reason,
        "canonicalWrites": False,
    }


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Top50 Variant Romance Anchor Proposals",
        "",
        "- 此 lane 僅處理不宜混入白話主來源的 romance-variant / 稗官野史人物線索。",
        "- 本批目標：馬雲騄。",
        "- 契約：`proposal-only`、`doNotMergeIntoBaihuaPrimary=true`、`canonicalWrites=false`。",
        "",
        "| 人物 | stage | traceKind | 對象 | 來源 | 線索 | 理由 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {target} | {stage} | {kind} | {counterparts} | `{source}` | {quote} | {reason} |".format(
                target=row.get("targetNameZhTw") or row.get("targetGeneralId") or "",
                stage=row.get("proposalStage") or "",
                kind=row.get("traceKind") or "",
                counterparts="、".join(string_list(row.get("counterpartNamesZhTw"))) or "—",
                source=row.get("sourceFile") or "",
                quote=str(row.get("sourceQuoteZhTw") or "").replace("|", "\\|"),
                reason=str(row.get("reasonZhTw") or "").replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


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
    name_map = stable_name_map(relationship_policy_path)
    target_ids = string_list(policy.get("targetGeneralIds"))
    outputs: list[dict[str, Any]] = []
    proposal_counter = 0

    female_profile_path = resolve_path(
        str(
            (policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}).get("femaleProfileOverridesPath")
            or "data/sanguo/catalogs/catalog-female-profile-overrides.jsonl"
        )
    )
    female_profiles = read_jsonl(female_profile_path) if female_profile_path.exists() else []

    for target_general_id in target_ids:
        target_name = name_map.get(target_general_id) or target_general_id

        for row in female_profiles:
            if compact_text(row.get("name")) != target_name:
                continue
            proposal_counter += 1
            outputs.append(
                trace_row(
                    proposal_id=f"variant-romance-anchor:{target_general_id}:{proposal_counter:04d}",
                    target_general_id=target_general_id,
                    target_name=target_name,
                    source_file=repo_relative(female_profile_path),
                    source_locator=str(row.get("id") or ""),
                    source_quote=compact_text(
                        "；".join(
                            [
                                f"原型：{row.get('archetype') or ''}",
                                f"互動重點：{'、'.join(string_list(row.get('interactionPriorities')))}",
                                f"關係焦點：{'、'.join(string_list(row.get('relationshipFocusNames')))}",
                                f"事件鉤子：{'、'.join(string_list(row.get('eventHooks')))}",
                            ]
                        )
                    ),
                    proposal_stage="structured-trace-only",
                    trace_kind="catalog-female-profile-override",
                    reason="本地 catalog 顯示其屬 romance_variant / 後世補完脈絡，適合作為 variant lane 的結構化線索，但仍不足以當白話主來源 quote。",
                    counterpart_names=string_list(row.get("relationshipFocusNames")),
                )
            )

        refactor_paths = string_list(
            (policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}).get("fallbackRefactorStablePaths")
        )
        for refactor_path_text in refactor_paths:
            refactor_path = resolve_path(refactor_path_text)
            if not refactor_path.exists():
                continue
            payload = read_json(refactor_path)
            proposals = payload.get("plainRelationshipProposals")
            if not isinstance(proposals, list):
                continue
            for row in proposals:
                if not isinstance(row, dict):
                    continue
                if str(row.get("fromId") or "").strip() != target_general_id:
                    continue
                proposal_counter += 1
                outputs.append(
                    trace_row(
                        proposal_id=f"variant-romance-anchor:{target_general_id}:{proposal_counter:04d}",
                        target_general_id=target_general_id,
                        target_name=target_name,
                        source_file=repo_relative(refactor_path),
                        source_locator=str(row.get("reason") or "plain-relationship-proposal-only"),
                        source_quote=compact_text(
                            f"{target_name} 與 {row.get('targetName') or row.get('toId') or ''} 在結構化 plain fields 中共同出現；"
                            f"來源欄位：{'、'.join(string_list(row.get('sourceFields'))[:6])}"
                        ),
                        proposal_stage="structured-trace-only",
                        trace_kind="refactor-plain-relationship-proposal",
                        reason="舊版 stable bootstrap 曾把此人物與對象做 plain_association 提案，可作 variant lane 的旁證，但仍非可直接鎖定的 quote-ready 證據。",
                        counterpart_names=[str(row.get("targetName") or row.get("toId") or "").strip()],
                    )
                )

        persona_paths = string_list(
            (policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}).get("personaCardPaths")
        )
        for persona_path_text in persona_paths:
            persona_path = resolve_path(persona_path_text)
            if not persona_path.exists():
                continue
            payload = read_json(persona_path)
            notes = compact_text(
                "；".join(
                    [
                        str((payload.get("sourceProfile") if isinstance(payload.get("sourceProfile"), dict) else {}).get("notes") or ""),
                        f"稱號：{payload.get('title') or ''}",
                        f"人物：{payload.get('displayName') or payload.get('name') or ''}",
                    ]
                )
            )
            if not notes:
                continue
            proposal_counter += 1
            outputs.append(
                trace_row(
                    proposal_id=f"variant-romance-anchor:{target_general_id}:{proposal_counter:04d}",
                    target_general_id=target_general_id,
                    target_name=target_name,
                    source_file=repo_relative(persona_path),
                    source_locator="sourceProfile.notes",
                    source_quote=notes,
                    proposal_stage="structured-trace-only",
                    trace_kind="persona-note",
                    reason="runtime / persona 註記已明講偏演義或遊戲擴充角色，支持其應獨立走 variant-romance lane，不宜回併 baihua primary。",
                )
            )

        runtime_relationships_path = resolve_path(
            str(
                (policy.get("inputs") if isinstance(policy.get("inputs"), dict) else {}).get("runtimeRelationshipsPath")
                or "artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles/ma-yun-lu/ma-yun-lu.relationships.json"
            )
        )
        if runtime_relationships_path.exists():
            runtime_relationships = read_json(runtime_relationships_path)
            proposal_counter += 1
            outputs.append(
                trace_row(
                    proposal_id=f"variant-romance-anchor:{target_general_id}:{proposal_counter:04d}",
                    target_general_id=target_general_id,
                    target_name=target_name,
                    source_file=repo_relative(runtime_relationships_path),
                    source_locator="relationshipCount",
                    source_quote=f"目前 runtime relationshipCount={int(runtime_relationships.get('relationshipCount') or 0)}",
                    proposal_stage="needs-new-source",
                    trace_kind="runtime-gap",
                    reason="runtime 目前沒有可用關係 anchor，表示此人物若要進一步收斂，仍需新增 romance-variant 專屬來源或人工審核書籍來源。",
                )
            )

    outputs.sort(
        key=lambda row: (
            str(row.get("targetGeneralId") or ""),
            {"quote-ready": 0, "structured-trace-only": 1, "needs-new-source": 2}.get(str(row.get("proposalStage") or ""), 9),
            str(row.get("traceKind") or ""),
            str(row.get("sourceFile") or ""),
        )
    )
    version_metadata = build_version_metadata(
        schema_version="top50-variant-romance-anchor-proposals.v1",
        artifact_paths=[policy_path, relationship_policy_path, female_profile_path],
        repo_root=REPO_ROOT,
    )
    for row in outputs:
        row.update(version_metadata)
    write_jsonl(jsonl_path, outputs)
    markdown_path.write_text(render_markdown(outputs), encoding="utf-8")

    summary = {
        "mode": "top50-variant-romance-anchor-proposals",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "policyPath": repo_relative(policy_path),
        "relationshipPolicyPath": repo_relative(relationship_policy_path),
        "targetGeneralIds": target_ids,
        "proposalCount": len(outputs),
        "proposalStageCounts": dict(sorted(Counter(str(row.get("proposalStage") or "") for row in outputs).items())),
        "traceKindCounts": dict(sorted(Counter(str(row.get("traceKind") or "") for row in outputs).items())),
        "outputs": {
            "jsonlPath": repo_relative(jsonl_path),
            "summaryPath": repo_relative(summary_path),
            "markdownPath": repo_relative(markdown_path),
        },
        "laneContract": {
            "sourceScope": policy.get("sourceScope") or "romance-variant-only",
            "doNotMergeIntoBaihuaPrimary": bool(policy.get("doNotMergeIntoBaihuaPrimary", True)),
        },
    }
    write_json(summary_path, summary)
    print(
        "[build_top50_variant_romance_anchor_proposals] "
        f"proposalCount={len(outputs)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
