"""add structured payload fields for early warning interventions

Revision ID: r1s2t3u4v5w6
Revises: p6q7r8s9t0u1
Create Date: 2026-11-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r1s2t3u4v5w6'
down_revision: Union[str, None] = 'p6q7r8s9t0u1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('interventions') as batch_op:
        batch_op.add_column(sa.Column('type', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('payload', sa.JSON(), nullable=True))
        batch_op.create_index('ix_interventions_type', ['type'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('interventions') as batch_op:
        batch_op.drop_index('ix_interventions_type')
        batch_op.drop_column('payload')
        batch_op.drop_column('type')
