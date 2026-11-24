
"""add student branch and cgpa fields

Revision ID: 9e16540daeca
Revises: 01b84d614f95
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9e16540daeca'
down_revision: Union[str, None] = '01b84d614f95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('students') as batch_op:
        batch_op.add_column(
            sa.Column('branch', sa.String(length=80), nullable=True)
        )
        batch_op.add_column(
            sa.Column('previous_cgpa', sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('current_cgpa', sa.Float(), nullable=True)
        )
        batch_op.alter_column(
            'previous_gpa',
            existing_type=sa.Float(),
            nullable=True,
        )
        batch_op.alter_column(
            'current_gpa',
            existing_type=sa.Float(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('students') as batch_op:
        batch_op.alter_column(
            'previous_gpa',
            existing_type=sa.Float(),
            nullable=False,
        )
        batch_op.alter_column(
            'current_gpa',
            existing_type=sa.Float(),
            nullable=False,
        )
        batch_op.drop_column('current_cgpa')
        batch_op.drop_column('previous_cgpa')
        batch_op.drop_column('branch')
