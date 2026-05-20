"""noise_source_proposal.py — M5-0504

將高散度 unresolved、缺 sourceRef 事件、低效外部來源都轉成 proposal。
產出三種 proposal：noise、sourceRef、sourceStatus。
"""
from __future__ import annotations
import argparse, json, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

# thresholds
NOISE_SCATTER_THRESHOLD: float = 0.7   # label scatter score above this → noise proposal
SOURCE_DOWNGRADE_THRESHOLD: float = 0.3  # siteReliabilityMultiplier below this → downgrade proposal
SOURCE_LOW_QUALITY_MIN_SAMPLES: int = 5  # minimum samples before considering downgrade


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


# ── proposal builders ─────────────────────────────────────────────────────────

def build_noise_proposals(
    scoreboard_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Proposal type: 'noise'
    Trigger: labelScatterScore > NOISE_SCATTER_THRESHOLD
    """
    proposals: list[dict[str, Any]] = []
    for row in scoreboard_rows:
        scatter_score = float(row.get("labelScatterScore", 0.0))
        if scatter_score <= NOISE_SCATTER_THRESHOLD:
            continue
        general_id = row.get("generalId", "")
        proposals.append(
            {
                "proposalId": f"noise-{uuid.uuid4().hex[:8]}",
                "proposalType": "noise",
                "targetId": general_id,
                "value": row.get("topUnresolvedLabel", ""),
                "labelScatterScore": scatter_score,
                "scatterThreshold": NOISE_SCATTER_THRESHOLD,
                "sandboxStatus": "pending",
                "canonicalWrites": False,
                "generatedAt": utc_now(),
                "sourceRow": {
                    "generalId": general_id,
                    "nextLane": row.get("nextLane", ""),
                },
            }
        )
    return proposals


def build_source_ref_proposals(
    seeds_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Proposal type: 'sourceRef'
    Trigger: anchor 100% match but missing sourceRef field
    """
    proposals: list[dict[str, Any]] = []
    for row in seeds_rows:
        anchor_match = float(row.get("anchorMatchScore", 0.0))
        has_source_ref = bool(row.get("sourceRef", "").strip())
        if anchor_match < 1.0 or has_source_ref:
            continue
        seed_id = row.get("seedId", row.get("id", ""))
        proposals.append(
            {
                "proposalId": f"sourceref-{uuid.uuid4().hex[:8]}",
                "proposalType": "sourceRef",
                "targetId": seed_id,
                "value": row.get("anchorRef", ""),
                "anchorMatchScore": anchor_match,
                "missingField": "sourceRef",
                "sandboxStatus": "pending",
                "canonicalWrites": False,
                "generatedAt": utc_now(),
                "sourceRow": {
                    "seedId": seed_id,
                    "generalId": row.get("generalId", ""),
                },
            }
        )
    return proposals


def build_source_downgrade_proposals(
    seeds_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Proposal type: 'sourceStatus'
    Trigger: siteReliabilityMultiplier < 0.3 AND persistently low quality (min samples)
    Requires human gated apply — canonicalWrites=false enforced.
    """
    # aggregate per site
    site_stats: dict[str, dict[str, Any]] = {}
    for row in seeds_rows:
        site = row.get("sourceSite", row.get("site", ""))
        if not site:
            continue
        multiplier = float(row.get("siteReliabilityMultiplier", 1.0))
        if site not in site_stats:
            site_stats[site] = {
                "sampleCount": 0,
                "multiplierSum": 0.0,
                "conflictCount": 0,
            }
        site_stats[site]["sampleCount"] += 1
        site_stats[site]["multiplierSum"] += multiplier
        if row.get("hasConflict", False):
            site_stats[site]["conflictCount"] += 1

    proposals: list[dict[str, Any]] = []
    for site, stats in site_stats.items():
        n = stats["sampleCount"]
        if n < SOURCE_LOW_QUALITY_MIN_SAMPLES:
            continue
        avg_multiplier = stats["multiplierSum"] / n
        if avg_multiplier >= SOURCE_DOWNGRADE_THRESHOLD:
            continue
        proposals.append(
            {
                "proposalId": f"srcstatus-{uuid.uuid4().hex[:8]}",
                "proposalType": "sourceStatus",
                "targetId": site,
                "value": "downgrade",
                "currentAvgMultiplier": round(avg_multiplier, 4),
                "sampleCount": n,
                "conflictCount": stats["conflictCount"],
                "downgradeThreshold": SOURCE_DOWNGRADE_THRESHOLD,
                "requiresHumanGatedApply": True,
                "sandboxStatus": "pending",
                "canonicalWrites": False,
                "generatedAt": utc_now(),
            }
        )
    return proposals


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Generate noise/sourceRef/sourceStatus proposals from scoreboard and seed data."
        )
    )
    p.add_argument(
        "--scoreboard-jsonl",
        required=True,
        help="Full roster scoreboard JSONL.",
    )
    p.add_argument(
        "--seeds-jsonl",
        required=True,
        help="Evidence seeds JSONL.",
    )
    p.add_argument(
        "--output-root",
        required=True,
        help="Directory for output proposal JSONL files.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    scoreboard_path = resolve_path(args.scoreboard_jsonl)
    seeds_path = resolve_path(args.seeds_jsonl)
    output_root = resolve_path(args.output_root)

    scoreboard_rows = read_jsonl(scoreboard_path)
    seeds_rows = read_jsonl(seeds_path)

    print(f"[noise_source] Scoreboard rows: {len(scoreboard_rows)}")
    print(f"[noise_source] Seeds rows: {len(seeds_rows)}")

    noise_props = build_noise_proposals(scoreboard_rows)
    sourceref_props = build_source_ref_proposals(seeds_rows)
    downgrade_props = build_source_downgrade_proposals(seeds_rows)

    all_proposals = noise_props + sourceref_props + downgrade_props

    # write combined ledger
    combined_path = output_root / "noise-source-proposals.jsonl"
    write_jsonl(combined_path, all_proposals)

    # write per-type
    write_jsonl(output_root / "noise-proposals.jsonl", noise_props)
    write_jsonl(output_root / "sourceref-proposals.jsonl", sourceref_props)
    write_jsonl(output_root / "source-downgrade-proposals.jsonl", downgrade_props)

    summary = {
        "generatedAt": utc_now(),
        "noiseProposals": len(noise_props),
        "sourceRefProposals": len(sourceref_props),
        "sourceStatusProposals": len(downgrade_props),
        "totalProposals": len(all_proposals),
        "canonicalWrites": False,
        "outputPath": str(combined_path),
    }
    write_json(output_root / "noise-source-proposals-summary.json", summary)

    print(f"[noise_source] Noise: {len(noise_props)}, SourceRef: {len(sourceref_props)}, Downgrade: {len(downgrade_props)}")
    print(f"[noise_source] Output: {combined_path}")


if __name__ == "__main__":
    main()
