"""Programme-scoped learner roster independent of courses (ADR-2026-11-03)."""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "programme_roster_member",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("programme_scope", sa.String(128), nullable=False, server_default="default"),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("external_learner_id", sa.String(128), nullable=True),
        sa.Column("import_batch_id", sa.String(64), nullable=True),
        sa.Column("invite_status", sa.String(32), nullable=False, server_default="invited"),
        sa.Column("claimed_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("programme_scope", "email", name="uq_programme_roster_member_scope_email"),
    )
    op.create_index("ix_programme_roster_member_programme_scope", "programme_roster_member", ["programme_scope"])
    op.create_index("ix_programme_roster_member_email", "programme_roster_member", ["email"])
    op.create_index("ix_programme_roster_member_import_batch_id", "programme_roster_member", ["import_batch_id"])
    op.create_index("ix_programme_roster_member_claimed_user_id", "programme_roster_member", ["claimed_user_id"])


def downgrade():
    op.drop_index("ix_programme_roster_member_claimed_user_id", table_name="programme_roster_member")
    op.drop_index("ix_programme_roster_member_import_batch_id", table_name="programme_roster_member")
    op.drop_index("ix_programme_roster_member_email", table_name="programme_roster_member")
    op.drop_index("ix_programme_roster_member_programme_scope", table_name="programme_roster_member")
    op.drop_table("programme_roster_member")

