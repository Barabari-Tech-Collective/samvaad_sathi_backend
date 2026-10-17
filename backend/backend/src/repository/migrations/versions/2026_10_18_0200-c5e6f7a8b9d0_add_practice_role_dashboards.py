"""add governed role dashboard records

Revision ID: c5e6f7a8b9d0
Revises: b4d5e6f7a8c9
Create Date: 2026-10-18 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5e6f7a8b9d0"
down_revision: str | None = "b4d5e6f7a8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "practice_consent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("granted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "purpose", name="uq_practice_consent_user_purpose"
        ),
    )
    op.create_index("ix_practice_consent_user_id", "practice_consent", ["user_id"])
    op.create_index("ix_practice_consent_purpose", "practice_consent", ["purpose"])
    op.create_index(
        "ix_practice_consent_expires_at", "practice_consent", ["expires_at"]
    )

    op.create_table(
        "practice_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(24), server_default="assigned", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_practice_assignment_student_id", "practice_assignment", ["student_id"]
    )
    op.create_index(
        "ix_practice_assignment_assigned_by", "practice_assignment", ["assigned_by"]
    )
    op.create_index("ix_practice_assignment_status", "practice_assignment", ["status"])

    op.create_table(
        "practice_shortlist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("opportunity", sa.String(200), nullable=False),
        sa.Column(
            "evidence_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.Integer(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["locked_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_practice_shortlist_status", "practice_shortlist", ["status"])
    op.create_index(
        "ix_practice_shortlist_created_by", "practice_shortlist", ["created_by"]
    )
    op.create_index(
        "ix_practice_shortlist_expires_at", "practice_shortlist", ["expires_at"]
    )

    op.create_table(
        "practice_shortlist_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shortlist_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("decided_by", sa.Integer(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["shortlist_id"], ["practice_shortlist.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shortlist_id", "student_id", name="uq_practice_shortlist_student"
        ),
    )
    op.create_index(
        "ix_practice_shortlist_entry_shortlist_id",
        "practice_shortlist_entry",
        ["shortlist_id"],
    )
    op.create_index(
        "ix_practice_shortlist_entry_student_id",
        "practice_shortlist_entry",
        ["student_id"],
    )

    op.create_table(
        "practice_dashboard_audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "event_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_practice_dashboard_audit_event_actor_id",
        "practice_dashboard_audit_event",
        ["actor_id"],
    )
    op.create_index(
        "ix_practice_dashboard_audit_event_action",
        "practice_dashboard_audit_event",
        ["action"],
    )
    op.create_index(
        "ix_practice_dashboard_audit_event_created_at",
        "practice_dashboard_audit_event",
        ["created_at"],
    )

    op.create_table(
        "practice_feature_control",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("practice_feature_control")
    op.drop_index(
        "ix_practice_dashboard_audit_event_created_at",
        table_name="practice_dashboard_audit_event",
    )
    op.drop_index(
        "ix_practice_dashboard_audit_event_action",
        table_name="practice_dashboard_audit_event",
    )
    op.drop_index(
        "ix_practice_dashboard_audit_event_actor_id",
        table_name="practice_dashboard_audit_event",
    )
    op.drop_table("practice_dashboard_audit_event")
    op.drop_index(
        "ix_practice_shortlist_entry_student_id", table_name="practice_shortlist_entry"
    )
    op.drop_index(
        "ix_practice_shortlist_entry_shortlist_id",
        table_name="practice_shortlist_entry",
    )
    op.drop_table("practice_shortlist_entry")
    op.drop_index("ix_practice_shortlist_expires_at", table_name="practice_shortlist")
    op.drop_index("ix_practice_shortlist_created_by", table_name="practice_shortlist")
    op.drop_index("ix_practice_shortlist_status", table_name="practice_shortlist")
    op.drop_table("practice_shortlist")
    op.drop_index("ix_practice_assignment_status", table_name="practice_assignment")
    op.drop_index(
        "ix_practice_assignment_assigned_by", table_name="practice_assignment"
    )
    op.drop_index("ix_practice_assignment_student_id", table_name="practice_assignment")
    op.drop_table("practice_assignment")
    op.drop_index("ix_practice_consent_expires_at", table_name="practice_consent")
    op.drop_index("ix_practice_consent_purpose", table_name="practice_consent")
    op.drop_index("ix_practice_consent_user_id", table_name="practice_consent")
    op.drop_table("practice_consent")
