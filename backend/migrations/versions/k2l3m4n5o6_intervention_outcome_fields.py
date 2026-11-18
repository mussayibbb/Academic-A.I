"""add the intervention outcome fields declared by the current ORM

Revision ID: k2l3m4n5o6
Revises: j3k4l5m6n7
Create Date: 2026-10-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k2l3m4n5o6'
down_revision: Union[str, None] = 'j3k4l5m6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('interventions') as batch_op:
        batch_op.add_column(sa.Column('outcome', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('outcome_notes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('observed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    populated_rows = connection.execute(sa.text(
        'SELECT COUNT(*) FROM interventions '
        'WHERE outcome IS NOT NULL OR outcome_notes IS NOT NULL OR observed_at IS NOT NULL'
    )).scalar_one()
    if populated_rows:
        raise RuntimeError(
            'Cannot downgrade intervention outcome fields while any of them contain data.'
        )

    with op.batch_alter_table('interventions') as batch_op:
        batch_op.drop_column('observed_at')
        batch_op.drop_column('outcome_notes')
        batch_op.drop_column('outcome')
