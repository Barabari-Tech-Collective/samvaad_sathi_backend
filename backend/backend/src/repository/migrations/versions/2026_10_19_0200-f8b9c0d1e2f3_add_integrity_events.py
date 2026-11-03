"""Store bounded browser observations without automatic penalties."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f8b9c0d1e2f3"
down_revision = "e7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "interview", sa.Column("integrity_events", postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("interview", "integrity_events")
