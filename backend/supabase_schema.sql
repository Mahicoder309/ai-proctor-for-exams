-- ============================================================
-- AI Exam Proctoring System — Supabase SQL Schema
-- ============================================================
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Exam Sessions table
CREATE TABLE IF NOT EXISTS "ExamSession" (
    id          TEXT PRIMARY KEY,
    student_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);

-- Violation Events table
CREATE TABLE IF NOT EXISTS "ViolationEvent" (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    type            TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    duration_sec    REAL DEFAULT 0.0,
    snapshot_path   TEXT,
    FOREIGN KEY (session_id) REFERENCES "ExamSession"(id)
);

-- Index for fast session lookups
CREATE INDEX IF NOT EXISTS idx_violation_session
    ON "ViolationEvent"(session_id);

CREATE INDEX IF NOT EXISTS idx_session_student
    ON "ExamSession"(student_id);

-- ============================================================
-- Verify tables were created:
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public';
-- ============================================================
