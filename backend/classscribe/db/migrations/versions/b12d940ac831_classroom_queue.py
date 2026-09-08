"""Persistent classroom FIFO and idempotent submissions."""
from alembic import op
import sqlalchemy as sa

revision = 'b12d940ac831'
down_revision = 'a8e2f1d9c704'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('queue_order', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('jobs', sa.Column('submission_key', sa.String(36), nullable=True))
    op.create_index('ix_jobs_queue_order', 'jobs', ['queue_order'])
    op.create_index('ix_jobs_submission_key', 'jobs', ['submission_key'], unique=True)
    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id FROM jobs ORDER BY created_at, id')).all()
    for order, (identifier,) in enumerate(rows, 1):
        connection.execute(sa.text('UPDATE jobs SET queue_order=:position WHERE id=:id'), {'position': order, 'id': identifier})


def downgrade() -> None:
    op.drop_index('ix_jobs_submission_key', table_name='jobs')
    op.drop_index('ix_jobs_queue_order', table_name='jobs')
    op.drop_column('jobs', 'submission_key')
    op.drop_column('jobs', 'queue_order')
