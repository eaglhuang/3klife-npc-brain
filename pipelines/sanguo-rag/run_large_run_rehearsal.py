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


def _required_list(policy: dict[str, Any], key: str) -> list[str]:
    contract = policy.get("contract") if isinstance(policy.get("contract"), dict) else {}
    values = contract.get(key)
    if not isinstance(values, list) or not values:
        raise SystemExit(f"[large_run_rehearsal] policy.contract.{key} must be a non-empty list")
    return [str(item).strip() for item in values if str(item).strip()]


def _require_policy_fields(policy: dict[str, Any]) -> None:
    budgets = policy.get("budgets") if isinstance(policy.get("budgets"), dict) else {}
    backpressure = policy.get("backpressure") if isinstance(policy.get("backpressure"), dict) else {}
    signals = policy.get("signalIds") if isinstance(policy.get("signalIds"), dict) else {}
    missing_budget = [key for key in _required_list(policy, "requiredBudgetFields") if key not in budgets]
    missing_backpressure = [key for key in _required_list(policy, "requiredBackpressureFields") if key not in backpressure]
    missing_signals = [key for key in _required_list(policy, "requiredSignalFields") if key not in signals]
    if missing_budget or missing_backpressure or missing_signals:
        raise SystemExit(
            "[large_run_rehearsal] policy contract missing fields "
            f"budgets={missing_budget} backpressure={missing_backpressure} signalIds={missing_signals}"
        )


def _require_source_fields(policy: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    required = _required_list(policy, "requiredSourceFields")
    missing_rows: list[str] = []
    for index, source in enumerate(sources):
        missing = [key for key in required if key not in source]
        if missing:
            source_id = str(source.get("sourceId") or f"index-{index}")
            missing_rows.append(f"{source_id}:{','.join(missing)}")
    if missing_rows:
        raise SystemExit("[large_run_rehearsal] sources-config missing required fields " + "; ".join(missing_rows))


def _signal(signal_ids: dict[str, Any], key: str) -> str:
    value = str(signal_ids[key]).strip()
    if not value:
        raise SystemExit(f"[large_run_rehearsal] policy.signalIds.{key} cannot be blank")
    return value


def _append_once(rows: list[str], value: str) -> None:
    if value and value not in rows:
        rows.append(value)


# =========================================================================
# Simulation primitives
# =========================================================================

def _simulate_round(
    *,
    round_index: int,
    sources: list[dict[str, Any]],
    budgets: dict[str, Any],
    backpressure: dict[str, Any],
    signal_ids: dict[str, Any],
    cumulative_artifact_bytes: int,
    previous_low_yield_streak: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, int]:
    """Run one rehearsal round and return (telemetry rows, round summary,
    new cumulative_artifact_bytes, new low_yield_streak)."""
    telemetry: list[dict[str, Any]] = []
    round_bytes = 0
    round_seeds = 0
    round_cards = 0
    round_vector_records = 0
    round_new_evidence = 0
    round_timeouts = 0
    backpressure_signals: list[str] = []
    for source in sources:
        source_id = source["sourceId"]
        family = source["sourceFamily"]
        layer = source["sourceLayer"]
        source_signals: list[str] = []
        requested_raw_bytes = int(source["expectedRawBytesPerRound"])
        raw_bytes = requested_raw_bytes
        if raw_bytes > int(budgets["maxRawBytesPerSource"]):
            _append_once(source_signals, _signal(signal_ids, "sourceRawBytesBudget"))
            raw_bytes = int(budgets["maxRawBytesPerSource"])
        if cumulative_artifact_bytes + raw_bytes > int(budgets["maxArtifactBytesPerRun"]):
            _append_once(source_signals, _signal(signal_ids, "artifactBudgetExhausted"))
            raw_bytes = 0
        if round_bytes + raw_bytes > int(budgets["maxRawBytesPerRound"]):
            _append_once(source_signals, _signal(signal_ids, "roundRawBytesBudget"))
            raw_bytes = max(0, int(budgets["maxRawBytesPerRound"]) - round_bytes)
        timeout_count = int(source["expectedTimeoutCount"])
        if timeout_count > int(budgets["maxSourceTimeoutPerRound"]):
            _append_once(source_signals, _signal(signal_ids, "sourceTimeoutExceeded"))
            timeout_count = int(budgets["maxSourceTimeoutPerRound"])
        seed_yield = int(source["expectedSeedsPerRound"])
        card_yield = int(source["expectedCardsPerRound"])
        vector_records = card_yield
        new_evidence = int(source["expectedNewEvidence"])
        resume_scan_seconds = float(source["expectedResumeScanSeconds"])
        if resume_scan_seconds > float(budgets["maxResumeScanSeconds"]):
            _append_once(source_signals, _signal(signal_ids, "resumeScanBudget"))
        round_bytes += raw_bytes
        round_seeds += seed_yield
        round_cards += card_yield
        round_vector_records += vector_records
        round_new_evidence += new_evidence
        round_timeouts += timeout_count
        if round_seeds > int(budgets["maxSeedsPerRound"]):
            _append_once(source_signals, _signal(signal_ids, "roundSeedBudget"))
        if round_cards > int(budgets["maxCardsPerRound"]):
            _append_once(source_signals, _signal(signal_ids, "roundCardBudget"))
        if round_vector_records > int(budgets["maxVectorRecordsPerRound"]):
            _append_once(source_signals, _signal(signal_ids, "roundVectorRecordBudget"))
        roi = (new_evidence / max(1, raw_bytes / 1024.0)) if raw_bytes else 0.0
        if roi < float(backpressure["minRoiPerSource"]):
            _append_once(source_signals, _signal(signal_ids, "sourceLowRoi"))
        for signal in source_signals:
            _append_once(backpressure_signals, signal)
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
            "resumeScanSeconds": resume_scan_seconds,
            "postgresRowCount": seed_yield + card_yield,
            "vectorRecordCount": vector_records,
            "roiScore": roi,
            "backpressureSignal": ",".join(source_signals) or "",
        })

    low_yield_streak = previous_low_yield_streak
    if round_new_evidence < int(backpressure["minNewEvidencePerRound"]):
        low_yield_streak += 1
        _append_once(backpressure_signals, _signal(signal_ids, "lowNewEvidence"))
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
    signal_ids: dict[str, Any],
) -> tuple[bool, str]:
    signals = round_summary.get("backpressureSignals") or []
    artifact_budget_exhausted = _signal(signal_ids, "artifactBudgetExhausted")
    if artifact_budget_exhausted in signals:
        return True, artifact_budget_exhausted
    if round_summary["cumulativeArtifactBytes"] >= int(budgets["maxArtifactBytesPerRun"]):
        return True, artifact_budget_exhausted
    if round_summary["consecutiveLowYieldRounds"] >= int(backpressure["consecutiveLowYieldRoundsStop"]):
        return True, _signal(signal_ids, "consecutiveLowYield")
    if round_summary["timeoutCount"] >= int(budgets["maxTimeoutsPerRound"]):
        return True, _signal(signal_ids, "timeoutSaturation")
    return False, ""


def run_rehearsal(
    *,
    policy_path: Path,
    mode_id: str | None,
    sources: list[dict[str, Any]],
    output_root: Path | None,
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    _require_policy_fields(policy)
    _require_source_fields(policy, sources)
    mode = _mode_id_or_die(policy, mode_id)
    budgets = policy.get("budgets") or {}
    backpressure = policy.get("backpressure") or {}
    signal_ids = policy.get("signalIds") or {}
    max_rounds = int(budgets["maxRounds"])
    max_sources_per_round = int(budgets["maxSourcesPerRound"])

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
            signal_ids=signal_ids,
            cumulative_artifact_bytes=cumulative_artifact_bytes,
            previous_low_yield_streak=low_yield_streak,
        )
        ledger.extend(telemetry)
        rounds.append(round_summary)
        stop, reason = _should_stop(round_summary, budgets, backpressure, signal_ids)
        if stop:
            stop_reason = reason
            break

    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "policyPath": str(policy_path),
        "mode": mode["id"],
        "modeDetails": mode,
        "contract": policy.get("contract") or {},
        "budgets": budgets,
        "backpressure": backpressure,
        "signalIds": signal_ids,
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
        "outputWriteAllowed": bool(output_root is not None and mode.get("writesJsonl") is not False),
    }
    ledger_envelope = {
        "schemaVersion": LEDGER_SCHEMA_VERSION,
        "generatedAt": report["generatedAt"],
        "mode": mode["id"],
        "rows": ledger,
    }

    if output_root is not None and mode.get("writesJsonl") is not False:
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
    parser.add_argument("--mode", default=None, help="rehearsal mode; validated against policy.modes")
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
