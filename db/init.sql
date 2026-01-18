-- ============================================
-- LangGraph Orchestrator Database Schema
-- ============================================

-- Extension für UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. Conversation History
-- (Kompatibel mit N8N n8n_chat_histories Format)
-- ============================================
CREATE TABLE IF NOT EXISTS conversation_history (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,  -- User ID
    message JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_conv_history_session ON conversation_history(session_id);
CREATE INDEX idx_conv_history_created ON conversation_history(created_at DESC);

-- ============================================
-- 2. Orchestrator Runs
-- Jeder Request = ein Run
-- ============================================
CREATE TABLE IF NOT EXISTS orchestrator_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    channel_id VARCHAR(255),
    user_message TEXT NOT NULL,
    final_response TEXT,
    status VARCHAR(50) DEFAULT 'running',  -- running, completed, failed
    error_message TEXT,

    -- Timing
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_ms INTEGER,

    -- Stats
    tools_planned INTEGER DEFAULT 0,
    tools_executed INTEGER DEFAULT 0,
    tools_failed INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0
);

CREATE INDEX idx_runs_user ON orchestrator_runs(user_id);
CREATE INDEX idx_runs_status ON orchestrator_runs(status);
CREATE INDEX idx_runs_started ON orchestrator_runs(started_at DESC);

-- ============================================
-- 3. Orchestrator Logs
-- Detaillierte Logs für Debugging
-- ============================================
CREATE TABLE IF NOT EXISTS orchestrator_logs (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES orchestrator_runs(id) ON DELETE CASCADE,

    -- Log Details
    node_name VARCHAR(100) NOT NULL,  -- planner, executor, checker, responder
    log_level VARCHAR(20) DEFAULT 'INFO',
    message TEXT NOT NULL,

    -- Structured Data
    data JSONB,  -- Für komplexe Daten (Plans, Tool Outputs, etc.)

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_logs_run ON orchestrator_logs(run_id);
CREATE INDEX idx_logs_node ON orchestrator_logs(node_name);
CREATE INDEX idx_logs_level ON orchestrator_logs(log_level);
CREATE INDEX idx_logs_created ON orchestrator_logs(created_at DESC);

-- ============================================
-- 4. Tool Executions
-- Detailliertes Tracking aller Tool-Aufrufe
-- ============================================
CREATE TABLE IF NOT EXISTS tool_executions (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES orchestrator_runs(id) ON DELETE CASCADE,

    -- Tool Info
    todo_id VARCHAR(50),
    tool_name VARCHAR(100) NOT NULL,

    -- Input/Output
    input_context TEXT,
    output JSONB,

    -- Status
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,

    -- Dependencies
    depends_on TEXT[],  -- Array von todo_ids

    -- Timing
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_ms INTEGER
);

CREATE INDEX idx_tool_exec_run ON tool_executions(run_id);
CREATE INDEX idx_tool_exec_tool ON tool_executions(tool_name);
CREATE INDEX idx_tool_exec_success ON tool_executions(success);

-- ============================================
-- 5. Planner Decisions
-- Was hat der Planner gedacht?
-- ============================================
CREATE TABLE IF NOT EXISTS planner_decisions (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES orchestrator_runs(id) ON DELETE CASCADE,

    -- Plan
    todo_list JSONB NOT NULL,  -- Die geplanten TODOs
    reasoning TEXT,            -- Warum dieser Plan

    -- Clarification
    needs_clarification BOOLEAN DEFAULT FALSE,
    clarification_question TEXT,

    -- Meta
    model_used VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_planner_run ON planner_decisions(run_id);

-- ============================================
-- Views für einfaches Debugging
-- ============================================

-- View: Letzte Runs mit Stats
CREATE OR REPLACE VIEW v_recent_runs AS
SELECT
    r.id,
    r.user_id,
    r.user_message,
    r.final_response,
    r.status,
    r.tools_planned,
    r.tools_executed,
    r.tools_failed,
    r.duration_ms,
    r.started_at,
    (SELECT COUNT(*) FROM orchestrator_logs l WHERE l.run_id = r.id) as log_count
FROM orchestrator_runs r
ORDER BY r.started_at DESC
LIMIT 50;

-- View: Tool Performance
CREATE OR REPLACE VIEW v_tool_stats AS
SELECT
    tool_name,
    COUNT(*) as total_calls,
    SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
    SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as failed,
    ROUND(AVG(duration_ms)) as avg_duration_ms,
    ROUND(100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate
FROM tool_executions
GROUP BY tool_name
ORDER BY total_calls DESC;

-- ============================================
-- Functions
-- ============================================

-- Function: Cleanup alte Logs (älter als 30 Tage)
CREATE OR REPLACE FUNCTION cleanup_old_logs()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    WITH deleted AS (
        DELETE FROM orchestrator_runs
        WHERE started_at < NOW() - INTERVAL '30 days'
        RETURNING id
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Initial Data / Test
-- ============================================
INSERT INTO orchestrator_runs (user_id, user_message, status, final_response)
VALUES ('system', 'Database initialized', 'completed', 'Schema created successfully')
ON CONFLICT DO NOTHING;

-- Grant permissions (für Docker)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO orchestrator;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO orchestrator;
