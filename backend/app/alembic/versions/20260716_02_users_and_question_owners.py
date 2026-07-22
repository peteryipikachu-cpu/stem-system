"""Add local users and question ownership."""
from alembic import op
import sqlalchemy as sa

revision = "20260716_02"
down_revision = "20260715_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始迁移使用当前模型的 metadata 建表；空库首次升级时 users/owner_id
    # 已经存在，而已有生产库升级时则需要在此迁移中补齐它们。
    inspector = sa.inspect(op.get_bind())
    if "users" not in inspector.get_table_names():
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("password_hash", sa.String(length=256), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("username", name="uq_users_username"),
        )
        op.create_index("ix_users_username", "users", ["username"])
        op.create_index("ix_users_role", "users", ["role"])
    if "owner_id" not in {column["name"] for column in inspector.get_columns("questions")}:
        op.add_column("questions", sa.Column("owner_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_questions_owner", "questions", "users", ["owner_id"], ["id"], ondelete="SET NULL")
        op.create_index("ix_questions_owner_id", "questions", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_questions_owner_id", table_name="questions")
    op.drop_constraint("fk_questions_owner", "questions", type_="foreignkey")
    op.drop_column("questions", "owner_id")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
