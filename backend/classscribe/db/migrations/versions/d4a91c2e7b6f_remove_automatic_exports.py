"""Remove the automatic export step; exports are created on demand."""

from alembic import op

revision = "d4a91c2e7b6f"
down_revision = "c7b93a140e52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Already written export files and their artifact rows remain downloadable.
    op.execute("DELETE FROM job_checkpoints WHERE checkpoint_key = 'automatic_exports'")


def downgrade() -> None:
    # The removed step produced only derived files; jobs remain valid without it.
    pass
