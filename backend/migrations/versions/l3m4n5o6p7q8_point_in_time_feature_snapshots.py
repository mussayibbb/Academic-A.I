"""add immutable point-in-time production feature snapshots

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6
Create Date: 2026-10-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l3m4n5o6p7q8'
down_revision: Union[str, None] = 'k2l3m4n5o6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'student_period_feature_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('academic_period', sa.String(length=30), nullable=False),
        sa.Column('feature_values', sa.JSON(), nullable=False),
        sa.Column('feature_observed_at', sa.JSON(), nullable=False),
        sa.Column('import_batch_id', sa.Integer(), nullable=False),
        sa.Column('source_row_number', sa.Integer(), nullable=False),
        sa.Column('source_row_checksum', sa.String(length=64), nullable=False),
        sa.Column('source_filename', sa.String(length=255), nullable=False),
        sa.Column('source_checksum', sa.String(length=64), nullable=False),
        sa.Column('recorded_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(['import_batch_id'], ['academic_data_import_batches.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('import_batch_id', 'source_row_number', name='uq_student_feature_snapshot_batch_row'),
        sa.UniqueConstraint('student_id', 'academic_period', name='uq_student_feature_snapshot_student_period'),
    )
    op.create_index(
        'ix_student_period_feature_snapshots_student_id',
        'student_period_feature_snapshots',
        ['student_id'],
    )
    op.create_index(
        'ix_student_period_feature_snapshots_academic_period',
        'student_period_feature_snapshots',
        ['academic_period'],
    )
    op.create_index(
        'ix_student_period_feature_snapshots_import_batch_id',
        'student_period_feature_snapshots',
        ['import_batch_id'],
    )
    op.create_index(
        'ix_student_period_feature_snapshots_recorded_by_user_id',
        'student_period_feature_snapshots',
        ['recorded_by_user_id'],
    )


def downgrade() -> None:
    connection = op.get_bind()
    snapshot_count = connection.execute(sa.text(
        'SELECT COUNT(*) FROM student_period_feature_snapshots'
    )).scalar_one()
    if snapshot_count:
        raise RuntimeError(
            'Cannot downgrade point-in-time feature snapshots while any source evidence remains.'
        )

    op.drop_index(
        'ix_student_period_feature_snapshots_recorded_by_user_id',
        table_name='student_period_feature_snapshots',
    )
    op.drop_index(
        'ix_student_period_feature_snapshots_import_batch_id',
        table_name='student_period_feature_snapshots',
    )
    op.drop_index(
        'ix_student_period_feature_snapshots_academic_period',
        table_name='student_period_feature_snapshots',
    )
    op.drop_index(
        'ix_student_period_feature_snapshots_student_id',
        table_name='student_period_feature_snapshots',
    )
    op.drop_table('student_period_feature_snapshots')
