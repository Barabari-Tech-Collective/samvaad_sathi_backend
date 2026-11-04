"""Formal opportunity selection revisions, decisions, and explicit release."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c3d4e5f6a7b9"
down_revision = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opportunity_selection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("rules", JSONB(), nullable=False),
        sa.Column("snapshot", JSONB()),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "locked_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "released_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")
        ),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_key", sa.String(36)),
        sa.Column("release_reason", sa.Text()),
        sa.UniqueConstraint(
            "opportunity_id", "revision", name="uq_opportunity_selection_revision"
        ),
        sa.UniqueConstraint("release_key", name="uq_selection_release_key"),
        sa.CheckConstraint(
            "status IN ('draft', 'locked', 'released')", name="ck_selection_status"
        ),
        sa.CheckConstraint(
            "version >= 1 AND revision >= 1", name="ck_selection_versions"
        ),
    )
    op.create_index(
        "ix_opportunity_selection_opportunity_id",
        "opportunity_selection",
        ["opportunity_id"],
    )
    op.create_table(
        "opportunity_selection_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "selection_id",
            sa.Integer(),
            sa.ForeignKey("opportunity_selection.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recommendation", sa.String(24)),
        sa.Column("recommendation_reason", sa.Text()),
        sa.Column(
            "recommended_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
        ),
        sa.Column("recommended_at", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.String(16)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("student_feedback", sa.Text()),
        sa.Column("next_step", sa.Text()),
        sa.Column(
            "decided_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_snapshot", JSONB()),
        sa.UniqueConstraint("selection_id", "student_id", name="uq_selection_student"),
        sa.CheckConstraint(
            "recommendation IS NULL OR recommendation IN ('include', 'exclude', 'needs_evidence')",
            name="ck_selection_recommendation",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('include', 'exclude')",
            name="ck_selection_decision",
        ),
    )
    for c in ("selection_id", "student_id"):
        op.create_index(
            f"ix_opportunity_selection_entry_{c}", "opportunity_selection_entry", [c]
        )
    op.execute("""
        CREATE FUNCTION protect_opportunity_selection() RETURNS trigger AS $$
        BEGIN
          IF OLD.status IN ('locked','released') THEN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'locked selection is immutable'; END IF;
            IF OLD.status = 'released' OR
               (to_jsonb(NEW) - ARRAY['status','version','released_by','released_at','release_key','release_reason'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','version','released_by','released_at','release_key','release_reason']) OR
               NEW.status <> 'released' OR NEW.version <> OLD.version + 1 OR
               NEW.released_at IS NULL OR NEW.released_by IS NULL OR NEW.release_key IS NULL
            THEN RAISE EXCEPTION 'locked selection is immutable'; END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER opportunity_selection_immutable BEFORE UPDATE OR DELETE ON opportunity_selection
        FOR EACH ROW EXECUTE FUNCTION protect_opportunity_selection();
    """)
    op.execute("""
        CREATE FUNCTION protect_opportunity_selection_entry() RETURNS trigger AS $$
        DECLARE parent_status text;
        BEGIN
          IF TG_OP IN ('UPDATE','DELETE') THEN
            SELECT status INTO parent_status FROM opportunity_selection WHERE id=OLD.selection_id FOR UPDATE;
            IF parent_status IN ('locked','released') THEN RAISE EXCEPTION 'locked selection evidence is immutable'; END IF;
          END IF;
          IF TG_OP IN ('INSERT','UPDATE') THEN
            SELECT status INTO parent_status FROM opportunity_selection WHERE id=NEW.selection_id FOR UPDATE;
            IF parent_status IN ('locked','released') THEN RAISE EXCEPTION 'locked selection evidence is immutable'; END IF;
            RETURN NEW;
          END IF;
          RETURN OLD;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER opportunity_selection_entry_immutable BEFORE INSERT OR UPDATE OR DELETE ON opportunity_selection_entry
        FOR EACH ROW EXECUTE FUNCTION protect_opportunity_selection_entry();
    """)


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS opportunity_selection_entry_immutable ON opportunity_selection_entry"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_opportunity_selection_entry()")
    op.execute(
        "DROP TRIGGER IF EXISTS opportunity_selection_immutable ON opportunity_selection"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_opportunity_selection()")
    op.drop_table("opportunity_selection_entry")
    op.drop_table("opportunity_selection")
