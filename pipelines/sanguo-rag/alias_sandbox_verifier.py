"""alias_sandbox_verifier.py — M5-0502

在不污染正式 alias map 的情況下，sandbox 測試 alias proposals。
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

MIN_YIELD_RATE: float = 0.1


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

def load_formal_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "aliases" in data:
            return data["aliases"]
        return {k: v for k, v in data.items() if isinstance(v, str)}
    return {}


def load_chapter_texts(chapters_root: Path | None) -> list[str]:
    """Load plain-text chapter files from a directory."""
    if chapters_root is None or not chapters_root.exists():
        return []
    texts: list[str] = []
    for ext in ("*.txt", "*.md"):
        for fpath in chapters_root.glob(ext):
            try:
                texts.append(fpath.read_text(encoding="utf-8"))
            except Exception:
                pass
    return texts


def count_mentions(pattern: str, texts: list[str]) -> int:
    """Count total occurrences of pattern across all texts."""
    if not texts:
        return 0
    total = 0
    for text in texts:
        total += len(re.findall(re.escape(pattern), text))
    return total


def detect_collisions(
    alias_value: str,
    sandbox_map: dict[str, str],
    formal_map: dict[str, str],
) -> int:
    """Count how many existing map entries would collide with this alias."""
    collision = 0
    for existing_alias in list(sandbox_map.keys()) + list(formal_map.keys()):
        if existing_alias != alias_value and (
            alias_value in existing_alias or existing_alias in alias_value
        ):
            collision += 1
    return collision


def detect_noise_promotion(
    target_id: str,
    alias_value: str,
    texts: list[str],
    sandbox_map: dict[str, str],
) -> int:
    """Estimate how many unrelated entities might be falsely promoted."""
    # simple heuristic: check if alias_value appears in contexts that clearly
    # don't belong to target_id (we count cross-matched entries in sandbox)
    noise = 0
    for existing_alias, existing_target in sandbox_map.items():
        if existing_target != target_id and existing_alias in alias_value:
            noise += 1
    return noise


# ── core verification ─────────────────────────────────────────────────────────

def verify_proposal(
    proposal: dict[str, Any],
    formal_map: dict[str, str],
    sandbox_map: dict[str, str],
    texts: list[str],
) -> dict[str, Any]:
    proposal_id: str = proposal["proposalId"]
    alias_value: str = proposal["value"]
    target_id: str = proposal["targetId"]
    original_count: int = int(proposal.get("evidenceMentionCount", 0))

    # inject alias into sandbox
    test_sandbox = dict(sandbox_map)
    test_sandbox[alias_value] = target_id

    # re-count mentions in sample chapters
    new_resolved_count = count_mentions(alias_value, texts) if texts else original_count

    # collision detection
    new_collision_count = detect_collisions(alias_value, sandbox_map, formal_map)

    # noise promotion estimate
    new_noise_promotion = detect_noise_promotion(
        target_id, alias_value, texts, sandbox_map
    )

    # yield rate: how many new resolves vs original mention count
    denominator = max(original_count, 1)
    yield_rate = round(new_resolved_count / denominator, 4)

    # determine sandbox status
    if new_collision_count > 0:
        status = "fail-collision"
    elif new_noise_promotion > 0:
        status = "fail-noise"
    elif yield_rate < MIN_YIELD_RATE:
        status = "fail-low-yield"
    else:
        status = "pass"

    return {
        "proposalId": proposal_id,
        "aliasValue": alias_value,
        "targetId": target_id,
        "newResolvedCount": new_resolved_count,
        "newCollisionCount": new_collision_count,
        "newNoisePromotion": new_noise_promotion,
        "yieldRate": yield_rate,
        "sandboxStatus": status,
        "verifiedAt": utc_now(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify alias proposals in a sandbox without touching the formal alias map."
    )
    p.add_argument(
        "--proposal-ledger",
        required=True,
        help="JSONL file with alias proposals (from propose_alias_from_observed.py).",
    )
    p.add_argument(
        "--formal-map",
        required=True,
        help="Path to formal alias map JSON (read-only).",
    )
    p.add_argument(
        "--sample-chapters-root",
        default=None,
        help="Directory of sample chapter text files for mention re-counting.",
    )
    p.add_argument(
        "--output-root",
        required=True,
        help="Directory for sandbox report output.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    ledger_path = resolve_path(args.proposal_ledger)
    formal_map_path = resolve_path(args.formal_map)
    output_root = resolve_path(args.output_root)
    chapters_root = (
        resolve_path(args.sample_chapters_root)
        if args.sample_chapters_root
        else None
    )

    proposals = read_jsonl(ledger_path)
    formal_map = load_formal_map(formal_map_path)
    texts = load_chapter_texts(chapters_root)

    print(f"[alias_sandbox] Proposals: {len(proposals)}")
    print(f"[alias_sandbox] Formal map entries: {len(formal_map)}")
    print(f"[alias_sandbox] Sample chapter files loaded: {len(texts)}")

    # build sandbox map starting from formal map
    sandbox_map: dict[str, str] = dict(formal_map)

    results: list[dict[str, Any]] = []
    for proposal in proposals:
        result = verify_proposal(proposal, formal_map, sandbox_map, texts)
        results.append(result)
        # accumulate passing aliases into sandbox for subsequent checks
        if result["sandboxStatus"] == "pass":
            sandbox_map[result["aliasValue"]] = result["targetId"]

    # write report
    report_path = output_root / "alias-sandbox-report.jsonl"
    write_jsonl(report_path, results)

    pass_count = sum(1 for r in results if r["sandboxStatus"] == "pass")
    summary = {
        "generatedAt": utc_now(),
        "totalProposals": len(proposals),
        "passed": pass_count,
        "failCollision": sum(1 for r in results if r["sandboxStatus"] == "fail-collision"),
        "failNoise": sum(1 for r in results if r["sandboxStatus"] == "fail-noise"),
        "failLowYield": sum(1 for r in results if r["sandboxStatus"] == "fail-low-yield"),
        "minYieldRate": MIN_YIELD_RATE,
        "outputPath": str(report_path),
    }
    write_json(output_root / "alias-sandbox-summary.json", summary)

    print(f"[alias_sandbox] Pass: {pass_count}/{len(results)}")
    print(f"[alias_sandbox] Report: {report_path}")


if __name__ == "__main__":
    main()
