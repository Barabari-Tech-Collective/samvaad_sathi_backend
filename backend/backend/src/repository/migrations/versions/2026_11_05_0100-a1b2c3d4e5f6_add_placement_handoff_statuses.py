"""Add shared_with_placement and accepted_in_placement handoff statuses.

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e0
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f4a5b6c7d8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("chk_bulk_ats_candidate_handoff_status", "bulk_ats_candidate", type_="check")
    op.create_check_constraint(
        "chk_bulk_ats_candidate_handoff_status",
        "bulk_ats_candidate",
        "handoff_status IN ('screened', 'in_program_review', 'approved_for_placement', 'shared_with_placement', 'accepted_in_placement', 'rejected', 'needs_info', 'referred_to_hr', 'archived')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_bulk_ats_candidate_handoff_status", "bulk_ats_candidate", type_="check")
    op.create_check_constraint(
        "chk_bulk_ats_candidate_handoff_status",
        "bulk_ats_candidate",
        "handoff_status IN ('screened', 'in_program_review', 'approved_for_placement', 'referred_to_hr', 'archived')",
    )

