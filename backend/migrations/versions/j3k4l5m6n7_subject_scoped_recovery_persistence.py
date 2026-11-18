"""add subject scope and schedule provenance to recovery records

Revision ID: j3k4l5m6n7
Revises: i2j3k4l5m6
Create Date: 2026-10-22 00:00:00.000000
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j3k4l5m6n7'
down_revision: Union[str, None] = 'i2j3k4l5m6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('support_plan_versions') as batch_op:
        batch_op.add_column(sa.Column('subject_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            'subject_scope_review_required',
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ))
        batch_op.add_column(sa.Column('blueprint_group_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column(
            'is_blueprint_group',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ))
        batch_op.add_column(sa.Column('source_blueprint_plan_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_support_plan_versions_subject_id_subjects',
            'subjects',
            ['subject_id'],
            ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_foreign_key(
            'fk_support_plan_versions_source_blueprint_plan_id',
            'support_plan_versions',
            ['source_blueprint_plan_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index('ix_support_plan_versions_subject_id', ['subject_id'])
        batch_op.create_index('ix_support_plan_versions_blueprint_group_id', ['blueprint_group_id'])
        batch_op.create_index('ix_support_plan_versions_source_blueprint_plan_id', ['source_blueprint_plan_id'])

    with op.batch_alter_table('recovery_tasks') as batch_op:
        batch_op.add_column(sa.Column('subject_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            'subject_scope_review_required',
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ))
        batch_op.add_column(sa.Column('scheduled_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('source_blueprint_plan_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('source_schedule_entry_key', sa.String(length=100), nullable=True))
        batch_op.create_foreign_key(
            'fk_recovery_tasks_subject_id_subjects',
            'subjects',
            ['subject_id'],
            ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_foreign_key(
            'fk_recovery_tasks_source_blueprint_plan_id',
            'support_plan_versions',
            ['source_blueprint_plan_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index('ix_recovery_tasks_subject_id', ['subject_id'])
        batch_op.create_index('ix_recovery_tasks_scheduled_date', ['scheduled_date'])
        batch_op.create_index('ix_recovery_tasks_source_blueprint_plan_id', ['source_blueprint_plan_id'])
        batch_op.create_unique_constraint(
            'uq_recovery_task_blueprint_schedule_entry',
            ['source_blueprint_plan_id', 'source_schedule_entry_key'],
        )

    with op.batch_alter_table('recovery_task_events') as batch_op:
        batch_op.add_column(sa.Column('subject_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_recovery_task_events_subject_id_subjects',
            'subjects',
            ['subject_id'],
            ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_index('ix_recovery_task_events_subject_id', ['subject_id'])

    connection = op.get_bind()
    plans = connection.execute(sa.text(
        'SELECT id, plan_json, subject_id, is_blueprint_group '
        'FROM support_plan_versions'
    )).mappings().all()
    plan_subject_ids: dict[int, int] = {}
    for row in plans:
        if row['subject_id'] is not None:
            plan_subject_ids[row['id']] = row['subject_id']
            continue
        if row['is_blueprint_group']:
            continue
        try:
            plan_json = json.loads(row['plan_json'] or '{}')
        except (TypeError, ValueError):
            continue
        definitions = plan_json.get('tasks')
        if not isinstance(definitions, list) or not definitions:
            continue
        if not all(
            isinstance(item, dict)
            and isinstance(item.get('subject_id'), int)
            and not isinstance(item.get('subject_id'), bool)
            and item['subject_id'] > 0
            for item in definitions
        ):
            continue
        subject_ids = {item['subject_id'] for item in definitions}
        if len(subject_ids) == 1:
            subject_id = next(iter(subject_ids))
            connection.execute(sa.text(
                'UPDATE support_plan_versions '
                'SET subject_id = :subject_id, subject_scope_review_required = 0 '
                'WHERE id = :plan_id'
            ), {'subject_id': subject_id, 'plan_id': row['id']})
            plan_subject_ids[row['id']] = subject_id

    task_rows = connection.execute(sa.text(
        'SELECT id, roadmap_version_id, task_key, subject_id '
        'FROM recovery_tasks'
    )).mappings().all()
    plan_json_by_id = {row['id']: row['plan_json'] for row in plans}
    for row in task_rows:
        if row['subject_id'] is not None:
            continue
        parent_subject_id = plan_subject_ids.get(row['roadmap_version_id'])
        try:
            plan_json = json.loads(plan_json_by_id.get(row['roadmap_version_id']) or '{}')
        except (TypeError, ValueError):
            plan_json = {}
        definitions = plan_json.get('tasks')
        if not isinstance(definitions, list):
            continue
        matches = [
            item for item in definitions
            if isinstance(item, dict) and item.get('task_key') == row['task_key']
        ]
        if len(matches) != 1:
            continue
        definition_subject_id = matches[0].get('subject_id')
        if definition_subject_id is not None and (
            not isinstance(definition_subject_id, int)
            or isinstance(definition_subject_id, bool)
            or definition_subject_id <= 0
            or (parent_subject_id is not None and definition_subject_id != parent_subject_id)
        ):
            continue
        subject_id = definition_subject_id or parent_subject_id
        if subject_id is None:
            continue
        connection.execute(sa.text(
            'UPDATE recovery_tasks '
            'SET subject_id = :subject_id, subject_scope_review_required = 0 '
            'WHERE id = :task_id'
        ), {'subject_id': subject_id, 'task_id': row['id']})

    op.execute(sa.text(
        'UPDATE recovery_task_events '
        'SET subject_id = (SELECT recovery_tasks.subject_id '
        'FROM recovery_tasks WHERE recovery_tasks.id = recovery_task_events.task_id) '
        'WHERE subject_id IS NULL'
    ))


def downgrade() -> None:
    raise RuntimeError(
        'This migration is additive and preserves recovery history; '
        'downgrade would discard subject and schedule provenance.'
    )
