"""persist recovery roadmap lifecycle and task events

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('support_plan_versions') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'))
        batch_op.add_column(sa.Column('evidence_json', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('generation_key', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('approved_by_user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('published_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_support_plan_versions_status', ['status'], unique=False)
        batch_op.create_index('ix_support_plan_versions_approved_by_user_id', ['approved_by_user_id'], unique=False)
        batch_op.create_unique_constraint('uq_support_plan_generation_key', ['student_id', 'generation_key'])
        batch_op.create_foreign_key(
            'fk_support_plan_versions_approved_by_user_id_users',
            'users',
            ['approved_by_user_id'],
            ['id'],
            ondelete='SET NULL',
        )

    op.create_table(
        'recovery_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('roadmap_version_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('task_key', sa.String(length=100), nullable=False),
        sa.Column('subject', sa.String(length=160), nullable=True),
        sa.Column('topic', sa.String(length=160), nullable=True),
        sa.Column('learning_objective', sa.Text(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('activity_type', sa.String(length=40), nullable=False),
        sa.Column('priority', sa.String(length=20), nullable=False, server_default='MEDIUM'),
        sa.Column('source', sa.String(length=30), nullable=False, server_default='AI_PRACTICE'),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('estimated_minutes', sa.Integer(), nullable=False),
        sa.Column('resources', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('completion_criteria', sa.Text(), nullable=False),
        sa.Column('mastery_criteria', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('student_response', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('assessed_score', sa.Float(), nullable=True),
        sa.Column('teacher_feedback', sa.Text(), nullable=True),
        sa.Column('revision_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['roadmap_version_id'], ['support_plan_versions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('roadmap_version_id', 'task_key', name='uq_recovery_task_roadmap_key'),
    )
    op.create_index('ix_recovery_tasks_roadmap_version_id', 'recovery_tasks', ['roadmap_version_id'], unique=False)
    op.create_index('ix_recovery_tasks_student_id', 'recovery_tasks', ['student_id'], unique=False)
    op.create_index('ix_recovery_tasks_scheduled_at', 'recovery_tasks', ['scheduled_at'], unique=False)
    op.create_index('ix_recovery_tasks_status', 'recovery_tasks', ['status'], unique=False)
    op.create_index('ix_recovery_task_student_status_schedule', 'recovery_tasks', ['student_id', 'status', 'scheduled_at'], unique=False)

    op.create_table(
        'recovery_task_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=40), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('idempotency_key', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['recovery_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_recovery_task_event_idempotency'),
    )
    op.create_index('ix_recovery_task_events_task_id', 'recovery_task_events', ['task_id'], unique=False)
    op.create_index('ix_recovery_task_events_student_id', 'recovery_task_events', ['student_id'], unique=False)
    op.create_index('ix_recovery_task_events_actor_user_id', 'recovery_task_events', ['actor_user_id'], unique=False)
    op.create_index('ix_recovery_task_events_event_type', 'recovery_task_events', ['event_type'], unique=False)
    op.create_index('ix_recovery_task_event_task_created', 'recovery_task_events', ['task_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_recovery_task_event_task_created', table_name='recovery_task_events')
    op.drop_index('ix_recovery_task_events_event_type', table_name='recovery_task_events')
    op.drop_index('ix_recovery_task_events_actor_user_id', table_name='recovery_task_events')
    op.drop_index('ix_recovery_task_events_student_id', table_name='recovery_task_events')
    op.drop_index('ix_recovery_task_events_task_id', table_name='recovery_task_events')
    op.drop_table('recovery_task_events')

    op.drop_index('ix_recovery_task_student_status_schedule', table_name='recovery_tasks')
    op.drop_index('ix_recovery_tasks_status', table_name='recovery_tasks')
    op.drop_index('ix_recovery_tasks_scheduled_at', table_name='recovery_tasks')
    op.drop_index('ix_recovery_tasks_student_id', table_name='recovery_tasks')
    op.drop_index('ix_recovery_tasks_roadmap_version_id', table_name='recovery_tasks')
    op.drop_table('recovery_tasks')

    with op.batch_alter_table('support_plan_versions') as batch_op:
        batch_op.drop_constraint('fk_support_plan_versions_approved_by_user_id_users', type_='foreignkey')
        batch_op.drop_constraint('uq_support_plan_generation_key', type_='unique')
        batch_op.drop_index('ix_support_plan_versions_approved_by_user_id')
        batch_op.drop_index('ix_support_plan_versions_status')
        batch_op.drop_column('published_at')
        batch_op.drop_column('approved_at')
        batch_op.drop_column('approved_by_user_id')
        batch_op.drop_column('generation_key')
        batch_op.drop_column('evidence_json')
        batch_op.drop_column('status')
