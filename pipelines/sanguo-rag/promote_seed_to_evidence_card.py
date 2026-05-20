from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_OUTPUT_ROOT = Path("local/codex-smoke/knowledge-growth/external-evidence-seeds-v3-r1")
DEFAULT_RANKING_JSON = DEFAULT_OUTPUT_ROOT / "external-evidence-seed-ranking.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_person_allowlist(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    payload = read_json(path)
    if isinstance(payload, dict):
        rows = payload.get("personIds")
    else:
        rows = payload
    if not isinstance(rows, list):
        return set()
    return {str(item or "").strip() for item in rows if str(item or "").strip()}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def stable_hash(*parts: Any, length: int = 20) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def person_id(seed: dict[str, Any]) -> str:
    return str(seed.get("generalId") or seed.get("candidatePersonId") or "").strip()


def can_promote(seed: dict[str, Any], min_score: float) -> tuple[bool, str]:
    if seed.get("promotionTarget") != "candidate-card":
        return False, "promotionTarget is not candidate-card"
    if float(seed.get("seedConfidenceScore") or 0.0) < min_score:
        return False, "seedConfidenceScore below threshold"
    if not person_id(seed):
        return False, "missing generalId/candidatePersonId"
    if not (seed.get("quote") or seed.get("seedText")):
        return False, "missing quote/seedText"
    if not (seed.get("locator") or seed.get("textHash")):
        return False, "missing locator/textHash"
    return True, "ok"


def claim_scopes_for_angle(angle: str) -> list[str]:
    if angle in {"identity", "relationship", "event", "location", "title", "trait", "activity", "role"}:
        return [angle]
    if angle in {"habit", "dialogue_seed", "worldbuilding_note"}:
        return ["trait", "activity"]
    return ["identity", "relationship", "event", "location", "title", "trait", "activity"]


def card_from_seed(seed: dict[str, Any]) -> dict[str, Any]:
    angle = str(seed.get("angleType") or "worldbuilding_note").strip()
    seed_id = str(seed.get("seedId") or "")
    evidence_id = "candidate-card:{digest}".format(
        digest=stable_hash(seed_id, seed.get("sourceUrl"), seed.get("locator"), seed.get("quote") or seed.get("seedText"))
    )
    person = person_id(seed)
    card: dict[str, Any] = {
        "version": "3.0.0",
        "evidenceId": evidence_id,
        "sourceSeedId": seed_id,
        "sourceEvidenceId": seed.get("sourceEvidenceId"),
        "sourcePolicyId": seed.get("sourceId"),
        "sourceFamily": seed.get("sourceFamily"),
        "sourceLayer": seed.get("sourceLayer"),
        "trustTier": seed.get("trustTier"),
        "singleSourceMaxGrade": "B",
        "url": seed.get("sourceUrl"),
        "pageTitle": seed.get("pageTitle"),
        "locator": seed.get("locator"),
        "quote": seed.get("quote") or seed.get("seedText"),
        "translatedTraditionalText": seed.get("translatedTraditionalText"),
        "textHash": seed.get("textHash"),
        "claimType": angle,
        "claimScopes": claim_scopes_for_angle(angle),
        "seedConfidenceScore": seed.get("seedConfidenceScore"),
        "siteReliabilityMultiplier": seed.get("siteReliabilityMultiplier"),
        "crossSiteMatchCount": seed.get("crossSiteMatchCount"),
        "crossSiteSourceFamilies": seed.get("crossSiteSourceFamilies") or [],
        "translationProfile": seed.get("translationProfile"),
        "sourceLanguage": seed.get("sourceLanguage"),
        "reviewGrade": "B",
        "promotionState": "staged-candidate",
        "canonicalWrites": False,
    }
    if seed.get("generalId"):
        card["generalIds"] = [person]
    else:
        card["candidatePersonIds"] = [person]
    return card


def render_markdown(summary: dict[str, Any], rejected: list[dict[str, Any]]) -> str:
    lines = [
        "# Seed -> Candidate Evidence Card Promotion",
        "",
        f"- Generated At: `{summary['generatedAt']}`",
        f"- canonicalWrites: `{summary['canonicalWrites']}`",
        f"- Promoted Candidate Cards: `{summary['metrics']['candidateCardCount']}`",
        f"- Rejected Seeds: `{summary['metrics']['rejectedSeedCount']}`",
        "",
        "這一步只產生 `staged-candidate`，單一網站或單一 seed 不會自動升 A，也不會寫入正式 canonical。",
        "",
        "## Rejection Reasons",
        "",
        "| Reason | Count |",
        "| --- | ---: |",
    ]
    reason_counts: dict[str, int] = {}
    for row in rejected:
        reason = str(row.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {reason} | {count} |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote high-quality EvidenceSeed rows into candidate EvidenceCard JSONL.")
    parser.add_argument("--ranking-json", default=str(DEFAULT_RANKING_JSON), help="Input external-evidence-seed-ranking.json.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output directory.")
    parser.add_argument(
        "--person-allowlist-json",
        default=None,
        help="Optional JSON (array or {personIds: []}) allowlist; only seeds for listed person ids are promoted.",
    )
    parser.add_argument("--min-score", type=float, default=70.0, help="Minimum seedConfidenceScore for candidate card promotion.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting outputs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ranking_path = resolve_path(args.ranking_json)
    allowlist_path = resolve_path(args.person_allowlist_json) if args.person_allowlist_json else None
    output_root = resolve_path(args.output_root)
    cards_path = output_root / "candidate-evidence-cards.jsonl"
    summary_path = output_root / "candidate-evidence-card-summary.json"
    markdown_path = output_root / "candidate-evidence-card-summary.zh-TW.md"
    if (cards_path.exists() or summary_path.exists() or markdown_path.exists()) and not args.overwrite:
        raise SystemExit(f"Promotion outputs already exist under {repo_relative(output_root)}")

    ranking = read_json(ranking_path)
    seeds = ranking.get("rankedSeeds") if isinstance(ranking, dict) else []
    if not isinstance(seeds, list):
        seeds = []
    person_allowlist = load_person_allowlist(allowlist_path)

    cards: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for seed in seeds:
        if not isinstance(seed, dict):
            continue
        pid = person_id(seed)
        if person_allowlist and pid not in person_allowlist:
            rejected.append({"seedId": seed.get("seedId"), "personId": pid, "reason": "personId not in allowlist"})
            continue
        ok, reason = can_promote(seed, args.min_score)
        if ok:
            cards.append(card_from_seed(seed))
        else:
            rejected.append({"seedId": seed.get("seedId"), "personId": pid, "reason": reason})

    written = write_jsonl(cards_path, cards)
    summary = {
        "version": "3.0.0",
        "generatedAt": utc_now(),
        "mode": "seed-to-candidate-evidence-card-promotion",
        "canonicalWrites": False,
        "inputs": {
            "rankingJson": repo_relative(ranking_path),
            "minScore": args.min_score,
            "personAllowlistJson": repo_relative(allowlist_path) if allowlist_path else None,
            "personAllowlistCount": len(person_allowlist),
        },
        "outputs": {
            "candidateEvidenceCardsJsonl": repo_relative(cards_path),
            "summaryJson": repo_relative(summary_path),
            "summaryMarkdown": repo_relative(markdown_path),
        },
        "metrics": {
            "inputSeedCount": len(seeds),
            "candidateCardCount": written,
            "rejectedSeedCount": len(rejected),
        },
        "notes": [
            "Candidate evidence cards are still review artifacts. They are not A-grade and do not write canonical data.",
            "Single-source evidence remains capped by singleSourceMaxGrade=B.",
        ],
    }
    write_json(summary_path, summary)
    markdown_path.write_text(render_markdown(summary, rejected), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ── SANGUO-AUTO-0303: Evidence card anchor schema ────────────────────────────

def build_anchor_evidence_for_card(anchor_result: dict[str, Any]) -> dict[str, Any]:
    """
    建立 card 的 anchorEvidence 子物件。
    規則：
    - anchor locator/hash 只能放在 supportingLocators/supportingTextHashes
    - 外部來源缺 locator 時，不能把 anchor locator 偽造為外部 locator
    - canonicalWrites=false
    """
    return {
        "anchorMatchCount": int(anchor_result.get("anchorMatchCount", 0)),
        "anchorHistoryMatchCount": int(anchor_result.get("anchorHistoryMatchCount", 0)),
        "anchorRomanceMatchCount": int(anchor_result.get("anchorRomanceMatchCount", 0)),
        "anchorVerdict": str(anchor_result.get("anchorVerdict", "unverified")),
        "supportingLocators": list(anchor_result.get("supportingLocators", [])),
        "supportingTextHashes": list(anchor_result.get("supportingTextHashes", [])),
        "canonicalWrites": False,
    }


def attach_anchor_evidence_to_card(
    card: dict[str, Any],
    anchor_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Attach anchor evidence to a candidate evidence card.
    anchor 的 locator 不會回填到 card 主欄位 locator。
    """
    if not anchor_result:
        return card
    updated = dict(card)
    updated["anchorEvidence"] = build_anchor_evidence_for_card(anchor_result)
    return updated


# ── SANGUO-AUTO-0304: Contradiction/unverified gate ──────────────────────────

ANCHOR_VERDICT_SUSPECTED_CONFLICT = "suspected-conflict"
ANCHOR_VERDICT_UNVERIFIED = "unverified"


def anchor_gate_check(
    card: dict[str, Any],
    anchor_result: dict[str, Any] | None,
) -> tuple[bool, str]:
    """
    Anchor verification gate:
    - suspected-conflict → block (requires human-review)
    - unverified → keep as seed-only, do not promote to A
    - returns (allowed_to_promote, reason)
    """
    if not anchor_result:
        return True, "no-anchor-evidence"
    verdict = str(anchor_result.get("anchorVerdict", "unverified"))
    if verdict == ANCHOR_VERDICT_SUSPECTED_CONFLICT:
        return False, f"anchor-gate: suspected-conflict for {card.get('evidenceId')}"
    if verdict == ANCHOR_VERDICT_UNVERIFIED:
        max_grade = card.get("singleSourceMaxGrade", "B")
        if max_grade in {"A", "A-history", "A-romance"}:
            return False, f"anchor-gate: unverified anchor cannot promote to A-grade"
    return True, "anchor-gate-pass"


if __name__ == "__main__":
    raise SystemExit(main())
