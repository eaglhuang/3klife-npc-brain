"""Opt-in evidence repository write seam for the convergence loop (SANGUO-RAGOPS-0602).

Thin adapter called at the end of each convergence round and at run completion.
Reads the opt-in contract from ``policy-convergence-evidence-repo.json``, checks
the SANGUO_RAG_CONVERGENCE_REPO_ENABLED env var, and, when enabled, routes
evidence_cards / source_runs / pipeline_runs rows into the evidence repository
adapter (SANGUO-RAGOPS-0202).

Isolation invariants
--------------------
* The convergence loop's canonical JSONL outputs are **never modified** by this seam.
* ``canonicalWrites`` is always ``False`` for all rows written through this seam.
* If any repository write errors, the error is appended to the error ledger inside
  the run root and the caller continues.  The seam never aborts the main pipeline.
* All modes, DSNs, schemas, namespaces, and budgets come from policy or env vars —
  nothing is hardcoded here.

Usage::

    seam = ConvergenceRepoSeam.from_policy(repo_root=REPO_ROOT)
    # seam.enabled is False unless SANGUO_RAG_CONVERGENCE_REPO_ENABLED=1
    seam.write_round(round_info=round_info, run_id=args.run_id, run_root=run_root)
    seam.write_run_summary(summary_payload=summary_payload, run_root=run_root)
    seam.close()
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_repository import (  # noqa: E402
    RepositorySettings,
    WriteResult,
    build_repository,
)


__all__ = [
    "ConvergenceRepoSeam",
    "ConvergenceSeamError",
]

_DEFAULT_POLICY_REL = "data/sanguo/policies/policy-convergence-evidence-repo.json"
_ENABLED_ENV = "SANGUO_RAG_CONVERGENCE_REPO_ENABLED"
_MODE_ENV = "SANGUO_RAG_CONVERGENCE_REPO_MODE"
_DRY_RUN_ENV = "SANGUO_RAG_CONVERGENCE_REPO_DRY_RUN"
_POLICY_PATH_ENV = "SANGUO_RAG_CONVERGENCE_REPO_POLICY"


class ConvergenceSeamError(RuntimeError):
    """Configuration or unrecoverable error in the convergence evidence seam."""


# =========================================================================
# Error ledger
# =========================================================================

def _append_error_ledger(run_root: Path, entry: dict[str, Any]) -> None:
    path = run_root / "_convergence-repo-error-ledger.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover
        pass  # error ledger write failure is silently swallowed


# =========================================================================
# Policy loader
# =========================================================================

def _load_policy(policy_path: Path) -> dict[str, Any]:
    if not policy_path.exists():
        return {}
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _resolve_policy_path(repo_root: Path) -> Path:
    env_override = os.environ.get(_POLICY_PATH_ENV, "").strip()
    if env_override:
        candidate = Path(env_override)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate
    return repo_root / _DEFAULT_POLICY_REL


def _review_status_contract(policy: dict[str, Any]) -> tuple[set[str], str]:
    values = policy.get("evidenceCardReviewStatuses")
    statuses = {str(item).strip() for item in values if str(item).strip()} if isinstance(values, list) else set()
    round_policy = policy.get("roundWritePolicy") if isinstance(policy.get("roundWritePolicy"), dict) else {}
    fallback = str(round_policy.get("reviewStatusCoercion") or "").strip()
    if fallback:
        statuses.add(fallback)
    return statuses, fallback


# =========================================================================
# Row coercers (convergence-loop specific)
# =========================================================================

def _stable_hash(*parts: Any) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _coerce_evidence_card_from_loop(
    row: dict[str, Any],
    *,
    run_id: str,
    source_id: str,
    artifact_uri: str,
    review_statuses: set[str],
    review_status_fallback: str,
) -> dict[str, Any] | None:
    """Coerce a convergence-loop evidence card dict into the evidence_cards schema."""
    evidence_id = (
        row.get("evidenceId")
        or row.get("id")
        or row.get("eventId")
    )
    if not evidence_id:
        return None
    review_status = str(row.get("reviewStatus") or review_status_fallback)
    if review_statuses and review_status not in review_statuses:
        review_status = review_status_fallback
    quote_source = row.get("sourceQuote") or row.get("summary") or ""
    return {
        "evidence_id": str(evidence_id),
        "run_id": run_id,
        "source_id": source_id,
        "source_family": str(row.get("sourceFamily") or ""),
        "source_layer": str(row.get("sourceLayer") or ""),
        "general_ids": list(row.get("generalIds") or []),
        "quote_hash": str(
            row.get("quoteHash")
            or hashlib.sha256(str(quote_source).encode("utf-8")).hexdigest()
        ),
        "locator": str(row.get("locator") or row.get("sourceRef") or ""),
        "anchor_evidence": row.get("anchorEvidence") or {},
        "trust_score": row.get("trustScore") or {},
        "review_status": review_status,
        "payload": dict(row),
        "payload_uri": artifact_uri,
    }


def _coerce_source_run(
    *,
    run_id: str,
    source_id: str,
    card_count: int,
    seed_count: int,
    fetch_count: int,
    source_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_id": source_id,
        "source_family": str(source_summary.get("sourceFamily") or ""),
        "source_layer": str(source_summary.get("sourceLayer") or ""),
        "fetch_count": fetch_count,
        "harvested_count": fetch_count,
        "seed_count": seed_count,
        "card_count": card_count,
        "timeout_count": int(source_summary.get("timeoutCount") or 0),
        "roi_score": None,
        "body_boundary_summary": {},
        "raw_payload": source_summary,
    }


def _coerce_pipeline_run(
    *,
    run_id: str,
    summary_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "lane": "convergence-loop",
        "run_profile": str(summary_payload.get("profile") or summary_payload.get("mode") or "convergence-loop"),
        "input_fingerprint": "",
        "canonical_writes": False,
        "status": str(summary_payload.get("stopReason") or "completed"),
        "started_at": str(summary_payload.get("generatedAt") or ""),
        "finished_at": str(summary_payload.get("generatedAt") or ""),
        "summary": {
            "rounds": int(summary_payload.get("roundsExecuted") or 0),
            "stopReason": str(summary_payload.get("stopReason") or ""),
            "dryRun": bool(summary_payload.get("dryRun")),
        },
        "policy_refs": ["data/sanguo/policies/policy-convergence-evidence-repo.json"],
        "raw_payload": {},
    }


# =========================================================================
# JSONL readers
# =========================================================================

def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


# =========================================================================
# Main seam class
# =========================================================================

@dataclass
class ConvergenceRepoSeam:
    """Opt-in evidence repository write seam for the convergence loop.

    Construct via :meth:`from_policy`. When disabled (default), all
    ``write_*`` methods are no-ops.
    """

    enabled: bool = False
    dry_run: bool = True
    mode: str = "postgres"
    _settings: RepositorySettings | None = field(default=None, repr=False)
    _repo: Any = field(default=None, repr=False)
    _error_count: int = field(default=0, repr=False)
    _write_results: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _review_statuses: set[str] = field(default_factory=set, repr=False)
    _review_status_fallback: str = field(default="", repr=False)

    @classmethod
    def from_policy(cls, repo_root: Path) -> "ConvergenceRepoSeam":
        """Build seam from policy file + environment variables."""
        policy_path = _resolve_policy_path(repo_root)
        policy = _load_policy(policy_path)
        review_statuses, review_status_fallback = _review_status_contract(policy)

        # Check opt-in flag (SANGUO_RAG_CONVERGENCE_REPO_ENABLED=1)
        opt_in_env = os.environ.get(_ENABLED_ENV, "0").strip()
        enabled = opt_in_env == "1"
        if not enabled:
            return cls(enabled=False)

        # Resolve mode and dry-run from env (convergence-specific vars take priority)
        mode_env = (
            os.environ.get(_MODE_ENV, "").strip()
            or policy.get("repoMode", {}).get("default", "postgres")
        )
        if mode_env not in {"jsonl", "postgres", "dual"}:
            mode_env = "postgres"

        dry_run_raw = os.environ.get(_DRY_RUN_ENV, "1").strip()
        dry_run = dry_run_raw != "0"

        settings = RepositorySettings.from_env(mode=mode_env, dry_run=dry_run)
        settings.jsonl_root = repo_root / "artifacts/data-pipeline/sanguo-rag/lake"

        try:
            repo = build_repository(settings)
        except Exception as exc:
            raise ConvergenceSeamError(
                f"convergence seam: failed to build repository ({exc})"
            ) from exc

        return cls(
            enabled=True,
            dry_run=dry_run,
            mode=mode_env,
            _settings=settings,
            _repo=repo,
            _review_statuses=review_statuses,
            _review_status_fallback=review_status_fallback,
        )

    # ------------------------------------------------------------------ #
    # Round write
    # ------------------------------------------------------------------ #

    def write_round(
        self,
        *,
        round_info: dict[str, Any],
        run_id: str,
        run_root: Path,
        repo_root: Path,
    ) -> list[WriteResult]:
        """Write evidence_cards and source_runs for one convergence round.

        Parameters
        ----------
        round_info:
            The round result dict returned by ``run_round()``.
        run_id:
            The top-level convergence run ID.
        run_root:
            Run output root (for error ledger).
        repo_root:
            Repo root (for resolving relative paths).
        """
        if not self.enabled or self._repo is None:
            return []

        results: list[WriteResult] = []

        # Collect evidence card paths from round
        card_path_keys = ["externalCardsPath", "globalCandidateCardsPath"]
        card_rows: list[dict[str, Any]] = []
        source_card_counts: dict[str, int] = {}
        source_seed_counts: dict[str, int] = {}
        source_fetch_counts: dict[str, int] = {}
        source_summaries: dict[str, dict[str, Any]] = {}

        for key in card_path_keys:
            path_text = str(round_info.get(key) or "").strip()
            if not path_text:
                continue
            candidate = Path(path_text)
            if not candidate.is_absolute():
                candidate = (repo_root / path_text).resolve()
            rows = _read_jsonl_rows(candidate)
            artifact_uri = f"atm://lake/{run_id}/convergence-loop/{key}/{candidate.name}"
            for row in rows:
                source_id = str(row.get("sourcePolicyId") or row.get("sourceId") or row.get("sourceFamily") or "")
                coerced = _coerce_evidence_card_from_loop(
                    row,
                    run_id=run_id,
                    source_id=source_id,
                    artifact_uri=artifact_uri,
                    review_statuses=self._review_statuses,
                    review_status_fallback=self._review_status_fallback,
                )
                if coerced is None:
                    continue
                card_rows.append(coerced)
                source_card_counts[source_id] = source_card_counts.get(source_id, 0) + 1

        if card_rows:
            try:
                result = self._repo.upsert("evidence_cards", card_rows)
                results.append(result)
                self._write_results.append({
                    "table": "evidence_cards",
                    "roundId": round_info.get("roundId"),
                    "requested": result.requested,
                    "written": result.written,
                    "skipped_duplicate": result.skipped_duplicate,
                    "errors": result.errors,
                })
                if result.errors:
                    for err in result.errors:
                        _append_error_ledger(run_root, {
                            "kind": "evidence-cards-write-error",
                            "roundId": round_info.get("roundId"),
                            "error": err,
                        })
                    self._error_count += len(result.errors)
            except Exception as exc:
                _append_error_ledger(run_root, {
                    "kind": "evidence-cards-exception",
                    "roundId": round_info.get("roundId"),
                    "message": str(exc),
                })
                self._error_count += 1

        # Build source_runs roll-up from external summary
        external_summary = round_info.get("externalSummary") or {}
        source_results = external_summary.get("sourceResults") if isinstance(external_summary, dict) else []
        if isinstance(source_results, list):
            for sr in source_results:
                if not isinstance(sr, dict):
                    continue
                sid = str(sr.get("sourceId") or "").strip()
                if sid:
                    source_summaries[sid] = sr
                    source_fetch_counts[sid] = int(sr.get("fetchedPageCount") or 0)
                    source_seed_counts[sid] = int(sr.get("seedCount") or 0)

        source_run_rows = []
        all_source_ids = set(source_card_counts) | set(source_seed_counts) | set(source_fetch_counts)
        for sid in all_source_ids:
            if not sid:
                continue
            source_run_rows.append(
                _coerce_source_run(
                    run_id=run_id,
                    source_id=sid,
                    card_count=source_card_counts.get(sid, 0),
                    seed_count=source_seed_counts.get(sid, 0),
                    fetch_count=source_fetch_counts.get(sid, 0),
                    source_summary=source_summaries.get(sid, {}),
                )
            )

        if source_run_rows:
            try:
                result = self._repo.upsert("source_runs", source_run_rows)
                results.append(result)
                self._write_results.append({
                    "table": "source_runs",
                    "roundId": round_info.get("roundId"),
                    "requested": result.requested,
                    "written": result.written,
                    "skipped_duplicate": result.skipped_duplicate,
                    "errors": result.errors,
                })
                if result.errors:
                    for err in result.errors:
                        _append_error_ledger(run_root, {
                            "kind": "source-runs-write-error",
                            "roundId": round_info.get("roundId"),
                            "error": err,
                        })
                    self._error_count += len(result.errors)
            except Exception as exc:
                _append_error_ledger(run_root, {
                    "kind": "source-runs-exception",
                    "roundId": round_info.get("roundId"),
                    "message": str(exc),
                })
                self._error_count += 1

        return results

    # ------------------------------------------------------------------ #
    # Run summary write
    # ------------------------------------------------------------------ #

    def write_run_summary(
        self,
        *,
        summary_payload: dict[str, Any],
        run_root: Path,
    ) -> WriteResult | None:
        """Write pipeline_runs row on convergence run completion."""
        if not self.enabled or self._repo is None:
            return None
        run_id = str(summary_payload.get("runId") or "").strip()
        if not run_id:
            return None
        pipeline_row = _coerce_pipeline_run(run_id=run_id, summary_payload=summary_payload)
        try:
            result = self._repo.upsert("pipeline_runs", [pipeline_row])
            self._write_results.append({
                "table": "pipeline_runs",
                "run_id": run_id,
                "requested": result.requested,
                "written": result.written,
                "skipped_duplicate": result.skipped_duplicate,
                "errors": result.errors,
            })
            if result.errors:
                for err in result.errors:
                    _append_error_ledger(run_root, {
                        "kind": "pipeline-runs-write-error",
                        "run_id": run_id,
                        "error": err,
                    })
                self._error_count += len(result.errors)
            return result
        except Exception as exc:
            _append_error_ledger(run_root, {
                "kind": "pipeline-runs-exception",
                "run_id": run_id,
                "message": str(exc),
            })
            self._error_count += 1
            return None

    # ------------------------------------------------------------------ #
    # Telemetry
    # ------------------------------------------------------------------ #

    def summary(self) -> dict[str, Any]:
        """Return a dict summarising all writes made through this seam."""
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "dryRun": self.dry_run,
            "errorCount": self._error_count,
            "writeResults": self._write_results,
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Close the underlying repository connection (if any)."""
        if self._repo is not None:
            try:
                self._repo.close()
            finally:
                self._repo = None
