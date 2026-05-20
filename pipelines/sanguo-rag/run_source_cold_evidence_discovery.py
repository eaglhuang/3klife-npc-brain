"""run_source_cold_evidence_discovery.py — M6-0601

針對 evidence-discovery / source-cold cohort 做 anchor-guided 外部採證。
計畫書偽代碼 C 段實作。
Dry-run 模式輸出 query list，不做真實 HTTP 請求。
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_MAX_GENERALS: int = 20
SOURCE_COLD_MIN_SOURCES: int = 2   # fewer than this → considered source-cold


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


# ── cohort identification ─────────────────────────────────────────────────────

def is_source_cold(row: dict[str, Any]) -> bool:
    """True if row qualifies as source-cold evidence-discovery cohort."""
    next_lane = row.get("nextLane", "")
    if next_lane != "evidence-discovery":
        return False
    distinct_sources = int(row.get("externalDistinctHistoryFamilyCount", 0))
    return distinct_sources < SOURCE_COLD_MIN_SOURCES


def build_source_cold_cohort(
    scoreboard_rows: list[dict[str, Any]],
    max_generals: int,
) -> list[dict[str, Any]]:
    cohort = [r for r in scoreboard_rows if is_source_cold(r)]
    # sort by mention count ascending (least evidence first = highest priority)
    cohort.sort(key=lambda r: r.get("mentionCount", 0))
    return cohort[:max_generals]


# ── anchor-guided query expansion ─────────────────────────────────────────────

def build_anchor_guided_queries(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Build anchor-guided search query objects for a source-cold general."""
    general_id: str = row.get("generalId", "")
    general_name: str = row.get("generalName", general_id)
    anchor_refs: list[str] = row.get("anchorRefs", [])

    queries: list[dict[str, Any]] = []

    # base query: general name
    queries.append(
        {
            "queryType": "name-search",
            "generalId": general_id,
            "queryText": f"{general_name} 三國",
            "targetSources": ["baidu-baike", "zh-wikipedia", "3kweb"],
            "anchorGuided": False,
        }
    )

    # anchor-guided queries: one per anchor ref
    for anchor in anchor_refs[:3]:  # limit to 3 anchors per general
        queries.append(
            {
                "queryType": "anchor-guided",
                "generalId": general_id,
                "queryText": f"{general_name} {anchor}",
                "targetSources": ["zh-wikipedia", "guoxue-net"],
                "anchorGuided": True,
                "anchorRef": anchor,
            }
        )

    return queries


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run source-cold evidence discovery for evidence-discovery lane cohort. "
            "Use --dry-run to output query list without making HTTP requests."
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
        help="Directory for output progress ledger and dry-run report.",
    )
    p.add_argument(
        "--max-generals",
        type=int,
        default=DEFAULT_MAX_GENERALS,
        help=f"Maximum generals to process per run (default: {DEFAULT_MAX_GENERALS}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Output query list without making real HTTP requests.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    scoreboard_path = resolve_path(args.scoreboard_jsonl)
    output_root = resolve_path(args.output_root)

    scoreboard_rows = read_jsonl(scoreboard_path)
    print(f"[source_cold] Total scoreboard rows: {len(scoreboard_rows)}")

    cohort = build_source_cold_cohort(scoreboard_rows, args.max_generals)
    print(f"[source_cold] Source-cold cohort size: {len(cohort)} (max: {args.max_generals})")

    # build queries for all cohort members
    all_queries: list[dict[str, Any]] = []
    progress_ledger: list[dict[str, Any]] = []

    for row in cohort:
        general_id = row.get("generalId", "")
        queries = build_anchor_guided_queries(row)
        all_queries.extend(queries)

        ledger_entry: dict[str, Any] = {
            "generalId": general_id,
            "generalName": row.get("generalName", general_id),
            "cohortReason": "source-cold-evidence-discovery",
            "currentDistinctSources": row.get("externalDistinctHistoryFamilyCount", 0),
            "queryCount": len(queries),
            "queries": queries,
            "status": "queued" if args.dry_run else "pending",
            "canonicalWrites": False,
            "processedAt": utc_now(),
        }
        progress_ledger.append(ledger_entry)

    # write outputs
    ledger_path = output_root / "source-cold-progress-ledger.jsonl"
    write_jsonl(ledger_path, progress_ledger)

    dry_run_report = {
        "schemaVersion": "source-cold-discovery.v0.1",
        "generatedAt": utc_now(),
        "dryRun": args.dry_run,
        "canonicalWrites": False,
        "cohortSize": len(cohort),
        "maxGenerals": args.max_generals,
        "totalQueries": len(all_queries),
        "queries": all_queries,
        "ledgerPath": str(ledger_path),
    }
    report_path = output_root / "source-cold-dry-run-report.json"
    write_json(report_path, dry_run_report)

    if args.dry_run:
        print(f"[source_cold] [DRY-RUN] Would execute {len(all_queries)} queries for {len(cohort)} generals.")
    else:
        print(f"[source_cold] Queued {len(all_queries)} queries for {len(cohort)} generals.")

    print(f"[source_cold] Progress ledger: {ledger_path}")
    print(f"[source_cold] Dry-run report: {report_path}")


if __name__ == "__main__":
    main()
