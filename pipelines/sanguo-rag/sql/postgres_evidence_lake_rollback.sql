-- SANGUO-RAGOPS-0201: rollback / truncate plan for the evidence-lake schema.
--
-- This file is intentionally NOT auto-applied. The dry-run script
-- apply_postgres_evidence_lake_schema.py only previews the apply path; the
-- operator must run rollback explicitly via psql, after confirming that
-- JSONL artifacts are still available as canonical export mirror.
--
-- Three rollback modes are supported, ordered from least to most
-- destructive. Use mode 1 (truncate) when retaining schema; mode 2 (drop
-- tables) when removing schema additions; mode 3 (drop schema cascade)
-- only when removing all sanguo_rag tables (also affects observed_mentions
-- / alias_map_entries / triage_label_decisions — use with caution).

-- ----------------- Mode 1: TRUNCATE evidence-lake tables -----------------
-- Removes rows but keeps schema. Preserves foreign-key dependencies.
-- Run inside a transaction so failure does not leave half-empty tables.

BEGIN;
TRUNCATE TABLE sanguo_rag.vector_ingestion_records RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.proposal_ledger          RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.anchor_passages          RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.evidence_cards           RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.evidence_seeds           RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.harvested_pages          RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.source_runs              RESTART IDENTITY CASCADE;
TRUNCATE TABLE sanguo_rag.pipeline_runs            RESTART IDENTITY CASCADE;
ROLLBACK;  -- change to COMMIT after operator review

-- ----------------- Mode 2: DROP evidence-lake tables ---------------------
-- Removes only the tables added by postgres_evidence_lake_schema.sql.
-- Leaves observed_mentions / alias_map_entries / triage_label_decisions
-- intact.

BEGIN;
DROP VIEW  IF EXISTS sanguo_rag.evidence_lake_run_summary;
DROP TABLE IF EXISTS sanguo_rag.vector_ingestion_records CASCADE;
DROP TABLE IF EXISTS sanguo_rag.proposal_ledger          CASCADE;
DROP TABLE IF EXISTS sanguo_rag.anchor_passages          CASCADE;
DROP TABLE IF EXISTS sanguo_rag.evidence_cards           CASCADE;
DROP TABLE IF EXISTS sanguo_rag.evidence_seeds           CASCADE;
DROP TABLE IF EXISTS sanguo_rag.harvested_pages          CASCADE;
DROP TABLE IF EXISTS sanguo_rag.source_runs              CASCADE;
DROP TABLE IF EXISTS sanguo_rag.pipeline_runs            CASCADE;
ROLLBACK;  -- change to COMMIT after operator review

-- ----------------- Mode 3: DROP SCHEMA CASCADE ---------------------------
-- Only when removing all Sanguo-RAG state including pre-existing tables.
-- Requires re-applying postgres_schema.sql + postgres_evidence_lake_schema.sql
-- afterwards.

BEGIN;
DROP SCHEMA IF EXISTS sanguo_rag CASCADE;
ROLLBACK;  -- change to COMMIT after operator review
