from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_relationship_claim_graph import (
    A_BASELINE_GRADES,
    A_CANON_GRADES,
    A_HISTORY_GRADES,
    A_ROMANCE_GRADES,
    HISTORY_SOURCE_FAMILIES,
    PROMOTABLE_HISTORY_TYPES,
    RELATIONSHIP_OUTPUT_FILES,
    has_quote_locator_hash,
)
from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_CLAIM_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/relationship-claim-graph")
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/relationship-validation-pass-study")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze relationship claim pass ratio, near-A blockers, and conflict pressure."
    )
    parser.add_argument("--claim-root", default=str(DEFAULT_CLAIM_ROOT))
    parser.add_argument("--claims")
    parser.add_argument("--rejected")
    parser.add_argument("--summary")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--round-id", default="current")
    parser.add_argument("--queue-limit", type=int, default=500)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def resolve_path(path: str | Path) -> Path:
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj
    return REPO_ROOT / path_obj


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            row["_sourceLine"] = line_no
            rows.append(row)
    return rows


def write_json(path: Path, payload: dict[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists. Re-run with --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists. Re-run with --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def write_text(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists. Re-run with --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole * 100.0, 2)


def has_cross_or_internal_trust(row: dict[str, Any]) -> bool:
    signals = {str(item) for item in row.get("confidenceSignals") or []}
    trace = {str(item) for item in row.get("promotionTrace") or []}
    cross_families = {str(item) for item in row.get("crossSiteSourceFamilies") or [] if str(item).strip()}
    return (
        "cross-source" in signals
        or "internal-external" in signals
        or "cross-family-history" in trace
        or len(cross_families) >= 2
    )


def blocker_reasons(row: dict[str, Any]) -> list[str]:
    grade = str(row.get("claimGrade") or "")
    rel_type = str(row.get("type") or "")
    source_family = str(row.get("sourceFamily") or "")
    blockers: list[str] = []
    if not row.get("directPairSignal"):
        blockers.append("missing_direct_pair_signal")
    if not has_quote_locator_hash(row):
        blockers.append("missing_quote_locator_hash")
    if rel_type not in PROMOTABLE_HISTORY_TYPES:
        blockers.append("non_promotable_relationship_type")
    if row.get("pairRelationRequired") and not row.get("pairRelationSignal"):
        blockers.append("missing_pair_relation_cue")
    if grade == "B-history":
        if source_family not in HISTORY_SOURCE_FAMILIES:
            blockers.append("history_source_family_not_primary")
        if not has_cross_or_internal_trust(row):
            blockers.append("missing_cross_or_internal_trust_signal")
    return blockers or ["policy_rank_other"]


def is_near_a(row: dict[str, Any]) -> bool:
    grade = str(row.get("claimGrade") or "")
    return grade in {"B-history", "B-romance"} and bool(row.get("directPairSignal")) and has_quote_locator_hash(row)


def is_pair_cue_repairable(row: dict[str, Any]) -> bool:
    return blocker_reasons(row) == ["missing_pair_relation_cue"]


def counter_dict(counter: Counter[Any], limit: int | None = None) -> dict[str, int]:
    items = counter.most_common(limit) if limit else counter.most_common()
    return {str(key): int(value) for key, value in items}


def top_group(rows: list[dict[str, Any]], keys: tuple[str, ...], limit: int = 20) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        key = "|".join(str(row.get(item) or "") for item in keys)
        counter[key] += 1
    return counter_dict(counter, limit)


def cue_binding_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        cue = row.get("pairRelationCue")
        if not isinstance(cue, dict):
            continue
        key = "|".join(
            [
                str(row.get("claimGrade") or ""),
                str(row.get("type") or ""),
                str(cue.get("binding") or ""),
            ]
        )
        counter[key] += 1
    return counter_dict(counter, 30)


def conflict_summary(summary: dict[str, Any]) -> dict[str, Any]:
    conflicts = summary.get("conflicts") if isinstance(summary.get("conflicts"), list) else []
    reason_counts = Counter(str(row.get("reason") or "") for row in conflicts if isinstance(row, dict))
    grade_counts = Counter(str(row.get("conflictingGrade") or "") for row in conflicts if isinstance(row, dict))
    type_counts = Counter(str(row.get("conflictingType") or "") for row in conflicts if isinstance(row, dict))
    pair_counts = Counter(str(row.get("pairKey") or "") for row in conflicts if isinstance(row, dict))
    return {
        "conflictCount": len(conflicts),
        "reasonCounts": counter_dict(reason_counts),
        "conflictingGradeCounts": counter_dict(grade_counts),
        "conflictingTypeCounts": counter_dict(type_counts, 20),
        "topPairs": counter_dict(pair_counts, 20),
    }


def repair_queue(rows: list[dict[str, Any]], queue_limit: int) -> list[dict[str, Any]]:
    candidates = [row for row in rows if is_near_a(row) and is_pair_cue_repairable(row)]
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("claimGrade") or "") == "B-history" else 1,
            str(row.get("type") or ""),
            str(row.get("sourceFamily") or ""),
            str(row.get("claimId") or ""),
        )
    )
    queue: list[dict[str, Any]] = []
    for row in candidates[: max(queue_limit, 0)]:
        queue.append(
            {
                "claimId": row.get("claimId"),
                "claimGrade": row.get("claimGrade"),
                "fromId": row.get("fromId"),
                "toId": row.get("toId"),
                "type": row.get("type"),
                "quote": row.get("quote"),
                "sourceFamily": row.get("sourceFamily"),
                "sourceLayer": row.get("sourceLayer"),
                "locator": row.get("locator"),
                "textHash": row.get("textHash"),
                "sourceFile": row.get("sourceFile"),
                "sourceLine": row.get("_sourceLine"),
                "promotionTrace": row.get("promotionTrace"),
                "pairRelationCue": row.get("pairRelationCue"),
                "repairAction": "repair_pair_relation_cue",
                "reviewStatus": "deterministic-repair-candidate",
            }
        )
    return queue


def markdown(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    near_a = summary["nearA"]
    lines = [
        "# Relationship Validation Pass Study",
        "",
        f"- Round: `{summary['roundId']}`",
        f"- Claims: `{metrics['claimCount']}`",
        f"- Raw rejected candidates: `{metrics['rejectedCount']}`",
        f"- A-canon claim pass ratio: `{metrics['aCanonClaimPassRatio']}%`",
        f"- A-canon + A-baseline claim pass ratio: `{metrics['aCanonPlusBaselineClaimPassRatio']}%`",
        f"- Raw extractor attempt pass ratio: `{metrics['aCanonRawAttemptPassRatio']}%`",
        f"- Subject-bound pair cues: `{metrics['subjectBoundPairCueCount']}`",
        "",
        "## Grade Counts",
        "",
    ]
    for key, value in summary["gradeCounts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Near-A Blockers",
            "",
            f"- Near-A rows: `{near_a['nearACount']}`",
            f"- Pair-cue repairable rows: `{near_a['pairCueRepairableCount']}`",
            f"- Pair-cue repairable uplift vs claims: `{near_a['pairCueRepairableClaimUpliftRatio']}%`",
            "",
        ]
    )
    for key, value in summary["nearABlockerCounts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Pair-Cue Repairable Types", ""])
    for key, value in summary["pairCueRepairableByType"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Subject-Bound Cue Bindings", ""])
    for key, value in summary["pairRelationCueBindingCounts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Unsafe History Source Families", ""])
    for key, value in summary["unsafeHistorySourceFamilies"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Conflict Pressure", ""])
    lines.append(f"- Conflicts: `{summary['conflicts']['conflictCount']}`")
    for key, value in summary["conflicts"]["topPairs"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Recommendations", ""])
    for item in summary["recommendations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    claim_root = resolve_path(args.claim_root)
    claims_path = resolve_path(args.claims) if args.claims else claim_root / RELATIONSHIP_OUTPUT_FILES["all"]
    rejected_path = resolve_path(args.rejected) if args.rejected else claim_root / RELATIONSHIP_OUTPUT_FILES["rejected"]
    summary_path = resolve_path(args.summary) if args.summary else claim_root / RELATIONSHIP_OUTPUT_FILES["summary"]
    output_root = resolve_path(args.output_root)

    claims = read_jsonl(claims_path)
    rejected = read_jsonl(rejected_path)
    claim_summary = read_json(summary_path)
    grade_counts = Counter(str(row.get("claimGrade") or "unknown") for row in claims)
    reject_counts = Counter(str(row.get("reason") or "unknown") for row in rejected)
    near_a_rows = [row for row in claims if is_near_a(row)]
    pair_cue_repairable_rows = [row for row in near_a_rows if is_pair_cue_repairable(row)]
    subject_bound_cue_rows = [row for row in claims if isinstance(row.get("pairRelationCue"), dict)]
    blocker_counts = Counter("|".join(blocker_reasons(row)) for row in near_a_rows)
    unsafe_history_rows = [
        row
        for row in near_a_rows
        if str(row.get("claimGrade") or "") == "B-history"
        and str(row.get("sourceFamily") or "") not in HISTORY_SOURCE_FAMILIES
    ]
    a_canon_count = sum(1 for row in claims if str(row.get("claimGrade") or "") in A_CANON_GRADES)
    a_baseline_count = sum(1 for row in claims if str(row.get("claimGrade") or "") in A_BASELINE_GRADES)
    a_history_count = sum(1 for row in claims if str(row.get("claimGrade") or "") in A_HISTORY_GRADES)
    a_romance_count = sum(1 for row in claims if str(row.get("claimGrade") or "") in A_ROMANCE_GRADES)
    attempt_count = len(claims) + len(rejected)

    payload = {
        "version": "1.0.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "roundId": args.round_id,
        "canonicalWrites": False,
        "inputs": {
            "claimsPath": repo_relative(claims_path),
            "rejectedPath": repo_relative(rejected_path),
            "summaryPath": repo_relative(summary_path),
        },
        "outputs": {
            "summary": repo_relative(output_root / f"{args.round_id}-relationship-validation-pass-study.json"),
            "markdown": repo_relative(output_root / f"{args.round_id}-relationship-validation-pass-study.md"),
            "pairCueRepairQueue": repo_relative(output_root / f"{args.round_id}-pair-cue-repair-queue.jsonl"),
        },
        "metrics": {
            "claimCount": len(claims),
            "rejectedCount": len(rejected),
            "rawAttemptCount": attempt_count,
            "aHistoryCount": a_history_count,
            "aRomanceCount": a_romance_count,
            "aCanonCount": a_canon_count,
            "aBaselineCount": a_baseline_count,
            "aCanonClaimPassRatio": pct(a_canon_count, len(claims)),
            "aCanonPlusBaselineClaimPassRatio": pct(a_canon_count + a_baseline_count, len(claims)),
            "aCanonRawAttemptPassRatio": pct(a_canon_count, attempt_count),
            "subjectBoundPairCueCount": len(subject_bound_cue_rows),
        },
        "gradeCounts": counter_dict(grade_counts),
        "rejectionReasonCounts": counter_dict(reject_counts),
        "nearA": {
            "nearACount": len(near_a_rows),
            "pairCueRepairableCount": len(pair_cue_repairable_rows),
            "unsafeHistorySourceCount": len(unsafe_history_rows),
            "pairCueRepairableClaimUpliftRatio": pct(len(pair_cue_repairable_rows), len(claims)),
            "pairCueRepairableRawAttemptUpliftRatio": pct(len(pair_cue_repairable_rows), attempt_count),
        },
        "nearABlockerCounts": counter_dict(blocker_counts, 30),
        "nearAByGradeType": top_group(near_a_rows, ("claimGrade", "type"), 30),
        "nearABySourceFamilyType": top_group(near_a_rows, ("sourceFamily", "type"), 30),
        "pairCueRepairableByType": top_group(pair_cue_repairable_rows, ("claimGrade", "type"), 30),
        "pairCueRepairableBySourceFamily": top_group(pair_cue_repairable_rows, ("sourceFamily", "type"), 30),
        "pairRelationCueBindingCounts": cue_binding_counts(claims),
        "unsafeHistorySourceFamilies": top_group(unsafe_history_rows, ("sourceFamily", "type"), 30),
        "conflicts": conflict_summary(claim_summary),
        "recommendations": [
            "Treat normalized claim pass ratio separately from raw extractor rejection ratio.",
            "Prioritize deterministic pair-relation cue repair for near-A B-history and B-romance rows.",
            "Do not promote non-primary history source families into A-history; use them as cue seeds or review queues.",
            "Split pair-global type-family conflicts into event-scoped or time-scoped validation before using them as release blockers.",
        ],
    }

    queue = repair_queue(near_a_rows, args.queue_limit)
    summary_output = output_root / f"{args.round_id}-relationship-validation-pass-study.json"
    markdown_output = output_root / f"{args.round_id}-relationship-validation-pass-study.md"
    queue_output = output_root / f"{args.round_id}-pair-cue-repair-queue.jsonl"
    write_json(summary_output, payload, args.overwrite)
    write_text(markdown_output, markdown(payload), args.overwrite)
    write_jsonl(queue_output, queue, args.overwrite)
    print(f"[analyze_relationship_validation_pass_ratio] wrote {summary_output}")
    print(f"[analyze_relationship_validation_pass_ratio] wrote {markdown_output}")
    print(f"[analyze_relationship_validation_pass_ratio] wrote {queue_output}")
    print(
        "[analyze_relationship_validation_pass_ratio] "
        f"claims={len(claims)} aCanon={a_canon_count} nearA={len(near_a_rows)} "
        f"pairCueRepairable={len(pair_cue_repairable_rows)} conflicts={payload['conflicts']['conflictCount']}"
    )


if __name__ == "__main__":
    main()
