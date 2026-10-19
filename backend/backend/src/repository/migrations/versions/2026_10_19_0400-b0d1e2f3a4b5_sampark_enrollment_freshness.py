"""Track authoritative Sampark enrollment freshness without guessing old assignments."""

from alembic import op
import sqlalchemy as sa

revision = "b0d1e2f3a4b5"
down_revision = "a9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user", sa.Column("sampark_student_unique_id", sa.String(128), nullable=True)
    )
    op.create_unique_constraint(
        "uq_user_sampark_student_unique_id", "user", ["sampark_student_unique_id"]
    )
    op.add_column(
        "user",
        sa.Column("enrollment_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("user", sa.Column("enrollment_source", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("user", "enrollment_source")
    op.drop_column("user", "enrollment_observed_at")
    op.drop_constraint("uq_user_sampark_student_unique_id", "user", type_="unique")
    op.drop_column("user", "sampark_student_unique_id")
