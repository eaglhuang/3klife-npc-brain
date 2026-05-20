"""propose_alias_from_observed.py — M5-0501

從 topUnresolvedLabels 產出 alias proposal ledger JSONL。
計畫書偽代碼 B 段實作。
"""
from __future__ import annotations
import argparse, json, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

# ── generic title filter ──────────────────────────────────────────────────────
_GENERIC_TITLES: set[str] = {
    "將軍", "主公", "公", "夫人", "大人", "先生", "老師", "師父", "師傅",
    "大哥", "二哥", "三哥", "兄長", "兄弟", "兄", "弟", "妹", "姐",
    "王", "侯", "爺", "老爺", "少爺", "公子", "小姐", "娘子",
}


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

def is_generic_title(label: str) -> bool:
    """Return True if label is a generic address / title and should be skipped."""
    return label in _GENERIC_TITLES or len(label) <= 1


def compute_collision_count(
    label: str,
    formal_map: dict[str, Any],
) -> int:
    """Count how many existing alias entries share or conflict with this label."""
    count = 0
    for existing_label, target_id in formal_map.items():
        if existing_label == label:
            count += 1
        elif label in existing_label or existing_label in label:
            # partial overlap → potential collision
            count += 1
    return count


def load_formal_map(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    # accept dict[str, str] or {"aliases": {...}}
    if isinstance(data, dict):
        if "aliases" in data:
            return data["aliases"]
        return data
    return {}


# ── core logic ────────────────────────────────────────────────────────────────

def build_proposals(
    top_unresolved: list[dict[str, Any]],
    formal_map: dict[str, Any],
    min_count: int,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []

    for entry in top_unresolved:
        label: str = entry.get("label", "").strip()
        count: int = int(entry.get("count", 0))
        scene_co: int = int(entry.get("sceneCoOccurrences", 0))

        # filter: too rare
        if count < min_count:
            continue

        # filter: generic title
        if is_generic_title(label):
            continue

        # derive targetId heuristic: use label as-is (pipeline can refine later)
        target_id = label

        collision_count = compute_collision_count(label, formal_map)

        proposal_id = f"alias-prop-{uuid.uuid4().hex[:8]}"
        proposals.append(
            {
                "proposalId": proposal_id,
                "proposalType": "alias",
                "targetId": target_id,
                "value": label,
                "evidenceMentionCount": count,
                "sceneCoOccurrences": scene_co,
                "collisionCount": collision_count,
                "sandboxStatus": "pending",
                "canonicalWrites": False,
                "generatedAt": utc_now(),
            }
        )

    return proposals


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate alias proposals from topUnresolvedLabels."
    )
    p.add_argument(
        "--observed-label-summary",
        required=True,
        help="JSON file with {topUnresolvedLabels: [{label, count, sceneCoOccurrences}]}",
    )
    p.add_argument(
        "--output-root",
        required=True,
        help="Directory for output proposal ledger JSONL.",
    )
    p.add_argument(
        "--min-count",
        type=int,
        default=8,
        help="Minimum mention count to consider (default: 8).",
    )
    p.add_argument(
        "--formal-map",
        default=None,
        help="Path to existing formal alias map JSON for collision detection.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    summary_path = resolve_path(args.observed_label_summary)
    output_root = resolve_path(args.output_root)
    formal_map_path = resolve_path(args.formal_map) if args.formal_map else None

    # load inputs
    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    top_unresolved: list[dict[str, Any]] = summary_data.get("topUnresolvedLabels", [])
    formal_map = load_formal_map(formal_map_path)

    print(f"[propose_alias] Loaded {len(top_unresolved)} unresolved labels.")
    print(f"[propose_alias] Formal map entries: {len(formal_map)}")
    print(f"[propose_alias] min_count={args.min_count}")

    proposals = build_proposals(top_unresolved, formal_map, args.min_count)

    # write output
    out_path = output_root / "alias-proposals.jsonl"
    write_jsonl(out_path, proposals)

    summary_out = {
        "generatedAt": utc_now(),
        "totalInputLabels": len(top_unresolved),
        "proposalsGenerated": len(proposals),
        "minCount": args.min_count,
        "outputPath": str(out_path),
    }
    write_json(output_root / "alias-proposals-summary.json", summary_out)

    print(f"[propose_alias] Proposals generated: {len(proposals)}")
    print(f"[propose_alias] Output: {out_path}")


if __name__ == "__main__":
    main()
