-- AURA Database Schema
-- ======================
-- WHY PostgreSQL:
--   Time-series density data fits well in append-only tables.
--   BRIN indexes are optimal for timestamp-ordered inserts (10x smaller than B-tree).

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==============================
-- Table 1: density_logs
-- ==============================
-- Stores every density reading from every section.
-- Partitioned by week in production (omitted here for clarity).
CREATE TABLE IF NOT EXISTS density_logs (
    id              BIGSERIAL PRIMARY KEY,
    section_id      VARCHAR(8)  NOT NULL,
    density_score   FLOAT4      NOT NULL CHECK (density_score BETWEEN 0 AND 1),
    raw_density     FLOAT4      NOT NULL,
    person_count    INT         NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    threshold_breached BOOLEAN  NOT NULL DEFAULT FALSE
);

-- BRIN index: efficient for naturally ordered time data
CREATE INDEX IF NOT EXISTS idx_density_logs_ts
    ON density_logs USING BRIN (timestamp);
CREATE INDEX IF NOT EXISTS idx_density_logs_section
    ON density_logs (section_id);


-- ==============================
-- Table 2: nudge_logs
-- ==============================
-- Records every triggered nudge and its parameters.
-- Used to correlate nudge type with crowd response (RL reward computation).
CREATE TABLE IF NOT EXISTS nudge_logs (
    id              BIGSERIAL PRIMARY KEY,
    action_id       VARCHAR(64)  NOT NULL UNIQUE,
    section_from    VARCHAR(8)   NOT NULL,
    section_to      VARCHAR(8)   NOT NULL,
    nudge_type      VARCHAR(32)  NOT NULL,   -- 'discount' | 'notification' | 'led_only'
    value           TEXT         NOT NULL,   -- human-readable incentive description
    reason          TEXT         NOT NULL,   -- explainability: WHY this nudge fired
    rl_confidence   FLOAT4       NOT NULL,
    timestamp       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Feedback columns (filled by RL reward loop)
    reward_score    FLOAT4,                  -- null until feedback received
    outcome_density FLOAT4                   -- post-nudge density in section_from
);

CREATE INDEX IF NOT EXISTS idx_nudge_logs_section_from
    ON nudge_logs (section_from);
CREATE INDEX IF NOT EXISTS idx_nudge_logs_ts
    ON nudge_logs USING BRIN (timestamp);


-- ==============================
-- Table 3: path_decisions
-- ==============================
-- Records every reroute path computed by A*.
-- Useful for auditing and improving graph weights.
CREATE TABLE IF NOT EXISTS path_decisions (
    id              BIGSERIAL PRIMARY KEY,
    path_json       JSONB        NOT NULL,   -- ["C", "F", "D"]
    total_cost      FLOAT4       NOT NULL,
    reasoning       TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_path_decisions_ts
    ON path_decisions USING BRIN (created_at);


-- ==============================
-- View: congestion_summary
-- ==============================
-- Aggregated view for dashboard analytics.
CREATE OR REPLACE VIEW congestion_summary AS
SELECT
    section_id,
    AVG(density_score)           AS avg_density,
    MAX(density_score)           AS peak_density,
    COUNT(*)                     AS reading_count,
    SUM(threshold_breached::int) AS breach_count,
    MAX(timestamp)               AS last_seen
FROM density_logs
WHERE timestamp > NOW() - INTERVAL '1 hour'
GROUP BY section_id
ORDER BY avg_density DESC;
