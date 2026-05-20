"""manual_seed_auto_mirror.py — M5-0503

建立 auto mirror，sandbox-pass 的 alias 可被下一輪 pipeline 選用，
但不改人工正本 manual-roster-seeds.json。
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_AUTO_MIRROR_RELPATH = "config/manual-roster-seeds.auto.json"


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


# ── helpers ───────────────────────────────────────────────────────────────────

def load_formal_map(path: Path) -> dict[str, Any]:
    """Load formal alias map (READ-ONLY reference — never written)."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "aliases" in data:
            return data["aliases"]
        return data
    return {}


def load_sandbox_report(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


# ── core logic ────────────────────────────────────────────────────────────────

def build_auto_mirror(
    sandbox_report: list[dict[str, Any]],
    formal_map: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Build auto mirror from sandbox-pass proposals.
    Returns (mirror_aliases, merge_summary_rows).
    Only includes entries that pass sandbox AND do not conflict with formal_map.
    """
    mirror_aliases: dict[str, Any] = {}
    merge_summary: list[dict[str, Any]] = []

    for entry in sandbox_report:
        if entry.get("sandboxStatus") != "pass":
            continue

        alias_value: str = entry["aliasValue"]
        target_id: str = entry["targetId"]
        proposal_id: str = entry.get("proposalId", "")

        # skip if already in formal map (formal map takes precedence)
        if alias_value in formal_map:
            merge_summary.append(
                {
                    "aliasValue": alias_value,
                    "targetId": target_id,
                    "source": "auto",
                    "proposalId": proposal_id,
                    "action": "skipped-formal-conflict",
                    "traceable": True,
                }
            )
            continue

        mirror_aliases[alias_value] = target_id
        merge_summary.append(
            {
                "aliasValue": alias_value,
                "targetId": target_id,
                "source": "auto",
                "proposalId": proposal_id,
                "action": "included",
                "traceable": True,
            }
        )

    return mirror_aliases, merge_summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build auto mirror from sandbox-pass alias proposals. "
            "Does NOT modify manual-roster-seeds.json."
        )
    )
    p.add_argument(
        "--sandbox-report",
        required=True,
        help="JSONL sandbox report from alias_sandbox_verifier.py.",
    )
    p.add_argument(
        "--formal-map",
        required=True,
        help="Path to formal alias map JSON (read-only; manual-roster-seeds.json).",
    )
    p.add_argument(
        "--output-root",
        default=None,
        help=(
            "Directory to write auto mirror (default: repo root). "
            f"Auto mirror written to <output-root>/{DEFAULT_AUTO_MIRROR_RELPATH}"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without writing any files.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    sandbox_path = resolve_path(args.sandbox_report)
    formal_map_path = resolve_path(args.formal_map)
    output_root = resolve_path(args.output_root) if args.output_root else REPO_ROOT

    sandbox_report = load_sandbox_report(sandbox_path)
    formal_map = load_formal_map(formal_map_path)

    print(f"[auto_mirror] Sandbox report entries: {len(sandbox_report)}")
    print(f"[auto_mirror] Formal map entries: {len(formal_map)}")
    print(f"[auto_mirror] Dry-run: {args.dry_run}")

    mirror_aliases, merge_summary = build_auto_mirror(sandbox_report, formal_map)

    auto_mirror_payload: dict[str, Any] = {
        "schemaVersion": "manual-roster-seeds.auto.v0.1",
        "description": (
            "Auto-generated alias mirror. "
            "This file is machine-written. DO NOT edit manually. "
            "Formal seeds live in config/manual-roster-seeds.json."
        ),
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "aliases": mirror_aliases,
        "mergeSummary": merge_summary,
    }

    auto_mirror_path = output_root / DEFAULT_AUTO_MIRROR_RELPATH
    summary_path = output_root / "config/manual-roster-seeds.auto.merge-summary.jsonl"

    if args.dry_run:
        print(f"[auto_mirror] [DRY-RUN] Would write mirror to: {auto_mirror_path}")
        print(f"[auto_mirror] [DRY-RUN] Entries to include: {len(mirror_aliases)}")
        print(
            f"[auto_mirror] [DRY-RUN] Skipped (formal conflict): "
            f"{sum(1 for r in merge_summary if r['action'] == 'skipped-formal-conflict')}"
        )
    else:
        write_json(auto_mirror_path, auto_mirror_payload)
        write_jsonl(summary_path, merge_summary)
        print(f"[auto_mirror] Auto mirror written: {auto_mirror_path}")
        print(f"[auto_mirror] Merge summary: {summary_path}")

    included = sum(1 for r in merge_summary if r["action"] == "included")
    skipped = sum(1 for r in merge_summary if r["action"] == "skipped-formal-conflict")
    print(f"[auto_mirror] Included: {included}, Skipped (conflict): {skipped}")


if __name__ == "__main__":
    main()
