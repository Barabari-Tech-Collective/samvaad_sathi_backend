"""Private accommodation requests and approved conditions (architecture pack F13)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d6e7f8a9b0c2"
down_revision = "c5d6e7f8a9b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accommodation_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("conditions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("decided_by", sa.Integer()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("student_id", "request_key", name="uq_accommodation_request"),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'declined', 'withdrawn')", name="ck_accommodation_status"
        ),
    )
    op.create_index("ix_accommodation_request_student_id", "accommodation_request", ["student_id"])


def downgrade():
    op.drop_index("ix_accommodation_request_student_id", "accommodation_request")
    op.drop_table("accommodation_request")
