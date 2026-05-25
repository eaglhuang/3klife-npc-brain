from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_SKILL_OUTPUT_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-focus-skill-output.jsonl"
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge and normalize baihua bootstrap relationship candidates.")
    parser.add_argument("--skill-output-path", default=str(DEFAULT_SKILL_OUTPUT_PATH))
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-file-name", default="merged-bootstrap-candidates.jsonl")
    parser.add_argument("--summary-file-name", default="merged-bootstrap-candidates-summary.json")
    parser.add_argument("--review-ready-support-min", type=int, default=2)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


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


def stable_trust_key(relationship_type: str, from_id: str, to_id: str, symmetric_types: set[str]) -> tuple[str, str, str, str]:
    left = from_id.strip()
    right = to_id.strip()
    if relationship_type in symmetric_types:
        left, right = sorted([left, right])
    trust_key = f"relationship|{relationship_type}|{left}|{right}"
    return trust_key, left, right, relationship_type


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


def main() -> int:
    args = parse_args()
    skill_output_path = Path(args.skill_output_path).resolve()
    policy_path = Path(args.policy_path).resolve()
    output_root = Path(args.output_root).resolve()
    output_path = output_root / args.output_file_name
    summary_path = output_root / args.summary_file_name

    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    policy = read_json(policy_path)
    relation_policy = policy.get("relationshipTypes") if isinstance(policy.get("relationshipTypes"), dict) else {}
    symmetric_types = {str(item).strip() for item in relation_policy.get("symmetric") or [] if str(item or "").strip()}
    allowed_types = {str(item).strip() for item in relation_policy.get("allowed") or [] if str(item or "").strip()}
    if not allowed_types:
        raise ValueError(f"policy relationshipTypes.allowed missing: {policy_path}")

    focus_rows = read_jsonl(skill_output_path)
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    relationship_input_counter: Counter[str] = Counter()
    skipped_rows = 0

    for focus_row in focus_rows:
        focus_id = str(focus_row.get("focusGeneralId") or "").strip()
        relationships = focus_row.get("relationships")
        if not isinstance(relationships, list):
            continue
        for item in relationships:
            if not isinstance(item, dict):
                continue
            relationship_type = str(item.get("relationshipType") or "").strip()
            from_id = str(item.get("fromId") or "").strip()
            to_id = str(item.get("toId") or "").strip()
            quote = str(item.get("evidenceQuoteZhTw") or "").strip()
            chapter_ref = str(item.get("chapterRef") or "").strip()
            passage_ref = str(item.get("sourcePassageRef") or "").strip()
            if not relationship_type or not from_id or not to_id:
                skipped_rows += 1
                continue
            if relationship_type not in allowed_types:
                skipped_rows += 1
                continue
            if not quote or not passage_ref:
                skipped_rows += 1
                continue
            relationship_input_counter[relationship_type] += 1
            trust_key, norm_from_id, norm_to_id, norm_type = stable_trust_key(relationship_type, from_id, to_id, symmetric_types)
            key = (trust_key, norm_from_id, norm_to_id, norm_type)
            if key not in grouped:
                grouped[key] = {
                    "trustKey": trust_key,
                    "relationshipType": norm_type,
                    "fromId": norm_from_id,
                    "toId": norm_to_id,
                    "sourceMode": "top50-baihua-bootstrap",
                    "supports": [],
                    "focusGeneralIds": [],
                    "timeScopeValues": [],
                    "confidenceValues": [],
                    "evidenceQuotes": [],
                    "chapterRefs": [],
                    "sourcePassageRefs": [],
                }
            bucket = grouped[key]
            bucket["supports"].append(item)
            bucket["focusGeneralIds"].append(focus_id)
            bucket["timeScopeValues"].append(str(item.get("timeScopeZhTw") or "").strip())
            bucket["confidenceValues"].append(float(item.get("confidence") or 0.0))
            bucket["evidenceQuotes"].append(quote)
            bucket["chapterRefs"].append(chapter_ref)
            bucket["sourcePassageRefs"].append(passage_ref)

    merged_rows: list[dict[str, Any]] = []
    stage_counter: Counter[str] = Counter()
    for (_key, bucket) in grouped.items():
        support_count = len(bucket["supports"])
        stage = "review-ready" if support_count >= max(1, int(args.review_ready_support_min)) else "bootstrap-candidate"
        stage_counter[stage] += 1
        time_scope_values = [value for value in bucket["timeScopeValues"] if value]
        time_scope = Counter(time_scope_values).most_common(1)[0][0] if time_scope_values else "時段未明"
        confidence_values = [float(value) for value in bucket["confidenceValues"] if float(value) > 0.0]
        confidence_aggregate = round(mean(confidence_values), 4) if confidence_values else 0.0

        merged_rows.append(
            {
                "trustKey": bucket["trustKey"],
                "sourceMode": "top50-baihua-bootstrap",
                "bootstrapStage": stage,
                "supportCount": support_count,
                "focusGeneralIds": sorted(unique_strings(bucket["focusGeneralIds"])),
                "relationshipType": bucket["relationshipType"],
                "fromId": bucket["fromId"],
                "toId": bucket["toId"],
                "evidenceQuotes": unique_strings(bucket["evidenceQuotes"]),
                "chapterRefs": sorted(unique_strings(bucket["chapterRefs"])),
                "sourcePassageRefs": sorted(unique_strings(bucket["sourcePassageRefs"])),
                "timeScopeZhTw": time_scope,
                "confidenceAggregate": confidence_aggregate,
                "conflictFlags": [],
                "canonicalWrites": False,
            }
        )

    merged_rows.sort(key=lambda row: (str(row.get("relationshipType")), str(row.get("fromId")), str(row.get("toId"))))
    write_jsonl(output_path, merged_rows)
    summary = {
        "mode": "baihua-bootstrap-merge-normalize",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "skillOutputPath": str(skill_output_path),
            "policyPath": str(policy_path),
        },
        "outputs": {
            "mergedCandidatePath": str(output_path),
            "summaryPath": str(summary_path),
            "mergedCount": len(merged_rows),
            "stageCounts": dict(sorted(stage_counter.items())),
            "inputRelationshipTypeCounts": dict(sorted(relationship_input_counter.items())),
            "skippedRelationshipRows": skipped_rows,
        },
    }
    write_json(summary_path, summary)
    print(f"[merge_baihua_bootstrap_candidates] wrote {output_path}")
    print(f"[merge_baihua_bootstrap_candidates] wrote {summary_path}")
    print(
        "[merge_baihua_bootstrap_candidates] "
        f"merged={len(merged_rows)} reviewReady={stage_counter.get('review-ready', 0)} "
        f"bootstrapCandidate={stage_counter.get('bootstrap-candidate', 0)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
