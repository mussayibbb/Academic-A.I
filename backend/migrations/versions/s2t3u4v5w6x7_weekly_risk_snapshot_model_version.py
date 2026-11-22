"""tag weekly risk snapshots with the scoring model version

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-11-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's2t3u4v5w6x7'
down_revision: Union[str, None] = 'r1s2t3u4v5w6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    with op.batch_alter_table('risk_snapshots') as batch_op:
        batch_op.add_column(sa.Column('model_version', sa.String(length=80), nullable=True))
        batch_op.create_index('ix_risk_snapshots_model_version', ['model_version'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('risk_snapshots') as batch_op:
        batch_op.drop_index('ix_risk_snapshots_model_version')
        batch_op.drop_column('model_version')
