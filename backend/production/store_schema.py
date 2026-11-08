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
CREATE TABLE IF NOT EXISTS production_brands (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS production_batches (
    id TEXT PRIMARY KEY REFERENCES jobs(id),
    request_key TEXT UNIQUE,
    request_json TEXT NOT NULL,
    items_json TEXT NOT NULL,
    log_json TEXT NOT NULL DEFAULT '[]',
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS production_asset_usage (
    batch_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    PRIMARY KEY(batch_id, source_id, asset_id)
);
"""
