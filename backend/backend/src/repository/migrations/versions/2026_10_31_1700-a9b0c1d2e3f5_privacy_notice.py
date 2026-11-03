"""One-time privacy notice acceptance."""

from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f5"
down_revision = "f8a9b0c1d2e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "privacy_notice_acceptance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "version", name="uq_privacy_notice_user_version"),
    )
    op.create_index("ix_privacy_notice_acceptance_user_id", "privacy_notice_acceptance", ["user_id"])


def downgrade():
    op.drop_table("privacy_notice_acceptance")
