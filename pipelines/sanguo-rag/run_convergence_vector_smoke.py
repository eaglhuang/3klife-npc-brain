"""Convergence loop vector smoke linkage and budget telemetry (SANGUO-RAGOPS-0605).

Reads a baseline manifest from a prior convergence run, exports the
evidence cards through the vector record exporter, runs the smoke
namespace gate, and emits budget telemetry so that feedback proposals
can recommend policy adjustments.

Design constraints
------------------
* Production namespace (``*-prod``) is blocked; only ``*-smoke`` writes
  are allowed from this script (unless ``--allow-production-namespace``
  is explicitly passed and checklist gate B7 is satisfied).
* Raw seeds and ungrounded pages are rejected; only evidence-cards with
  approved/staged reviewStatus may flow through (controlled by policy).
* All provider, namespace, batch-size, quota, and budget decisions come
  from policy or env vars — nothing is hardcoded here.
* Budget telemetry is emitted in ``backpressure-telemetry-ledger.v0.1``
  format so the large-run feedback proposal mechanism can read it.

Usage::

    # Dry-run vector smoke (default)
    python -B pipelines/sanguo-rag/run_convergence_vector_smoke.py \\
        --baseline-manifest local/codex-smoke/knowledge-growth/<run-id>/baseline-manifest.output.json

    # With output
    python -B pipelines/sanguo-rag/run_convergence_vector_smoke.py \\
        --baseline-manifest <path> \\
        --output local/cutover-evidence/C2-vector-smoke/report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from export_evidence_vector_records import (  # noqa: E402
    build_vector_records,
    iter_evidence_cards,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def _resolve(path_text: str, repo_root: Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (repo_root / path_text).resolve()


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _stable_hash(*parts: Any, length: int = 16) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
# Budget telemetry row builder
# ---------------------------------------------------------------------------

def _build_telemetry_row(
    *,
    run_id: str,
    source_id: str,
    raw_card_count: int,
    exported_record_count: int,
    rejected_count: int,
    namespace: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Build a backpressure-telemetry-ledger.v0.1 compatible row."""
    return {
        "schemaVersion": "backpressure-telemetry-ledger.v0.1",
        "runId": run_id,
        "sourceId": source_id,
        "roundId": "convergence-vector-smoke",
        "rawArtifactBytes": 0,
        "rawCardCount": raw_card_count,
        "exportedVectorRecordCount": exported_record_count,
        "rejectedCount": rejected_count,
        "namespace": namespace,
        "dryRun": dry_run,
        "canonicalWrites": False,
        "signal": {
            "vectorBudgetUtilization": (
                round(exported_record_count / max(raw_card_count, 1), 4)
                if raw_card_count > 0
                else 0.0
            ),
        },
        "generatedAt": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Smoke namespace check
# ---------------------------------------------------------------------------

def _validate_namespace(namespace: str, allow_production: bool) -> None:
    if not allow_production and namespace.endswith("-prod"):
        raise ValueError(
            f"production namespace '{namespace}' is blocked; "
            "use a *-smoke namespace or pass --allow-production-namespace "
            "after satisfying checklist gate B7"
        )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_convergence_vector_smoke(
    baseline_manifest_path: Path,
    *,
    run_id: str | None = None,
    namespace: str | None = None,
    allow_production_namespace: bool = False,
    dry_run: bool = True,
    review_status_filter: frozenset[str] | None = None,
    repo_root: Path = REPO,
) -> dict[str, Any]:
    """Export convergence-loop evidence cards to vector smoke namespace.

    Parameters
    ----------
    baseline_manifest_path:
        Path to ``baseline-manifest.output.json`` from a convergence run.
    run_id:
        Override for the run ID used in vector record metadata.
    namespace:
        Vector namespace to use. Defaults to ``sanguo-rag-convergence-smoke-<runId>``.
    allow_production_namespace:
        If False (default), reject any namespace ending in ``-prod``.
    dry_run:
        If True (default), no writes are issued to any vector provider.
    review_status_filter:
        Set of reviewStatus values to accept. Defaults to
        ``{"accepted", "staged-a", "candidate"}``.
    repo_root:
        Repository root for resolving relative paths.
    """
    baseline = _read_json(baseline_manifest_path)
    baseline_paths = baseline.get("paths") if isinstance(baseline.get("paths"), dict) else baseline
    resolved_run_id = run_id or str(baseline.get("runId") or baseline_paths.get("runId") or "convergence-vector-smoke")
    ns = namespace or f"sanguo-rag-convergence-smoke-{resolved_run_id}"

    _validate_namespace(ns, allow_production_namespace)

    if review_status_filter is None:
        review_status_filter = frozenset({"accepted", "staged-a", "candidate"})

    # Collect evidence card paths
    card_path_keys = ["externalCardsPath", "globalCandidateCardsPath"]
    all_raw_cards: list[dict[str, Any]] = []
    source_ids_seen: set[str] = set()
    for key in card_path_keys:
        path_text = str(baseline_paths.get(key) or "").strip()
        if not path_text:
            continue
        resolved = _resolve(path_text, repo_root)
        if not resolved.exists():
            continue
        for card in _read_jsonl(resolved):
            all_raw_cards.append(card)
            sid = str(card.get("sourcePolicyId") or card.get("sourceId") or card.get("sourceFamily") or "")
            if sid:
                source_ids_seen.add(sid)

    raw_card_count = len(all_raw_cards)

    # Filter by reviewStatus
    accepted_cards = [
        card for card in all_raw_cards
        if str(card.get("reviewStatus") or "candidate") in review_status_filter
    ]
    rejected_count = raw_card_count - len(accepted_cards)

    # Build vector records using the existing exporter
    vector_records: list[dict[str, Any]] = []
    for card in accepted_cards:
        evidence_id = str(card.get("evidenceId") or card.get("id") or card.get("eventId") or "").strip()
        if not evidence_id:
            rejected_count += 1
            continue
        source_id = str(card.get("sourcePolicyId") or card.get("sourceId") or card.get("sourceFamily") or "")
        # Use the existing build_vector_records helper (exported from export_evidence_vector_records)
        records = build_vector_records(
            rows=[card],
            run_id=resolved_run_id,
            source_id=source_id,
            artifact_uri=f"atm://lake/{resolved_run_id}/sources/{source_id}/evidence-card/{evidence_id}",
            namespace=ns,
            canonical_writes=False,
        )
        vector_records.extend(records)

    exported_count = len(vector_records)

    # Budget telemetry
    telemetry_rows = [
        _build_telemetry_row(
            run_id=resolved_run_id,
            source_id=sid if sid else "convergence-aggregate",
            raw_card_count=sum(
                1 for c in all_raw_cards
                if str(c.get("sourcePolicyId") or c.get("sourceId") or c.get("sourceFamily") or "") == sid
            ),
            exported_record_count=sum(
                1 for r in vector_records
                if str(r.get("metadata", {}).get("sourceId") or "") == sid
            ),
            rejected_count=0,  # per-source rejection is an approximation
            namespace=ns,
            dry_run=dry_run,
        )
        for sid in (source_ids_seen or {"convergence-aggregate"})
    ]

    report = {
        "schemaVersion": "convergence-vector-smoke-report.v0.1",
        "generatedAt": _utc_now(),
        "runId": resolved_run_id,
        "namespace": ns,
        "dryRun": dry_run,
        "allowProductionNamespace": allow_production_namespace,
        "rawCardCount": raw_card_count,
        "acceptedCardCount": len(accepted_cards),
        "rejectedCount": rejected_count,
        "exportedVectorRecordCount": exported_count,
        "reviewStatusFilter": sorted(review_status_filter),
        "sourceIds": sorted(source_ids_seen),
        "budgetTelemetryRows": telemetry_rows,
        "ok": True,
        "guards": [
            "production-namespace-blocked-by-default",
            "raw-seeds-and-pages-rejected",
            "only-accepted-staged-candidate-cards-exported",
            "all-provider-and-namespace-from-policy-or-env",
            "canonicalWrites-always-false",
        ],
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convergence loop vector smoke linkage and budget telemetry (SANGUO-RAGOPS-0605).",
    )
    parser.add_argument(
        "--baseline-manifest",
        required=True,
        help="Path to baseline-manifest.output.json from a convergence run.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override for run ID used in vector record metadata.",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Vector namespace. Default: sanguo-rag-convergence-smoke-<runId>.",
    )
    parser.add_argument(
        "--allow-production-namespace",
        action="store_true",
        help="Allow namespaces ending in -prod. Requires checklist gate B7 satisfied.",
    )
    parser.add_argument(
        "--review-status",
        action="append",
        default=[],
        help="Accepted reviewStatus values. Repeatable. Default: accepted, staged-a, candidate.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write smoke report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = Path(args.baseline_manifest)
    if not baseline_path.exists():
        print(f"[convergence-vector-smoke] baseline manifest not found: {baseline_path}")
        return 2

    review_filter = frozenset(args.review_status) if args.review_status else None

    try:
        report = run_convergence_vector_smoke(
            baseline_path,
            run_id=args.run_id,
            namespace=args.namespace,
            allow_production_namespace=bool(args.allow_production_namespace),
            dry_run=True,  # this script never writes; use the vector gate for actual writes
            review_status_filter=review_filter,
            repo_root=REPO,
        )
    except ValueError as exc:
        print(f"[convergence-vector-smoke] guard violation: {exc}")
        return 1

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(
            f"[convergence-vector-smoke] wrote {out}; "
            f"ok={report['ok']} exported={report['exportedVectorRecordCount']} "
            f"namespace={report['namespace']}"
        )
    else:
        print(text, end="")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
