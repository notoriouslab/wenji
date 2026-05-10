-- wenji v0.2 single-file schema (schema_version = "2")
-- See design.md D3 (v0.1.0 baseline) + wenji-limitations-v0-2 design.md L5.
-- Tokenizer: unicode61 + jieba pre-tokenization at ingest layer
-- (libsimple extension considered for v0.2+ char-unigram fallback path).
--
-- v0.2 changes vs v0.1.0:
--   - articles_meta: + path TEXT UNIQUE NOT NULL (article identity key)
--   - articles_meta: + source_urls_json TEXT NOT NULL DEFAULT '' (plural source URLs)
--   - schema_version bumped 1 → 2; v0.1.0 DBs MUST rebuild from disk.

-- ============================================================
-- 1. wenji_meta: key/value
-- ============================================================
-- Live keys (read by code):
--   * schema_version — verified at connect time (core/db.py)
--   * embedder       — init-only constant (no update path; informational)
--
-- DEPRECATED keys (specced in v0.1.0 but never maintained by any code path
-- since; ingest never writes, observability never reads). Kept for v2
-- schema compatibility. The `cleanup-build-telemetry` followup change will
-- decide whether to drop these columns (schema_version bump) or wire up
-- maintenance. DO NOT add new readers that assume these are alive.
--   * build_started_at, build_completed_at  — DEAD: build telemetry stub
--   * n_articles, n_chunks, n_doc_vectors   — DEAD: row-count counters
-- ============================================================
CREATE TABLE IF NOT EXISTS wenji_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO wenji_meta (key, value) VALUES
    ('schema_version', '2'),
    ('embedder', 'BGE-M3-INT8-ONNX'),
    -- DEPRECATED below: see header note. Will be revisited by
    -- `cleanup-build-telemetry` change; do not add readers.
    ('build_started_at', ''),
    ('build_completed_at', ''),
    ('n_articles', '0'),
    ('n_chunks', '0'),
    ('n_doc_vectors', '0');

-- ============================================================
-- 2. articles_meta: 一筆一篇 metadata
-- ============================================================
CREATE TABLE IF NOT EXISTS articles_meta (
    article_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT,
    source_type TEXT,
    pub_date TEXT,
    pub_year INTEGER,
    content_length INTEGER,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    indexed_at TEXT,
    category TEXT,
    author TEXT,
    source_url TEXT,
    source_urls_json TEXT NOT NULL DEFAULT '',
    subtype TEXT,
    tags TEXT,
    description TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_meta_source_type ON articles_meta(source_type);
CREATE INDEX IF NOT EXISTS idx_articles_meta_pub_year   ON articles_meta(pub_year);
CREATE INDEX IF NOT EXISTS idx_articles_meta_category   ON articles_meta(category);

-- ============================================================
-- 3. articles_fts: article-level FTS5
--   *      = jieba pre-tokenized text (space-joined; phrase search)
--   *_raw  = original text (display + substring metric)
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    article_id    UNINDEXED,
    title,
    title_raw     UNINDEXED,
    content,
    content_raw   UNINDEXED,
    tags,
    tags_raw      UNINDEXED,
    category      UNINDEXED,
    source_type   UNINDEXED,
    pub_date      UNINDEXED,
    pub_year      UNINDEXED,
    tokenize = 'unicode61'
);

-- ============================================================
-- 4. chunks_fts: chunk-level FTS5 (same shape, chunk_text instead of content)
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id        UNINDEXED,
    article_id      UNINDEXED,
    chunk_index     UNINDEXED,
    title,
    title_raw       UNINDEXED,
    chunk_text,
    chunk_text_raw  UNINDEXED,
    tags,
    tags_raw        UNINDEXED,
    source_type     UNINDEXED,
    pub_year        UNINDEXED,
    tokenize = 'unicode61'
);

-- ============================================================
-- 5. doc_vectors: BGE-M3 1024-dim float32 BLOB (L2-normalised)
-- ============================================================
CREATE TABLE IF NOT EXISTS doc_vectors (
    article_id TEXT PRIMARY KEY,
    vec BLOB NOT NULL  -- 1024 * float32 = 4096 bytes
);

-- ============================================================
-- 6. article_axes: M:N classification (one primary per article)
-- ============================================================
CREATE TABLE IF NOT EXISTS article_axes (
    article_id TEXT NOT NULL,
    axis_id    TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (article_id, axis_id),
    FOREIGN KEY (article_id) REFERENCES articles_meta(article_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_article_axes_primary
    ON article_axes(article_id) WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS idx_article_axes_axis ON article_axes(axis_id);

-- ============================================================
-- 7. query_rewrite_cache: LLM rewrite cache (TTL handled at app layer, default 30 days)
-- ============================================================
CREATE TABLE IF NOT EXISTS query_rewrite_cache (
    raw        TEXT PRIMARY KEY,
    rewritten  TEXT NOT NULL,
    created_at TEXT NOT NULL  -- ISO 8601 timestamp
);
CREATE INDEX IF NOT EXISTS idx_query_rewrite_created ON query_rewrite_cache(created_at);

-- ============================================================
-- 8. aggregate_cache: Aggregator result cache (TTL handled at app layer, default 30 days)
--   Key: sha256(function_name + ":" + canonical_args_json)
--   Value: JSON-serialised TopicSummary / ConceptPerspectives
--   Schema_version unchanged (CREATE IF NOT EXISTS handles backward-compat upgrade).
-- ============================================================
CREATE TABLE IF NOT EXISTS aggregate_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL  -- ISO 8601 UTC timestamp
);
CREATE INDEX IF NOT EXISTS idx_aggregate_cache_created ON aggregate_cache(created_at);
