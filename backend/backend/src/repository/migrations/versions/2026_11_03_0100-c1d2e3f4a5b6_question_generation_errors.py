"""Persist safe staff-facing errors for asynchronous question generation."""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_profile", sa.Column("generation_error", sa.String(length=240), nullable=True))


def downgrade():
    op.drop_column("job_profile", "generation_error")
