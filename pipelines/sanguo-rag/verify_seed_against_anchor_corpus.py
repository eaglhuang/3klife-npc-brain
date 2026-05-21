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
DEFAULT_POLICY_PATH = Path("pipelines/sanguo-rag/config/anchor-verification-policy.json")
SCHEMA_VERSION = "anchor.verification.v0.1"

ANCHOR_VERDICT_HISTORY_CORROBORATED = "history-corroborated"
ANCHOR_VERDICT_ROMANCE_CORROBORATED = "romance-corroborated"
ANCHOR_VERDICT_SUSPECTED_CONFLICT = "suspected-conflict"
ANCHOR_VERDICT_UNVERIFIED = "unverified"

DEFAULT_POLICY = {
    "minNgramOverlap": 4,
    "minGroundedHits": 1,
    "directPersonMatchBoost": 5,
    "targetOnlyPersonMatchBoost": 20,
    "targetOnlyAngleMatchBoost": 3,
    "conflictKeywordPatterns": [],
}

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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def load_policy(path: Path | None) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    if path is not None:
        configured = read_json(path)
        for key, value in configured.items():
            if key == "conflictKeywordPatterns" and isinstance(value, list):
                policy[key] = [str(item) for item in value if str(item).strip()]
            elif key in policy:
                policy[key] = value
    policy["minNgramOverlap"] = max(int(policy.get("minNgramOverlap") or 4), 1)
    policy["minGroundedHits"] = max(int(policy.get("minGroundedHits") or 1), 1)
    policy["directPersonMatchBoost"] = max(int(policy.get("directPersonMatchBoost") or 0), 0)
    policy["targetOnlyPersonMatchBoost"] = max(int(policy.get("targetOnlyPersonMatchBoost") or 0), 0)
    policy["targetOnlyAngleMatchBoost"] = max(int(policy.get("targetOnlyAngleMatchBoost") or 0), 0)
    return policy


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_cjk(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def normalized_ngrams(text: str, n: int = 4) -> set[str]:
    if len(text) < n:
        return set()
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def ngram_overlap(a: str, b: str, n: int = 4) -> int:
    na, nb = normalize_cjk(a), normalize_cjk(b)
    grams_a = normalized_ngrams(na, n)
    grams_b = normalized_ngrams(nb, n)
    return len(grams_a & grams_b)


def load_anchor_index(index_root: Path) -> list[dict[str, Any]]:
    """從 anchor index 目錄載入所有 passage records。"""
    all_passages: list[dict[str, Any]] = []
    for jsonl_file in sorted(index_root.glob("*-passages.jsonl")):
        all_passages.extend(read_jsonl(jsonl_file))
    return all_passages


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def is_target_only_seed(seed: dict[str, Any]) -> bool:
    manual_quote = seed.get("manualQuote") if isinstance(seed.get("manualQuote"), dict) else {}
    target_only = as_bool(seed.get("manualQuoteTarget")) or as_bool(manual_quote.get("targetOnly"))
    has_direct_quote = as_bool(seed.get("manualQuoteHasDirectQuote")) or as_bool(manual_quote.get("hasDirectQuote"))
    return bool(target_only and not has_direct_quote)


def seed_search_text(seed: dict[str, Any]) -> str:
    if not is_target_only_seed(seed):
        return str(seed.get("seedText") or seed.get("quote") or seed.get("translatedTraditionalText") or "")
    manual_quote = seed.get("manualQuote") if isinstance(seed.get("manualQuote"), dict) else {}
    parts = [
        seed.get("matchedName"),
        manual_quote.get("curationKeyword"),
        seed.get("displayName"),
        seed.get("generalName"),
        seed.get("generalId"),
        seed.get("angleType"),
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def passage_has_person(passage: dict[str, Any], general_id: str) -> bool:
    if not general_id:
        return False
    person_ids = passage.get("personIds")
    if isinstance(person_ids, list) and general_id in {str(item) for item in person_ids}:
        return True
    return str(passage.get("generalId") or "") == general_id


def passage_angle_match(passage: dict[str, Any], angle_type: str) -> bool:
    angle = str(angle_type or "").strip()
    if not angle:
        return False
    locator = str(passage.get("locator") or "")
    return f"field=page-text-{angle}" in locator or f"field={angle}" in locator


class AnchorSearchIndex:
    """In-memory n-gram index for anchor passages."""

    def __init__(self, passages: list[dict[str, Any]], ngram_size: int = 4) -> None:
        self.passages = passages
        self.ngram_size = max(int(ngram_size or 4), 1)
        self.normalized_texts: list[str] = []
        self.gram_sets: list[set[str]] = []
        self.gram_to_indices: dict[str, set[int]] = {}
        self.person_to_indices: dict[str, set[int]] = {}

        for idx, passage in enumerate(passages):
            normalized_text = normalize_cjk(str(passage.get("normalizedText") or passage.get("text") or ""))
            grams = normalized_ngrams(normalized_text, self.ngram_size)
            self.normalized_texts.append(normalized_text)
            self.gram_sets.append(grams)
            for gram in grams:
                self.gram_to_indices.setdefault(gram, set()).add(idx)

            person_ids: set[str] = set()
            raw_person_ids = passage.get("personIds")
            if isinstance(raw_person_ids, list):
                person_ids.update(str(item).strip() for item in raw_person_ids if str(item).strip())
            general_id = str(passage.get("generalId") or "").strip()
            if general_id:
                person_ids.add(general_id)
            for person_id in person_ids:
                self.person_to_indices.setdefault(person_id, set()).add(idx)

    def seed_grams(self, seed_text: str) -> set[str]:
        return normalized_ngrams(normalize_cjk(seed_text), self.ngram_size)

    def candidate_indices(self, seed_text: str, general_id: str, *, target_only: bool = False) -> set[int]:
        candidates: set[int] = set()
        for gram in self.seed_grams(seed_text):
            candidates.update(self.gram_to_indices.get(gram, set()))
        if target_only and general_id:
            candidates.update(self.person_to_indices.get(general_id, set()))
        return candidates

    def overlap_count(self, passage_index: int, seed_grams: set[str]) -> int:
        if not seed_grams:
            return 0
        return len(self.gram_sets[passage_index] & seed_grams)


def hybrid_retrieve(
    passages: list[dict[str, Any]] | AnchorSearchIndex,
    seed_text: str,
    general_id: str,
    *,
    angle_type: str = "",
    target_only: bool = False,
    policy: dict[str, Any] | None = None,
    topk: int = 8,
) -> list[dict[str, Any]]:
    """從 anchor passages 中找出與 seed_text 最相關的 topk 條。"""
    active_policy = policy or DEFAULT_POLICY
    index = passages if isinstance(passages, AnchorSearchIndex) else AnchorSearchIndex(passages)
    query_grams = index.seed_grams(seed_text)
    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for passage_index in sorted(index.candidate_indices(seed_text, general_id, target_only=target_only)):
        passage = index.passages[passage_index]
        overlap = index.overlap_count(passage_index, query_grams)
        person_match = passage_has_person(passage, general_id)
        angle_match = passage_angle_match(passage, angle_type)
        if target_only:
            if not person_match and overlap <= 0:
                continue
            score = overlap
            if person_match:
                score += int(active_policy.get("targetOnlyPersonMatchBoost") or 0)
            if angle_match:
                score += int(active_policy.get("targetOnlyAngleMatchBoost") or 0)
        else:
            if overlap <= 0:
                continue
            score = overlap + (int(active_policy.get("directPersonMatchBoost") or 0) if person_match else 0)
        scored.append((score, int(person_match), int(angle_match), passage))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return [p for _, _, _, p in scored[:topk]]


def is_conflict_hint(passage_text: str, seed_text: str, policy: dict[str, Any]) -> bool:
    for pattern in policy.get("conflictKeywordPatterns") or []:
        if re.search(pattern, passage_text):
            return True
    return False


def classify_anchor_verdict(
    grounded: list[dict[str, Any]],
    seed_text: str,
    policy: dict[str, Any],
) -> str:
    if not grounded:
        return ANCHOR_VERDICT_UNVERIFIED
    for hit in grounded:
        if is_conflict_hint(hit.get("normalizedText", ""), seed_text, policy):
            return ANCHOR_VERDICT_SUSPECTED_CONFLICT
    history_hits = [h for h in grounded if h.get("layer") == "history"]
    romance_hits = [h for h in grounded if h.get("layer") == "romance"]
    if history_hits:
        return ANCHOR_VERDICT_HISTORY_CORROBORATED
    if romance_hits:
        return ANCHOR_VERDICT_ROMANCE_CORROBORATED
    return ANCHOR_VERDICT_UNVERIFIED


def dedupe_anchor_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        key = (str(hit.get("locator") or ""), str(hit.get("textHash") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


def verify_seed(
    seed: dict[str, Any],
    passages: list[dict[str, Any]] | AnchorSearchIndex,
    policy: dict[str, Any],
    topk: int = 8,
) -> dict[str, Any]:
    seed_text = seed_search_text(seed)
    general_id = str(seed.get("generalId", "") or "")
    angle_type = str(seed.get("angleType") or "")
    target_only = is_target_only_seed(seed)
    hits = hybrid_retrieve(
        passages,
        seed_text,
        general_id,
        angle_type=angle_type,
        target_only=target_only,
        policy=policy,
        topk=topk,
    )
    min_overlap = int(policy.get("minNgramOverlap") or 4)
    if target_only:
        grounded = [
            hit for hit in hits
            if passage_has_person(hit, general_id)
            or ngram_overlap(hit.get("normalizedText", ""), seed_text, n=min_overlap) >= min_overlap
        ]
    else:
        grounded = [
            hit for hit in hits
            if ngram_overlap(hit.get("normalizedText", ""), seed_text, n=min_overlap) >= min_overlap
        ]
    grounded = dedupe_anchor_hits(grounded)
    if len(grounded) < int(policy.get("minGroundedHits") or 1):
        grounded = []
    verdict = classify_anchor_verdict(grounded, seed_text, policy)
    history_hits = [h for h in grounded if h.get("layer") == "history"]
    romance_hits = [h for h in grounded if h.get("layer") == "romance"]
    return {
        "generalId": general_id,
        "seedId": seed.get("seedId", ""),
        "targetOnlySeed": target_only,
        "searchTextHash": "sha256:" + hashlib.sha256(normalize_cjk(seed_text).encode("utf-8")).hexdigest()[:16],
        "anchorMatchCount": len(grounded),
        "anchorHistoryMatchCount": len(history_hits),
        "anchorRomanceMatchCount": len(romance_hits),
        "anchorVerdict": verdict,
        "supportingLocators": [h["locator"] for h in grounded[:3]],
        "supportingTextHashes": [h["textHash"] for h in grounded[:3]],
        "supportingSnippets": [h.get("normalizedText", "") for h in grounded[:3]],
        "canonicalWrites": False,
        "verifiedAt": utc_now(),
    }


def verify_seeds_batch(
    seeds: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    policy: dict[str, Any],
    topk: int = 8,
) -> list[dict[str, Any]]:
    index = AnchorSearchIndex(passages)
    results = []
    for seed in seeds:
        result = verify_seed(seed, index, policy=policy, topk=topk)
        results.append({**seed, "anchorEvidence": result})
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify external seeds against anchor corpus.")
    parser.add_argument("--seeds-jsonl", required=True, help="Input seeds JSONL path")
    parser.add_argument("--anchor-index-root", default=str(DEFAULT_ANCHOR_INDEX_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--policy-json", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--topk", type=int, default=8)
    parser.add_argument("--general-id", help="Filter by generalId")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds_path = resolve_path(args.seeds_jsonl)
    anchor_root = resolve_path(args.anchor_index_root)
    out_root = resolve_path(args.output_root)
    policy_path = resolve_path(args.policy_json) if args.policy_json else None
    policy = load_policy(policy_path)

    print(f"Loading seeds from {seeds_path}...")
    seeds = read_jsonl(seeds_path)
    if args.general_id:
        seeds = [s for s in seeds if s.get("generalId") == args.general_id]

    print(f"Loading anchor index from {anchor_root}...")
    passages = load_anchor_index(anchor_root)
    if not passages:
        print("WARNING: No anchor passages found. Run anchor_passage_index_builder.py first.")

    print(f"Verifying {len(seeds)} seeds against {len(passages)} passages...")
    results = verify_seeds_batch(seeds, passages, policy=policy, topk=args.topk)

    out_path = out_root / "seed-anchor-verification.jsonl"
    write_jsonl(out_path, results)
    print(f"Done. Results written to {out_path}")

    verdicts = {}
    for r in results:
        v = r.get("anchorEvidence", {}).get("anchorVerdict", "unverified")
        verdicts[v] = verdicts.get(v, 0) + 1
    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "seedsJsonl": str(seeds_path),
            "anchorIndexRoot": str(anchor_root),
            "policyJson": str(policy_path) if policy_path else None,
            "topk": args.topk,
            "generalId": args.general_id,
        },
        "outputs": {
            "verifiedSeedsJsonl": str(out_path),
            "summaryJson": str(out_root / "seed-anchor-verification-summary.json"),
        },
        "metrics": {
            "seedCount": len(results),
            "anchorPassageCount": len(passages),
            "verdictCounts": dict(sorted(verdicts.items())),
            "anchorMatchedSeedCount": sum(
                1 for row in results if int(row.get("anchorEvidence", {}).get("anchorMatchCount") or 0) > 0
            ),
            "targetOnlySeedCount": sum(
                1 for row in results if bool(row.get("anchorEvidence", {}).get("targetOnlySeed"))
            ),
            "targetOnlyMatchedSeedCount": sum(
                1
                for row in results
                if bool(row.get("anchorEvidence", {}).get("targetOnlySeed"))
                and int(row.get("anchorEvidence", {}).get("anchorMatchCount") or 0) > 0
            ),
        },
    }
    write_json(out_root / "seed-anchor-verification-summary.json", summary)
    print("Verdict distribution:", verdicts)


if __name__ == "__main__":
    main()
