"""Record the account that started each audit run."""

from alembic import op
import sqlalchemy as sa


revision = "20260723_07"
down_revision = "20260717_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始迁移基于当前 metadata 建表；空库升级时字段已存在，历史库才需补齐。
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("check_runs")}
    if "requested_by_user_id" not in columns:
        op.add_column("check_runs", sa.Column("requested_by_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_check_runs_requested_by_user",
            "check_runs",
            "users",
            ["requested_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    indexes = {index["name"] for index in inspector.get_indexes("check_runs")}
    if "ix_check_runs_requested_by_user_id" not in indexes:
        op.create_index("ix_check_runs_requested_by_user_id", "check_runs", ["requested_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_check_runs_requested_by_user_id", table_name="check_runs")
    op.drop_constraint("fk_check_runs_requested_by_user", "check_runs", type_="foreignkey")
    op.drop_column("check_runs", "requested_by_user_id")
