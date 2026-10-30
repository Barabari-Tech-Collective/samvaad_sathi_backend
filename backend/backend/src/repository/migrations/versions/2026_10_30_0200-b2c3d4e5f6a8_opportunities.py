"""Opportunities: an opening with candidates and a round plan; links from shortlists,
human interviews and resume-screening batches (architecture pack F25, slice 2)."""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opportunity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("organisation", sa.String(160), nullable=False),
        sa.Column(
            "job_profile_id",
            sa.Integer(),
            sa.ForeignKey("job_profile.id", ondelete="SET NULL"),
        ),
        sa.Column("requirements", sa.Text()),
        sa.Column("max_evidence_age_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("min_assessments", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("eligibility_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("created_by", "request_key", name="uq_opportunity_request"),
        sa.CheckConstraint("status IN ('open', 'closed', 'archived')", name="ck_opportunity_status"),
        sa.CheckConstraint("max_evidence_age_days BETWEEN 1 AND 365", name="ck_opportunity_evidence_age"),
        sa.CheckConstraint("min_assessments BETWEEN 0 AND 20", name="ck_opportunity_min_assessments"),
    )
    for column in ("course_id", "job_profile_id", "status", "created_by"):
        op.create_index(f"ix_opportunity_{column}", "opportunity", [column])

    op.create_table(
        "opportunity_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("opportunity_id", "student_id", name="uq_opportunity_candidate"),
    )
    op.create_index("ix_opportunity_candidate_opportunity_id", "opportunity_candidate", ["opportunity_id"])
    op.create_index("ix_opportunity_candidate_student_id", "opportunity_candidate", ["student_id"])

    op.create_table(
        "opportunity_round",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column(
            "job_profile_id",
            sa.Integer(),
            sa.ForeignKey("job_profile.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('ai', 'human')", name="ck_opportunity_round_kind"),
        sa.CheckConstraint(
            "kind = 'human' OR job_profile_id IS NOT NULL",
            name="ck_opportunity_round_ai_template",
        ),
    )
    op.create_index("ix_opportunity_round_opportunity_id", "opportunity_round", ["opportunity_id"])

    for table in ("practice_shortlist", "human_interview", "bulk_ats_batch"):
        op.add_column(
            table,
            sa.Column(
                "opportunity_id",
                sa.Integer(),
                sa.ForeignKey("opportunity.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_opportunity_id", table, ["opportunity_id"])


def downgrade():
    for table in ("bulk_ats_batch", "human_interview", "practice_shortlist"):
        op.drop_index(f"ix_{table}_opportunity_id", table_name=table)
        op.drop_column(table, "opportunity_id")
    op.drop_index("ix_opportunity_round_opportunity_id", table_name="opportunity_round")
    op.drop_table("opportunity_round")
    op.drop_index("ix_opportunity_candidate_student_id", table_name="opportunity_candidate")
    op.drop_index("ix_opportunity_candidate_opportunity_id", table_name="opportunity_candidate")
    op.drop_table("opportunity_candidate")
    for column in ("created_by", "status", "job_profile_id", "course_id"):
        op.drop_index(f"ix_opportunity_{column}", table_name="opportunity")
    op.drop_table("opportunity")
