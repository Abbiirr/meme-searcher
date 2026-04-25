CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS eval;
CREATE SCHEMA IF NOT EXISTS feedback;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE core.images (
    image_id    TEXT PRIMARY KEY,
    sha256      BYTEA NOT NULL UNIQUE,
    source_uri  TEXT NOT NULL,
    width       INT NOT NULL,
    height      INT NOT NULL,
    format      TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX images_sha256_idx ON core.images (sha256);

CREATE TABLE core.image_items (
    image_id           TEXT PRIMARY KEY REFERENCES core.images(image_id) ON DELETE CASCADE,
    thumbnail_uri      TEXT,
    ocr_text           TEXT,
    ocr_full_text      TEXT,
    ocr_boxes          JSONB,
    caption_text       TEXT,          -- legacy single-caption field; kept for compat
    caption_model      TEXT,
    caption_literal    TEXT,          -- added 2026-04-20 per PHASE_0_RETRIEVAL_PLAN §2.3
    caption_figurative TEXT,
    template_name      TEXT,
    tags               TEXT[],
    retrieval_text     TEXT,          -- concatenated blob that feeds BGE-M3
    has_ocr            BOOL NOT NULL DEFAULT FALSE,
    has_caption        BOOL NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX image_items_image_id_idx ON core.image_items (image_id);
CREATE INDEX image_items_ocr_trgm ON core.image_items USING gin (ocr_full_text gin_trgm_ops);
CREATE INDEX image_items_template_idx
    ON core.image_items (template_name)
    WHERE template_name IS NOT NULL AND template_name <> 'unknown';
CREATE INDEX image_items_tags_idx
    ON core.image_items USING gin (tags);
CREATE INDEX image_items_retrieval_trgm
    ON core.image_items USING gin (retrieval_text gin_trgm_ops);

CREATE TABLE ops.jobs (
    job_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_path  TEXT,
    state        TEXT NOT NULL CHECK (state IN ('pending','running','done','failed','cancelled')),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    error        TEXT,
    summary      JSONB
);
CREATE INDEX jobs_state_idx ON ops.jobs (state);

CREATE TABLE ops.ingest_steps (
    image_id TEXT NOT NULL,
    step     TEXT NOT NULL,
    state    TEXT NOT NULL CHECK (state IN ('pending','running','done','skipped','error')),
    attempts INT NOT NULL DEFAULT 0,
    meta     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (image_id, step)
);
CREATE INDEX ingest_steps_state_idx ON ops.ingest_steps (state);

CREATE TABLE ops.model_versions (
    model_key    TEXT PRIMARY KEY,
    family       TEXT NOT NULL,
    version      TEXT NOT NULL,
    revision     TEXT,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    config       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE eval.queries (
    query_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text       TEXT NOT NULL,
    intent     TEXT NOT NULL CHECK (intent IN ('exact_text','fuzzy_text','semantic_description','mixed_visual_description')),
    target_image_id TEXT,
    notes      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eval.qrels (
    query_id   UUID NOT NULL REFERENCES eval.queries(query_id),
    image_id   TEXT NOT NULL REFERENCES core.images(image_id),
    grade      SMALLINT NOT NULL CHECK (grade BETWEEN 0 AND 3),
    judge      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (query_id, image_id, judge)
);

CREATE TABLE eval.runs (
    run_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_hash TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    notes       TEXT
);

CREATE TABLE eval.run_results (
    run_id   UUID NOT NULL REFERENCES eval.runs(run_id) ON DELETE CASCADE,
    query_id UUID NOT NULL REFERENCES eval.queries(query_id),
    image_id TEXT NOT NULL,
    rank     INT NOT NULL,
    score    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, query_id, rank)
);

CREATE TABLE eval.metrics (
    run_id UUID NOT NULL REFERENCES eval.runs(run_id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    value  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, metric)
);

CREATE TABLE feedback.events (
    event_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    image_id   TEXT REFERENCES core.images(image_id),
    signal     TEXT NOT NULL CHECK (signal IN ('thumbs_up','thumbs_down','clicked','dwell','reported_wrong')),
    value      DOUBLE PRECISION,
    user_token TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX feedback_image_idx ON feedback.events (image_id);
CREATE INDEX feedback_query_idx ON feedback.events USING gin (query_text gin_trgm_ops);

INSERT INTO ops.model_versions (model_key, family, version, config) VALUES
    ('ocr_det', 'paddleocr', 'PP-OCRv5_server_det', '{}'),
    ('ocr_rec', 'paddleocr', 'PP-OCRv5_server_rec', '{}'),
    ('text_dense', 'bge-m3', 'BAAI/bge-m3', '{}'),
    ('text_sparse', 'bge-m3', 'BAAI/bge-m3', '{}'),
    ('visual', 'siglip2', 'google/siglip2-so400m-patch16-384', '{}'),
    ('reranker', 'jina-reranker', 'jinaai/jina-reranker-v2-base-multilingual', '{}')
ON CONFLICT (model_key) DO NOTHING;
