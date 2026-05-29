from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)


RELATIONSHIP_LABELS: dict[str, str] = {
    "parent_child": "親子",
    "adoptive_parent_child": "義親子",
    "spouse": "夫妻",
    "sibling": "手足",
    "sworn_sibling": "結義手足",
    "ruler_subject": "君臣",
    "faction_membership": "陣營歸屬",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adapt baihua bootstrap candidates to trust-zone review lane records.")
    parser.add_argument("--input-path", default="")
    parser.add_argument("--output-root", default=str(REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"))
    parser.add_argument("--output-file-name", default="top50-bootstrap-review-lane.jsonl")
    parser.add_argument("--summary-file-name", default="top50-bootstrap-review-lane-summary.json")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be object: {path}:{line_no}")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def relationship_label(relationship_type: str) -> str:
    return RELATIONSHIP_LABELS.get(relationship_type, relationship_type)


def bootstrap_score(stage: str, support_count: int, has_conflict: bool) -> float:
    if has_conflict:
        return 72.0
    if stage == "review-ready":
        return min(89.0, 84.0 + min(4, support_count) * 1.25)
    if stage == "bootstrap-candidate":
        return min(84.0, 79.0 + min(3, support_count) * 1.2)
    return 78.0


def trust_zone_id(trust_key: str) -> str:
    digest = hashlib.sha1(trust_key.encode("utf-8")).hexdigest()[:18]
    return f"bootstrap-trustzone.{digest}"


def build_claim_sentence(from_id: str, to_id: str, relationship_type: str) -> str:
    label = relationship_label(relationship_type)
    return f"{from_id} 與 {to_id} 具有「{label}」關係。"


def trim_quote(quote: str, limit: int = 180) -> str:
    value = quote.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    input_path = (
        Path(args.input_path).resolve()
        if str(args.input_path).strip()
        else output_root / "merged-bootstrap-candidates-conflict-checked.jsonl"
    )
    output_path = output_root / args.output_file_name
    summary_path = output_root / args.summary_file_name

    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    input_rows = read_jsonl(input_path)
    output_rows: list[dict[str, Any]] = []
    stage_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    score_values: list[float] = []

    for row in input_rows:
        trust_key = str(row.get("trustKey") or "").strip()
        relationship_type = str(row.get("relationshipType") or "").strip()
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        bootstrap_stage = str(row.get("bootstrapStage") or "bootstrap-candidate").strip()
        support_count = int(row.get("supportCount") or 0)
        evidence_quotes = row.get("evidenceQuotes") if isinstance(row.get("evidenceQuotes"), list) else []
        conflict_flags = [str(item).strip() for item in (row.get("conflictFlags") or []) if str(item or "").strip()]
        has_conflict = len(conflict_flags) > 0
        if not trust_key or not relationship_type or not from_id or not to_id:
            continue

        stage_counter[bootstrap_stage] += 1
        type_counter[relationship_type] += 1
        score = round(bootstrap_score(bootstrap_stage, support_count, has_conflict), 2)
        score_values.append(score)

        support_rows: list[dict[str, Any]] = []
        for index, quote_value in enumerate(evidence_quotes[:5], 1):
            support_rows.append(
                {
                    "claimId": f"bootstrap-{trust_zone_id(trust_key)}-{index}",
                    "sourceFamily": "baihua-bootstrap",
                    "sourceLayer": "stable-bootstrap-seed",
                    "sourceId": "sanguoyanyi-baihua-zh-tw",
                    "quote": trim_quote(str(quote_value or "")),
                    "locator": "",
                    "textHash": "",
                    "pairRelationSignal": True,
                    "directPairSignal": True,
                    "confidenceSignals": ["anchor-passage-pair-cue", "baihua-translation-anchor"],
                    "canonicalWrites": False,
                }
            )
        if not support_rows:
            support_rows.append(
                {
                    "claimId": f"bootstrap-{trust_zone_id(trust_key)}-1",
                    "sourceFamily": "baihua-bootstrap",
                    "sourceLayer": "stable-bootstrap-seed",
                    "sourceId": "sanguoyanyi-baihua-zh-tw",
                    "quote": "",
                    "locator": "",
                    "textHash": "",
                    "pairRelationSignal": True,
                    "directPairSignal": True,
                    "confidenceSignals": ["anchor-passage-pair-cue"],
                    "canonicalWrites": False,
                }
            )

        label = relationship_label(relationship_type)
        blockers = ["bootstrap-needs-review-before-stable"]
        if has_conflict:
            blockers.append("bootstrap-conflict-needs-human")

        output_rows.append(
            {
                "trustZoneId": trust_zone_id(trust_key),
                "trustKey": trust_key,
                "dimension": "relationship",
                "relationshipType": relationship_type,
                "fromId": from_id,
                "toId": to_id,
                "subjectId": to_id,
                "controllerId": from_id,
                "score": score,
                "zone": "review",
                "bootstrapStage": bootstrap_stage,
                "canonicalWrites": False,
                "noRecompute": False,
                "negativeCondition": False,
                "fixedAliasLike": False,
                "evidenceCount": len(support_rows),
                "distinctSourceFamilies": ["baihua-bootstrap"],
                "supportingEvidence": support_rows,
                "stableBlockers": blockers,
                "conflictFlags": conflict_flags,
                "claimSentenceZhTw": build_claim_sentence(from_id, to_id, relationship_type),
                "humanReview": {
                    "decision": "pending",
                    "reviewer": "human",
                    "canonicalWrites": False,
                },
                "scope": {
                    "mode": "metadata-only",
                    "values": [
                        {
                            "scopeKey": "global",
                            "chapter": None,
                            "validFrom": None,
                            "validTo": None,
                        }
                    ],
                },
                "factCheckQueries": [
                    {
                        "polarity": "support",
                        "query": f"{from_id} {to_id} {label} 關係 證據",
                        "targets": ["approved-external-relationship-sites", "anchor-corpus", "stable-bootstrap", "relationship-claim-graph"],
                    },
                    {
                        "polarity": "challenge",
                        "query": f"{from_id} 不是 {to_id} {label} 關係",
                        "targets": ["approved-external-relationship-sites", "anchor-corpus", "stable-bootstrap", "relationship-claim-graph"],
                    },
                ],
            }
        )

    output_rows.sort(key=lambda item: (str(item.get("relationshipType")), str(item.get("fromId")), str(item.get("toId"))))
    write_jsonl(output_path, output_rows)
    summary = {
        "mode": "baihua-bootstrap-to-trust-zone-adapter",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "candidatePath": str(input_path),
            "candidateCount": len(input_rows),
        },
        "outputs": {
            "reviewLanePath": str(output_path),
            "summaryPath": str(summary_path),
            "reviewLaneCount": len(output_rows),
            "bootstrapStageCounts": dict(sorted(stage_counter.items())),
            "relationshipTypeCounts": dict(sorted(type_counter.items())),
            "scoreAverage": round(mean(score_values), 3) if score_values else 0.0,
        },
    }
    write_json(summary_path, summary)
    print(f"[adapt_baihua_bootstrap_to_trust_zone] wrote {output_path}")
    print(f"[adapt_baihua_bootstrap_to_trust_zone] wrote {summary_path}")
    print(
        "[adapt_baihua_bootstrap_to_trust_zone] "
        f"rows={len(output_rows)} scoreAverage={summary['outputs']['scoreAverage']} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
