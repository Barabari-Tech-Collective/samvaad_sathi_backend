"""Add conflict-safe per-person feature restrictions without changing role defaults."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "c1e2f3a4b5c6"
down_revision = "b0d1e2f3a4b5"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user", sa.Column("feature_permissions", JSONB, nullable=True))
    op.add_column("user", sa.Column("feature_permissions_version", sa.Integer, nullable=False, server_default="0"))

def downgrade():
    op.drop_column("user", "feature_permissions_version")
    op.drop_column("user", "feature_permissions")
