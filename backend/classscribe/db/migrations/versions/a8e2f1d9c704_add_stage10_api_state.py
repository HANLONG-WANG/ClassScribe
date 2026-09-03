"""add stage 10 API and artifact state

Revision ID: a8e2f1d9c704
Revises: eeec80ee63cf
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8e2f1d9c704"
down_revision: str | Sequence[str] | None = "eeec80ee63cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_checkpoints") as batch:
        batch.drop_constraint("uq_job_checkpoints_job_id", type_="unique")
        batch.create_unique_constraint(
            "uq_job_checkpoint_scope",
            ["job_id", "stage", "checkpoint_key", "segment_id"],
        )
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("options_json", sa.JSON(), server_default="{}", nullable=False))
    with op.batch_alter_table("transcript_segments") as batch:
        batch.add_column(sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False))
        batch.add_column(
            sa.Column("supersedes_segment_ids_json", sa.JSON(), server_default="[]", nullable=False)
        )
        batch.create_index("ix_transcript_segments_is_active", ["is_active"], unique=False)
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )
    op.create_table(
        "profile_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("scenario", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("benchmark_run_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_settings")),
        sa.UniqueConstraint("language", "scenario", name="uq_profile_language_scenario"),
    )
    op.create_table(
        "glossary_materials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("glossary_id", sa.String(length=36), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("suggestion_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["glossary_id"], ["glossaries.id"], name=op.f("fk_glossary_materials_glossary_id_glossaries"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_materials")),
        sa.UniqueConstraint("file_id", name=op.f("uq_glossary_materials_file_id")),
    )
    op.create_index(
        "ix_glossary_materials_glossary",
        "glossary_materials",
        ["glossary_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "export_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("output_format", sa.String(length=16), nullable=False),
        sa.Column("text_layer", sa.String(length=16), nullable=False),
        sa.Column("view", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_export_artifacts_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_artifacts")),
        sa.UniqueConstraint("relative_path", name=op.f("uq_export_artifacts_relative_path")),
    )
    op.create_index(
        "ix_export_artifacts_job",
        "export_artifacts",
        ["job_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_export_artifacts_job", table_name="export_artifacts")
    op.drop_table("export_artifacts")
    op.drop_index("ix_glossary_materials_glossary", table_name="glossary_materials")
    op.drop_table("glossary_materials")
    op.drop_table("profile_settings")
    op.drop_table("app_settings")
    with op.batch_alter_table("transcript_segments") as batch:
        batch.drop_index("ix_transcript_segments_is_active")
        batch.drop_column("supersedes_segment_ids_json")
        batch.drop_column("is_active")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("options_json")
    with op.batch_alter_table("job_checkpoints") as batch:
        batch.drop_constraint("uq_job_checkpoint_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_job_checkpoints_job_id", ["job_id", "stage", "checkpoint_key"]
        )
