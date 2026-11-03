"""Confirmed project and experience context written by the student (architecture pack F10)."""

from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a0"
down_revision = "a3b4c5d6e7f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "learner_context",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('project', 'experience')", name="ck_learner_context_kind"),
        sa.CheckConstraint("status IN ('draft', 'confirmed')", name="ck_learner_context_status"),
    )
    op.create_index("ix_learner_context_user_id", "learner_context", ["user_id"])


def downgrade():
    op.drop_index("ix_learner_context_user_id", "learner_context")
    op.drop_table("learner_context")
