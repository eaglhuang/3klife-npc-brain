from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from run_relationship_semantic_review_cache import evidence_packets_from_cache, semantic_runner_policy


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = Path("data/sanguo/policies/policy-relationship-trust-zone.json")
DEFAULT_SKILL_PATH = Path("integrations/codex-skills/sanguo-relationship-semantic-review/SKILL.md")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def default_paths(policy: dict[str, Any], output_root: Path) -> dict[str, Path]:
    outputs = object_map(policy.get("outputs"))
    inputs = object_map(policy.get("inputs"))
    semantic_policy = semantic_runner_policy(policy, "primary")
    bridge_policy = object_map(semantic_policy.get("codexSkillBridge"))
    bridge_root = resolve_path(output_root / "codex-skill-review")
    return {
        "queue": resolve_path(output_root / str(semantic_policy.get("queueFileName") or "relationship-trust-zone.semantic-review-queue.jsonl")),
        "cache": resolve_path(output_root / str(semantic_policy.get("cacheFileName") or "relationship-trust-zone.semantic-review-cache.jsonl")),
        "evidence": resolve_path(str(inputs.get("semanticReviewEvidencePath") or output_root / str(semantic_policy.get("evidenceFileName") or "relationship-trust-zone.semantic-review-evidence.jsonl"))),
        "summary": resolve_path(output_root / "codex-skill-review-summary.json"),
        "packet": resolve_path(bridge_root / str(bridge_policy.get("packetFileName") or "codex-relationship-semantic-review-packet.json")),
        "packet_md": resolve_path(bridge_root / str(bridge_policy.get("packetMarkdownFileName") or "codex-relationship-semantic-review-packet.md")),
        "reviewed_cache": resolve_path(bridge_root / str(bridge_policy.get("reviewedCacheFileName") or "codex-relationship-semantic-reviewed-cache.jsonl")),
        "skill": resolve_path(str(bridge_policy.get("skillPath") or DEFAULT_SKILL_PATH)),
        "relationship_output_root": resolve_path(str(outputs.get("outputRoot") or "artifacts/data-pipeline/sanguo-rag/extracted/relationship-trust-zone")),
    }


def compact_unit(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "semanticReviewUnitId": unit.get("semanticReviewUnitId"),
        "promptVersion": unit.get("promptVersion"),
        "sentenceHash": unit.get("sentenceHash"),
        "sourceSentence": unit.get("sourceSentence"),
        "sentenceQualityScore": unit.get("sentenceQualityScore"),
        "sourceRefs": unit.get("sourceRefs") or [],
        "allowedEntities": unit.get("allowedEntities") or [],
        "allowedRelationshipTypes": unit.get("allowedRelationshipTypes") or [],
        "candidates": unit.get("candidates") or [],
        "canonicalWrites": False,
    }


def render_packet_md(packet: dict[str, Any]) -> str:
    lines = [
        "# Codex 句級關係語意審查包",
        "",
        f"- 產生時間：`{packet['generatedAt']}`",
        f"- 使用 skill：`{packet['codexSkillPath']}`",
        f"- 待審句子數：`{len(packet['entries'])}`",
        f"- 輸出 JSONL：`{packet['expectedReviewedCachePath']}`",
        "- 原則：只根據原文句子判斷，不憑記憶補事實；不確定就寫 `not_enough_context`。",
        "- 原則：`canonicalWrites=false`；這只是 evidence/proposal，不直接寫正式關係白名單。",
        "",
        "## 輸出要求",
        "",
        "請依 `sanguo-relationship-semantic-review` skill，為每個 `entries[]` 產生一行 reviewed cache JSONL。",
        "",
        "## 待審項目摘要",
        "",
    ]
    for index, entry in enumerate(packet.get("entries") or [], 1):
        candidates = entry.get("candidates") or []
        candidate_labels = [
            f"{candidate.get('relationshipType')}:{candidate.get('fromId')}->{candidate.get('toId')}"
            for candidate in candidates
            if isinstance(candidate, dict)
        ]
        lines.extend(
            [
                f"### {index}. `{entry.get('semanticReviewUnitId')}`",
                "",
                f"- 原文：{entry.get('sourceSentence')}",
                f"- 候選：{'; '.join(candidate_labels)}",
                "",
            ]
        )
    return "\n".join(lines)


def export_packet(args: argparse.Namespace, policy: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    queue_path = resolve_path(args.queue or paths["queue"])
    packet_path = resolve_path(args.packet_out or paths["packet"])
    packet_md_path = resolve_path(args.packet_md_out or paths["packet_md"])
    reviewed_cache_path = resolve_path(args.reviewed_cache or paths["reviewed_cache"])
    queue_rows = read_jsonl(queue_path)
    selected_rows = queue_rows[: args.limit] if args.limit > 0 else queue_rows
    packet = {
        "schemaVersion": "codex-relationship-semantic-review-packet.v1",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "sourceQueuePath": repo_relative(queue_path),
        "codexSkillPath": repo_relative(resolve_path(args.skill_path or paths["skill"])),
        "expectedReviewedCachePath": repo_relative(reviewed_cache_path),
        "entryCount": len(selected_rows),
        "entries": [compact_unit(row) for row in selected_rows],
    }
    write_json(packet_path, packet)
    packet_md_path.parent.mkdir(parents=True, exist_ok=True)
    packet_md_path.write_text(render_packet_md(packet), encoding="utf-8")
    return {
        "mode": "export",
        "queuePath": repo_relative(queue_path),
        "packetPath": repo_relative(packet_path),
        "packetMarkdownPath": repo_relative(packet_md_path),
        "expectedReviewedCachePath": repo_relative(reviewed_cache_path),
        "exportedUnitCount": len(selected_rows),
        "canonicalWrites": False,
    }


def validate_reviewed_cache_row(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    unit_id = str(row.get("semanticReviewUnitId") or "").strip()
    if not unit_id:
        errors.append("missing semanticReviewUnitId")
    if not bool_value(row.get("semanticReviewPerformed")):
        errors.append(f"{unit_id}: semanticReviewPerformed must be true")
    if bool_value(row.get("canonicalWrites")):
        errors.append(f"{unit_id}: canonicalWrites must be false")
    relationships = row.get("relationships")
    if not isinstance(relationships, list):
        errors.append(f"{unit_id}: relationships must be a list")
        return errors
    candidate_keys = {
        str(candidate.get("trustKey") or "").strip()
        for candidate in row.get("candidates") or []
        if isinstance(candidate, dict) and str(candidate.get("trustKey") or "").strip()
    }
    allowed_verdicts = {"supported", "contradicted", "uncertain", "not_enough_context"}
    for index, relation in enumerate(relationships, 1):
        if not isinstance(relation, dict):
            errors.append(f"{unit_id}: relationship #{index} is not an object")
            continue
        trust_key = str(relation.get("trustKey") or "").strip()
        if not trust_key:
            errors.append(f"{unit_id}: relationship #{index} missing trustKey")
        if candidate_keys and trust_key not in candidate_keys:
            errors.append(f"{unit_id}: relationship #{index} trustKey not in candidates")
        verdict = str(relation.get("verdict") or "").strip()
        if verdict not in allowed_verdicts:
            errors.append(f"{unit_id}: relationship #{index} invalid verdict {verdict}")
        try:
            score = float(relation.get("semanticTrustScore"))
        except (TypeError, ValueError):
            errors.append(f"{unit_id}: relationship #{index} semanticTrustScore must be numeric")
            continue
        if score < 0.0 or score > 100.0:
            errors.append(f"{unit_id}: relationship #{index} semanticTrustScore outside 0-100")
        if bool_value(relation.get("canonicalWrites")):
            errors.append(f"{unit_id}: relationship #{index} canonicalWrites must be false")
    return errors


def import_reviewed_cache(args: argparse.Namespace, policy: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    reviewed_cache_path = resolve_path(args.reviewed_cache or paths["reviewed_cache"])
    cache_path = resolve_path(args.cache or paths["cache"])
    evidence_path = resolve_path(args.evidence_out or paths["evidence"])
    reviewed_rows = read_jsonl(reviewed_cache_path)
    validation_errors: list[str] = []
    for row in reviewed_rows:
        validation_errors.extend(validate_reviewed_cache_row(row))
    if validation_errors:
        raise ValueError("invalid Codex reviewed cache rows: " + "; ".join(validation_errors[:10]))

    existing_cache = {str(row.get("semanticReviewUnitId") or ""): row for row in read_jsonl(cache_path)}
    for row in reviewed_rows:
        unit_id = str(row.get("semanticReviewUnitId") or "").strip()
        if unit_id:
            existing_cache[unit_id] = row
    merged_cache_rows = sorted(existing_cache.values(), key=lambda item: str(item.get("semanticReviewUnitId") or ""))
    skip_evidence_write = bool(args.skip_evidence_write)
    if not args.dry_run:
        write_jsonl(cache_path, merged_cache_rows)
        evidence_packets = evidence_packets_from_cache(merged_cache_rows, policy)
        if not skip_evidence_write:
            write_jsonl(evidence_path, evidence_packets)
    else:
        evidence_packets = evidence_packets_from_cache(merged_cache_rows, policy)
    return {
        "mode": "import",
        "dryRun": bool(args.dry_run),
        "skipEvidenceWrite": skip_evidence_write,
        "reviewedCachePath": repo_relative(reviewed_cache_path),
        "cachePath": repo_relative(cache_path),
        "evidencePath": repo_relative(evidence_path),
        "importedReviewedUnitCount": len(reviewed_rows),
        "mergedCacheUnitCount": len(merged_cache_rows),
        "evidencePacketCount": len(evidence_packets),
        "canonicalWrites": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export/import Codex-side semantic review packets for Sanguo relationship trust-zone evidence.")
    parser.add_argument("--mode", choices=["export", "import"], required=True)
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--queue", default="")
    parser.add_argument("--cache", default="")
    parser.add_argument("--packet-out", default="")
    parser.add_argument("--packet-md-out", default="")
    parser.add_argument("--reviewed-cache", default="")
    parser.add_argument("--evidence-out", default="")
    parser.add_argument("--skill-path", default="")
    parser.add_argument("--summary-out", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-evidence-write", action="store_true", help="Import reviewed cache rows without rewriting semantic evidence output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = resolve_path(args.policy)
    policy = read_json(policy_path)
    output_root = resolve_path(args.output_root or object_map(policy.get("outputs")).get("outputRoot") or "artifacts/data-pipeline/sanguo-rag/extracted/relationship-trust-zone")
    paths = default_paths(policy, output_root)
    summary_path = resolve_path(args.summary_out or paths["summary"])
    if args.mode == "export":
        summary = export_packet(args, policy, paths)
    else:
        summary = import_reviewed_cache(args, policy, paths)
    summary.update({"generatedAt": utc_now(), "policyPath": repo_relative(policy_path)})
    write_json(summary_path, summary)
    print(
        "[run_codex_relationship_semantic_review_bridge] "
        f"mode={summary['mode']} canonicalWrites={summary['canonicalWrites']}"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
