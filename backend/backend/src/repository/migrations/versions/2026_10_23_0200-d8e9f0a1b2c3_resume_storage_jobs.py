"""Short-lived original-file staging and bounded storage delivery."""
from alembic import op
import sqlalchemy as sa
revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = depends_on = None


def upgrade():
    op.create_table("resume_storage_job",
        sa.Column("user_id",sa.Integer(),sa.ForeignKey("user.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("file_sha256",sa.String(64),primary_key=True),
        sa.Column("payload",sa.LargeBinary(),nullable=True),
        sa.Column("filename",sa.String(256),nullable=False),
        sa.Column("content_type",sa.String(128),nullable=False),
        sa.Column("resume_text",sa.Text(),nullable=True),
        sa.Column("state",sa.String(16),server_default="pending",nullable=False),
        sa.Column("attempts",sa.Integer(),server_default="0",nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("state IN ('pending','stored','failed','expired')",name="ck_resume_storage_job_state"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 3",name="ck_resume_storage_job_attempts"),
    )


def downgrade():
    op.drop_table("resume_storage_job")
