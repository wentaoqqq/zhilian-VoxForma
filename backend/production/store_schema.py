"""Shared DDL for fresh installs and Alembic upgrades."""

PRODUCT_WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_products (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS production_product_assets (
    product_id TEXT NOT NULL REFERENCES production_products(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES media_assets(id),
    video_type_id TEXT NOT NULL DEFAULT '',
    category_id TEXT,
    role TEXT NOT NULL DEFAULT 'input' CHECK(role IN ('input','material','outro','sticker')),
    classification_status TEXT NOT NULL DEFAULT 'pending' CHECK(classification_status IN ('pending','classified','unclassified')),
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(product_id, asset_id)
);
CREATE INDEX IF NOT EXISTS production_product_assets_scope
    ON production_product_assets(product_id, video_type_id, category_id);
CREATE TABLE IF NOT EXISTS production_conversations (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    video_type_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS production_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    batch_id TEXT,
    request_key TEXT UNIQUE,
    created_at REAL NOT NULL
);
"""

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
""" + PRODUCT_WORKSPACE_SCHEMA
