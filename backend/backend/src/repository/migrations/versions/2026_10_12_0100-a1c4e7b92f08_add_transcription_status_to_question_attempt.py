"""add transcription status to question_attempt

Transcription moves out of the upload request and into the arq worker, so the
request can no longer report the outcome in its response. These columns are
where the client reads progress from.

Existing rows are backfilled from what is already known: a row that has a
transcription is transcribed, anything else is left pending. Nothing polls old
attempts, so pending is a safe resting state for them.

Revision ID: a1c4e7b92f08
Revises: 7af582260700
Create Date: 2026-10-12 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "a1c4e7b92f08"
down_revision = "7af582260700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_attempt",
        sa.Column(
            "transcription_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "question_attempt", sa.Column("transcription_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "question_attempt",
        sa.Column("transcription_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Answers already transcribed must not look unprocessed, or a client polling
    # an older attempt would wait for work that will never be queued.
    op.execute(
        "UPDATE question_attempt SET transcription_status = 'transcribed', "
        "transcription_updated_at = created_at WHERE transcription IS NOT NULL"
    )
    # Supports the worker's sweep for rows stalled in pending/processing.
    op.create_index(
        op.f("ix_question_attempt_transcription_status"),
        "question_attempt",
        ["transcription_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_question_attempt_transcription_status"), table_name="question_attempt"
    )
    op.drop_column("question_attempt", "transcription_updated_at")
    op.drop_column("question_attempt", "transcription_error")
    op.drop_column("question_attempt", "transcription_status")
