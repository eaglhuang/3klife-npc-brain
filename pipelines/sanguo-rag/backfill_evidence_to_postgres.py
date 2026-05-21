"""Backfill JSONL evidence artifacts into the PostgreSQL evidence-lake mirror.

SANGUO-RAGOPS-0203. Reads an evidence manifest (SANGUO-RAGOPS-0102) and
its referenced JSONL artifacts, then upserts rows into the PostgreSQL
evidence-lake schema (SANGUO-RAGOPS-0201) via the repository adapter
(SANGUO-RAGOPS-0202).

Default mode is ``--dry-run`` and ``mode=postgres`` (offline preview). To
actually write the PostgreSQL mirror, pass ``--apply`` and ensure
``SANGUO_RAG_PG_DSN`` is set. JSONL artifacts are never modified by this
runner; the only side effect on JSONL is the repository's ``_state/``
ledger if mode=dual.

The runner emits a parity report comparing JSONL row counts and sha256
fingerprints against the rows upserted into PostgreSQL. The report
satisfies the parity acceptance criterion for M2-0204.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_manifest import EvidenceManifest, load_manifest  # noqa: E402
from evidence_repository import (  # noqa: E402
    RepositorySettings,
    RetryPolicy,
    WriteResult,
    build_repository,
)


# =========================================================================
# JSONL row interpreters
# =========================================================================

ARTIFACT_TYPE_TO_TABLE = {
    "evidence-seed": "evidence_seeds",
    "evidence-card": "evidence_cards",
    "anchor-passage": "anchor_passages",
    "harvested-page": "harvested_pages",
    "proposal": "proposal_ledger",
}


def _row_count_and_hash(path: Path) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    hasher = hashlib.sha256()
    rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows += 1
                hasher.update(line.strip().encode("utf-8"))
                hasher.update(b"\n")
    return rows, hasher.hexdigest()


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _coerce_evidence_seed(row: dict[str, Any], run_id: str, source_id: str, payload_uri: str) -> dict[str, Any] | None:
    seed_id = row.get("seedId") or row.get("id")
    if not seed_id:
        return None
    return {
        "seed_id": str(seed_id),
        "run_id": run_id,
        "source_id": source_id,
        "general_id": str(row.get("generalId") or ""),
        "angle_type": str(row.get("angleType") or row.get("angle") or ""),
        "seed_text_hash": str(row.get("seedTextHash") or row.get("textHash") or hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()),
        "score": row.get("score") or {},
        "anchor": row.get("anchor") or {},
        "payload": row,
        "payload_uri": payload_uri,
    }


def _coerce_evidence_card(row: dict[str, Any], run_id: str, source_id: str, payload_uri: str) -> dict[str, Any] | None:
    evidence_id = row.get("evidenceId") or row.get("id")
    if not evidence_id:
        return None
    return {
        "evidence_id": str(evidence_id),
        "run_id": run_id,
        "source_id": source_id,
        "source_family": str(row.get("sourceFamily") or ""),
        "source_layer": str(row.get("sourceLayer") or ""),
        "general_ids": list(row.get("generalIds") or []),
        "quote_hash": str(row.get("quoteHash") or hashlib.sha256(str(row.get("sourceQuote") or "").encode("utf-8")).hexdigest()),
        "locator": str(row.get("locator") or ""),
        "anchor_evidence": row.get("anchorEvidence") or {},
        "trust_score": row.get("trustScore") or {},
        "review_status": str(row.get("reviewStatus") or "candidate"),
        "payload": row,
        "payload_uri": payload_uri,
    }


def _coerce_anchor_passage(row: dict[str, Any], run_id: str, payload_uri: str) -> dict[str, Any] | None:
    passage_id = row.get("passageId") or row.get("id")
    if not passage_id:
        return None
    return {
        "passage_id": str(passage_id),
        "run_id": run_id,
        "corpus_id": str(row.get("corpusId") or ""),
        "layer": str(row.get("layer") or ""),
        "locator": str(row.get("locator") or ""),
        "text_hash": str(row.get("textHash") or hashlib.sha256(str(row.get("normalizedText") or "").encode("utf-8")).hexdigest()),
        "normalized_text": str(row.get("normalizedText") or ""),
        "artifact_uri": payload_uri,
        "raw_payload": row,
    }


def _coerce_harvested_page(row: dict[str, Any], run_id: str, source_id: str, artifact_uri: str) -> dict[str, Any] | None:
    url = row.get("url")
    if not url:
        return None
    url_hash = str(row.get("urlHash") or hashlib.sha256(str(url).encode("utf-8")).hexdigest())
    return {
        "run_id": run_id,
        "source_id": source_id,
        "url": str(url),
        "url_hash": url_hash,
        "title": str(row.get("title") or ""),
        "text_hash": str(row.get("textHash") or hashlib.sha256(str(row.get("bodyText") or row.get("rawText") or "").encode("utf-8")).hexdigest()),
        "body_start": row.get("bodyStart"),
        "body_end": row.get("bodyEnd"),
        "raw_bytes": int(row.get("rawBytes") or 0),
        "artifact_uri": artifact_uri,
        "source_policy_id": str(row.get("sourcePolicyId") or ""),
        "raw_payload": row,
    }


def _coerce_proposal(row: dict[str, Any], run_id: str, source_id: str, artifact_uri: str) -> dict[str, Any] | None:
    proposal_id = row.get("proposalId") or row.get("id")
    if not proposal_id:
        return None
    kind = str(row.get("proposalKind") or row.get("kind") or "body-boundary-residual")
    return {
        "proposal_id": str(proposal_id),
        "run_id": run_id,
        "proposal_kind": kind,
        "source_id": source_id,
        "signature": str(row.get("signature") or hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()),
        "status": str(row.get("status") or "proposed"),
        "sandbox_outcome": row.get("sandboxOutcome") or {},
        "payload": row,
        "artifact_uri": artifact_uri,
        "decided_at": row.get("decidedAt"),
    }


def _coerce(table: str, row: dict[str, Any], context: dict[str, str]) -> dict[str, Any] | None:
    run_id = context["run_id"]
    source_id = context.get("source_id", "")
    artifact_uri = context.get("artifact_uri", "")
    if table == "evidence_seeds":
        return _coerce_evidence_seed(row, run_id, source_id, artifact_uri)
    if table == "evidence_cards":
        return _coerce_evidence_card(row, run_id, source_id, artifact_uri)
    if table == "anchor_passages":
        return _coerce_anchor_passage(row, run_id, artifact_uri)
    if table == "harvested_pages":
        return _coerce_harvested_page(row, run_id, source_id, artifact_uri)
    if table == "proposal_ledger":
        return _coerce_proposal(row, run_id, source_id, artifact_uri)
    return None


# =========================================================================
# Backfill driver
# =========================================================================

def backfill(manifest: EvidenceManifest, settings: RepositorySettings, lake_root: Path) -> dict[str, Any]:
    repo = build_repository(settings)
    jsonl_counts: Counter[str] = Counter()
    jsonl_hashes: dict[str, str] = {}
    pg_results: dict[str, WriteResult] = {}
    parity: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    # Always insert pipeline_runs first
    pipeline_row = {
        "run_id": manifest.run_id,
        "lane": manifest.lane or "",
        "run_profile": str(manifest.summary.get("runProfile") or ""),
        "input_fingerprint": str(manifest.input_fingerprint.get("sha256") or ""),
        "canonical_writes": bool(manifest.canonical_writes),
        "status": str(manifest.summary.get("status") or "succeeded"),
        "started_at": manifest.generated_at,
        "finished_at": manifest.updated_at,
        "summary": dict(manifest.summary),
        "policy_refs": list(manifest.policy_refs),
        "raw_payload": {},
    }
    pg_results["pipeline_runs"] = repo.upsert("pipeline_runs", [pipeline_row])
    jsonl_counts["pipeline_runs"] = 1

    # Group manifest entries by (table, source_id)
    grouped: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    for entry in manifest.files:
        table = ARTIFACT_TYPE_TO_TABLE.get(entry.artifact_type)
        if not table:
            continue
        candidate = (lake_root / entry.path) if not Path(entry.path).is_absolute() else Path(entry.path)
        grouped.setdefault((table, entry.source_id), []).append((candidate, entry.artifact_uri))

    # source_runs roll-up
    source_runs_rows: list[dict[str, Any]] = []
    per_source: dict[str, dict[str, int]] = {}

    for (table, source_id), paths in grouped.items():
        rows_to_write: list[dict[str, Any]] = []
        for path, artifact_uri in paths:
            count, digest = _row_count_and_hash(path)
            jsonl_counts[table] += count
            jsonl_hashes.setdefault(table, "")
            if digest:
                jsonl_hashes[table] = hashlib.sha256(
                    (jsonl_hashes[table] + digest).encode("utf-8")
                ).hexdigest()
            for raw_row in _iter_jsonl(path):
                coerced = _coerce(table, raw_row, {
                    "run_id": manifest.run_id,
                    "source_id": source_id,
                    "artifact_uri": artifact_uri,
                })
                if coerced is None:
                    skipped.append({"table": table, "sourceId": source_id, "path": str(path)})
                    continue
                rows_to_write.append(coerced)
            bucket = per_source.setdefault(source_id, {"harvested": 0, "seed": 0, "card": 0})
            if table == "harvested_pages":
                bucket["harvested"] += count
            elif table == "evidence_seeds":
                bucket["seed"] += count
            elif table == "evidence_cards":
                bucket["card"] += count
        if rows_to_write:
            existing = pg_results.get(table)
            current = repo.upsert(table, rows_to_write)
            pg_results[table] = existing.merge(current) if existing else current

    for source_id, counts in per_source.items():
        source_runs_rows.append({
            "run_id": manifest.run_id,
            "source_id": source_id,
            "source_family": "",
            "source_layer": "",
            "fetch_count": counts.get("harvested", 0),
            "harvested_count": counts.get("harvested", 0),
            "seed_count": counts.get("seed", 0),
            "card_count": counts.get("card", 0),
            "timeout_count": 0,
            "roi_score": None,
            "body_boundary_summary": {},
            "raw_payload": {},
        })
    if source_runs_rows:
        pg_results["source_runs"] = repo.upsert("source_runs", source_runs_rows)
        jsonl_counts["source_runs"] = len(source_runs_rows)

    for table, write_result in pg_results.items():
        backend_count = max(1, write_result.backend_count)
        logical_row_count = jsonl_counts.get(table, write_result.requested // backend_count)
        # Sum first, divide later: backends may differ on which row they
        # consider "written" vs "skipped_duplicate" (the JSONL backend may
        # see prior rows as duplicates while PG ON CONFLICT counts them as
        # writes, and vice versa). Floor-dividing each tally separately
        # under-counts mismatched cases.
        per_backend_total = (write_result.written + write_result.skipped_duplicate) // backend_count
        parity.append({
            "table": table,
            "jsonlRowCount": logical_row_count,
            "jsonlSha256": jsonl_hashes.get(table, ""),
            "pgRequested": write_result.requested,
            "pgWritten": write_result.written,
            "pgSkippedDuplicate": write_result.skipped_duplicate,
            "pgErrors": write_result.errors,
            "backendCount": backend_count,
            "perBackendTotal": per_backend_total,
            "parityOk": (
                logical_row_count == per_backend_total
                and not write_result.errors
            ),
        })

    repo.close()
    overall_ok = all(item["parityOk"] for item in parity)
    return {
        "schemaVersion": "backfill-parity-report.v0.1",
        "runId": manifest.run_id,
        "mode": settings.mode,
        "dryRun": settings.dry_run,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "parity": parity,
        "skipped": skipped,
        "ok": overall_ok,
        "guards": [
            "jsonl-canonical-export-not-modified",
            "no-destructive-postgres-operation",
            "idempotent-upsert-only",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill JSONL evidence artifacts into PostgreSQL evidence-lake mirror (SANGUO-RAGOPS-0203).",
    )
    parser.add_argument("--manifest", required=True, help="evidence manifest JSON path")
    parser.add_argument("--lake-root", default=".", help="root prefix for resolving manifest entry paths")
    parser.add_argument("--mode", default="postgres", choices=["jsonl", "postgres", "dual"], help="repository mode")
    parser.add_argument("--apply", action="store_true", help="actually write to PostgreSQL (default is dry-run)")
    parser.add_argument("--retry", type=int, default=3, help="max attempts for PostgreSQL upsert")
    parser.add_argument("--output", default="", help="optional path to write parity report JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"[backfill_evidence_to_postgres] manifest not found: {manifest_path}")
        return 2
    manifest = load_manifest(manifest_path)
    settings = RepositorySettings.from_env(
        mode=args.mode,
        dry_run=not args.apply,
    )
    settings.retry = RetryPolicy(max_attempts=args.retry)
    settings.jsonl_root = Path(args.lake_root)
    report = backfill(manifest, settings, lake_root=Path(args.lake_root))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[backfill_evidence_to_postgres] wrote {out}")
    else:
        print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
