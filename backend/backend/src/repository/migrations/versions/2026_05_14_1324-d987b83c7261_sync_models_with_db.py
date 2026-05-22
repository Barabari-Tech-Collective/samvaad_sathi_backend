"""sync_models_with_db — uploaded_file table (removed, now a no-op)

Revision ID: d987b83c7261
Revises: pacing_practice_001
Create Date: 2026-05-14 13:24:10.997914

The uploaded_file table was created only in the feature/roles-page-api branch.
The UploadedFile model has been removed as part of the stateless upload refactor.
This migration is now a no-op; the upgrade creates nothing and the downgrade is safe.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd987b83c7261'
down_revision = 'pacing_practice_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # uploaded_file table removed — no-op.
    pass


def downgrade() -> None:
    # Nothing was created in upgrade, so nothing to drop.
    pass

