-- SANGUO-RAGOPS-0201: PostgreSQL evidence-lake schema migration.
--
-- This migration is additive only. It does not modify the existing
-- observed_mentions / alias_map_entries / triage_label_decisions tables
-- created by postgres_schema.sql, and it does not force the runtime to
-- enable PostgreSQL. JSONL canonical export remains the source of truth
-- until M5 cutover (see policy-postgres-state-migration-plan.json).
--
-- All high-volume tables expose an idempotent key, text/hash key,
-- run/source query indexes, and a JSONB raw payload column so that the
-- repository adapter (M2-0202) can perform idempotent upserts.

CREATE SCHEMA IF NOT EXISTS sanguo_rag;

-- =========================================================================
-- pipeline_runs : per-run profile, status, canonicalWrites flag
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.pipeline_runs (
  run_id              TEXT PRIMARY KEY,
  lane                TEXT NOT NULL DEFAULT '',
  run_profile         TEXT NOT NULL DEFAULT '',
  input_fingerprint   TEXT NOT NULL DEFAULT '',
  canonical_writes    BOOLEAN NOT NULL DEFAULT FALSE,
  status              TEXT NOT NULL DEFAULT 'created'
                       CHECK (status IN ('created', 'running', 'succeeded', 'failed', 'cancelled', 'rolled-back')),
  started_at          TIMESTAMPTZ,
  finished_at         TIMESTAMPTZ,
  summary             JSONB NOT NULL DEFAULT '{}'::jsonb,
  policy_refs         JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
  inserted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_lane_status
  ON sanguo_rag.pipeline_runs (lane, status);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_input_fingerprint
  ON sanguo_rag.pipeline_runs (input_fingerprint);

-- =========================================================================
-- source_runs : per (run, source) ROI / fetch / timeout / counts
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.source_runs (
  source_run_id        BIGSERIAL PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE CASCADE,
  source_id            TEXT NOT NULL,
  source_family        TEXT NOT NULL DEFAULT '',
  source_layer         TEXT NOT NULL DEFAULT '',
  fetch_count          INTEGER NOT NULL DEFAULT 0,
  harvested_count      INTEGER NOT NULL DEFAULT 0,
  seed_count           INTEGER NOT NULL DEFAULT 0,
  card_count           INTEGER NOT NULL DEFAULT 0,
  timeout_count        INTEGER NOT NULL DEFAULT 0,
  roi_score            DOUBLE PRECISION,
  body_boundary_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_source_runs_run_source UNIQUE (run_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source_runs_source_id
  ON sanguo_rag.source_runs (source_id);

CREATE INDEX IF NOT EXISTS idx_source_runs_family_layer
  ON sanguo_rag.source_runs (source_family, source_layer);

-- =========================================================================
-- harvested_pages : url / textHash / body bounds / artifact pointer
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.harvested_pages (
  page_id              BIGSERIAL PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE CASCADE,
  source_id            TEXT NOT NULL,
  url                  TEXT NOT NULL,
  url_hash             TEXT NOT NULL,
  title                TEXT NOT NULL DEFAULT '',
  text_hash            TEXT NOT NULL,
  body_start           INTEGER,
  body_end             INTEGER,
  raw_bytes            INTEGER NOT NULL DEFAULT 0,
  artifact_uri         TEXT NOT NULL,
  source_policy_id     TEXT NOT NULL DEFAULT '',
  raw_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_harvested_pages_run_url UNIQUE (run_id, url_hash, text_hash)
);

CREATE INDEX IF NOT EXISTS idx_harvested_pages_run_source
  ON sanguo_rag.harvested_pages (run_id, source_id);

CREATE INDEX IF NOT EXISTS idx_harvested_pages_text_hash
  ON sanguo_rag.harvested_pages (text_hash);

CREATE INDEX IF NOT EXISTS idx_harvested_pages_source_policy
  ON sanguo_rag.harvested_pages (source_policy_id);

-- =========================================================================
-- evidence_seeds : seedId / generalId / angle / scores / anchor / payload
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.evidence_seeds (
  seed_id              TEXT PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE CASCADE,
  source_id            TEXT NOT NULL,
  general_id           TEXT NOT NULL DEFAULT '',
  angle_type           TEXT NOT NULL DEFAULT '',
  seed_text_hash       TEXT NOT NULL,
  score                JSONB NOT NULL DEFAULT '{}'::jsonb,
  anchor               JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload_uri          TEXT NOT NULL,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_seeds_run_source
  ON sanguo_rag.evidence_seeds (run_id, source_id);

CREATE INDEX IF NOT EXISTS idx_evidence_seeds_general_angle
  ON sanguo_rag.evidence_seeds (general_id, angle_type);

CREATE INDEX IF NOT EXISTS idx_evidence_seeds_text_hash
  ON sanguo_rag.evidence_seeds (seed_text_hash);

-- =========================================================================
-- evidence_cards : evidenceId / sourceFamily / quoteHash / locator / trust
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.evidence_cards (
  evidence_id          TEXT PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE CASCADE,
  source_id            TEXT NOT NULL,
  source_family        TEXT NOT NULL DEFAULT '',
  source_layer         TEXT NOT NULL DEFAULT '',
  general_ids          TEXT[] NOT NULL DEFAULT '{}',
  quote_hash           TEXT NOT NULL,
  locator              TEXT NOT NULL DEFAULT '',
  anchor_evidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
  trust_score          JSONB NOT NULL DEFAULT '{}'::jsonb,
  review_status        TEXT NOT NULL DEFAULT 'candidate'
                       CHECK (review_status IN ('candidate', 'accepted', 'rejected', 'staged-a', 'staged-b')),
  payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload_uri          TEXT NOT NULL,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_cards_run_source
  ON sanguo_rag.evidence_cards (run_id, source_id);

CREATE INDEX IF NOT EXISTS idx_evidence_cards_family_layer_status
  ON sanguo_rag.evidence_cards (source_family, source_layer, review_status);

CREATE INDEX IF NOT EXISTS idx_evidence_cards_quote_hash
  ON sanguo_rag.evidence_cards (quote_hash);

CREATE INDEX IF NOT EXISTS idx_evidence_cards_general_ids_gin
  ON sanguo_rag.evidence_cards USING GIN (general_ids);

-- =========================================================================
-- anchor_passages : corpusId / layer / locator / textHash / normalizedText
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.anchor_passages (
  passage_id           TEXT PRIMARY KEY,
  run_id               TEXT REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE SET NULL,
  corpus_id            TEXT NOT NULL,
  layer                TEXT NOT NULL,
  locator              TEXT NOT NULL,
  text_hash            TEXT NOT NULL,
  normalized_text      TEXT NOT NULL,
  artifact_uri         TEXT NOT NULL,
  raw_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_anchor_passages_corpus_locator_hash UNIQUE (corpus_id, layer, locator, text_hash)
);

CREATE INDEX IF NOT EXISTS idx_anchor_passages_corpus_layer
  ON sanguo_rag.anchor_passages (corpus_id, layer);

CREATE INDEX IF NOT EXISTS idx_anchor_passages_text_hash
  ON sanguo_rag.anchor_passages (text_hash);

-- =========================================================================
-- proposal_ledger : alias / noise / source-ref / source-status / body-boundary
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.proposal_ledger (
  proposal_id          TEXT PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE CASCADE,
  proposal_kind        TEXT NOT NULL
                       CHECK (proposal_kind IN ('alias', 'noise', 'source-ref', 'source-status', 'body-boundary-residual')),
  source_id            TEXT NOT NULL DEFAULT '',
  signature            TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'proposed'
                       CHECK (status IN ('proposed', 'sandbox-pass', 'sandbox-fail', 'accepted', 'rejected', 'expired')),
  sandbox_outcome      JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
  artifact_uri         TEXT NOT NULL,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  decided_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_proposal_ledger_kind_status
  ON sanguo_rag.proposal_ledger (proposal_kind, status);

CREATE INDEX IF NOT EXISTS idx_proposal_ledger_signature
  ON sanguo_rag.proposal_ledger (signature);

CREATE INDEX IF NOT EXISTS idx_proposal_ledger_run_source
  ON sanguo_rag.proposal_ledger (run_id, source_id);

-- =========================================================================
-- vector_ingestion_records : provider / namespace / record sha / manifests
-- =========================================================================
CREATE TABLE IF NOT EXISTS sanguo_rag.vector_ingestion_records (
  ingestion_id         BIGSERIAL PRIMARY KEY,
  run_id               TEXT REFERENCES sanguo_rag.pipeline_runs(run_id) ON DELETE SET NULL,
  provider             TEXT NOT NULL,
  namespace            TEXT NOT NULL,
  record_id            TEXT NOT NULL,
  record_sha256        TEXT NOT NULL,
  source_table         TEXT NOT NULL
                       CHECK (source_table IN (
                         'evidence_cards',
                         'anchor_passages',
                         'evidence_seeds',
                         'events',
                         'persona_cards',
                         'keyword_options'
                       )),
  upsert_manifest_uri  TEXT NOT NULL,
  probe_manifest_uri   TEXT NOT NULL DEFAULT '',
  rollback_manifest_uri TEXT NOT NULL DEFAULT '',
  status               TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'upserted', 'probed', 'rolled-back', 'failed')),
  payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
  inserted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_vector_ingestion_provider_namespace_record UNIQUE (provider, namespace, record_id, record_sha256)
);

CREATE INDEX IF NOT EXISTS idx_vector_ingestion_run_status
  ON sanguo_rag.vector_ingestion_records (run_id, status);

CREATE INDEX IF NOT EXISTS idx_vector_ingestion_namespace
  ON sanguo_rag.vector_ingestion_records (provider, namespace);

CREATE INDEX IF NOT EXISTS idx_vector_ingestion_source_table
  ON sanguo_rag.vector_ingestion_records (source_table);

-- =========================================================================
-- evidence_lake_run_summary view : aggregate seed/card counts per run+source
-- =========================================================================
CREATE OR REPLACE VIEW sanguo_rag.evidence_lake_run_summary AS
SELECT
  r.run_id,
  r.lane,
  r.status,
  r.canonical_writes,
  COALESCE(SUM(s.harvested_count), 0)::INTEGER AS harvested_count,
  COALESCE(SUM(s.seed_count), 0)::INTEGER      AS seed_count,
  COALESCE(SUM(s.card_count), 0)::INTEGER      AS card_count,
  COUNT(DISTINCT s.source_id)::INTEGER         AS source_count
FROM sanguo_rag.pipeline_runs AS r
LEFT JOIN sanguo_rag.source_runs AS s
  ON s.run_id = r.run_id
GROUP BY r.run_id, r.lane, r.status, r.canonical_writes;
