"""Add batch capacity controls and manual-review diagnostics."""
from alembic import op
import sqlalchemy as sa


revision = "20260717_04"
down_revision = "20260716_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("check_batches", sa.Column("priority", sa.String(), nullable=False, server_default="batch"))
    op.add_column("check_batches", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("check_batches", sa.Column("estimated_complete_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("check_batches", sa.Column("manual_review_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_check_batches_priority", "check_batches", ["priority"])
    op.create_index("ix_check_batches_deadline_at", "check_batches", ["deadline_at"])

    op.add_column("check_work_items", sa.Column("queue_owner_id", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("check_work_items", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("check_work_items", sa.Column("error_status_code", sa.Integer(), nullable=True))
    op.add_column("check_work_items", sa.Column("manual_review_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_check_work_items_queue_owner_id", "check_work_items", ["queue_owner_id"])
    op.create_index("ix_check_work_items_error_code", "check_work_items", ["error_code"])


def downgrade() -> None:
    op.drop_index("ix_check_work_items_error_code", table_name="check_work_items")
    op.drop_index("ix_check_work_items_queue_owner_id", table_name="check_work_items")
    op.drop_column("check_work_items", "manual_review_at")
    op.drop_column("check_work_items", "error_status_code")
    op.drop_column("check_work_items", "error_code")
    op.drop_column("check_work_items", "queue_owner_id")

    op.drop_index("ix_check_batches_deadline_at", table_name="check_batches")
    op.drop_index("ix_check_batches_priority", table_name="check_batches")
    op.drop_column("check_batches", "manual_review_count")
    op.drop_column("check_batches", "estimated_complete_at")
    op.drop_column("check_batches", "deadline_at")
    op.drop_column("check_batches", "priority")
