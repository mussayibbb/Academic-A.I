"""persist evidence-linked recovery adaptation decisions

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-15 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recovery_adaptation_decisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('roadmap_version_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('trigger_event_id', sa.Integer(), nullable=False),
        sa.Column('decision_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=40), nullable=False),
        sa.Column('recommended_action', sa.Text(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('evidence_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['roadmap_version_id'], ['support_plan_versions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['recovery_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trigger_event_id'], ['recovery_task_events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trigger_event_id', name='uq_recovery_adaptation_trigger_event'),
    )
    op.create_index(
        'ix_recovery_adaptation_student_created',
        'recovery_adaptation_decisions',
        ['student_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_recovery_adaptation_decisions_roadmap_version_id',
        'recovery_adaptation_decisions',
        ['roadmap_version_id'],
        unique=False,
    )
    op.create_index(
        'ix_recovery_adaptation_decisions_task_id',
        'recovery_adaptation_decisions',
        ['task_id'],
        unique=False,
    )
    op.create_index(
        'ix_recovery_adaptation_decisions_decision_type',
        'recovery_adaptation_decisions',
        ['decision_type'],
        unique=False,
    )
    op.create_index(
        'ix_recovery_adaptation_decisions_status',
        'recovery_adaptation_decisions',
        ['status'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_recovery_adaptation_decisions_status', table_name='recovery_adaptation_decisions')
    op.drop_index('ix_recovery_adaptation_decisions_decision_type', table_name='recovery_adaptation_decisions')
    op.drop_index('ix_recovery_adaptation_decisions_task_id', table_name='recovery_adaptation_decisions')
    op.drop_index('ix_recovery_adaptation_decisions_roadmap_version_id', table_name='recovery_adaptation_decisions')
    op.drop_index('ix_recovery_adaptation_student_created', table_name='recovery_adaptation_decisions')
    op.drop_table('recovery_adaptation_decisions')
