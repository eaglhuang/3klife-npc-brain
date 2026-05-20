from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ConvergenceLoopStatePolicyLoader = Callable[..., dict[str, Any]]


def apply_convergence_loop_state_governance_atom(
    *,
    governance_root: str | Path | None,
    convergence_state_policy: str | Path | None,
    load_convergence_loop_state_policy_fn: ConvergenceLoopStatePolicyLoader,
) -> dict[str, Any]:
    policy = load_convergence_loop_state_policy_fn(
        governance_root,
        convergence_state_policy=convergence_state_policy,
    )
    return dict(policy)


# ── SANGUO-AUTO-0603: Round ledger 與停止條件 ────────────────────────────────

ROUND_LEDGER_SCHEMA = "round-ledger.v0.1"
DEFAULT_NO_PROGRESS_PATIENCE = 3


def utc_now_str() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_round_ledger_entry(
    round_id: int,
    run_id: str,
    prev_scoreboard: list[dict[str, Any]],
    new_scoreboard: list[dict[str, Any]],
    new_card_count: int = 0,
    verdict_distribution: dict[str, int] | None = None,
    rollback_reason: str | None = None,
) -> dict[str, Any]:
    """
    建立 round ledger entry。
    ledger 有 deltaWorldbuilding、deltaHistorical、newCardCount、
    verdictDistribution、rollbackReason。
    """
    prev_by_id = {r.get("generalId"): r for r in prev_scoreboard}
    delta_worldbuilding = 0.0
    delta_historical = 0.0
    for row in new_scoreboard:
        gid = row.get("generalId")
        prev = prev_by_id.get(gid)
        if prev:
            delta_worldbuilding += float(row.get("worldbuildingUsabilityScore", 0)) - float(prev.get("worldbuildingUsabilityScore", 0))
            delta_historical += float(row.get("historicalTrustScore", 0)) - float(prev.get("historicalTrustScore", 0))

    return {
        "schemaVersion": ROUND_LEDGER_SCHEMA,
        "roundId": round_id,
        "runId": run_id,
        "recordedAt": utc_now_str(),
        "deltaWorldbuilding": round(delta_worldbuilding, 4),
        "deltaHistorical": round(delta_historical, 4),
        "newCardCount": new_card_count,
        "verdictDistribution": verdict_distribution or {},
        "rollbackReason": rollback_reason,
        "hasProgress": (delta_worldbuilding > 0.001 or delta_historical > 0.001 or new_card_count > 0),
    }


def check_stop_conditions(
    round_ledger: list[dict[str, Any]],
    no_progress_patience: int = DEFAULT_NO_PROGRESS_PATIENCE,
    max_delta_historical_per_round: float = 5.0,
) -> dict[str, Any]:
    """
    檢查停止條件：
    1. 無進展 N 輪 → 進 residual dossier
    2. 單輪 historicalTrustScore 上升超過閾值 → 觸發 rollback alert
    """
    if not round_ledger:
        return {"shouldStop": False, "reason": None}

    no_progress_count = 0
    for entry in reversed(round_ledger):
        if not entry.get("hasProgress"):
            no_progress_count += 1
        else:
            break

    latest = round_ledger[-1]
    historical_spike = abs(float(latest.get("deltaHistorical", 0))) > max_delta_historical_per_round

    if no_progress_count >= no_progress_patience:
        return {
            "shouldStop": True,
            "reason": f"no progress for {no_progress_count} rounds (patience={no_progress_patience})",
            "action": "residual-dossier",
        }
    if historical_spike:
        return {
            "shouldStop": True,
            "reason": f"historical score spike detected: delta={latest.get('deltaHistorical'):.4f} > {max_delta_historical_per_round}",
            "action": "rollback-alert",
        }
    return {"shouldStop": False, "reason": None}


def write_round_ledger(
    output_path: Path,
    entries: list[dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
