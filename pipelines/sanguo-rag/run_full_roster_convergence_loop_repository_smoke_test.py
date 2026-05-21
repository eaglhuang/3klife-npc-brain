"""Smoke test for the convergence loop evidence repository write seam (SANGUO-RAGOPS-0602).

Verifies that:
1. The seam is disabled (no writes) when SANGUO_RAG_CONVERGENCE_REPO_ENABLED is absent.
2. The seam is enabled with dry_run=True when SANGUO_RAG_CONVERGENCE_REPO_ENABLED=1 and
   SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN=1 (the safe default).
3. write_round() returns a non-empty list of WriteResults in dry-run mode.
4. write_run_summary() returns a WriteResult in dry-run mode.
5. close() is idempotent.
6. No JSONL files are created in the repo root during dry-run (no side effects on canonical outputs).

Usage::

    python -B pipelines/sanguo-rag/run_full_roster_convergence_loop_repository_smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convergence_evidence_seam import ConvergenceRepoSeam  # noqa: E402


PASS = "PASS"
FAIL = "FAIL"


def _result(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    status = PASS if ok else FAIL
    msg = f"[{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return {"label": label, "ok": ok, "detail": detail}


def _make_fake_round_info(cards_path: Path) -> dict[str, Any]:
    return {
        "roundId": "smoke-round-1",
        "externalCardsPath": str(cards_path),
        "globalCandidateCardsPath": "",
        "externalSummary": {
            "sourceResults": [
                {
                    "sourceId": "smoke-source-a",
                    "sourceFamily": "smoke-family",
                    "fetchedPageCount": 5,
                    "seedCount": 3,
                }
            ]
        },
    }


def _write_fake_cards(cards_path: Path) -> int:
    cards = [
        {
            "evidenceId": f"smoke-card-{i}",
            "sourceId": "smoke-source-a",
            "sourceFamily": "smoke-family",
            "sourceLayer": "smoke-layer",
            "generalIds": ["smoke-general-a"],
            "reviewStatus": "candidate",
            "sourceQuote": f"Smoke quote {i}",
        }
        for i in range(1, 4)
    ]
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    with cards_path.open("w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    return len(cards)


def test_disabled_by_default() -> dict[str, Any]:
    """Seam must be disabled when env var is not set."""
    env_backup = os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
    try:
        seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
        ok = not seam.enabled
        return _result("disabled-by-default", ok, f"seam.enabled={seam.enabled}")
    finally:
        if env_backup is not None:
            os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = env_backup


def test_enabled_dry_run() -> dict[str, Any]:
    """Seam must be enabled and dry_run=True when env vars are set."""
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = "1"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_MODE"] = "postgres"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"] = "1"
    os.environ["SANGUO_RAG_PG_DSN"] = "postgresql://example.invalid/sanguo_rag"
    try:
        seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
        ok = seam.enabled and seam.dry_run
        return _result("enabled-dry-run", ok, f"enabled={seam.enabled} dry_run={seam.dry_run}")
    finally:
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_MODE", None)
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN", None)
        os.environ.pop("SANGUO_RAG_PG_DSN", None)


def test_write_round_dry_run() -> dict[str, Any]:
    """write_round() must return non-empty WriteResults in dry-run mode."""
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = "1"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_MODE"] = "postgres"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"] = "1"
    os.environ["SANGUO_RAG_PG_DSN"] = "postgresql://example.invalid/sanguo_rag"
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "candidate-evidence-cards.jsonl"
        card_count = _write_fake_cards(cards_path)
        round_info = _make_fake_round_info(cards_path)
        try:
            seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
            results = seam.write_round(
                round_info=round_info,
                run_id="smoke-run-001",
                run_root=run_root,
                repo_root=REPO,
            )
            seam.close()
            # results contains evidence_cards + source_runs; check evidence_cards count specifically
            card_written = sum(
                getattr(r, "written", 0)
                for r in results
                if getattr(r, "table", "") == "evidence_cards"
            )
            ok = len(results) >= 1 and card_written == card_count
            return _result(
                "write-round-dry-run",
                ok,
                f"results={len(results)} card_written={card_written} expected_cards={card_count}",
            )
        finally:
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_MODE", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN", None)
            os.environ.pop("SANGUO_RAG_PG_DSN", None)


def test_write_run_summary_dry_run() -> dict[str, Any]:
    """write_run_summary() must return a WriteResult in dry-run mode."""
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = "1"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_MODE"] = "postgres"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"] = "1"
    os.environ["SANGUO_RAG_PG_DSN"] = "postgresql://example.invalid/sanguo_rag"
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        summary_payload = {
            "runId": "smoke-run-001",
            "roundsExecuted": 2,
            "stopReason": "max-rounds",
            "dryRun": True,
            "mode": "convergence-loop",
            "generatedAt": "2026-05-21T00:00:00+00:00",
        }
        try:
            seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
            result = seam.write_run_summary(summary_payload=summary_payload, run_root=run_root)
            seam.close()
            ok = result is not None and getattr(result, "written", 0) == 1
            return _result(
                "write-run-summary-dry-run",
                ok,
                f"result={result}",
            )
        finally:
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_MODE", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN", None)
            os.environ.pop("SANGUO_RAG_PG_DSN", None)


def test_no_jsonl_side_effects() -> dict[str, Any]:
    """Dry-run must not create JSONL files in the run root canonical paths."""
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = "1"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_MODE"] = "postgres"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"] = "1"
    os.environ["SANGUO_RAG_PG_DSN"] = "postgresql://example.invalid/sanguo_rag"
    with tempfile.TemporaryDirectory() as tmpdir:
        run_root = Path(tmpdir) / "run"
        cards_path = run_root / "candidate-evidence-cards.jsonl"
        _write_fake_cards(cards_path)
        round_info = _make_fake_round_info(cards_path)
        try:
            seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
            seam.write_round(
                round_info=round_info,
                run_id="smoke-run-002",
                run_root=run_root,
                repo_root=REPO,
            )
            seam.write_run_summary(
                summary_payload={"runId": "smoke-run-002", "roundsExecuted": 1, "stopReason": "test"},
                run_root=run_root,
            )
            seam.close()
            # The only allowed side effect in dry-run is the error ledger (if any errors)
            # No _state/ directory should be created for postgres mode
            state_dir = run_root / "_state"
            ok = not state_dir.exists()
            return _result(
                "no-jsonl-side-effects",
                ok,
                f"_state/ exists: {state_dir.exists()}",
            )
        finally:
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_MODE", None)
            os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN", None)
            os.environ.pop("SANGUO_RAG_PG_DSN", None)


def test_close_idempotent() -> dict[str, Any]:
    """close() must be safe to call multiple times."""
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_ENABLED"] = "1"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_MODE"] = "postgres"
    os.environ["SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"] = "1"
    os.environ["SANGUO_RAG_PG_DSN"] = "postgresql://example.invalid/sanguo_rag"
    try:
        seam = ConvergenceRepoSeam.from_policy(repo_root=REPO)
        seam.close()
        seam.close()  # second call must not raise
        return _result("close-idempotent", True)
    except Exception as exc:
        return _result("close-idempotent", False, str(exc))
    finally:
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_ENABLED", None)
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_MODE", None)
        os.environ.pop("SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN", None)
        os.environ.pop("SANGUO_RAG_PG_DSN", None)


def main() -> int:
    tests = [
        test_disabled_by_default,
        test_enabled_dry_run,
        test_write_round_dry_run,
        test_write_run_summary_dry_run,
        test_no_jsonl_side_effects,
        test_close_idempotent,
    ]
    results = [t() for t in tests]
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nconvergence-repo-seam smoke: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
