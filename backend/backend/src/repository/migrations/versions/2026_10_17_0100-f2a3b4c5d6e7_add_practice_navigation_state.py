"""add server-authoritative practice navigation state

Revision ID: f2a3b4c5d6e7
Revises: f6a7b8c9d0e1
Create Date: 2026-10-17 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview",
        sa.Column("state_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "interview", sa.Column("current_question_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "interview",
        sa.Column(
            "question_set_version", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "interview", sa.Column("question_set_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "interview",
        sa.Column("question_set_frozen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview",
        sa.Column("last_transition_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "interview",
        sa.Column(
            "last_transition_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_interview_current_question_id_interview_question",
        "interview",
        "interview_question",
        ["current_question_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_interview_current_question_id",
        "interview",
        ["current_question_id"],
        unique=False,
    )

    op.add_column(
        "question_attempt",
        sa.Column(
            "attempt_state",
            sa.String(length=24),
            server_default="in_progress",
            nullable=False,
        ),
    )
    op.add_column(
        "question_attempt",
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "question_attempt",
        sa.Column("follow_up_disposition", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "question_attempt",
        sa.Column("follow_up_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_question_attempt_attempt_state",
        "question_attempt",
        ["attempt_state"],
        unique=False,
    )
    op.create_index(
        "ix_question_attempt_follow_up_disposition",
        "question_attempt",
        ["follow_up_disposition"],
        unique=False,
    )

    # Existing answers become durable checkpoints. Legacy interview cursors
    # and hashes are initialized by the first resume under a row lock, using
    # the exact question order already persisted for that sitting.
    op.execute("""
        UPDATE question_attempt
        SET attempt_state = 'answer_saved'
        WHERE answer_saved_at IS NOT NULL
           OR audio_url IS NOT NULL
           OR transcription IS NOT NULL
        """)


def downgrade() -> None:
    op.drop_index(
        "ix_question_attempt_follow_up_disposition", table_name="question_attempt"
    )
    op.drop_index("ix_question_attempt_attempt_state", table_name="question_attempt")
    op.drop_column("question_attempt", "follow_up_resolved_at")
    op.drop_column("question_attempt", "follow_up_disposition")
    op.drop_column("question_attempt", "skipped_at")
    op.drop_column("question_attempt", "attempt_state")

    op.drop_index("ix_interview_current_question_id", table_name="interview")
    op.drop_constraint(
        "fk_interview_current_question_id_interview_question",
        "interview",
        type_="foreignkey",
    )
    op.drop_column("interview", "last_transition_response")
    op.drop_column("interview", "last_transition_id")
    op.drop_column("interview", "question_set_frozen_at")
    op.drop_column("interview", "question_set_hash")
    op.drop_column("interview", "question_set_version")
    op.drop_column("interview", "current_question_id")
    op.drop_column("interview", "state_version")
