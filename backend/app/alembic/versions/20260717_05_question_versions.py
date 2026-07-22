"""Add immutable question version history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260717_05"
down_revision = "20260717_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("questions", sa.Column("current_version_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("questions", sa.Column("current_version_author_id", sa.Integer(), nullable=True))
    op.add_column("questions", sa.Column("current_version_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_questions_current_version_author_id_users",
        "questions",
        "users",
        ["current_version_author_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE questions SET current_version_created_at = created_at, current_version_author_id = owner_id "
        "WHERE current_version_created_at IS NULL"
    )

    op.create_table(
        "question_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("version_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("question_id", "version_number", name="uq_question_version_number"),
    )
    op.create_index("ix_question_versions_question_id", "question_versions", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_question_versions_question_id", table_name="question_versions")
    op.drop_table("question_versions")
    op.drop_constraint("fk_questions_current_version_author_id_users", "questions", type_="foreignkey")
    op.drop_column("questions", "current_version_note")
    op.drop_column("questions", "current_version_author_id")
    op.drop_column("questions", "current_version_created_at")
    op.drop_column("questions", "current_version")
