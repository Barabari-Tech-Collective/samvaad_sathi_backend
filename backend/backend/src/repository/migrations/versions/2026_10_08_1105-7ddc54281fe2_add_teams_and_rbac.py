"""add_teams_and_rbac

Revision ID: 7ddc54281fe2
Revises: 9b6ce6018b3f
Create Date: 2026-10-08 11:05:25.723041

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7ddc54281fe2"
down_revision = "9b6ce6018b3f"
branch_labels = None
depends_on = None


def _require_columns(inspector, table_name: str, required: set[str]) -> None:
    present = {column["name"] for column in inspector.get_columns(table_name)}
    missing = required - present
    if missing:
        raise RuntimeError(
            f"Existing {table_name!r} table is incomplete; missing columns: "
            f"{sorted(missing)}"
        )


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(
        index["name"] == index_name for index in inspector.get_indexes(table_name)
    )


def _has_unique_team_members(inspector) -> bool:
    return any(
        constraint.get("column_names") == ["team_id", "user_id"]
        for constraint in inspector.get_unique_constraints("team_member")
    )


def _require_foreign_key(
    inspector,
    *,
    constrained_column: str,
    referred_table: str,
) -> None:
    matches = any(
        foreign_key.get("constrained_columns") == [constrained_column]
        and foreign_key.get("referred_table") == referred_table
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in inspector.get_foreign_keys("team_member")
    )
    if not matches:
        raise RuntimeError(
            "Existing 'team_member' table is missing the required foreign key "
            f"from {constrained_column!r} to {referred_table!r}.id"
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "team" not in tables:
        op.create_table(
            "team",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'active'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_team_name"), "team", ["name"], unique=False)
    else:
        _require_columns(
            inspector,
            "team",
            {"id", "name", "description", "status", "created_at", "updated_at"},
        )
        if not _has_index(inspector, "team", "ix_team_name"):
            op.create_index(op.f("ix_team_name"), "team", ["name"], unique=False)

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "team_member" not in tables:
        op.create_table(
            "team_member",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("access_role", sa.String(length=128), nullable=False),
            sa.Column(
                "purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column("domain", sa.String(length=128), nullable=True),
            sa.Column(
                "colleges", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'active'"),
                nullable=False,
            ),
            sa.Column("removed_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_team_member_team_id"), "team_member", ["team_id"], unique=False
        )
        op.create_index(
            op.f("ix_team_member_user_id"), "team_member", ["user_id"], unique=False
        )
        op.create_unique_constraint(
            "uq_team_member_team_id_user_id", "team_member", ["team_id", "user_id"]
        )
    else:
        _require_columns(
            inspector,
            "team_member",
            {
                "id",
                "team_id",
                "user_id",
                "access_role",
                "purposes",
                "domain",
                "colleges",
                "status",
                "removed_date",
                "created_at",
                "updated_at",
            },
        )
        _require_foreign_key(
            inspector,
            constrained_column="team_id",
            referred_table="team",
        )
        _require_foreign_key(
            inspector,
            constrained_column="user_id",
            referred_table="user",
        )
        if not _has_index(inspector, "team_member", "ix_team_member_team_id"):
            op.create_index(
                op.f("ix_team_member_team_id"),
                "team_member",
                ["team_id"],
                unique=False,
            )
        if not _has_index(inspector, "team_member", "ix_team_member_user_id"):
            op.create_index(
                op.f("ix_team_member_user_id"),
                "team_member",
                ["user_id"],
                unique=False,
            )
        if not _has_unique_team_members(inspector):
            op.create_unique_constraint(
                "uq_team_member_team_id_user_id",
                "team_member",
                ["team_id", "user_id"],
            )

    user_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("user")
    }
    if "is_super_admin" not in user_columns:
        op.add_column(
            "user",
            sa.Column(
                "is_super_admin",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("user", "is_super_admin")
    op.drop_constraint("uq_team_member_team_id_user_id", "team_member", type_="unique")
    op.drop_index(op.f("ix_team_member_user_id"), table_name="team_member")
    op.drop_index(op.f("ix_team_member_team_id"), table_name="team_member")
    op.drop_table("team_member")
    op.drop_index(op.f("ix_team_name"), table_name="team")
    op.drop_table("team")
    # ### end Alembic commands ###
