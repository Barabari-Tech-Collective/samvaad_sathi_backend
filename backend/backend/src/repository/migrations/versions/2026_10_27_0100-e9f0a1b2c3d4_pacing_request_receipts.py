"""Account-scoped durable pacing creation receipts."""
from alembic import op
import sqlalchemy as sa
revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("pacing_practice_session", sa.Column("client_request_id", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_pacing_user_request", "pacing_practice_session", ["user_id", "client_request_id"])

def downgrade():
    op.drop_constraint("uq_pacing_user_request", "pacing_practice_session", type_="unique")
    op.drop_column("pacing_practice_session", "client_request_id")
