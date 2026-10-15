"""add durable answer identity

Revision ID: f19e4b7a2c31
Revises: 7ddc54281fe2
Create Date: 2026-10-13 10:30:00
"""

from alembic import op
import sqlalchemy as sa

revision = "f19e4b7a2c31"
down_revision = "7ddc54281fe2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("question_attempt", sa.Column("client_attempt_id", sa.String(64), nullable=True))
    op.add_column("question_attempt", sa.Column("answer_checksum", sa.String(64), nullable=True))
    op.add_column(
        "question_attempt",
        sa.Column("answer_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("question_attempt", sa.Column("answer_kind", sa.String(16), nullable=True))
    op.add_column(
        "question_attempt", sa.Column("answer_saved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_question_attempt_interview_client_attempt",
        "question_attempt",
        ["interview_id", "client_attempt_id"],
    )
    # Preserve the durable state of answers created before these explicit
    # checkpoint columns existed. Without this backfill, resume would present
    # already-recorded historical answers as unsaved after deployment.
    op.execute(
        """
        UPDATE question_attempt
        SET answer_saved_at = created_at,
            answer_version = 1,
            answer_kind = CASE WHEN audio_url IS NOT NULL THEN 'audio' ELSE 'text' END
        WHERE answer_saved_at IS NULL
          AND (audio_url IS NOT NULL OR transcription IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_question_attempt_interview_client_attempt", "question_attempt", type_="unique"
    )
    op.drop_column("question_attempt", "answer_saved_at")
    op.drop_column("question_attempt", "answer_kind")
    op.drop_column("question_attempt", "answer_version")
    op.drop_column("question_attempt", "answer_checksum")
    op.drop_column("question_attempt", "client_attempt_id")
