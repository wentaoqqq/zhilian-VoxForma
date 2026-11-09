"""Independent products, scoped assets and product chat history."""
from alembic import op
import sqlalchemy as sa

revision = '0020_product_workspace'
down_revision = '0019_production_agent'
branch_labels = depends_on = None


def upgrade():
    # Frozen DDL: historical upgrades must not depend on future runtime schemas.
    schema = """
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
    for statement in schema.split(';'):
        if statement.strip():
            op.get_bind().execute(sa.text(statement))


def downgrade():
    for table in ('production_messages', 'production_conversations', 'production_product_assets', 'production_products'):
        op.drop_table(table)
