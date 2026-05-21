"""Smoke tests for evidence_repository (SANGUO-RAGOPS-0202).

Exercises jsonl, postgres (dry-run), and dual modes. PostgreSQL is run in
``dry_run=True`` to keep the smoke test offline; real DB execution lives
in the M2-0204 parity gate harness.

Run:

    python -B pipelines/sanguo-rag/evidence_repository_smoke_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_repository import (  # noqa: E402
    DualEvidenceRepository,
    JsonlEvidenceRepository,
    PostgresEvidenceRepository,
    RepositoryError,
    RepositorySettings,
    RetryPolicy,
    build_repository,
)


def _expect(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def _settings(tmp: Path, mode: str, dry_run: bool = True) -> RepositorySettings:
    return RepositorySettings(
        mode=mode,
        jsonl_root=tmp,
        postgres_dsn="postgresql://example.invalid/sanguo_rag",
        postgres_schema="sanguo_rag",
        dry_run=dry_run,
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.0, backoff_multiplier=1.0),
    )


def test_jsonl_idempotent_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = JsonlEvidenceRepository(_settings(Path(tmp), "jsonl", dry_run=False))
        rows = [
            {
                "seed_id": "seed-001",
                "run_id": "run-x",
                "source_id": "3kweb",
                "general_id": "zhang-fei",
                "angle_type": "battle",
                "seed_text_hash": "a" * 64,
                "score": {"a": 1},
                "anchor": {},
                "payload": {},
                "payload_uri": "atm://lake/run-x/sources/3kweb/evidence-seeds/0001/seeds-r1.jsonl",
            }
        ]
        first = repo.upsert("evidence_seeds", rows)
        second = repo.upsert("evidence_seeds", rows)
        _expect("jsonl first write reports 1 written", first.written == 1)
        _expect("jsonl second write skips duplicate", second.skipped_duplicate == 1 and second.written == 0)
        target = Path(tmp) / "_state" / "evidence_seeds.jsonl"
        contents = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        _expect("jsonl file persists exactly one row", len(contents) == 1)


def test_jsonl_dry_run_does_not_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = JsonlEvidenceRepository(_settings(Path(tmp), "jsonl", dry_run=True))
        result = repo.upsert(
            "pipeline_runs",
            [{"run_id": "run-y", "lane": "smoke", "run_profile": "strict-local",
              "input_fingerprint": "f" * 64, "canonical_writes": False, "status": "created",
              "started_at": "2026-05-21T00:00:00+00:00", "finished_at": None,
              "summary": {}, "policy_refs": [], "raw_payload": {}}],
        )
        _expect("jsonl dry-run reports 1 written", result.written == 1)
        _expect("jsonl dry-run does not create _state/", not (Path(tmp) / "_state").exists())


def test_postgres_dry_run_returns_written() -> None:
    repo = PostgresEvidenceRepository(_settings(Path("."), "postgres", dry_run=True))
    result = repo.upsert(
        "harvested_pages",
        [
            {
                "run_id": "run-z",
                "source_id": "3kweb",
                "url": "https://example.invalid/p/1",
                "url_hash": "u" * 64,
                "title": "p1",
                "text_hash": "t" * 64,
                "body_start": 0,
                "body_end": 100,
                "raw_bytes": 100,
                "artifact_uri": "atm://lake/run-z/sources/3kweb/harvested-pages/0001/u.harvested.json",
                "source_policy_id": "policy-3kweb",
                "raw_payload": {"foo": "bar"},
            }
        ],
    )
    _expect("postgres dry-run reports 1 written", result.written == 1)
    _expect("postgres dry-run records no errors", result.errors == [])


def test_postgres_requires_dsn_when_live() -> None:
    try:
        PostgresEvidenceRepository(
            RepositorySettings(mode="postgres", postgres_dsn=None, dry_run=False)
        )
    except RepositoryError:
        _expect("PostgresEvidenceRepository raises when DSN missing and not dry-run", True)
        return
    _expect("PostgresEvidenceRepository raises when DSN missing and not dry-run", False)


def test_dual_aggregates_results() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp), "dual", dry_run=True)
        repo = DualEvidenceRepository(
            JsonlEvidenceRepository(settings),
            PostgresEvidenceRepository(settings),
        )
        result = repo.upsert(
            "source_runs",
            [
                {
                    "run_id": "run-d",
                    "source_id": "3kweb",
                    "source_family": "external",
                    "source_layer": "browser",
                    "fetch_count": 3,
                    "harvested_count": 2,
                    "seed_count": 5,
                    "card_count": 1,
                    "timeout_count": 0,
                    "roi_score": 0.83,
                    "body_boundary_summary": {},
                    "raw_payload": {},
                }
            ],
        )
        _expect("dual mode requested counts each backend once", result.requested == 2)
        _expect("dual mode written counts each backend once", result.written == 2)
        _expect("dual mode backend label is 'dual'", result.backend == "dual")


def test_factory_builds_jsonl_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repository(_settings(Path(tmp), "jsonl"))
        _expect("factory builds JsonlEvidenceRepository by default", isinstance(repo, JsonlEvidenceRepository))


def test_factory_rejects_unknown_mode() -> None:
    try:
        RepositorySettings(mode="vector").mode  # nothing illegal here, but...
        # build_repository should reject the unknown mode below
        build_repository(RepositorySettings(mode="vector", dry_run=True))
    except RepositoryError:
        _expect("factory rejects unknown mode", True)
        return
    _expect("factory rejects unknown mode", False)


def main() -> int:
    tests = [
        test_jsonl_idempotent_write,
        test_jsonl_dry_run_does_not_write,
        test_postgres_dry_run_returns_written,
        test_postgres_requires_dsn_when_live,
        test_dual_aggregates_results,
        test_factory_builds_jsonl_by_default,
        test_factory_rejects_unknown_mode,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} evidence_repository smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
