"""Shared DDL for fresh installs and Alembic upgrades."""

PRODUCTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS production_runs (
    id TEXT PRIMARY KEY REFERENCES jobs(id),
    plan_json TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '{}'
);
"""
