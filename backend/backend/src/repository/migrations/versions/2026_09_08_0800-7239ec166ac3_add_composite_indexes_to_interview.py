"""Add composite indexes to Interview for hot lookup/pagination queries

Revision ID: 7239ec166ac3
Revises: 973b34efacf4
Create Date: 2026-09-08 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7239ec166ac3'
down_revision = '973b34efacf4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Serves InterviewCRUDRepository.get_active_by_user: WHERE user_id = ? AND
    # status = ? ORDER BY id DESC, called on every interview start/resume.
    op.create_index(
        'ix_interview_user_id_status_id',
        'interview',
        ['user_id', 'status', 'id'],
        unique=False,
    )
    # Serves list_by_user_cursor / list_by_user_cursor_with_summary: WHERE
    # user_id = ? [AND id < cursor_id] ORDER BY id DESC, the interview history
    # pagination queries.
    op.create_index(
        'ix_interview_user_id_id',
        'interview',
        ['user_id', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_interview_user_id_id', table_name='interview')
    op.drop_index('ix_interview_user_id_status_id', table_name='interview')
