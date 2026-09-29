"""Actually create pronunciation_practice table

Revision ID: b7eb7787beee
Revises: daef9f7d4ec2
Create Date: 2026-09-08 11:27:38.696639

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b7eb7787beee'
down_revision = 'daef9f7d4ec2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pronunciation_practice was originally created in October 2025. A later
    # generated migration dropped it on one deployment branch, so this repair
    # revision must support both states: create it when absent, or bring the
    # existing table in line with the current model when replaying from scratch.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'pronunciation_practice' not in inspector.get_table_names():
        op.create_table('pronunciation_practice',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('difficulty', sa.String(length=16), nullable=False),
            sa.Column('words', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        existing_indexes: set[str] = set()
    else:
        op.alter_column('pronunciation_practice', 'difficulty',
                        existing_type=sa.String(length=20),
                        type_=sa.String(length=16),
                        existing_nullable=False)
        op.alter_column('pronunciation_practice', 'status',
                        existing_type=sa.String(length=20),
                        type_=sa.String(length=32),
                        existing_nullable=False)
        existing_indexes = {
            index['name'] for index in inspector.get_indexes('pronunciation_practice')
        }

    indexes = {
        'ix_pronunciation_practice_difficulty': ['difficulty'],
        'ix_pronunciation_practice_status': ['status'],
        'ix_pronunciation_practice_user_id': ['user_id'],
    }
    for name, columns in indexes.items():
        if name not in existing_indexes:
            op.create_index(name, 'pronunciation_practice', columns, unique=False)


def downgrade() -> None:
    # The declared migration chain already had this table before this repair,
    # so downgrading must retain it and only undo the model-alignment changes.
    op.execute('DROP INDEX IF EXISTS ix_pronunciation_practice_status')
    op.execute('DROP INDEX IF EXISTS ix_pronunciation_practice_difficulty')
    op.alter_column('pronunciation_practice', 'status',
                    existing_type=sa.String(length=32),
                    type_=sa.String(length=20),
                    existing_nullable=False)
    op.alter_column('pronunciation_practice', 'difficulty',
                    existing_type=sa.String(length=16),
                    type_=sa.String(length=20),
                    existing_nullable=False)
