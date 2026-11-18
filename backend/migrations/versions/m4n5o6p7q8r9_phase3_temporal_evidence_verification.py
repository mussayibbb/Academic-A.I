"""track timezone-verified Phase 3 temporal evidence

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-10-22 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm4n5o6p7q8r9'
down_revision: Union[str, None] = 'l3m4n5o6p7q8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'student_subject_academic_records',
        sa.Column('temporal_evidence_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'academic_risk_outcomes',
        sa.Column('temporal_evidence_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('academic_risk_outcomes', 'temporal_evidence_verified')
    op.drop_column('student_subject_academic_records', 'temporal_evidence_verified')
