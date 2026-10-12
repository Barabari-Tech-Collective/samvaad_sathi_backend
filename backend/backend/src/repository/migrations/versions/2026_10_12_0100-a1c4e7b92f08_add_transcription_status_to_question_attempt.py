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

revision = "a1c4e7b92f08"
down_revision = "7af582260700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS throughout, rather than op.add_column, because startup also
    # patches these columns in: production cannot be migrated yet, and without
    # the patch every question_attempt query would fail the moment the model
    # ships. The app therefore adds them on boot, and a plain add_column would
    # then abort this migration with DuplicateColumn. Whichever mechanism runs
    # first, the other must be a no-op - the same collision that left
    # interview.difficulty unmigratable.
    op.execute(
        "ALTER TABLE question_attempt ADD COLUMN IF NOT EXISTS "
        "transcription_status VARCHAR(16) NOT NULL DEFAULT 'pending'"
    )
    op.execute(
        "ALTER TABLE question_attempt ADD COLUMN IF NOT EXISTS transcription_error TEXT"
    )
    op.execute(
        "ALTER TABLE question_attempt ADD COLUMN IF NOT EXISTS "
        "transcription_updated_at TIMESTAMPTZ"
    )
    # Answers already transcribed must not look unprocessed, or a client polling
    # an older attempt would wait for work that will never be queued. Scoped to
    # rows still at the default so a live job's status cannot be overwritten.
    op.execute(
        "UPDATE question_attempt SET transcription_status = 'transcribed', "
        "transcription_updated_at = created_at "
        "WHERE transcription IS NOT NULL AND transcription_status = 'pending'"
    )
    # Supports the worker's sweep for rows stalled in pending/processing.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_question_attempt_transcription_status "
        "ON question_attempt (transcription_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_question_attempt_transcription_status")
    op.execute("ALTER TABLE question_attempt DROP COLUMN IF EXISTS transcription_updated_at")
    op.execute("ALTER TABLE question_attempt DROP COLUMN IF EXISTS transcription_error")
    op.execute("ALTER TABLE question_attempt DROP COLUMN IF EXISTS transcription_status")
