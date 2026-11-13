-- 0001_init.sql  (design.md section 3)
-- Applied by store/db.py's migration runner. Idempotent at the runner level:
-- a version already recorded in schema_migrations is never re-applied.

CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL
);

-- One row per repository identity.
CREATE TABLE repos (
  id              INTEGER PRIMARY KEY,
  canonical_url   TEXT NOT NULL UNIQUE,   -- 'github.com/owner/name', lowercased
  owner           TEXT NOT NULL,          -- display case preserved
  name            TEXT NOT NULL,
  html_url        TEXT NOT NULL,
  first_seen_at   TEXT NOT NULL
);

-- One row per (repo, commit SHA, schema version). THE cache.
CREATE TABLE analyses (
  id                       INTEGER PRIMARY KEY,
  repo_id                  INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  cache_key                TEXT NOT NULL UNIQUE,   -- sha256(url@sha@schema_version)
  canonical_url            TEXT NOT NULL,
  last_commit_sha          TEXT NOT NULL,
  last_commit_at           TEXT NOT NULL,
  as_of                    TEXT NOT NULL,   -- frozen fetch timestamp (UTC, ISO 8601)
  analysis_schema_version  INTEGER NOT NULL,
  scorecard_version        INTEGER NOT NULL,
  prompt_template_version  INTEGER,
  raw_github_json          TEXT NOT NULL,   -- all raw payloads, keyed by endpoint
  normalized_json          TEXT NOT NULL,   -- NormalizedFacts
  scorecard_json           TEXT NOT NULL,   -- full factor breakdown
  narrative_json           TEXT,            -- LlmNarrative (null if llm failed)
  score                    INTEGER NOT NULL,
  band                     TEXT NOT NULL,
  llm_status               TEXT NOT NULL,   -- ok | failed | skipped | fallback
  llm_model                TEXT,
  github_calls_used        INTEGER NOT NULL,
  duration_ms              INTEGER NOT NULL,
  created_at               TEXT NOT NULL
);
CREATE INDEX idx_analyses_url_created ON analyses(canonical_url, created_at DESC);
CREATE INDEX idx_analyses_score       ON analyses(score DESC);

-- Presentation record. One current exhibit per repo.
CREATE TABLE exhibits (
  id            INTEGER PRIMARY KEY,
  repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  analysis_id   INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  slug          TEXT NOT NULL UNIQUE,       -- 'owner--name'
  plaque_title  TEXT NOT NULL,
  is_demo       INTEGER NOT NULL DEFAULT 0,
  curated       INTEGER NOT NULL DEFAULT 0,
  curator_note  TEXT,
  display_order INTEGER NOT NULL DEFAULT 1000,
  updated_at    TEXT NOT NULL,
  UNIQUE(repo_id)
);
CREATE INDEX idx_exhibits_order ON exhibits(is_demo DESC, display_order ASC);

-- Curated demo set, mirrors graveyard/manifest.json for integrity checks.
CREATE TABLE manifest_entries (
  slug              TEXT PRIMARY KEY,
  canonical_url     TEXT NOT NULL,
  pinned_commit_sha TEXT,
  curator_note      TEXT,
  display_order     INTEGER NOT NULL,
  expected_band     TEXT,         -- curation intent; drift warns, never fails a request
  loaded_at         TEXT
);

-- Rate-limit observability (R-14.2, R-14.8).
CREATE TABLE rate_limit_ledger (
  id          INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  status      INTEGER NOT NULL,
  rl_limit    INTEGER,
  rl_remaining INTEGER,
  rl_used     INTEGER,
  rl_reset_at TEXT,
  token_present INTEGER NOT NULL
);
CREATE INDEX idx_ledger_observed ON rate_limit_ledger(observed_at DESC);

-- Conditional-request cache (R-14.7).
CREATE TABLE etags (
  endpoint_key TEXT PRIMARY KEY,   -- method + path + query
  etag         TEXT NOT NULL,
  body_json    TEXT NOT NULL,
  stored_at    TEXT NOT NULL
);

-- LLM accounting (R-15.4).
CREATE TABLE llm_calls (
  id                      INTEGER PRIMARY KEY,
  analysis_id             INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
  cache_key               TEXT NOT NULL,
  model                   TEXT NOT NULL,
  prompt_template_version INTEGER NOT NULL,
  prompt_hash             TEXT NOT NULL,
  tokens_in               INTEGER,
  tokens_out              INTEGER,
  latency_ms              INTEGER,
  outcome                 TEXT NOT NULL,   -- ok | invalid_json | schema_fail | ungrounded | error
  attempt                 INTEGER NOT NULL,
  created_at              TEXT NOT NULL
);
CREATE INDEX idx_llm_calls_cache_key ON llm_calls(cache_key);

CREATE TABLE jobs (
  job_id             TEXT PRIMARY KEY,
  canonical_url      TEXT NOT NULL,
  cache_key          TEXT,
  state              TEXT NOT NULL,  -- queued | running | done | failed
  result_analysis_id INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
  error_code         TEXT,
  error_message      TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE INDEX idx_jobs_created ON jobs(created_at DESC);
