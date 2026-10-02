"""Clip provenance and durable online send intents."""

import sqlalchemy as sa
from alembic import op

revision = "f2a6c8419d30"
down_revision = "b12d940ac831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recordings") as batch:
        batch.add_column(sa.Column("parent_recording_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_start_sample", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_end_sample", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("clip_submission_key", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_recordings_parent_recording_id_recordings",
            "recordings",
            ["parent_recording_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_recordings_parent_recording_id", ["parent_recording_id"])
        batch.create_unique_constraint("uq_recordings_clip_submission_key", ["clip_submission_key"])
    op.create_table(
        "online_request_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("audio_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("service_request_id", sa.String(256)),
        sa.Column("response_path", sa.Text()),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared', 'sending', 'responded', 'imported', 'uncertain', 'failed')",
            name="online_attempt_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("online_request_attempts")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_constraint("uq_recordings_clip_submission_key", type_="unique")
        batch.drop_index("ix_recordings_parent_recording_id")
        batch.drop_constraint("fk_recordings_parent_recording_id_recordings", type_="foreignkey")
        for name in (
            "clip_submission_key",
            "source_end_sample",
            "source_start_sample",
            "parent_recording_id",
        ):
            batch.drop_column(name)
