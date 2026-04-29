CREATE SCHEMA IF NOT EXISTS feedback;

CREATE TABLE IF NOT EXISTS feedback.ranker_versions (
    ranker_version_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('baseline','shadow','candidate','online','disabled')),
    feature_version INT NOT NULL DEFAULT 1,
    artifact_uri TEXT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at TIMESTAMPTZ
);

INSERT INTO feedback.ranker_versions (ranker_version_id, kind, status, feature_version)
VALUES ('baseline', 'phase0_order', 'baseline', 1)
ON CONFLICT (ranker_version_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS feedback.search_sessions (
    search_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    query_redacted TEXT,
    intent TEXT NOT NULL,
    client_session_id TEXT NOT NULL,
    user_hash TEXT,
    result_count INT NOT NULL DEFAULT 0,
    ranker_version_id TEXT NOT NULL REFERENCES feedback.ranker_versions(ranker_version_id),
    ranker_mode TEXT NOT NULL DEFAULT 'baseline' CHECK (ranker_mode IN ('baseline','shadow','online','exploration')),
    feature_version INT NOT NULL DEFAULT 1,
    propensity_method TEXT NOT NULL DEFAULT 'deterministic_no_ope',
    exploration_policy TEXT NOT NULL DEFAULT 'none',
    target_id TEXT,
    consent_scope TEXT NOT NULL DEFAULT 'feedback_only',
    opt_out BOOLEAN NOT NULL DEFAULT false,
    deleted_at TIMESTAMPTZ,
    served_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE IF EXISTS feedback.search_sessions
    ADD COLUMN IF NOT EXISTS exploration_policy TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS target_id TEXT,
    ADD COLUMN IF NOT EXISTS consent_scope TEXT NOT NULL DEFAULT 'feedback_only',
    ADD COLUMN IF NOT EXISTS opt_out BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS feedback.search_impressions (
    impression_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    rank INT NOT NULL CHECK (rank >= 1),
    base_rank INT NOT NULL CHECK (base_rank >= 1),
    retrieval_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    rerank_score DOUBLE PRECISION,
    learned_score DOUBLE PRECISION,
    propensity DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    propensity_method TEXT NOT NULL DEFAULT 'deterministic_no_ope',
    is_exploration BOOLEAN NOT NULL DEFAULT false,
    features_jsonb JSONB NOT NULL,
    served_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (search_id, image_id),
    UNIQUE (search_id, rank)
);

ALTER TABLE IF EXISTS feedback.search_impressions
    ADD COLUMN IF NOT EXISTS is_exploration BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS feedback.judgments (
    judgment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    impression_id UUID REFERENCES feedback.search_impressions(impression_id) ON DELETE CASCADE,
    image_id TEXT REFERENCES core.images(image_id) ON DELETE SET NULL,
    action TEXT NOT NULL CHECK (action IN ('select','reject','none_correct','undo')),
    value DOUBLE PRECISION,
    client_session_id TEXT NOT NULL,
    user_hash TEXT,
    token_nonce TEXT NOT NULL,
    token_version INT NOT NULL DEFAULT 1,
    ranker_version_id TEXT NOT NULL REFERENCES feedback.ranker_versions(ranker_version_id),
    feature_version INT NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tombstoned_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS judgments_idempotency_idx
    ON feedback.judgments (
        search_id,
        COALESCE(impression_id, '00000000-0000-0000-0000-000000000000'::uuid),
        action
    )
    WHERE tombstoned_at IS NULL AND action <> 'undo';

CREATE TABLE IF NOT EXISTS feedback.preference_pairs (
    pair_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    source_judgment_id UUID NOT NULL REFERENCES feedback.judgments(judgment_id) ON DELETE CASCADE,
    winner_impression_id UUID NOT NULL REFERENCES feedback.search_impressions(impression_id) ON DELETE CASCADE,
    loser_impression_id UUID NOT NULL REFERENCES feedback.search_impressions(impression_id) ON DELETE CASCADE,
    winner_image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    loser_image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    feature_version INT NOT NULL DEFAULT 1,
    derivation_method TEXT NOT NULL DEFAULT 'selected_vs_skipped',
    pair_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tombstoned_at TIMESTAMPTZ,
    CHECK (winner_impression_id <> loser_impression_id)
);

ALTER TABLE IF EXISTS feedback.preference_pairs
    ADD COLUMN IF NOT EXISTS derivation_method TEXT NOT NULL DEFAULT 'selected_vs_skipped',
    ADD COLUMN IF NOT EXISTS pair_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0;

CREATE UNIQUE INDEX IF NOT EXISTS preference_pairs_unique_active_idx
    ON feedback.preference_pairs (search_id, winner_impression_id, loser_impression_id)
    WHERE tombstoned_at IS NULL;

CREATE TABLE IF NOT EXISTS feedback.training_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ranker_version_id TEXT REFERENCES feedback.ranker_versions(ranker_version_id),
    feature_version INT NOT NULL,
    pair_count INT NOT NULL DEFAULT 0,
    judgment_count INT NOT NULL DEFAULT 0,
    query_count INT NOT NULL DEFAULT 0,
    source_started_at TIMESTAMPTZ,
    source_ended_at TIMESTAMPTZ,
    export_sha256 TEXT,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE IF EXISTS feedback.training_snapshots
    ADD COLUMN IF NOT EXISTS source_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source_ended_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS export_sha256 TEXT;

CREATE TABLE IF NOT EXISTS feedback.redaction_events (
    redaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES feedback.search_sessions(search_id) ON DELETE SET NULL,
    judgment_id UUID REFERENCES feedback.judgments(judgment_id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS feedback.invalid_token_attempts (
    attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_session_id TEXT,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS feedback.rate_limit_events (
    rate_limit_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_session_id TEXT,
    user_hash TEXT,
    action TEXT NOT NULL,
    bucket TEXT NOT NULL,
    allowed BOOLEAN NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS search_sessions_client_served_idx
    ON feedback.search_sessions (client_session_id, served_at DESC);
CREATE INDEX IF NOT EXISTS search_sessions_target_idx
    ON feedback.search_sessions (target_id)
    WHERE target_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS search_impressions_search_idx
    ON feedback.search_impressions (search_id);
CREATE INDEX IF NOT EXISTS search_impressions_image_idx
    ON feedback.search_impressions (image_id);
CREATE INDEX IF NOT EXISTS search_impressions_features_gin
    ON feedback.search_impressions USING gin (features_jsonb);
CREATE INDEX IF NOT EXISTS judgments_search_idx
    ON feedback.judgments (search_id);
CREATE INDEX IF NOT EXISTS judgments_image_idx
    ON feedback.judgments (image_id);
CREATE INDEX IF NOT EXISTS judgments_session_created_idx
    ON feedback.judgments (client_session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS judgments_user_created_idx
    ON feedback.judgments (user_hash, created_at DESC)
    WHERE user_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS preference_pairs_search_idx
    ON feedback.preference_pairs (search_id);
CREATE INDEX IF NOT EXISTS invalid_token_attempts_session_idx
    ON feedback.invalid_token_attempts (client_session_id, created_at DESC)
    WHERE client_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS rate_limit_events_session_idx
    ON feedback.rate_limit_events (client_session_id, created_at DESC)
    WHERE client_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS rate_limit_events_user_idx
    ON feedback.rate_limit_events (user_hash, created_at DESC)
    WHERE user_hash IS NOT NULL;
