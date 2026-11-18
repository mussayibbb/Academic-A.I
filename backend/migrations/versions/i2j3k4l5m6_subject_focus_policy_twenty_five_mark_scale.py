"""append the authoritative 12 out of 25 subject-focus policy

Revision ID: i2j3k4l5m6
Revises: h1i2j3k4l5
Create Date: 2026-10-20 00:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i2j3k4l5m6'
down_revision: Union[str, None] = 'h1i2j3k4l5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    current = bind.execute(sa.text(
        'SELECT threshold_score, threshold_maximum '
        'FROM subject_focus_policies ORDER BY version DESC LIMIT 1'
    )).first()
    if (
        current is not None
        and current.threshold_score == 12
        and current.threshold_maximum == 25
    ):
        return
    next_version = bind.execute(sa.text(
        'SELECT COALESCE(MAX(version), 0) + 1 FROM subject_focus_policies'
    )).scalar_one()
    bind.execute(sa.text(
        'INSERT INTO subject_focus_policies '
        '(version, threshold_score, threshold_maximum, actor_user_id, change_reason, created_at) '
        'VALUES (:version, :score, :maximum, NULL, :reason, :created_at)'
    ), {
        'version': next_version,
        'score': 12.0,
        'maximum': 25.0,
        'reason': 'Institutional policy correction: strictly below 12 out of 25 after proportional normalization.',
        'created_at': datetime.utcnow(),
    })


def downgrade() -> None:
    raise RuntimeError(
        'This policy revision is append-only; downgrade would remove institutional policy history.'
    )
