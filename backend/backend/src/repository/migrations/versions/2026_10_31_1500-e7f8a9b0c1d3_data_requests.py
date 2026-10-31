"""Student data export and deletion requests (architecture pack F32)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e7f8a9b0c1d3"
down_revision = "d6e7f8a9b0c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("verification_note", sa.Text()),
        sa.Column("handled_by", sa.Integer()),
        sa.Column("outcome", JSONB()),
        sa.Column("closure_note", sa.Text()),
        sa.Column("export_available_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("student_id", "request_key", name="uq_data_request_key"),
        sa.CheckConstraint("kind IN ('export', 'deletion')", name="ck_data_request_kind"),
        sa.CheckConstraint(
            "status IN ('submitted', 'verified', 'completed', 'rejected', 'withdrawn')",
            name="ck_data_request_status",
        ),
    )
    op.create_index("ix_data_request_student_id", "data_request", ["student_id"])


def downgrade():
    op.drop_index("ix_data_request_student_id", "data_request")
    op.drop_table("data_request")
