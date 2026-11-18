"""persist verified FA-1 maximum-mark evidence

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-10-23 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n5o6p7q8r9s0'
down_revision: Union[str, None] = 'm4n5o6p7q8r9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'student_subject_academic_records',
        sa.Column('fa1_maximum_marks', sa.Float(), nullable=True),
    )
    op.add_column(
        'student_subject_academic_records',
        sa.Column('fa1_maximum_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'student_subject_academic_record_history',
        sa.Column('fa1_maximum_marks', sa.Float(), nullable=True),
    )
    op.add_column(
        'student_subject_academic_record_history',
        sa.Column('fa1_maximum_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('student_subject_academic_record_history', 'fa1_maximum_verified')
    op.drop_column('student_subject_academic_record_history', 'fa1_maximum_marks')
    op.drop_column('student_subject_academic_records', 'fa1_maximum_verified')
    op.drop_column('student_subject_academic_records', 'fa1_maximum_marks')
