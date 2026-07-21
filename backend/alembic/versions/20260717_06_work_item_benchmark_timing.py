"""Persist per-work-item benchmark timing data."""

from alembic import op
import sqlalchemy as sa


revision = "20260717_06"
down_revision = "20260717_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("check_work_items", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("check_work_items", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "check_work_items",
        sa.Column("execution_ms", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("check_work_items", "execution_ms")
    op.drop_column("check_work_items", "completed_at")
    op.drop_column("check_work_items", "started_at")
