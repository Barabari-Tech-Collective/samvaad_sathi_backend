"""Seal published templates and version AI/manual draft authoring."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a9c0d1e2f3a4"
down_revision = "f8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("generation_result_count", sa.Integer()),
        ("authoring_mode", sa.String(16)),
        ("published_at", sa.DateTime(timezone=True)),
        ("publication_snapshot", postgresql.JSONB()),
        ("parent_profile_id", sa.Integer()),
        ("generation_token", sa.String(64)),
        ("generation_state", sa.String(32)),
    ]:
        op.add_column("job_profile", sa.Column(name, kind, nullable=True))
    op.add_column(
        "job_profile",
        sa.Column("content_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_job_profile_parent_publication",
        "job_profile",
        "job_profile",
        ["parent_profile_id"],
        ["id"],
    )
    # Capture what is actually published; never infer historical AI/manual provenance.
    op.execute(
        """UPDATE job_profile p SET published_at=COALESCE(p.updated_at,p.created_at), publication_snapshot=jsonb_build_object('version',1,'profile',to_jsonb(p)-'publication_snapshot','questions',COALESCE((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.id) FROM job_profile_question q WHERE q.job_profile_id=p.id),'[]'::jsonb)) WHERE p.status='published'"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION ss_guard_publication() RETURNS trigger AS $$ BEGIN
      IF OLD.status='published' OR OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Published templates are immutable; create a new draft copy' USING ERRCODE='23514';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        "CREATE TRIGGER ss_guard_publication BEFORE UPDATE OR DELETE ON job_profile FOR EACH ROW EXECUTE FUNCTION ss_guard_publication()"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION ss_guard_published_question() RETURNS trigger AS $$ DECLARE parent_id integer; frozen boolean; BEGIN
      IF TG_OP IN ('UPDATE','DELETE') THEN
        parent_id:=OLD.job_profile_id;
        SELECT (status='published' OR published_at IS NOT NULL) INTO frozen FROM job_profile WHERE id=parent_id FOR UPDATE;
        IF frozen THEN RAISE EXCEPTION 'Published questions are immutable' USING ERRCODE='23514'; END IF;
      END IF;
      IF TG_OP IN ('INSERT','UPDATE') THEN
        parent_id:=NEW.job_profile_id;
        SELECT (status='published' OR published_at IS NOT NULL) INTO frozen FROM job_profile WHERE id=parent_id FOR UPDATE;
        IF frozen THEN RAISE EXCEPTION 'Published questions are immutable' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END IF;
      RETURN OLD;
    END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        "CREATE TRIGGER ss_guard_published_question BEFORE INSERT OR UPDATE OR DELETE ON job_profile_question FOR EACH ROW EXECUTE FUNCTION ss_guard_published_question()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS ss_guard_published_question ON job_profile_question"
    )
    op.execute("DROP FUNCTION IF EXISTS ss_guard_published_question()")
    op.execute("DROP TRIGGER IF EXISTS ss_guard_publication ON job_profile")
    op.execute("DROP FUNCTION IF EXISTS ss_guard_publication()")
    op.drop_constraint(
        "fk_job_profile_parent_publication", "job_profile", type_="foreignkey"
    )
    for name in (
        "generation_result_count",
        "content_revision",
        "generation_state",
        "generation_token",
        "parent_profile_id",
        "publication_snapshot",
        "published_at",
        "authoring_mode",
    ):
        op.drop_column("job_profile", name)
