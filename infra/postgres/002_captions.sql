-- 002_captions.sql — Add caption + retrieval_text columns to core.image_items
-- Idempotent: safe to run multiple times against an existing PG18 volume.
-- Paired with 001_schema.sql, which already declares these columns for fresh installs.

ALTER TABLE core.image_items
    ADD COLUMN IF NOT EXISTS caption_literal    TEXT,
    ADD COLUMN IF NOT EXISTS caption_figurative TEXT,
    ADD COLUMN IF NOT EXISTS template_name      TEXT,
    ADD COLUMN IF NOT EXISTS tags               TEXT[],
    ADD COLUMN IF NOT EXISTS retrieval_text     TEXT;

-- Sparse GIN over template_name / tags for cheap filter lookups.
CREATE INDEX IF NOT EXISTS image_items_template_idx
    ON core.image_items (template_name)
    WHERE template_name IS NOT NULL AND template_name <> 'unknown';

CREATE INDEX IF NOT EXISTS image_items_tags_idx
    ON core.image_items USING gin (tags);

-- Trigram over retrieval_text for debug lookups; the serious retrieval is via BGE-M3 in Qdrant,
-- but having a DB-side fuzzy text match is handy for ad-hoc verification.
CREATE INDEX IF NOT EXISTS image_items_retrieval_trgm
    ON core.image_items USING gin (retrieval_text gin_trgm_ops);
