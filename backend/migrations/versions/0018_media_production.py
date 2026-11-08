"""Product workflow assets and plans, without altering existing projects."""
from alembic import op
import sqlalchemy as sa

revision = "0018_media_production"
down_revision = "0017_job_events_unique_seq"
branch_labels = depends_on = None


def upgrade():
    schema = """
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
    for statement in schema.split(";"):
        if statement.strip():
            op.get_bind().execute(sa.text(statement))


def downgrade():
    op.drop_table("production_runs")
    op.drop_table("media_assets")
