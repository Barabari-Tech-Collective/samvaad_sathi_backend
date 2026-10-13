"""add bulk ats batch/candidate tables and user.role for recruiter RBAC

Revision ID: f1b2c3d4e5a6
Revises: 9b6ce6018b3f
Create Date: 2026-10-13 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f1b2c3d4e5a6'
down_revision = '9b6ce6018b3f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bulk ATS is screening-assistance for staff, not students. Nullable role
    # column alongside the existing is_admin flag (see
    # src/api/dependencies/admin.py): is_admin keeps meaning "cross-student
    # data", role names the narrower recruiter/placement_staff grant so
    # neither flag has to be stretched to cover both. NULL (the default for
    # every existing row, students included) grants nothing.
    op.execute(
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS role VARCHAR(32)'
    )
    op.execute(
        "ALTER TABLE \"user\" DROP CONSTRAINT IF EXISTS chk_user_role"
    )
    op.execute(
        "ALTER TABLE \"user\" ADD CONSTRAINT chk_user_role "
        "CHECK (role IS NULL OR role IN ('admin', 'recruiter', 'placement_staff'))"
    )

    op.create_table(
        "bulk_ats_batch",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("job_profile_id", sa.Integer(), sa.ForeignKey("job_profile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_name", sa.String(length=160), nullable=False),
        sa.Column("experience_mode", sa.String(length=16), nullable=False, server_default="experienced"),
        sa.Column("weight_overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("experience_mode IN ('fresher', 'experienced')", name="chk_bulk_ats_batch_experience_mode"),
        sa.CheckConstraint("status IN ('draft', 'processing', 'completed', 'failed')", name="chk_bulk_ats_batch_status"),
    )
    op.create_index("ix_bulk_ats_batch_job_profile_id", "bulk_ats_batch", ["job_profile_id"])
    op.create_index("ix_bulk_ats_batch_status", "bulk_ats_batch", ["status"])
    op.create_index("ix_bulk_ats_batch_created_by", "bulk_ats_batch", ["created_by"])
    op.create_index("ix_bulk_ats_batch_created_at", "bulk_ats_batch", ["created_at"])

    op.create_table(
        "bulk_ats_candidate",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("bulk_ats_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_name", sa.String(length=256), nullable=True),
        sa.Column("email_or_candidate_id", sa.String(length=256), nullable=True),
        sa.Column("candidate_key", sa.String(length=320), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("resume_url", sa.String(length=2048), nullable=True),
        sa.Column("s3_key", sa.String(length=512), nullable=True),
        sa.Column("original_filename", sa.String(length=256), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extracted_resume_text", sa.Text(), nullable=True),
        sa.Column("parsed_resume_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deterministic_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("jev_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("link_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("score_band", sa.String(length=16), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "file_sha256", name="uq_bulk_ats_candidate_batch_sha256"),
        sa.UniqueConstraint("batch_id", "candidate_key", name="uq_bulk_ats_candidate_batch_key"),
        sa.CheckConstraint(
            "status IN ('queued', 'parsing', 'evaluating', 'completed', 'needs_review', 'failed')",
            name="chk_bulk_ats_candidate_status",
        ),
    )
    op.create_index("ix_bulk_ats_candidate_batch_id", "bulk_ats_candidate", ["batch_id"])
    op.create_index("ix_bulk_ats_candidate_email_or_candidate_id", "bulk_ats_candidate", ["email_or_candidate_id"])
    op.create_index("ix_bulk_ats_candidate_file_sha256", "bulk_ats_candidate", ["file_sha256"])
    op.create_index("ix_bulk_ats_candidate_status", "bulk_ats_candidate", ["status"])
    op.create_index("ix_bulk_ats_candidate_created_at", "bulk_ats_candidate", ["created_at"])
    op.create_index("ix_bulk_ats_candidate_batch_status", "bulk_ats_candidate", ["batch_id", "status"])
    op.create_index("ix_bulk_ats_candidate_batch_score", "bulk_ats_candidate", ["batch_id", "final_score"])


def downgrade() -> None:
    op.drop_table("bulk_ats_candidate")
    op.drop_table("bulk_ats_batch")
    op.execute('ALTER TABLE "user" DROP CONSTRAINT IF EXISTS chk_user_role')
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS role')
