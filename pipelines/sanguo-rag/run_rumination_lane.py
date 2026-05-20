"""run_rumination_lane.py — M6-0602

主動重驗既有 A，讓系統能降級，不只靠被動 ledger。
計畫書偽代碼 D 段實作。
支援四個 cohort：single-source A、old-low-score A、missing-proof A、A-romance female。
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

# thresholds
OLD_LOW_SCORE_THRESHOLD: float = 65.0   # historicalTrustScore below this → old-low-score cohort
DOWNGRADE_SCORE_DELTA: float = -10.0    # score adjustment for downgrade verdict
ESCALATE_SCORE_DELTA: float = 5.0       # score adjustment for escalate verdict


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ── cohort classifiers ────────────────────────────────────────────────────────

def classify_cohort_reasons(row: dict[str, Any]) -> list[str]:
    """Return list of cohort reasons that apply to this row."""
    grade = row.get("grade", row.get("gradeType", ""))
    reasons: list[str] = []

    # only process A-grade rows
    if not grade.startswith("A"):
        return reasons

    # 1. single-source A
    if int(row.get("externalDistinctHistoryFamilyCount", 0)) == 1:
        reasons.append("single-source-A")

    # 2. old-low-score A
    trust_score = float(row.get("historicalTrustScore", row.get("historyTrustScore", 100.0)))
    if trust_score < OLD_LOW_SCORE_THRESHOLD:
        reasons.append("old-low-score-A")

    # 3. missing-proof A: no locator or no textHash
    has_locator = bool(row.get("locator", "").strip())
    has_text_hash = bool(row.get("textHash", "").strip())
    if not has_locator or not has_text_hash:
        reasons.append("missing-proof-A")

    # 4. A-romance female
    if grade == "A-romance" and row.get("gender", "").lower() in ("female", "f", "女"):
        reasons.append("A-romance-female")

    return reasons


# ── rumination verdict ────────────────────────────────────────────────────────

def compute_rumination_verdict(
    row: dict[str, Any],
    cohort_reasons: list[str],
) -> tuple[str, float]:
    """
    Compute verdict and new score.
    Returns (verdict, newScore) where verdict is 'keep' | 'downgrade' | 'escalate'.
    """
    base_score = float(
        row.get("historicalTrustScore", row.get("historyTrustScore", 70.0))
    )
    has_locator = bool(row.get("locator", "").strip())
    has_text_hash = bool(row.get("textHash", "").strip())
    distinct_sources = int(row.get("externalDistinctHistoryFamilyCount", 0))

    # escalate: has full proof AND multiple sources
    if has_locator and has_text_hash and distinct_sources >= 2:
        return "escalate", min(100.0, base_score + ESCALATE_SCORE_DELTA)

    # downgrade triggers
    downgrade_triggers = 0
    if "missing-proof-A" in cohort_reasons:
        downgrade_triggers += 2
    if "single-source-A" in cohort_reasons:
        downgrade_triggers += 1
    if "old-low-score-A" in cohort_reasons:
        downgrade_triggers += 1
    if "A-romance-female" in cohort_reasons:
        # romance female: softer trigger, only downgrade if also missing proof
        if "missing-proof-A" in cohort_reasons:
            downgrade_triggers += 1

    if downgrade_triggers >= 2:
        return "downgrade", max(0.0, base_score + DOWNGRADE_SCORE_DELTA)

    return "keep", base_score


# ── core runner ───────────────────────────────────────────────────────────────

def run_rumination(
    scoreboard_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (all_audits, downgrade_ledger).
    downgrade_ledger contains only verdict=downgrade entries.
    """
    all_audits: list[dict[str, Any]] = []
    downgrade_ledger: list[dict[str, Any]] = []

    for row in scoreboard_rows:
        cohort_reasons = classify_cohort_reasons(row)
        if not cohort_reasons:
            continue  # not in any rumination cohort

        general_id = row.get("generalId", "")
        verdict, new_score = compute_rumination_verdict(row, cohort_reasons)

        audit: dict[str, Any] = {
            "generalId": general_id,
            "generalName": row.get("generalName", general_id),
            "cohortReason": cohort_reasons,
            "grade": row.get("grade", row.get("gradeType", "")),
            "verdict": verdict,
            "previousScore": float(
                row.get("historicalTrustScore", row.get("historyTrustScore", 70.0))
            ),
            "newScore": round(new_score, 2),
            "canonicalWrites": False,
            "auditedAt": utc_now(),
        }
        all_audits.append(audit)

        if verdict == "downgrade":
            downgrade_entry = dict(audit)
            downgrade_entry["stagingStatus"] = "pending-human-review"
            downgrade_ledger.append(downgrade_entry)

    return all_audits, downgrade_ledger


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Actively re-validate A-grade generals for potential downgrade. "
            "Supports four cohorts: single-source-A, old-low-score-A, missing-proof-A, A-romance-female. "
            "Outputs rumination audits and a downgrade staging ledger. canonicalWrites=false."
        )
    )
    p.add_argument(
        "--scoreboard-jsonl",
        required=True,
        help="Full roster scoreboard JSONL.",
    )
    p.add_argument(
        "--output-root",
        required=True,
        help="Directory for rumination audit output.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    scoreboard_path = resolve_path(args.scoreboard_jsonl)
    output_root = resolve_path(args.output_root)

    scoreboard_rows = read_jsonl(scoreboard_path)
    print(f"[rumination] Total scoreboard rows: {len(scoreboard_rows)}")

    all_audits, downgrade_ledger = run_rumination(scoreboard_rows)

    # cohort breakdown
    cohort_counts: dict[str, int] = {}
    for audit in all_audits:
        for reason in audit["cohortReason"]:
            cohort_counts[reason] = cohort_counts.get(reason, 0) + 1

    print(f"[rumination] Cohort members audited: {len(all_audits)}")
    for reason, cnt in sorted(cohort_counts.items()):
        print(f"[rumination]   {reason}: {cnt}")

    verdict_counts: dict[str, int] = {}
    for audit in all_audits:
        v = audit["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
    print(f"[rumination] Verdicts: {verdict_counts}")

    # write outputs
    audit_path = output_root / "rumination-audit.jsonl"
    downgrade_path = output_root / "rumination-downgrade-ledger.jsonl"
    write_jsonl(audit_path, all_audits)
    write_jsonl(downgrade_path, downgrade_ledger)

    summary = {
        "generatedAt": utc_now(),
        "totalAudited": len(all_audits),
        "cohortCounts": cohort_counts,
        "verdictCounts": verdict_counts,
        "downgradeStagingCount": len(downgrade_ledger),
        "canonicalWrites": False,
        "auditPath": str(audit_path),
        "downgradeLedgerPath": str(downgrade_path),
    }
    write_json(output_root / "rumination-summary.json", summary)

    print(f"[rumination] Downgrade staging entries: {len(downgrade_ledger)}")
    print(f"[rumination] Audit: {audit_path}")
    print(f"[rumination] Downgrade ledger: {downgrade_path}")


if __name__ == "__main__":
    main()
