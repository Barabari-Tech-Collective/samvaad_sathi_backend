"""Persist account-owned extraction operation receipts."""
from alembic import op
import sqlalchemy as sa
revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = depends_on = None


def upgrade():
    op.create_table("resume_operation",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("request_key", sa.String(36), primary_key=True),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.CheckConstraint("state IN ('started','completed','failed')", name="ck_resume_operation_state"),
    )


def downgrade():
    op.drop_table("resume_operation")
