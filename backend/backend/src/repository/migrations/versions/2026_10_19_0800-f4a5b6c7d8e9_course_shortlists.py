"""Bind new shortlists to courses; retain untagged legacy rows."""

from alembic import op
import sqlalchemy as sa

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("practice_shortlist", sa.Column("course_id", sa.String(128)))
    op.add_column("practice_shortlist", sa.Column("request_key", sa.String(36)))
    op.add_column("practice_shortlist", sa.Column("request_hash", sa.String(64)))
    op.create_index(
        "ix_practice_shortlist_course_id", "practice_shortlist", ["course_id"]
    )
    op.create_unique_constraint(
        "uq_practice_shortlist_request",
        "practice_shortlist",
        ["created_by", "request_key"],
    )
    op.execute(
        """CREATE FUNCTION protect_course_shortlist() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.course_id IS NOT NULL AND OLD.status = 'locked' THEN
        RAISE EXCEPTION 'Finalized course shortlists are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$"""
    )
    op.execute(
        """CREATE TRIGGER protect_course_shortlist BEFORE UPDATE OR DELETE ON practice_shortlist
                  FOR EACH ROW EXECUTE FUNCTION protect_course_shortlist()"""
    )
    op.execute(
        """CREATE FUNCTION protect_course_shortlist_entry() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE old_id integer; new_id integer;
    BEGIN
      IF TG_OP != 'INSERT' THEN old_id := OLD.shortlist_id; END IF;
      IF TG_OP != 'DELETE' THEN new_id := NEW.shortlist_id; END IF;
      -- Preserve the existing student-account FK cascade. The account row is
      -- already gone when its referential action removes private decisions.
      IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM "user" WHERE id = OLD.student_id) THEN
        RETURN OLD;
      END IF;
      PERFORM id FROM practice_shortlist WHERE id IN (old_id, new_id) ORDER BY id FOR UPDATE;
      IF EXISTS (SELECT 1 FROM practice_shortlist WHERE id IN (old_id, new_id)
                 AND course_id IS NOT NULL AND status = 'locked') THEN
        RAISE EXCEPTION 'Finalized course shortlist decisions are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$"""
    )
    op.execute(
        """CREATE TRIGGER protect_course_shortlist_entry BEFORE INSERT OR UPDATE OR DELETE ON practice_shortlist_entry
                  FOR EACH ROW EXECUTE FUNCTION protect_course_shortlist_entry()"""
    )


def downgrade():
    op.execute(
        "DROP TRIGGER protect_course_shortlist_entry ON practice_shortlist_entry"
    )
    op.execute("DROP FUNCTION protect_course_shortlist_entry()")
    op.execute("DROP TRIGGER protect_course_shortlist ON practice_shortlist")
    op.execute("DROP FUNCTION protect_course_shortlist()")
    op.drop_constraint(
        "uq_practice_shortlist_request", "practice_shortlist", type_="unique"
    )
    op.drop_index("ix_practice_shortlist_course_id", table_name="practice_shortlist")
    for column in ("request_hash", "request_key", "course_id"):
        op.drop_column("practice_shortlist", column)
