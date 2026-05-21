"""Large-run rehearsal driver (SANGUO-RAGOPS-0401).

Reads ``policy-large-run-rehearsal.json`` and drives the evidence
pipeline through a budgeted simulation that emits a per-round
backpressure telemetry ledger and a rehearsal report. The driver is
explicitly mode-aware:

* ``no-write``: no writes anywhere (default).
* ``jsonl-only``: JSONL canonical mirror only.
* ``dual-write``: JSONL + PostgreSQL mirror (via M2 repository adapter).
* ``vector-smoke``: above + evidence vector smoke namespace (M3 gate).

Backpressure decisions are entirely driven by the policy:

* per-source raw byte budget
* per-round raw byte budget
* per-round seed / card / vector record budgets
* per-round artifact byte budget
* per-source timeout count
* minimum new-evidence delta per round
* consecutive low-yield rounds before stop
* resume scan ceiling

Nothing in this script may hardcode a general id, source id, page-tail
cleanup string, or quote string. Every decision lives in policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_POLICY = ROOT.parent.parent / "data" / "sanguo" / "policies" / "policy-large-run-rehearsal.json"

REPORT_SCHEMA_VERSION = "large-run-rehearsal-report.v0.1"
LEDGER_SCHEMA_VERSION = "backpressure-telemetry-ledger.v0.1"


# =========================================================================
# Policy + mode helpers
# =========================================================================

def _load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mode_id_or_die(policy: dict[str, Any], mode: str | None) -> dict[str, Any]:
    modes = {entry["id"]: entry for entry in policy.get("modes", [])}
    if not modes:
        raise SystemExit("[large_run_rehearsal] policy has no modes defined")
    resolved = mode or policy.get("defaultMode") or next(iter(modes))
    if resolved not in modes:
        raise SystemExit(f"[large_run_rehearsal] mode {resolved!r} not in policy.modes (allowed: {list(modes)})")
    return modes[resolved]


# =========================================================================
# Simulation primitives
# =========================================================================

def _simulate_round(
    *,
    round_index: int,
    sources: list[dict[str, Any]],
    budgets: dict[str, Any],
    backpressure: dict[str, Any],
    cumulative_artifact_bytes: int,
    previous_low_yield_streak: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, int]:
    """Run one rehearsal round and return (telemetry rows, round summary,
    new cumulative_artifact_bytes, new low_yield_streak)."""
    telemetry: list[dict[str, Any]] = []
    round_bytes = 0
    round_seeds = 0
    round_cards = 0
    round_new_evidence = 0
    round_timeouts = 0
    backpressure_signals: list[str] = []
    for source_index, source in enumerate(sources):
        source_id = source["sourceId"]
        family = source.get("sourceFamily") or "external"
        layer = source.get("sourceLayer") or "browser"
        raw_bytes = min(int(source.get("expectedRawBytesPerRound", 524288)), int(budgets.get("maxRawBytesPerSource", 1024 * 1024)))
        if cumulative_artifact_bytes + raw_bytes > int(budgets.get("maxArtifactBytesPerRun", 1)):
            backpressure_signals.append("artifact-budget-exhausted")
            raw_bytes = 0
        if round_bytes + raw_bytes > int(budgets.get("maxRawBytesPerRound", 1)):
            backpressure_signals.append("round-raw-bytes-budget")
            raw_bytes = max(0, int(budgets.get("maxRawBytesPerRound", 1)) - round_bytes)
        timeout_count = int(source.get("expectedTimeoutCount", 0))
        if timeout_count > int(budgets.get("maxSourceTimeoutPerRound", 4)):
            backpressure_signals.append(f"source-timeout-exceeded:{source_id}")
            timeout_count = int(budgets.get("maxSourceTimeoutPerRound", 4))
        seed_yield = int(source.get("expectedSeedsPerRound", 30))
        card_yield = int(source.get("expectedCardsPerRound", 6))
        new_evidence = int(source.get("expectedNewEvidence", seed_yield // 4))
        round_bytes += raw_bytes
        round_seeds += seed_yield
        round_cards += card_yield
        round_new_evidence += new_evidence
        round_timeouts += timeout_count
        if round_seeds > int(budgets.get("maxSeedsPerRound", 1)):
            backpressure_signals.append("round-seed-budget")
        if round_cards > int(budgets.get("maxCardsPerRound", 1)):
            backpressure_signals.append("round-card-budget")
        roi = (new_evidence / max(1, raw_bytes / 1024.0)) if raw_bytes else 0.0
        telemetry.append({
            "roundId": f"round-{round_index:02d}",
            "sourceId": source_id,
            "sourceFamily": family,
            "sourceLayer": layer,
            "fetchCount": int(source.get("expectedFetchCount", 1)),
            "harvestedCount": int(source.get("expectedHarvestedCount", 1)),
            "seedCount": seed_yield,
            "cardCount": card_yield,
            "newEvidenceCount": new_evidence,
            "timeoutCount": timeout_count,
            "rawBytes": raw_bytes,
            "artifactBytes": raw_bytes,
            "resumeScanSeconds": float(source.get("expectedResumeScanSeconds", 0.5)),
            "postgresRowCount": seed_yield + card_yield,
            "vectorRecordCount": card_yield,
            "roiScore": roi,
            "backpressureSignal": ",".join(backpressure_signals) or "",
        })

    low_yield_streak = previous_low_yield_streak
    if round_new_evidence < int(backpressure.get("minNewEvidencePerRound", 5)):
        low_yield_streak += 1
        backpressure_signals.append("low-new-evidence")
    else:
        low_yield_streak = 0

    cumulative_artifact_bytes += round_bytes
    round_summary = {
        "roundId": f"round-{round_index:02d}",
        "rawBytes": round_bytes,
        "seedCount": round_seeds,
        "cardCount": round_cards,
        "newEvidenceCount": round_new_evidence,
        "timeoutCount": round_timeouts,
        "backpressureSignals": sorted(set(backpressure_signals)),
        "cumulativeArtifactBytes": cumulative_artifact_bytes,
        "consecutiveLowYieldRounds": low_yield_streak,
    }
    return telemetry, round_summary, cumulative_artifact_bytes, low_yield_streak


def _should_stop(
    round_summary: dict[str, Any],
    budgets: dict[str, Any],
    backpressure: dict[str, Any],
) -> tuple[bool, str]:
    signals = round_summary.get("backpressureSignals") or []
    if "artifact-budget-exhausted" in signals:
        return True, "artifact-budget-exhausted"
    if round_summary["cumulativeArtifactBytes"] >= int(budgets.get("maxArtifactBytesPerRun", float("inf"))):
        return True, "artifact-budget-exhausted"
    if round_summary["consecutiveLowYieldRounds"] >= int(backpressure.get("consecutiveLowYieldRoundsStop", 2)):
        return True, "consecutive-low-yield"
    if round_summary["timeoutCount"] > int(budgets.get("maxSourceTimeoutPerRound", 1)) * max(1, len(signals)):
        return True, "timeout-saturation"
    return False, ""


def run_rehearsal(
    *,
    policy_path: Path,
    mode_id: str | None,
    sources: list[dict[str, Any]],
    output_root: Path | None,
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    mode = _mode_id_or_die(policy, mode_id)
    budgets = policy.get("budgets") or {}
    backpressure = policy.get("backpressure") or {}
    max_rounds = int(budgets.get("maxRounds", 1))
    max_sources_per_round = int(budgets.get("maxSourcesPerRound", len(sources)))

    rounds: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    cumulative_artifact_bytes = 0
    low_yield_streak = 0
    stop_reason = ""

    for round_index in range(1, max_rounds + 1):
        round_sources = sources[:max_sources_per_round]
        telemetry, round_summary, cumulative_artifact_bytes, low_yield_streak = _simulate_round(
            round_index=round_index,
            sources=round_sources,
            budgets=budgets,
            backpressure=backpressure,
            cumulative_artifact_bytes=cumulative_artifact_bytes,
            previous_low_yield_streak=low_yield_streak,
        )
        ledger.extend(telemetry)
        rounds.append(round_summary)
        stop, reason = _should_stop(round_summary, budgets, backpressure)
        if stop:
            stop_reason = reason
            break

    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "policyPath": str(policy_path),
        "mode": mode["id"],
        "modeDetails": mode,
        "budgets": budgets,
        "backpressure": backpressure,
        "rounds": rounds,
        "stopReason": stop_reason or "max-rounds-reached",
        "totals": {
            "roundsExecuted": len(rounds),
            "totalSeeds": sum(item["seedCount"] for item in rounds),
            "totalCards": sum(item["cardCount"] for item in rounds),
            "totalNewEvidence": sum(item["newEvidenceCount"] for item in rounds),
            "totalArtifactBytes": cumulative_artifact_bytes,
        },
        "guards": policy.get("guards") or [],
    }
    ledger_envelope = {
        "schemaVersion": LEDGER_SCHEMA_VERSION,
        "generatedAt": report["generatedAt"],
        "mode": mode["id"],
        "rows": ledger,
    }

    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "rehearsal-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_root / "backpressure-telemetry-ledger.json").write_text(
            json.dumps(ledger_envelope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {"report": report, "ledger": ledger_envelope}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Large-run rehearsal driver (SANGUO-RAGOPS-0401).")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="policy-large-run-rehearsal.json path")
    parser.add_argument("--mode", default=None, choices=[None, "no-write", "jsonl-only", "dual-write", "vector-smoke"], help="rehearsal mode")
    parser.add_argument("--sources-config", required=True, help="JSON file describing source budgets / expected yields")
    parser.add_argument("--output-root", default="", help="optional root for rehearsal-report.json + backpressure-telemetry-ledger.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = Path(args.policy)
    sources_config = json.loads(Path(args.sources_config).read_text(encoding="utf-8"))
    sources = list(sources_config.get("sources") or [])
    if not sources:
        raise SystemExit("[large_run_rehearsal] sources-config must contain a non-empty 'sources' array")
    output_root = Path(args.output_root) if args.output_root else None
    result = run_rehearsal(
        policy_path=policy_path,
        mode_id=args.mode,
        sources=sources,
        output_root=output_root,
    )
    print(
        f"[large_run_rehearsal] mode={result['report']['mode']} rounds={result['report']['totals']['roundsExecuted']} "
        f"stop={result['report']['stopReason']} totalNewEvidence={result['report']['totals']['totalNewEvidence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
