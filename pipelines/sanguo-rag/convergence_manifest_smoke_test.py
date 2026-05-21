"""Smoke tests for convergence manifest helper (SANGUO-RAGOPS-0603).

Tests:
1. build_convergence_manifest() produces a valid manifest with present files.
2. write_convergence_manifest() writes to run_root/evidence-manifest.json.
3. scan_prior_convergence_manifest() returns ok=True on unmodified files.
4. Hash mismatch detected correctly after file modification.
5. Missing file detected correctly.
6. Empty files list (dry-run) returns manifest with file_count=0.

Usage::

    python -B pipelines/sanguo-rag/convergence_manifest_smoke_test.py
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

from convergence_manifest_helper import (  # noqa: E402
    build_convergence_manifest,
    scan_prior_convergence_manifest,
    write_convergence_manifest,
)

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
            fh.write(json.dumps({"evidenceId": f"c{i}", "generalIds": ["g1"]}) + "\n")


def _write_fake_scoreboard(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": [], "version": "1.0"}) + "\n", encoding="utf-8")


def test_build_manifest_with_files() -> dict[str, Any]:
    """build_convergence_manifest() produces a manifest with entries for present files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "evidence-cards.jsonl"
        scoreboard_path = run_root / "scoreboard.json"
        _write_fake_cards(cards_path)
        _write_fake_scoreboard(scoreboard_path)
        final_paths = {
            "externalCardsPath": str(cards_path),
            "scoreboardJsonPath": str(scoreboard_path),
        }
        summary = {"runId": "test-run-001", "roundsExecuted": 2, "stopReason": "max-rounds", "dryRun": False}
        manifest = build_convergence_manifest(
            run_id="test-run-001",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        ok = (
            manifest.run_id == "test-run-001"
            and manifest.file_count == 2
            and not manifest.canonical_writes
            and manifest.lane == "convergence-loop"
        )
        return _result(
            "build-manifest-with-files",
            ok,
            f"file_count={manifest.file_count} lane={manifest.lane} canonical_writes={manifest.canonical_writes}",
        )


def test_write_manifest() -> dict[str, Any]:
    """write_convergence_manifest() creates evidence-manifest.json in run_root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "evidence-cards.jsonl"
        _write_fake_cards(cards_path)
        final_paths = {"externalCardsPath": str(cards_path)}
        summary = {"runId": "test-run-002", "roundsExecuted": 1, "stopReason": "max-rounds", "dryRun": False}
        manifest = build_convergence_manifest(
            run_id="test-run-002",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        out_path = write_convergence_manifest(manifest, run_root=run_root)
        ok = out_path.exists() and out_path.name == "evidence-manifest.json"
        return _result("write-manifest", ok, f"path={out_path}")


def test_resume_scan_ok() -> dict[str, Any]:
    """scan_prior_convergence_manifest() returns ok=True on unmodified files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "evidence-cards.jsonl"
        _write_fake_cards(cards_path)
        final_paths = {"externalCardsPath": str(cards_path)}
        summary = {"runId": "test-run-003", "roundsExecuted": 1, "stopReason": "max-rounds", "dryRun": False}
        manifest = build_convergence_manifest(
            run_id="test-run-003",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        manifest_path = write_convergence_manifest(manifest, run_root=run_root)
        report = scan_prior_convergence_manifest(manifest_path, repo_root=REPO, verify_sha256=True)
        ok = report.ok and report.run_id == "test-run-003" and report.file_count == 1
        return _result(
            "resume-scan-ok",
            ok,
            f"ok={report.ok} file_count={report.file_count} missing={report.missing} mismatch={report.hash_mismatch}",
        )


def test_resume_scan_hash_mismatch() -> dict[str, Any]:
    """scan_prior_convergence_manifest() detects hash mismatch after file modification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "evidence-cards.jsonl"
        _write_fake_cards(cards_path)
        final_paths = {"externalCardsPath": str(cards_path)}
        summary = {"runId": "test-run-004", "roundsExecuted": 1, "stopReason": "max-rounds", "dryRun": False}
        manifest = build_convergence_manifest(
            run_id="test-run-004",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        manifest_path = write_convergence_manifest(manifest, run_root=run_root)
        # Modify the file after manifest was written
        with cards_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"evidenceId": "extra-card"}) + "\n")
        report = scan_prior_convergence_manifest(manifest_path, repo_root=REPO, verify_sha256=True)
        ok = not report.ok and len(report.hash_mismatch) == 1
        return _result(
            "resume-scan-hash-mismatch",
            ok,
            f"ok={report.ok} hash_mismatch={len(report.hash_mismatch)}",
        )


def test_resume_scan_missing_file() -> dict[str, Any]:
    """scan_prior_convergence_manifest() detects missing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "evidence-cards.jsonl"
        _write_fake_cards(cards_path)
        final_paths = {"externalCardsPath": str(cards_path)}
        summary = {"runId": "test-run-005", "roundsExecuted": 1, "stopReason": "max-rounds", "dryRun": False}
        manifest = build_convergence_manifest(
            run_id="test-run-005",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        manifest_path = write_convergence_manifest(manifest, run_root=run_root)
        # Delete the file after manifest was written
        cards_path.unlink()
        report = scan_prior_convergence_manifest(manifest_path, repo_root=REPO, verify_sha256=True)
        ok = not report.ok and len(report.missing) == 1
        return _result(
            "resume-scan-missing-file",
            ok,
            f"ok={report.ok} missing={len(report.missing)}",
        )


def test_empty_final_paths() -> dict[str, Any]:
    """build_convergence_manifest() returns manifest with file_count=0 on dry-run (no artifacts)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        final_paths: dict[str, Any] = {}
        summary = {"runId": "dry-run-001", "roundsExecuted": 0, "stopReason": "max-rounds", "dryRun": True}
        manifest = build_convergence_manifest(
            run_id="dry-run-001",
            final_paths=final_paths,
            summary_payload=summary,
            repo_root=REPO,
        )
        ok = manifest.file_count == 0
        return _result(
            "empty-final-paths",
            ok,
            f"file_count={manifest.file_count}",
        )


def main() -> int:
    tests = [
        test_build_manifest_with_files,
        test_write_manifest,
        test_resume_scan_ok,
        test_resume_scan_hash_mismatch,
        test_resume_scan_missing_file,
        test_empty_final_paths,
    ]
    results = [t() for t in tests]
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nconvergence-manifest smoke: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
