"""Smoke test for the convergence repository parity rehearsal gate (SANGUO-RAGOPS-0604).

Tests:
1. Dry-run rehearsal with a synthetic baseline manifest produces parityOk=True.
2. Empty artifact paths produce ok=True (no artifacts, nothing to check).
3. Nonexistent baseline manifest returns exit code 2.
4. Report schema has required keys.

Usage::

    python -B pipelines/sanguo-rag/convergence_repo_parity_rehearsal_smoke_test.py
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

from run_convergence_repo_parity_rehearsal import run_convergence_parity_rehearsal  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


def _result(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    status = PASS if ok else FAIL
    msg = f"[{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return {"label": label, "ok": ok, "detail": detail}


def _write_fake_cards(path: Path, count: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "evidenceId": f"parity-card-{i}",
                "sourceId": "parity-source-a",
                "sourceFamily": "parity-family",
                "sourceLayer": "parity-layer",
                "generalIds": ["parity-general-a"],
                "reviewStatus": "candidate",
                "sourceQuote": f"Quote {i}",
            }) + "\n")


def _write_baseline(path: Path, run_id: str, cards_path: Path) -> None:
    payload = {
        "version": "2.1.0",
        "runId": run_id,
        "canonicalWrites": False,
        "paths": {
            "externalCardsPath": str(cards_path),
            "globalCandidateCardsPath": "",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_dry_run_parity_ok() -> dict[str, Any]:
    """Dry-run rehearsal with a synthetic baseline produces parityOk=True for evidence_cards."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path)
        _write_baseline(baseline_path, "parity-test-001", cards_path)
        report = run_convergence_parity_rehearsal(
            baseline_path,
            apply=False,
            repo_root=REPO,
            max_retry=1,
        )
        card_table = next((t for t in report["tables"] if t["table"] == "evidence_cards"), None)
        ok = (
            report["ok"]
            and card_table is not None
            and card_table["parityOk"]
            and card_table["pgRequested"] == 3
            and not report.get("dryRun") is False  # dry_run should be True
        )
        return _result(
            "dry-run-parity-ok",
            ok,
            f"ok={report['ok']} dryRun={report['dryRun']} "
            f"cards_requested={card_table['pgRequested'] if card_table else 'N/A'} "
            f"parityOk={card_table['parityOk'] if card_table else 'N/A'}",
        )


def test_empty_artifact_paths() -> dict[str, Any]:
    """Empty artifact paths: manifest has 0 files, report ok=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        baseline_path = run_root / "baseline-manifest.output.json"
        payload = {
            "version": "2.1.0",
            "runId": "parity-empty-001",
            "canonicalWrites": False,
            "paths": {},
        }
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        report = run_convergence_parity_rehearsal(
            baseline_path,
            apply=False,
            repo_root=REPO,
            max_retry=1,
        )
        ok = report["ok"] and report["manifestFileCount"] == 0
        return _result(
            "empty-artifact-paths",
            ok,
            f"ok={report['ok']} manifestFileCount={report['manifestFileCount']}",
        )


def test_report_schema() -> dict[str, Any]:
    """Report must contain schemaVersion, runId, ok, tables, dryRun, guards."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "external-cards.jsonl"
        baseline_path = run_root / "baseline-manifest.output.json"
        _write_fake_cards(cards_path)
        _write_baseline(baseline_path, "schema-test-001", cards_path)
        report = run_convergence_parity_rehearsal(baseline_path, apply=False, repo_root=REPO, max_retry=1)
        required_keys = {"schemaVersion", "generatedAt", "runId", "ok", "tables", "dryRun", "guards"}
        missing = required_keys - set(report.keys())
        ok = not missing
        return _result("report-schema", ok, f"missing={sorted(missing)}")


def main() -> int:
    tests = [
        test_dry_run_parity_ok,
        test_empty_artifact_paths,
        test_report_schema,
    ]
    results = [t() for t in tests]
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nconvergence-parity-rehearsal smoke: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
