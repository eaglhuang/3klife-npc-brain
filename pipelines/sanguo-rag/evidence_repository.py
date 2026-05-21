"""Evidence repository adapter (jsonl / postgres / dual modes).

SANGUO-RAGOPS-0202. Provides a thin abstraction layer for the Sanguo-RAG
evidence pipeline so that pipeline_runs, source_runs, harvested_pages,
evidence_seeds, evidence_cards, anchor_passages, proposal_ledger, and
vector_ingestion_records can be persisted to JSONL (canonical), PostgreSQL
(mirror), or both at once (dual-write).

Design constraints
------------------
* JSONL canonical export remains the source of truth. ``mode='jsonl'``
  is the default and ``mode='dual'`` only mirrors to PostgreSQL.
* All writes are idempotent. Per table, the natural / unique key is used
  by both backends so a re-run produces the same final state.
* No DSN, schema name, or namespace is hardcoded. Connection settings are
  resolved from environment variables (``SANGUO_RAG_PG_DSN`` etc.) or via
  ``RepositorySettings``.
* Failures are written to an error ledger (``errors``) rather than thrown
  out of the adapter. The caller decides whether to abort. PostgreSQL
  writes retry with exponential backoff according to ``RetryPolicy``.
* The adapter runs in dry-run mode if ``dry_run=True``; in dry-run, no
  files are written and no DB statements are issued.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


__all__ = [
    "EvidenceRepository",
    "JsonlEvidenceRepository",
    "PostgresEvidenceRepository",
    "DualEvidenceRepository",
    "RepositorySettings",
    "RetryPolicy",
    "RepositoryError",
    "WriteResult",
    "build_repository",
]


# =========================================================================
# Configuration
# =========================================================================

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0

    def sleep_for(self, attempt: int) -> float:
        return self.backoff_seconds * (self.backoff_multiplier ** max(0, attempt - 1))


@dataclass
class RepositorySettings:
    mode: str = "jsonl"
    jsonl_root: Path = field(default_factory=lambda: Path("artifacts/data-pipeline/sanguo-rag/lake"))
    postgres_dsn: str | None = None
    postgres_schema: str = "sanguo_rag"
    dry_run: bool = False
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_env(
        cls,
        *,
        mode: str | None = None,
        dry_run: bool | None = None,
        jsonl_root: str | Path | None = None,
    ) -> "RepositorySettings":
        resolved_mode = (mode or os.environ.get("SANGUO_RAG_REPO_MODE") or "jsonl").lower()
        if resolved_mode not in {"jsonl", "postgres", "dual"}:
            raise RepositoryError(f"unknown repository mode: {resolved_mode!r}")
        dsn = os.environ.get("SANGUO_RAG_PG_DSN")
        schema = os.environ.get("SANGUO_RAG_PG_SCHEMA", "sanguo_rag")
        root = Path(jsonl_root) if jsonl_root else Path(
            os.environ.get(
                "SANGUO_RAG_LAKE_ROOT",
                "artifacts/data-pipeline/sanguo-rag/lake",
            )
        )
        dry = dry_run if dry_run is not None else bool(int(os.environ.get("SANGUO_RAG_REPO_DRY_RUN", "0") or "0"))
        return cls(
            mode=resolved_mode,
            jsonl_root=root,
            postgres_dsn=dsn,
            postgres_schema=schema,
            dry_run=dry,
        )


class RepositoryError(RuntimeError):
    """Raised for adapter-level configuration or unrecoverable errors."""


# =========================================================================
# WriteResult / error ledger
# =========================================================================

@dataclass
class WriteResult:
    table: str
    requested: int = 0
    written: int = 0
    skipped_duplicate: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    backend: str = ""
    backend_count: int = 1

    def merge(self, other: "WriteResult") -> "WriteResult":
        return WriteResult(
            table=self.table,
            requested=self.requested + other.requested,
            written=self.written + other.written,
            skipped_duplicate=self.skipped_duplicate + other.skipped_duplicate,
            errors=self.errors + other.errors,
            backend=";".join(part for part in (self.backend, other.backend) if part),
            backend_count=self.backend_count + other.backend_count,
        )


# =========================================================================
# Idempotent key derivation per table
# =========================================================================

_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "pipeline_runs": ("run_id",),
    "source_runs": ("run_id", "source_id"),
    "harvested_pages": ("run_id", "url_hash", "text_hash"),
    "evidence_seeds": ("seed_id",),
    "evidence_cards": ("evidence_id",),
    "anchor_passages": ("passage_id",),
    "proposal_ledger": ("proposal_id",),
    "vector_ingestion_records": ("provider", "namespace", "record_id", "record_sha256"),
}


def _idempotent_key(table: str, row: dict[str, Any]) -> str:
    keys = _TABLE_KEYS.get(table)
    if not keys:
        raise RepositoryError(f"unknown table: {table!r}")
    parts: list[str] = []
    for key in keys:
        if key not in row:
            raise RepositoryError(f"row missing idempotent key {key!r} for table {table!r}")
        parts.append(str(row[key]))
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# =========================================================================
# Abstract repository
# =========================================================================

class EvidenceRepository(ABC):
    backend_name: str = ""

    @abstractmethod
    def upsert(self, table: str, rows: Sequence[dict[str, Any]]) -> WriteResult:
        ...

    def upsert_iter(self, table: str, rows: Iterable[dict[str, Any]]) -> WriteResult:
        return self.upsert(table, list(rows))

    def close(self) -> None:
        return None


# =========================================================================
# JSONL backend
# =========================================================================

class JsonlEvidenceRepository(EvidenceRepository):
    backend_name = "jsonl"

    def __init__(self, settings: RepositorySettings) -> None:
        self._settings = settings
        self._seen_keys: dict[str, set[str]] = {}

    def upsert(self, table: str, rows: Sequence[dict[str, Any]]) -> WriteResult:
        result = WriteResult(table=table, backend=self.backend_name, requested=len(rows))
        if not rows:
            return result
        target = self._settings.jsonl_root / "_state" / f"{table}.jsonl"
        if self._settings.dry_run:
            for row in rows:
                key = _idempotent_key(table, row)
                seen = self._seen_keys.setdefault(table, set())
                if key in seen:
                    result.skipped_duplicate += 1
                else:
                    seen.add(key)
                    result.written += 1
            return result
        target.parent.mkdir(parents=True, exist_ok=True)
        seen = self._seen_keys.setdefault(table, self._load_existing_keys(table))
        with target.open("a", encoding="utf-8") as handle:
            for row in rows:
                key = _idempotent_key(table, row)
                if key in seen:
                    result.skipped_duplicate += 1
                    continue
                seen.add(key)
                row_to_write = dict(row)
                row_to_write.setdefault("_idempotentKey", key)
                handle.write(json.dumps(row_to_write, ensure_ascii=False) + "\n")
                result.written += 1
        return result

    def _load_existing_keys(self, table: str) -> set[str]:
        target = self._settings.jsonl_root / "_state" / f"{table}.jsonl"
        if not target.exists():
            return set()
        keys: set[str] = set()
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cached = row.get("_idempotentKey")
            if isinstance(cached, str):
                keys.add(cached)
            else:
                try:
                    keys.add(_idempotent_key(table, row))
                except RepositoryError:
                    continue
        return keys


# =========================================================================
# PostgreSQL backend
# =========================================================================

_PG_UPSERT_TEMPLATES: dict[str, dict[str, Any]] = {
    "pipeline_runs": {
        "columns": ("run_id", "lane", "run_profile", "input_fingerprint", "canonical_writes",
                    "status", "started_at", "finished_at", "summary", "policy_refs", "raw_payload"),
        "conflict": ("run_id",),
    },
    "source_runs": {
        "columns": ("run_id", "source_id", "source_family", "source_layer", "fetch_count",
                    "harvested_count", "seed_count", "card_count", "timeout_count", "roi_score",
                    "body_boundary_summary", "raw_payload"),
        "conflict": ("run_id", "source_id"),
    },
    "harvested_pages": {
        "columns": ("run_id", "source_id", "url", "url_hash", "title", "text_hash",
                    "body_start", "body_end", "raw_bytes", "artifact_uri",
                    "source_policy_id", "raw_payload"),
        "conflict": ("run_id", "url_hash", "text_hash"),
    },
    "evidence_seeds": {
        "columns": ("seed_id", "run_id", "source_id", "general_id", "angle_type",
                    "seed_text_hash", "score", "anchor", "payload", "payload_uri"),
        "conflict": ("seed_id",),
    },
    "evidence_cards": {
        "columns": ("evidence_id", "run_id", "source_id", "source_family", "source_layer",
                    "general_ids", "quote_hash", "locator", "anchor_evidence",
                    "trust_score", "review_status", "payload", "payload_uri"),
        "conflict": ("evidence_id",),
    },
    "anchor_passages": {
        "columns": ("passage_id", "run_id", "corpus_id", "layer", "locator", "text_hash",
                    "normalized_text", "artifact_uri", "raw_payload"),
        "conflict": ("passage_id",),
    },
    "proposal_ledger": {
        "columns": ("proposal_id", "run_id", "proposal_kind", "source_id", "signature",
                    "status", "sandbox_outcome", "payload", "artifact_uri", "decided_at"),
        "conflict": ("proposal_id",),
    },
    "vector_ingestion_records": {
        "columns": ("run_id", "provider", "namespace", "record_id", "record_sha256",
                    "source_table", "upsert_manifest_uri", "probe_manifest_uri",
                    "rollback_manifest_uri", "status", "payload"),
        "conflict": ("provider", "namespace", "record_id", "record_sha256"),
    },
}


def _build_upsert_sql(schema: str, table: str) -> str:
    template = _PG_UPSERT_TEMPLATES.get(table)
    if not template:
        raise RepositoryError(f"no upsert template for {table!r}")
    columns: tuple[str, ...] = template["columns"]
    conflict: tuple[str, ...] = template["conflict"]
    column_csv = ", ".join(columns)
    placeholders = ", ".join(f"%({col})s" for col in columns)
    conflict_csv = ", ".join(conflict)
    update_set = ", ".join(
        f"{col} = EXCLUDED.{col}" for col in columns if col not in conflict
    )
    if update_set:
        return (
            f"INSERT INTO {schema}.{table} ({column_csv}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_csv}) DO UPDATE SET {update_set}"
        )
    return (
        f"INSERT INTO {schema}.{table} ({column_csv}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_csv}) DO NOTHING"
    )


class PostgresEvidenceRepository(EvidenceRepository):
    backend_name = "postgres"

    def __init__(self, settings: RepositorySettings) -> None:
        if not settings.postgres_dsn and not settings.dry_run:
            raise RepositoryError(
                "PostgresEvidenceRepository requires postgres_dsn (or SANGUO_RAG_PG_DSN) when dry_run=False"
            )
        self._settings = settings
        self._conn = None
        if not settings.dry_run:
            self._conn = self._connect()

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RepositoryError(
                "psycopg is not installed; run pip install psycopg[binary] or use --dry-run"
            ) from exc
        return psycopg.connect(self._settings.postgres_dsn)

    def upsert(self, table: str, rows: Sequence[dict[str, Any]]) -> WriteResult:
        result = WriteResult(table=table, backend=self.backend_name, requested=len(rows))
        if not rows:
            return result
        sql = _build_upsert_sql(self._settings.postgres_schema, table)
        if self._settings.dry_run:
            result.written = len(rows)
            return result

        retry = self._settings.retry
        attempts = 0
        for attempt in range(1, retry.max_attempts + 1):
            attempts = attempt
            try:
                with self._conn.cursor() as cur:  # type: ignore[union-attr]
                    cur.executemany(sql, [self._coerce(row) for row in rows])
                self._conn.commit()  # type: ignore[union-attr]
                result.written = len(rows)
                return result
            except Exception as exc:  # pragma: no cover - depends on driver
                self._conn.rollback()  # type: ignore[union-attr]
                if attempt >= retry.max_attempts:
                    result.errors.append(
                        {"kind": "postgres-upsert", "attempts": attempt, "message": str(exc)}
                    )
                    return result
                time.sleep(retry.sleep_for(attempt))
        result.errors.append({"kind": "postgres-upsert-exhausted", "attempts": attempts})
        return result

    @staticmethod
    def _coerce(row: dict[str, Any]) -> dict[str, Any]:
        coerced: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)) and key in {
                "summary",
                "policy_refs",
                "raw_payload",
                "score",
                "anchor",
                "payload",
                "anchor_evidence",
                "trust_score",
                "body_boundary_summary",
                "sandbox_outcome",
            }:
                coerced[key] = json.dumps(value, ensure_ascii=False)
            else:
                coerced[key] = value
        return coerced

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


# =========================================================================
# Dual backend
# =========================================================================

class DualEvidenceRepository(EvidenceRepository):
    backend_name = "dual"

    def __init__(
        self,
        jsonl: JsonlEvidenceRepository,
        postgres: PostgresEvidenceRepository,
    ) -> None:
        self._jsonl = jsonl
        self._postgres = postgres

    def upsert(self, table: str, rows: Sequence[dict[str, Any]]) -> WriteResult:
        jsonl_result = self._jsonl.upsert(table, rows)
        pg_result = self._postgres.upsert(table, rows)
        merged = jsonl_result.merge(pg_result)
        merged.backend = self.backend_name
        return merged

    def close(self) -> None:
        try:
            self._jsonl.close()
        finally:
            self._postgres.close()


# =========================================================================
# Factory
# =========================================================================

def build_repository(settings: RepositorySettings | None = None) -> EvidenceRepository:
    settings = settings or RepositorySettings.from_env()
    if settings.mode == "jsonl":
        return JsonlEvidenceRepository(settings)
    if settings.mode == "postgres":
        return PostgresEvidenceRepository(settings)
    if settings.mode == "dual":
        return DualEvidenceRepository(
            JsonlEvidenceRepository(settings),
            PostgresEvidenceRepository(settings),
        )
    raise RepositoryError(f"unknown repository mode: {settings.mode!r}")
