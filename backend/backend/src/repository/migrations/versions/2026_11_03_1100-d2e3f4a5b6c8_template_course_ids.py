"""Which courses a template is for (empty means every learner)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d2e3f4a5b6c8"
down_revision = "c1d2e3f4a5b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_profile", sa.Column("course_ids", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("job_profile", "course_ids")
