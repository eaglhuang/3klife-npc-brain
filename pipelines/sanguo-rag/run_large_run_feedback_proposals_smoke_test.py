"""Smoke tests for large-run feedback proposal generation.

The tests use temporary rehearsal artifacts only. They verify that
backpressure telemetry can become sandbox proposals without mutating the
source policy or writing formal evidence data.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_large_run_feedback_proposals import build_feedback_proposals  # noqa: E402
from run_large_run_rehearsal import DEFAULT_POLICY, run_rehearsal  # noqa: E402

SOURCES_FIXTURE = ROOT / "fixtures" / "large-run-sources-smoke.json"


def _temp_parent() -> Path:
    base_text = os.environ.get("SANGUO_RAG_TEST_TMPDIR")
    base = Path(base_text) if base_text else Path.cwd() / "local" / "tmp" / "sanguo-rag-smoke"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _expect(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_sources() -> list[dict]:
    payload = json.loads(SOURCES_FIXTURE.read_text(encoding="utf-8"))
    return list(payload["sources"])


def _tight_policy(root: Path) -> Path:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    policy["budgets"]["maxArtifactBytesPerRun"] = 8 * 1024 * 1024
    policy["budgets"]["maxRawBytesPerRound"] = 4 * 1024 * 1024
    policy["budgets"]["maxRawBytesPerSource"] = 2 * 1024 * 1024
    policy["budgets"]["maxVectorRecordsPerRound"] = 12
    path = root / "policy-large-run-rehearsal.tight.json"
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_feedback_builds_budget_and_source_proposals() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        root = Path(tmp)
        policy_path = _tight_policy(root)
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        result = run_rehearsal(policy_path=policy_path, mode_id="no-write", sources=_load_sources(), output_root=None)
        proposals = build_feedback_proposals(
            policy=policy,
            policy_path_text=str(policy_path),
            reports=[result["report"]],
            ledgers=[result["ledger"]],
        )
    kinds = {row["proposalKind"] for row in proposals["proposals"]}
    _expect("feedback schema stamped", proposals["schemaVersion"] == "large-run-feedback-proposals.v0.1")
    _expect("feedback remains proposal-only", proposals["canonicalWrites"] is False)
    _expect("budget increase proposal emitted", "large-run-budget-increase" in kinds)
    _expect("source ROI review proposal emitted", "large-run-source-roi-review" in kinds)
    _expect("all proposals require review", all(row["proposalStatus"] == "sandbox-proposed" for row in proposals["proposals"]))


def test_feedback_waits_for_minimum_rounds() -> None:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    result = run_rehearsal(policy_path=DEFAULT_POLICY, mode_id="no-write", sources=_load_sources(), output_root=None)
    report = dict(result["report"])
    report["rounds"] = report["rounds"][:1]
    proposals = build_feedback_proposals(
        policy=policy,
        policy_path_text=str(DEFAULT_POLICY),
        reports=[report],
        ledgers=[result["ledger"]],
    )
    _expect("minimum round gate suppresses proposals", proposals["proposalCount"] == 0)


def test_cli_writes_only_requested_temp_output_and_preserves_policy() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        root = Path(tmp)
        policy_path = _tight_policy(root)
        before_hash = _sha256(policy_path)
        result = run_rehearsal(policy_path=policy_path, mode_id="no-write", sources=_load_sources(), output_root=None)
        run_root = root / "run-root"
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "rehearsal-report.json").write_text(
            json.dumps(result["report"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (run_root / "backpressure-telemetry-ledger.json").write_text(
            json.dumps(result["ledger"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output_path = root / "feedback-proposals.json"
        cmd = [
            sys.executable,
            str(ROOT / "run_large_run_feedback_proposals.py"),
            "--policy",
            str(policy_path),
            "--run-root",
            str(run_root),
            "--output-json",
            str(output_path),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        _expect("feedback CLI exits 0", proc.returncode == 0)
        _expect("feedback output written on explicit request", output_path.exists())
        _expect("feedback output contains proposals", payload["proposalCount"] > 0)
        _expect("policy file hash preserved", _sha256(policy_path) == before_hash)


def main() -> int:
    tests = [
        test_feedback_builds_budget_and_source_proposals,
        test_feedback_waits_for_minimum_rounds,
        test_cli_writes_only_requested_temp_output_and_preserves_policy,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} large-run feedback proposal smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
