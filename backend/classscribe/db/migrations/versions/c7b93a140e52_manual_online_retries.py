"""Keep each explicitly authorized online request as a separate attempt."""

import sqlalchemy as sa
from alembic import op

revision = "c7b93a140e52"
down_revision = "f2a6c8419d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("online_request_attempts") as batch:
        batch.drop_constraint("uq_online_request_attempts_job_id", type_="unique")
        batch.add_column(
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("diagnostics_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.create_unique_constraint(
            "uq_online_request_attempt_number", ["job_id", "attempt_number"]
        )
        batch.create_check_constraint("online_attempt_number_positive", "attempt_number >= 1")
    with op.batch_alter_table("online_request_attempts") as batch:
        batch.alter_column("attempt_number", server_default=None)
        batch.alter_column("diagnostics_json", server_default=None)


def downgrade() -> None:
    # Downgrading a populated history would discard attempts. Refuse instead.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM online_request_attempts WHERE attempt_number > 1 LIMIT 1")
    ).first():
        raise RuntimeError("Cannot downgrade an online request history containing manual retries")
    with op.batch_alter_table("online_request_attempts") as batch:
        batch.drop_constraint("uq_online_request_attempt_number", type_="unique")
        batch.drop_constraint("online_attempt_number_positive", type_="check")
        batch.drop_column("diagnostics_json")
        batch.drop_column("attempt_number")
        batch.create_unique_constraint("uq_online_request_attempts_job_id", ["job_id"])
