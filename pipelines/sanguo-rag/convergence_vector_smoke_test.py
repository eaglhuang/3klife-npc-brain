"""Smoke test for convergence vector smoke linkage and budget telemetry (SANGUO-RAGOPS-0605).

Tests:
1. Dry-run with synthetic baseline produces ok=True and exportedVectorRecordCount > 0.
2. Production namespace blocked by default guard.
3. Report schema has required keys.
4. Cards without valid evidenceId are rejected (rejectedCount increments).

Usage::

    python -B pipelines/sanguo-rag/convergence_vector_smoke_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_convergence_vector_smoke import run_convergence_vector_smoke  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


def _result(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    status = PASS if ok else FAIL
    msg = f"[{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return {"label": label, "ok": ok, "detail": detail}


def _write_fake_cards(path: Path, count: int = 3, *, review_status: str = "candidate") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "evidenceId": f"vec-card-{i}",
                "sourceId": "vec-source-a",
                "sourceFamily": "vec-family",
                "sourceLayer": "vec-layer",
                "generalIds": ["vec-general-a"],
                "reviewStatus": review_status,
                "sourceQuote": f"Vector smoke quote {i}: test content for embedding.",
            }) + "\n")


def _write_no_id_cards(path: Path, count: int = 2) -> None:
    """Cards missing evidenceId/id/eventId — should be rejected."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "sourceId": "no-id-source",
                "sourceFamily": "no-id-family",
                "sourceLayer": "no-id-layer",
                "generalIds": [],
                "reviewStatus": "candidate",
                "sourceQuote": f"Card with no evidence ID {i}.",
            }) + "\n")


def _write_baseline(path: Path, run_id: str, cards_path: Path,
                    extra_cards_path: Path | None = None) -> None:
    payload = {
        "version": "2.1.0",
        "runId": run_id,
        "canonicalWrites": False,
        "paths": {
            "externalCardsPath": str(cards_path),
            "globalCandidateCardsPath": str(extra_cards_path) if extra_cards_path else "",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_dry_run_ok() -> dict[str, Any]:
    """Dry-run with synthetic cards produces ok=True and exportedVectorRecordCount > 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path, count=3)
        _write_baseline(baseline_path, "vec-smoke-001", cards_path)
        report = run_convergence_vector_smoke(
            baseline_path,
            dry_run=True,
            repo_root=REPO,
        )
        ok = (
            report["ok"]
            and report["dryRun"]
            and report["rawCardCount"] == 3
            and report["exportedVectorRecordCount"] >= 1
            and not report["namespace"].endswith("-prod")
        )
        return _result(
            "dry-run-ok",
            ok,
            f"ok={report['ok']} exported={report['exportedVectorRecordCount']} "
            f"namespace={report['namespace']} raw={report['rawCardCount']}",
        )


def test_production_namespace_blocked() -> dict[str, Any]:
    """Namespace ending in -prod is blocked unless --allow-production-namespace passed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path, count=2)
        _write_baseline(baseline_path, "vec-smoke-002", cards_path)
        blocked = False
        try:
            run_convergence_vector_smoke(
                baseline_path,
                namespace="sanguo-rag-convergence-prod",
                allow_production_namespace=False,
                dry_run=True,
                repo_root=REPO,
            )
        except ValueError:
            blocked = True
        return _result(
            "production-namespace-blocked",
            blocked,
            "ValueError raised as expected" if blocked else "guard did NOT raise ValueError",
        )


def test_production_namespace_allowed_when_flag_set() -> dict[str, Any]:
    """Namespace ending in -prod succeeds when allow_production_namespace=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path, count=2)
        _write_baseline(baseline_path, "vec-smoke-003", cards_path)
        try:
            report = run_convergence_vector_smoke(
                baseline_path,
                namespace="sanguo-rag-convergence-prod",
                allow_production_namespace=True,
                dry_run=True,
                repo_root=REPO,
            )
            ok = report["ok"] and report["namespace"] == "sanguo-rag-convergence-prod"
        except ValueError as exc:
            ok = False
            return _result("production-namespace-allowed-when-flag-set", ok,
                           f"unexpected ValueError: {exc}")
        return _result(
            "production-namespace-allowed-when-flag-set",
            ok,
            f"ok={report['ok']} namespace={report['namespace']}",
        )


def test_report_schema() -> dict[str, Any]:
    """Report must contain all required top-level keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path, count=2)
        _write_baseline(baseline_path, "schema-test-001", cards_path)
        report = run_convergence_vector_smoke(
            baseline_path,
            dry_run=True,
            repo_root=REPO,
        )
        required_keys = {
            "schemaVersion",
            "generatedAt",
            "runId",
            "namespace",
            "dryRun",
            "ok",
            "rawCardCount",
            "acceptedCardCount",
            "rejectedCount",
            "exportedVectorRecordCount",
            "reviewStatusFilter",
            "sourceIds",
            "budgetTelemetryRows",
            "guards",
        }
        missing = required_keys - set(report.keys())
        ok = not missing
        return _result("report-schema", ok, f"missing={sorted(missing)}")


def test_no_id_cards_rejected() -> dict[str, Any]:
    """Cards missing evidenceId/id/eventId increment rejectedCount."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        valid_path = run_root / "external-cards.jsonl"
        no_id_path = run_root / "global-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(valid_path, count=3)   # 3 valid cards
        _write_no_id_cards(no_id_path, count=2)   # 2 cards with no evidenceId
        _write_baseline(baseline_path, "no-id-test-001", valid_path,
                        extra_cards_path=no_id_path)
        report = run_convergence_vector_smoke(
            baseline_path,
            dry_run=True,
            repo_root=REPO,
        )
        # 2 no-id cards should be rejected after passing reviewStatus filter
        ok = (
            report["ok"]
            and report["rawCardCount"] == 5
            and report["rejectedCount"] >= 2
            and report["exportedVectorRecordCount"] >= 3  # at least the valid ones
        )
        return _result(
            "no-id-cards-rejected",
            ok,
            f"raw={report['rawCardCount']} rejected={report['rejectedCount']} "
            f"exported={report['exportedVectorRecordCount']}",
        )


def test_budget_telemetry_format() -> dict[str, Any]:
    """Budget telemetry rows use backpressure-telemetry-ledger.v0.1 schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path, count=3)
        _write_baseline(baseline_path, "budget-test-001", cards_path)
        report = run_convergence_vector_smoke(
            baseline_path,
            dry_run=True,
            repo_root=REPO,
        )
        rows = report.get("budgetTelemetryRows") or []
        required_row_keys = {
            "schemaVersion",
            "runId",
            "sourceId",
            "roundId",
            "rawCardCount",
            "exportedVectorRecordCount",
            "rejectedCount",
            "namespace",
            "dryRun",
            "canonicalWrites",
            "signal",
            "generatedAt",
        }
        ok = len(rows) >= 1
        if ok:
            row = rows[0]
            missing = required_row_keys - set(row.keys())
            ok = not missing and row["schemaVersion"] == "backpressure-telemetry-ledger.v0.1"
            if not ok:
                return _result(
                    "budget-telemetry-format",
                    ok,
                    f"row_keys_missing={sorted(missing)}, schema={row.get('schemaVersion')}",
                )
        return _result(
            "budget-telemetry-format",
            ok,
            f"rows={len(rows)} schema={rows[0]['schemaVersion'] if rows else 'N/A'} "
            f"canonicalWrites={rows[0].get('canonicalWrites') if rows else 'N/A'}",
        )


def main() -> int:
    tests = [
        test_dry_run_ok,
        test_production_namespace_blocked,
        test_production_namespace_allowed_when_flag_set,
        test_report_schema,
        test_no_id_cards_rejected,
        test_budget_telemetry_format,
    ]
    results = [t() for t in tests]
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nconvergence-vector-smoke: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
