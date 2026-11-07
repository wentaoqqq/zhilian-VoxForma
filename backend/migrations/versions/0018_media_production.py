"""Product workflow assets and plans, without altering existing projects."""
from alembic import op
import sqlalchemy as sa

revision = "0018_media_production"
down_revision = "0017_job_events_unique_seq"
branch_labels = depends_on = None


def upgrade():
    from production.store_schema import PRODUCTION_SCHEMA
    for statement in PRODUCTION_SCHEMA.split(";"):
        if statement.strip():
            op.get_bind().execute(sa.text(statement))


def downgrade():
    op.drop_table("production_runs")
    op.drop_table("media_assets")
