"""Build policy adjustment proposals from large-run rehearsal telemetry.

The generator is intentionally proposal-only: it reads rehearsal reports and
backpressure ledgers, then emits sandbox-proposed policy changes. It never
mutates the policy file, evidence JSONL, PostgreSQL, or vector backends.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_large_run_rehearsal import DEFAULT_POLICY  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def signals_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def clamp_proposed_value(current: float, observed: float, proposal_policy: dict[str, Any], direction: str) -> int:
    adjustment = proposal_policy.get("adjustment") if isinstance(proposal_policy.get("adjustment"), dict) else {}
    headroom_ratio = float(adjustment["headroomRatio"])
    max_increase_ratio = float(adjustment["maxIncreaseRatio"])
    max_decrease_ratio = float(adjustment["maxDecreaseRatio"])
    min_value = int(adjustment["minProposedValue"])
    if direction == "increase":
        observed_target = observed * (1.0 + headroom_ratio)
        capped_target = current * (1.0 + max_increase_ratio)
        return max(min_value, int(round(min(max(observed_target, current), capped_target))))
    observed_target = observed * (1.0 + headroom_ratio)
    capped_floor = current * (1.0 - max_decrease_ratio)
    return max(min_value, int(round(max(observed_target, capped_floor))))


def proposal_base(
    *,
    kind: str,
    target: dict[str, Any],
    evidence: dict[str, Any],
    policy: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    proposal_policy = policy["feedbackProposal"]
    identity = {
        "kind": kind,
        "target": target,
        "evidence": evidence,
        "reason": reason,
    }
    return {
        "proposalId": f"large-run-feedback:{stable_hash(identity)}",
        "proposalKind": kind,
        "proposalStatus": proposal_policy["proposalStatus"],
        "reviewGate": proposal_policy["reviewGate"],
        "target": target,
        "reason": reason,
        "evidence": evidence,
        "canonicalWrites": False,
    }


def collect_inputs(report_paths: list[Path], ledger_paths: list[Path], run_roots: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reports = [load_json(path) for path in report_paths]
    ledgers = [load_json(path) for path in ledger_paths]
    for root in run_roots:
        report_path = root / "rehearsal-report.json"
        ledger_path = root / "backpressure-telemetry-ledger.json"
        if report_path.exists():
            reports.append(load_json(report_path))
        if ledger_path.exists():
            ledgers.append(load_json(ledger_path))
    return reports, ledgers


def all_rounds(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for row in as_list(report.get("rounds")):
            if isinstance(row, dict):
                rows.append(row)
    return rows


def all_ledger_rows(ledgers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ledger in ledgers:
        for row in as_list(ledger.get("rows")):
            if isinstance(row, dict):
                rows.append(row)
    return rows


def signal_counts(rounds: list[dict[str, Any]], ledger_rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rounds:
        counts.update(signals_from_value(row.get("backpressureSignals")))
    for row in ledger_rows:
        counts.update(signals_from_value(row.get("backpressureSignal")))
    return counts


def max_metric(rows: list[dict[str, Any]], field: str) -> float:
    values: list[float] = []
    for row in rows:
        if field in row:
            values.append(float(row[field]))
    return max(values) if values else 0.0


def source_stats(ledger_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        source_id = str(row.get("sourceId") or "").strip()
        if source_id:
            grouped[source_id].append(row)
    result: dict[str, dict[str, Any]] = {}
    for source_id, rows in grouped.items():
        roi_values = [float(row.get("roiScore") or 0.0) for row in rows]
        signal_counter: Counter[str] = Counter()
        for row in rows:
            signal_counter.update(signals_from_value(row.get("backpressureSignal")))
        result[source_id] = {
            "observationCount": len(rows),
            "averageRoi": sum(roi_values) / max(1, len(roi_values)),
            "maxTimeoutCount": max(float(row.get("timeoutCount") or 0.0) for row in rows),
            "maxResumeScanSeconds": max(float(row.get("resumeScanSeconds") or 0.0) for row in rows),
            "signals": dict(signal_counter),
        }
    return result


def build_budget_proposals(
    *,
    policy: dict[str, Any],
    policy_path_text: str,
    reports: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    counts: Counter[str],
) -> list[dict[str, Any]]:
    proposal_policy = policy["feedbackProposal"]
    signal_ids = policy["signalIds"]
    budgets = policy["budgets"]
    signal_to_budget = proposal_policy["signalToBudget"]
    budget_metric_map = proposal_policy["budgetMetricMap"]
    adjustment = proposal_policy["adjustment"]
    proposals: list[dict[str, Any]] = []

    for signal_key, config in signal_to_budget.items():
        signal_id = str(signal_ids[signal_key])
        if counts.get(signal_id, 0) <= 0:
            continue
        budget_field = str(config["budgetField"])
        metric_field = str(config["metricField"])
        current = float(budgets[budget_field])
        observed = max(max_metric(rounds, metric_field), max_metric(ledger_rows, metric_field))
        proposed = clamp_proposed_value(current, observed, proposal_policy, "increase")
        if proposed <= int(current):
            continue
        proposals.append(
            proposal_base(
                kind=str(config["proposalKind"]),
                target={
                    "policyPath": policy_path_text,
                    "field": f"budgets.{budget_field}",
                    "currentValue": int(current),
                    "proposedValue": proposed,
                },
                evidence={
                    "signalId": signal_id,
                    "signalCount": counts[signal_id],
                    "observedMax": observed,
                    "reportCount": len(reports),
                    "roundCount": len(rounds),
                },
                policy=policy,
                reason=str(config["reason"]),
            )
        )

    for budget_field, metric_field in budget_metric_map.items():
        current = float(budgets[budget_field])
        observed = max(max_metric(rounds, str(metric_field)), max_metric(ledger_rows, str(metric_field)))
        if current <= 0:
            continue
        utilization = observed / current
        if utilization >= float(adjustment["lowUtilizationRatio"]):
            continue
        proposed = clamp_proposed_value(current, observed, proposal_policy, "decrease")
        if proposed >= int(current):
            continue
        decrease_config = proposal_policy["defaultBudgetDecreaseProposal"]
        proposals.append(
            proposal_base(
                kind=str(decrease_config["proposalKind"]),
                target={
                    "policyPath": policy_path_text,
                    "field": f"budgets.{budget_field}",
                    "currentValue": int(current),
                    "proposedValue": proposed,
                },
                evidence={
                    "observedMax": observed,
                    "utilization": utilization,
                    "reportCount": len(reports),
                    "roundCount": len(rounds),
                },
                policy=policy,
                reason=str(decrease_config["reason"]),
            )
        )
    return proposals


def build_source_proposals(
    *,
    policy: dict[str, Any],
    policy_path_text: str,
    ledger_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    proposal_policy = policy["feedbackProposal"]
    signal_ids = policy["signalIds"]
    source_configs = proposal_policy["sourceSignalProposals"]
    min_observation = proposal_policy["minObservation"]
    stats = source_stats(ledger_rows)
    proposals: list[dict[str, Any]] = []
    for source_id, stat in sorted(stats.items()):
        if int(stat["observationCount"]) < int(min_observation["sourceObservationCount"]):
            continue
        source_signal_counts = stat["signals"]
        for signal_key, config in source_configs.items():
            signal_id = str(signal_ids[signal_key])
            if source_signal_counts.get(signal_id, 0) <= 0:
                continue
            proposals.append(
                proposal_base(
                    kind=str(config["proposalKind"]),
                    target={
                        "sourceId": source_id,
                        "policyPath": policy_path_text,
                        "field": "source-level-budget-or-priority",
                    },
                    evidence={
                        "signalId": signal_id,
                        "signalCount": source_signal_counts[signal_id],
                        "observationCount": stat["observationCount"],
                        "averageRoi": stat["averageRoi"],
                        "maxTimeoutCount": stat["maxTimeoutCount"],
                        "maxResumeScanSeconds": stat["maxResumeScanSeconds"],
                    },
                    policy=policy,
                    reason=str(config["reason"]),
                )
            )
    return proposals


def build_feedback_proposals(
    *,
    policy: dict[str, Any],
    policy_path_text: str,
    reports: list[dict[str, Any]],
    ledgers: list[dict[str, Any]],
) -> dict[str, Any]:
    proposal_policy = policy["feedbackProposal"]
    rounds = all_rounds(reports)
    ledger_rows = all_ledger_rows(ledgers)
    min_round_count = int(proposal_policy["minObservation"]["roundCount"])
    if len(rounds) < min_round_count:
        proposals: list[dict[str, Any]] = []
    else:
        counts = signal_counts(rounds, ledger_rows)
        proposals = [
            *build_budget_proposals(
                policy=policy,
                policy_path_text=policy_path_text,
                reports=reports,
                rounds=rounds,
                ledger_rows=ledger_rows,
                counts=counts,
            ),
            *build_source_proposals(policy=policy, policy_path_text=policy_path_text, ledger_rows=ledger_rows),
        ]
        proposals.sort(key=lambda row: (str(row["proposalKind"]), json.dumps(row["target"], sort_keys=True)))

    return {
        "schemaVersion": proposal_policy["schemaVersion"],
        "generatedAt": utc_now(),
        "proposalStatus": proposal_policy["proposalStatus"],
        "reviewGate": proposal_policy["reviewGate"],
        "reportCount": len(reports),
        "ledgerCount": len(ledgers),
        "roundCount": len(rounds),
        "ledgerRowCount": len(ledger_rows),
        "proposalCount": len(proposals),
        "canonicalWrites": False,
        "guards": proposal_policy["guards"],
        "proposals": proposals,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build proposal-only large-run policy feedback from rehearsal telemetry.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="policy-large-run-rehearsal.json path")
    parser.add_argument("--report", action="append", default=[], help="rehearsal-report.json path")
    parser.add_argument("--ledger", action="append", default=[], help="backpressure-telemetry-ledger.json path")
    parser.add_argument("--run-root", action="append", default=[], help="directory containing rehearsal-report.json and backpressure-telemetry-ledger.json")
    parser.add_argument("--output-json", default="", help="optional proposal JSON output path; omitted prints to stdout only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = Path(args.policy)
    policy = load_json(policy_path)
    reports, ledgers = collect_inputs(
        [Path(path) for path in args.report],
        [Path(path) for path in args.ledger],
        [Path(path) for path in args.run_root],
    )
    if not reports:
        raise SystemExit("[large_run_feedback] at least one --report or --run-root with rehearsal-report.json is required")
    result = build_feedback_proposals(policy=policy, policy_path_text=str(policy_path), reports=reports, ledgers=ledgers)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"[large_run_feedback] wrote {output_path} proposals={result['proposalCount']} canonicalWrites=false")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
