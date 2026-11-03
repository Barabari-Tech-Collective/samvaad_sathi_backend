"""In-app notifications and email delivery records (architecture pack F30)."""

from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f9"
down_revision = "f2a3b4c5d6e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("href", sa.String(300)),
        sa.Column("related_type", sa.String(32)),
        sa.Column("related_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notification_recipient_id", "notification", ["recipient_id"])
    op.create_index("ix_notification_created_at", "notification", ["created_at"])
    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="email"),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("related_type", sa.String(32), nullable=False, server_default="human_interview"),
        sa.Column("related_id", sa.Integer(), nullable=False),
        sa.Column("expected_starts_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'skipped', 'superseded')",
            name="ck_notification_delivery_status",
        ),
    )
    op.create_index("ix_notification_delivery_recipient_id", "notification_delivery", ["recipient_id"])
    op.create_index("ix_notification_delivery_related_id", "notification_delivery", ["related_id"])
    op.create_index("ix_notification_delivery_due", "notification_delivery", ["status", "scheduled_for"])


def downgrade():
    op.drop_table("notification_delivery")
    op.drop_table("notification")
