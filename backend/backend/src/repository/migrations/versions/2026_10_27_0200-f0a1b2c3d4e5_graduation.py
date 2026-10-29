"""Human placement/course verification and independent graduation approval."""
from alembic import op
import sqlalchemy as sa
revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("graduation",
        sa.Column("student_id",sa.Integer(),sa.ForeignKey("user.id"),primary_key=True),
        sa.Column("course_id",sa.String(128),primary_key=True),
        sa.Column("version",sa.Integer(),nullable=False),sa.Column("state",sa.String(20),nullable=False),
        sa.Column("course_evidence",sa.JSON()),sa.Column("placement_evidence",sa.JSON()),sa.Column("approval",sa.JSON()),
        sa.CheckConstraint("state IN ('draft','ready','graduated')",name="ck_graduation_state"))
    op.create_table("graduation_event",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("student_id",sa.Integer(),sa.ForeignKey("user.id"),nullable=False),
        sa.Column("course_id",sa.String(128),nullable=False),sa.Column("version",sa.Integer(),nullable=False),
        sa.Column("actor_id",sa.Integer(),sa.ForeignKey("user.id"),nullable=False),
        sa.Column("action",sa.String(32),nullable=False),sa.Column("reason",sa.String(1000),nullable=False),sa.Column("snapshot",sa.JSON(),nullable=False),
        sa.UniqueConstraint("student_id","course_id","version",name="uq_graduation_event_version"))
    op.create_index("ix_graduation_event_student_id","graduation_event",["student_id"])
    op.execute("CREATE FUNCTION prevent_graduation_event_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Graduation audit history is append-only'; END $$")
    op.execute("CREATE TRIGGER graduation_event_immutable BEFORE UPDATE OR DELETE ON graduation_event FOR EACH ROW EXECUTE FUNCTION prevent_graduation_event_change()")

def downgrade():
    op.drop_table("graduation_event")
    op.execute("DROP FUNCTION prevent_graduation_event_change()")
    op.drop_table("graduation")
