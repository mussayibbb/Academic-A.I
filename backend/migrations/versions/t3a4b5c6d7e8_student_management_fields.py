"""add student semester and data provenance

Revision ID: t3a4b5c6d7e8
Revises: s2t3u4v5w6x7
Create Date: 2026-11-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't3a4b5c6d7e8'
down_revision: Union[str, None] = 's2t3u4v5w6x7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('students', sa.Column('semester', sa.String(length=40), nullable=True))
    op.add_column(
        'students',
        sa.Column('data_origin', sa.String(length=32), nullable=False, server_default='LEGACY / UNVERIFIED'),
    )
    op.create_index('ix_students_semester', 'students', ['semester'], unique=False)
    op.add_column(
        'academic_data_import_batches',
        sa.Column('source_provenance', sa.JSON(), nullable=False, server_default='{}'),
    )
def downgrade() -> None:
    op.drop_column('academic_data_import_batches', 'source_provenance')
    op.drop_index('ix_students_semester', table_name='students')
    op.drop_column('students', 'data_origin')
    op.drop_column('students', 'semester')
