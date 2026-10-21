"""Bind verified central identities independently of legacy student identifiers."""

from alembic import op
import sqlalchemy as sa

revision = "d2f3a4b5c6d7"
down_revision = "c1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user", sa.Column("central_identity_id", sa.String(128), nullable=True)
    )
    op.create_unique_constraint(
        "uq_user_central_identity_id", "user", ["central_identity_id"]
    )


def downgrade():
    op.drop_constraint("uq_user_central_identity_id", "user", type_="unique")
    op.drop_column("user", "central_identity_id")
