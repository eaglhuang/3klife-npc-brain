"""Dry-run / apply runner for the evidence-lake schema migration.

SANGUO-RAGOPS-0201. Default mode is ``--dry-run``: the script parses the
schema SQL into individual statements and reports which CREATE TABLE /
CREATE INDEX / CREATE VIEW statements would run, without touching any
PostgreSQL instance. Apply mode requires both ``--apply`` and a valid
``SANGUO_RAG_PG_DSN`` (or ``--dsn`` override).

The runner intentionally does not invoke production DSN unless the
operator passes ``--apply``. It also never executes the rollback SQL;
operators must run rollback statements manually via psql after reviewing
the rollback plan in ``postgres_evidence_lake_rollback.sql``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_SCHEMA = ROOT / "sql" / "postgres_evidence_lake_schema.sql"
DEFAULT_BASE_SCHEMA = ROOT / "sql" / "postgres_schema.sql"
DEFAULT_ROLLBACK = ROOT / "sql" / "postgres_evidence_lake_rollback.sql"

STATEMENT_PATTERN = re.compile(r";\s*(?:--[^\n]*\n)?", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run / apply evidence-lake schema migration (SANGUO-RAGOPS-0201).",
    )
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="schema SQL path")
    parser.add_argument("--base-schema", default=str(DEFAULT_BASE_SCHEMA), help="base schema SQL path (observed_mentions etc.)")
    parser.add_argument("--rollback", default=str(DEFAULT_ROLLBACK), help="rollback SQL path (informational)")
    parser.add_argument("--dry-run", action="store_true", help="emit plan only, no DB connection")
    parser.add_argument("--apply", action="store_true", help="actually execute the schema SQL")
    parser.add_argument("--include-base", action="store_true", help="also apply the base postgres_schema.sql before the evidence-lake schema")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; overrides SANGUO_RAG_PG_DSN")
    parser.add_argument("--output", default="", help="optional JSON report output path")
    return parser.parse_args()


def split_statements(sql_text: str) -> list[str]:
    cleaned = re.sub(r"--[^\n]*", "", sql_text)
    parts = [chunk.strip() for chunk in cleaned.split(";")]
    return [chunk for chunk in parts if chunk]


def classify(statement: str) -> str:
    head = statement.lstrip().upper()
    if head.startswith("CREATE SCHEMA"):
        return "create-schema"
    if head.startswith("CREATE TABLE"):
        return "create-table"
    if head.startswith("CREATE INDEX") or head.startswith("CREATE UNIQUE INDEX"):
        return "create-index"
    if head.startswith("CREATE OR REPLACE VIEW") or head.startswith("CREATE VIEW"):
        return "create-view"
    if head.startswith("ALTER TABLE"):
        return "alter-table"
    if head.startswith("DROP"):
        return "drop"
    return "other"


def summarize(statements: list[str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    table_names: list[str] = []
    index_names: list[str] = []
    view_names: list[str] = []
    for stmt in statements:
        kind = classify(stmt)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "create-table":
            match = re.search(r"CREATE TABLE IF NOT EXISTS\s+([^\s(]+)", stmt, re.IGNORECASE)
            if match:
                table_names.append(match.group(1))
        elif kind == "create-index":
            match = re.search(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS\s+(\S+)", stmt, re.IGNORECASE)
            if match:
                index_names.append(match.group(1))
        elif kind == "create-view":
            match = re.search(r"CREATE OR REPLACE VIEW\s+(\S+)", stmt, re.IGNORECASE)
            if match:
                view_names.append(match.group(1))
    return {
        "statementCount": len(statements),
        "counts": counts,
        "tables": table_names,
        "indexes": index_names,
        "views": view_names,
    }


def execute(dsn: str, statements: list[str]) -> dict[str, Any]:
    try:
        import psycopg  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - import-time path
        raise SystemExit(
            "[apply_postgres_evidence_lake_schema] psycopg not available; install psycopg[binary] or run with --dry-run"
        ) from exc

    executed: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt + ";")
                executed.append(classify(stmt))
        conn.commit()
    return {"executedCount": len(executed), "byKind": dict.fromkeys(executed, 0) | _count(executed)}


def _count(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def main() -> int:
    args = parse_args()
    schema_path = Path(args.schema)
    base_path = Path(args.base_schema)
    rollback_path = Path(args.rollback)

    if not schema_path.exists():
        print(f"[apply_postgres_evidence_lake_schema] schema not found: {schema_path}")
        return 2

    schema_statements = split_statements(schema_path.read_text(encoding="utf-8"))
    base_statements: list[str] = []
    if args.include_base:
        if not base_path.exists():
            print(f"[apply_postgres_evidence_lake_schema] base schema not found: {base_path}")
            return 2
        base_statements = split_statements(base_path.read_text(encoding="utf-8"))

    plan_statements = base_statements + schema_statements
    plan = {
        "schemaPath": str(schema_path),
        "baseSchemaPath": str(base_path) if args.include_base else None,
        "rollbackPath": str(rollback_path),
        "schemaSummary": summarize(schema_statements),
        "baseSummary": summarize(base_statements) if args.include_base else None,
        "plannedStatementCount": len(plan_statements),
    }

    if args.apply:
        dsn = args.dsn or os.environ.get("SANGUO_RAG_PG_DSN")
        if not dsn:
            print("[apply_postgres_evidence_lake_schema] --apply requires SANGUO_RAG_PG_DSN or --dsn")
            return 2
        apply_result = execute(dsn, plan_statements)
        plan["apply"] = apply_result
        plan["mode"] = "apply"
    else:
        plan["mode"] = "dry-run"

    output = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"[apply_postgres_evidence_lake_schema] wrote {out_path}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
