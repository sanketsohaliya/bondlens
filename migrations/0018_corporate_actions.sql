CREATE TABLE corporate_action_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok BOOLEAN,
    spec JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX corporate_action_runs_source_mode_time_idx
    ON corporate_action_runs (source, mode, run_at DESC);
CREATE INDEX corporate_action_runs_running_idx
    ON corporate_action_runs (source, run_at) WHERE finished_at IS NULL;
CREATE INDEX corporate_action_runs_history_configuration_idx
    ON corporate_action_runs (source, run_at DESC, id DESC)
    WHERE spec @> '{"configure_history": true}'::jsonb;

CREATE TABLE corporate_action_fetches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES corporate_action_runs (id),
    source TEXT NOT NULL,
    window_from DATE NOT NULL,
    window_to DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    index_status TEXT NOT NULL DEFAULT 'pending',
    indexed_at TIMESTAMPTZ,
    coverage_applied_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    index_error TEXT,
    spec JSONB NOT NULL DEFAULT '{}'::jsonb,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest JSONB NOT NULL DEFAULT '[]'::jsonb,
    accepted_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX corporate_action_fetches_source_time_idx
    ON corporate_action_fetches (source, started_at DESC);
CREATE INDEX corporate_action_fetches_run_idx ON corporate_action_fetches (run_id);
CREATE INDEX corporate_action_fetches_pending_idx
    ON corporate_action_fetches (source, run_id, id)
    WHERE status IN ('success', 'empty') AND index_status <> 'indexed';
CREATE INDEX corporate_action_fetches_status_idx
    ON corporate_action_fetches (source, status, index_status);
CREATE INDEX corporate_action_fetches_request_idx
    ON corporate_action_fetches (source, (spec ->> 'request_key'), started_at DESC, id DESC);
CREATE INDEX corporate_action_fetches_coverage_idx
    ON corporate_action_fetches (source, id)
    WHERE status IN ('success', 'empty') AND index_status = 'indexed' AND coverage_applied_at IS NULL;
CREATE INDEX corporate_action_fetches_unresolved_idx
    ON corporate_action_fetches (source, (spec ->> 'kind'), (spec ->> 'request_key'))
    WHERE resolved_at IS NULL AND status IN ('pending', 'running', 'failed', 'invalid', 'incomplete', 'split');

CREATE TABLE corporate_action_raw (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    record_scope TEXT NOT NULL DEFAULT '',
    row_sha256 TEXT NOT NULL,
    content BYTEA NOT NULL,
    size_bytes BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, record_scope, row_sha256)
);

CREATE TABLE corporate_actions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_id BIGINT NOT NULL UNIQUE REFERENCES corporate_action_raw (id),
    source TEXT NOT NULL,
    source_record_id TEXT,
    scrip_code TEXT,
    issuer_name TEXT,
    source_issuer_id TEXT,
    category_raw TEXT,
    headline TEXT,
    announced_at TIMESTAMPTZ,
    attachment_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url TEXT,
    raw JSONB NOT NULL,
    extractor_version TEXT NOT NULL DEFAULT '',
    metadata_status TEXT NOT NULL DEFAULT 'ok',
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    first_fetch_id BIGINT NOT NULL REFERENCES corporate_action_fetches (id),
    last_fetch_id BIGINT NOT NULL REFERENCES corporate_action_fetches (id),
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX corporate_actions_source_record_idx
    ON corporate_actions (source, source_record_id);
CREATE INDEX corporate_actions_source_time_idx
    ON corporate_actions (source, announced_at DESC);
CREATE INDEX corporate_actions_scrip_idx ON corporate_actions (source, scrip_code);

CREATE TABLE corporate_action_isins (
    action_id BIGINT NOT NULL REFERENCES corporate_actions (id),
    isin TEXT NOT NULL,
    method TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    extractor_version TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (action_id, isin, method)
);

CREATE INDEX corporate_action_isins_isin_idx ON corporate_action_isins (isin, action_id);

CREATE TABLE corporate_action_targets (
    source TEXT NOT NULL,
    isin TEXT NOT NULL,
    mapping_hash TEXT NOT NULL DEFAULT '',
    mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_progress_at TIMESTAMPTZ,
    history_from DATE,
    history_to DATE,
    pending JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_pending JSONB NOT NULL DEFAULT '[]'::jsonb,
    state TEXT NOT NULL DEFAULT 'pending',
    blocked_reason TEXT,
    generation INTEGER NOT NULL DEFAULT 1,
    last_active_date DATE,
    PRIMARY KEY (source, isin)
);

CREATE INDEX corporate_action_targets_state_idx
    ON corporate_action_targets (source, state, active);
CREATE INDEX corporate_action_targets_progress_idx
    ON corporate_action_targets (source, last_progress_at);
