"""Brand profiles and durable batches for the full media-production agent."""
from alembic import op
import sqlalchemy as sa

revision = '0019_production_agent'
down_revision = '0018_media_production'
branch_labels = depends_on = None


def upgrade():
    schema = """CREATE TABLE IF NOT EXISTS production_brands (
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
    for statement in schema.split(';'):
        if statement.strip():
            op.get_bind().execute(sa.text(statement))


def downgrade():
    for table in ['production_asset_usage', 'production_batches', 'production_brands']:
        op.drop_table(table)
