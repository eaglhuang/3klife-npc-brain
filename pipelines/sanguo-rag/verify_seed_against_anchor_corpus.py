"""
Seed Anchor Verification CLI — SANGUO-AUTO-0301
把外部 seed 對 anchor corpus 做支持、未支持與疑似衝突分流。
輸出 anchorMatchCount、anchorHistoryMatchCount、anchorRomanceMatchCount、
anchorVerdict、supportingLocators、supportingTextHashes。
不把 anchor locator 偽裝成外部來源自己的 locator。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_ANCHOR_INDEX_ROOT = Path("artifacts/data-pipeline/sanguo-rag/anchor-index")
DEFAULT_OUTPUT_ROOT = Path("local/verify-seed-anchor")
SCHEMA_VERSION = "anchor.verification.v0.1"

ANCHOR_VERDICT_HISTORY_CORROBORATED = "history-corroborated"
ANCHOR_VERDICT_ROMANCE_CORROBORATED = "romance-corroborated"
ANCHOR_VERDICT_SUSPECTED_CONFLICT = "suspected-conflict"
ANCHOR_VERDICT_UNVERIFIED = "unverified"

MIN_NGRAM_OVERLAP = 4
MIN_GROUNDED_HITS = 1
CONFLICT_KEYWORD_PATTERNS = [
    r"並非",
    r"不是",
    r"誤傳",
    r"後世附會",
    r"無此記載",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
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


def normalize_cjk(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def ngram_overlap(a: str, b: str, n: int = 4) -> int:
    na, nb = normalize_cjk(a), normalize_cjk(b)
    grams_a = {na[i:i+n] for i in range(len(na) - n + 1)}
    grams_b = {nb[i:i+n] for i in range(len(nb) - n + 1)}
    return len(grams_a & grams_b)


def load_anchor_index(index_root: Path) -> list[dict[str, Any]]:
    """從 anchor index 目錄載入所有 passage records。"""
    all_passages: list[dict[str, Any]] = []
    for jsonl_file in sorted(index_root.glob("*-passages.jsonl")):
        all_passages.extend(read_jsonl(jsonl_file))
    return all_passages


def hybrid_retrieve(
    passages: list[dict[str, Any]],
    seed_text: str,
    general_id: str,
    topk: int = 8,
) -> list[dict[str, Any]]:
    """從 anchor passages 中找出與 seed_text 最相關的 topk 條。"""
    scored: list[tuple[int, dict[str, Any]]] = []
    for passage in passages:
        overlap = ngram_overlap(passage.get("normalizedText", ""), seed_text)
        if overlap > 0:
            scored.append((overlap, passage))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:topk]]


def is_conflict_hint(passage_text: str, seed_text: str) -> bool:
    for pattern in CONFLICT_KEYWORD_PATTERNS:
        if re.search(pattern, passage_text):
            return True
    return False


def classify_anchor_verdict(
    grounded: list[dict[str, Any]],
    seed_text: str,
) -> str:
    if not grounded:
        return ANCHOR_VERDICT_UNVERIFIED
    for hit in grounded:
        if is_conflict_hint(hit.get("normalizedText", ""), seed_text):
            return ANCHOR_VERDICT_SUSPECTED_CONFLICT
    history_hits = [h for h in grounded if h.get("layer") == "history"]
    romance_hits = [h for h in grounded if h.get("layer") == "romance"]
    if history_hits:
        return ANCHOR_VERDICT_HISTORY_CORROBORATED
    if romance_hits:
        return ANCHOR_VERDICT_ROMANCE_CORROBORATED
    return ANCHOR_VERDICT_UNVERIFIED


def verify_seed(
    seed: dict[str, Any],
    passages: list[dict[str, Any]],
    topk: int = 8,
) -> dict[str, Any]:
    seed_text = seed.get("seedText", "")
    general_id = seed.get("generalId", "")
    hits = hybrid_retrieve(passages, seed_text, general_id, topk=topk)
    grounded = [
        hit for hit in hits
        if ngram_overlap(hit.get("normalizedText", ""), seed_text, n=MIN_NGRAM_OVERLAP) >= MIN_NGRAM_OVERLAP
    ]
    verdict = classify_anchor_verdict(grounded, seed_text)
    history_hits = [h for h in grounded if h.get("layer") == "history"]
    romance_hits = [h for h in grounded if h.get("layer") == "romance"]
    return {
        "generalId": general_id,
        "seedId": seed.get("seedId", ""),
        "anchorMatchCount": len(grounded),
        "anchorHistoryMatchCount": len(history_hits),
        "anchorRomanceMatchCount": len(romance_hits),
        "anchorVerdict": verdict,
        "supportingLocators": [h["locator"] for h in grounded[:3]],
        "supportingTextHashes": [h["textHash"] for h in grounded[:3]],
        "canonicalWrites": False,
        "verifiedAt": utc_now(),
    }


def verify_seeds_batch(
    seeds: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    topk: int = 8,
) -> list[dict[str, Any]]:
    results = []
    for seed in seeds:
        result = verify_seed(seed, passages, topk=topk)
        results.append({**seed, "anchorEvidence": result})
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify external seeds against anchor corpus.")
    parser.add_argument("--seeds-jsonl", required=True, help="Input seeds JSONL path")
    parser.add_argument("--anchor-index-root", default=str(DEFAULT_ANCHOR_INDEX_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--topk", type=int, default=8)
    parser.add_argument("--general-id", help="Filter by generalId")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds_path = resolve_path(args.seeds_jsonl)
    anchor_root = resolve_path(args.anchor_index_root)
    out_root = resolve_path(args.output_root)

    print(f"Loading seeds from {seeds_path}...")
    seeds = read_jsonl(seeds_path)
    if args.general_id:
        seeds = [s for s in seeds if s.get("generalId") == args.general_id]

    print(f"Loading anchor index from {anchor_root}...")
    passages = load_anchor_index(anchor_root)
    if not passages:
        print("WARNING: No anchor passages found. Run anchor_passage_index_builder.py first.")

    print(f"Verifying {len(seeds)} seeds against {len(passages)} passages...")
    results = verify_seeds_batch(seeds, passages, topk=args.topk)

    out_path = out_root / "seed-anchor-verification.jsonl"
    write_jsonl(out_path, results)
    print(f"Done. Results written to {out_path}")

    verdicts = {}
    for r in results:
        v = r.get("anchorEvidence", {}).get("anchorVerdict", "unverified")
        verdicts[v] = verdicts.get(v, 0) + 1
    print("Verdict distribution:", verdicts)


if __name__ == "__main__":
    main()
