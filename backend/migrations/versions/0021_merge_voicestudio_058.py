"""Join the upstream and VoxForma database histories after syncing VoiceStudio 0.5.8."""

revision = "0021_merge_voicestudio_058"
down_revision = ("0018_pronunciation_profile_scope", "0020_product_workspace")
branch_labels = depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
