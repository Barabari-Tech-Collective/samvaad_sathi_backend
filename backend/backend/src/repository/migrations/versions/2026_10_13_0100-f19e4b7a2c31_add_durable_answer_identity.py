"""add durable answer identity

Revision ID: f19e4b7a2c31
Revises: c8a58ef421ba
Create Date: 2026-10-13 10:30:00
"""

from alembic import op
import sqlalchemy as sa

revision = "f19e4b7a2c31"
down_revision = "c8a58ef421ba"
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


def downgrade() -> None:
    op.drop_constraint(
        "uq_question_attempt_interview_client_attempt", "question_attempt", type_="unique"
    )
    op.drop_column("question_attempt", "answer_saved_at")
    op.drop_column("question_attempt", "answer_kind")
    op.drop_column("question_attempt", "answer_version")
    op.drop_column("question_attempt", "answer_checksum")
    op.drop_column("question_attempt", "client_attempt_id")
