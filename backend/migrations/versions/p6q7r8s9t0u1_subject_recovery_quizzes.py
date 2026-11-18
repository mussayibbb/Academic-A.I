"""persist subject recovery module quizzes and attempts

Revision ID: p6q7r8s9t0u1
Revises: n5o6p7q8r9s0
Create Date: 2026-11-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p6q7r8s9t0u1'
down_revision: Union[str, None] = 'n5o6p7q8r9s0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'module_library',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('module_no', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('extracted_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='READY'),
        sa.Column('uploaded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('module_no >= 1 AND module_no <= 5', name='ck_module_library_module_no'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['teachers.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'module_no', name='uq_module_library_subject_module'),
    )
    op.create_index('ix_module_library_subject_id', 'module_library', ['subject_id'], unique=False)
    op.create_index('ix_module_library_status', 'module_library', ['status'], unique=False)
    op.create_index('ix_module_library_uploaded_by', 'module_library', ['uploaded_by'], unique=False)
    op.create_index('ix_module_library_subject_status', 'module_library', ['subject_id', 'status'], unique=False)

    op.create_table(
        'recovery_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('source_exam', sa.String(length=20), nullable=False, server_default='FA-1'),
        sa.Column('marks', sa.Float(), nullable=False),
        sa.Column('selected_modules', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('topics_per_day', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('support_plan_version_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'topics_ready', 'quizzes_ready', 'assigned')", name='ck_recovery_plan_status'),
        sa.CheckConstraint('topics_per_day IN (1, 2, 3, 5)', name='ck_recovery_plan_topics_per_day'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['support_plan_version_id'], ['support_plan_versions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'subject_id', 'source_exam', name='uq_recovery_plan_student_subject_exam'),
    )
    op.create_index('ix_recovery_plans_student_id', 'recovery_plans', ['student_id'], unique=False)
    op.create_index('ix_recovery_plans_subject_id', 'recovery_plans', ['subject_id'], unique=False)
    op.create_index('ix_recovery_plans_status', 'recovery_plans', ['status'], unique=False)
    op.create_index('ix_recovery_plans_created_by', 'recovery_plans', ['created_by'], unique=False)
    op.create_index('ix_recovery_plans_support_plan_version_id', 'recovery_plans', ['support_plan_version_id'], unique=False)
    op.create_index('ix_recovery_plan_subject_status', 'recovery_plans', ['subject_id', 'status'], unique=False)

    op.create_table(
        'recovery_topics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('module_no', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('included', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('module_no >= 1 AND module_no <= 5', name='ck_recovery_topic_module_no'),
        sa.ForeignKeyConstraint(['plan_id'], ['recovery_plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', 'module_no', 'name', name='uq_recovery_topic_plan_module_name'),
    )
    op.create_index('ix_recovery_topics_plan_id', 'recovery_topics', ['plan_id'], unique=False)
    op.create_index('ix_recovery_topic_plan_included', 'recovery_topics', ['plan_id', 'included'], unique=False)

    op.create_table(
        'recovery_quizzes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=True),
        sa.Column('quiz_key', sa.String(length=100), nullable=False),
        sa.Column('questions', sa.JSON(), nullable=False),
        sa.Column('max_score', sa.Float(), nullable=False, server_default='10'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['recovery_plans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['topic_id'], ['recovery_topics.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', 'quiz_key', name='uq_recovery_quiz_plan_key'),
    )
    op.create_index('ix_recovery_quizzes_plan_id', 'recovery_quizzes', ['plan_id'], unique=False)
    op.create_index('ix_recovery_quizzes_topic_id', 'recovery_quizzes', ['topic_id'], unique=False)
    op.create_index('ix_recovery_quiz_plan_topic', 'recovery_quizzes', ['plan_id', 'topic_id'], unique=False)

    op.create_table(
        'recovery_generation_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name='ck_recovery_generation_job_status'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['recovery_plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_recovery_generation_job_idempotency'),
    )
    op.create_index('ix_recovery_generation_jobs_plan_id', 'recovery_generation_jobs', ['plan_id'], unique=False)
    op.create_index('ix_recovery_generation_jobs_status', 'recovery_generation_jobs', ['status'], unique=False)
    op.create_index('ix_recovery_generation_jobs_created_by', 'recovery_generation_jobs', ['created_by'], unique=False)
    op.create_index('ix_recovery_generation_job_plan_status', 'recovery_generation_jobs', ['plan_id', 'status'], unique=False)
    op.create_index('ix_recovery_generation_job_actor_created', 'recovery_generation_jobs', ['created_by', 'created_at'], unique=False)

    op.create_table(
        'recovery_generation_rate_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['recovery_generation_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recovery_generation_rate_events_user_id', 'recovery_generation_rate_events', ['user_id'], unique=False)
    op.create_index('ix_recovery_generation_rate_events_job_id', 'recovery_generation_rate_events', ['job_id'], unique=False)
    op.create_index('ix_recovery_generation_rate_events_created_at', 'recovery_generation_rate_events', ['created_at'], unique=False)
    op.create_index(
        'ix_recovery_generation_rate_actor_created',
        'recovery_generation_rate_events',
        ['user_id', 'created_at'],
        unique=False,
    )

    with op.batch_alter_table('recovery_tasks') as batch_op:
        batch_op.add_column(sa.Column('recovery_plan_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('recovery_topic_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('recovery_quiz_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('day_no', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_recovery_tasks_recovery_plan_id_recovery_plans',
            'recovery_plans',
            ['recovery_plan_id'],
            ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_foreign_key(
            'fk_recovery_tasks_recovery_topic_id_recovery_topics',
            'recovery_topics',
            ['recovery_topic_id'],
            ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_foreign_key(
            'fk_recovery_tasks_recovery_quiz_id_recovery_quizzes',
            'recovery_quizzes',
            ['recovery_quiz_id'],
            ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_index('ix_recovery_tasks_recovery_plan_id', ['recovery_plan_id'], unique=False)
        batch_op.create_index('ix_recovery_tasks_recovery_topic_id', ['recovery_topic_id'], unique=False)
        batch_op.create_index('ix_recovery_tasks_recovery_quiz_id', ['recovery_quiz_id'], unique=False)

    op.create_table(
        'quiz_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('max_score', sa.Float(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('score >= 0 AND score <= max_score AND max_score > 0', name='ck_quiz_attempt_score_range'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['recovery_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'student_id', name='uq_quiz_attempt_task_student'),
    )
    op.create_index('ix_quiz_attempts_task_id', 'quiz_attempts', ['task_id'], unique=False)
    op.create_index('ix_quiz_attempts_student_id', 'quiz_attempts', ['student_id'], unique=False)
    op.create_index('ix_quiz_attempts_submitted_at', 'quiz_attempts', ['submitted_at'], unique=False)
    op.create_index('ix_quiz_attempt_student_submitted', 'quiz_attempts', ['student_id', 'submitted_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_quiz_attempt_student_submitted', table_name='quiz_attempts')
    op.drop_index('ix_quiz_attempts_submitted_at', table_name='quiz_attempts')
    op.drop_index('ix_quiz_attempts_student_id', table_name='quiz_attempts')
    op.drop_index('ix_quiz_attempts_task_id', table_name='quiz_attempts')
    op.drop_table('quiz_attempts')

    with op.batch_alter_table('recovery_tasks') as batch_op:
        batch_op.drop_index('ix_recovery_tasks_recovery_topic_id')
        batch_op.drop_index('ix_recovery_tasks_recovery_quiz_id')
        batch_op.drop_index('ix_recovery_tasks_recovery_plan_id')
        batch_op.drop_constraint('fk_recovery_tasks_recovery_quiz_id_recovery_quizzes', type_='foreignkey')
        batch_op.drop_constraint('fk_recovery_tasks_recovery_topic_id_recovery_topics', type_='foreignkey')
        batch_op.drop_constraint('fk_recovery_tasks_recovery_plan_id_recovery_plans', type_='foreignkey')
        batch_op.drop_column('day_no')
        batch_op.drop_column('recovery_quiz_id')
        batch_op.drop_column('recovery_topic_id')
        batch_op.drop_column('recovery_plan_id')

    op.drop_index('ix_recovery_generation_rate_actor_created', table_name='recovery_generation_rate_events')
    op.drop_index('ix_recovery_generation_rate_events_created_at', table_name='recovery_generation_rate_events')
    op.drop_index('ix_recovery_generation_rate_events_job_id', table_name='recovery_generation_rate_events')
    op.drop_index('ix_recovery_generation_rate_events_user_id', table_name='recovery_generation_rate_events')
    op.drop_table('recovery_generation_rate_events')

    for index in (
        'ix_recovery_generation_job_actor_created',
        'ix_recovery_generation_job_plan_status',
        'ix_recovery_generation_jobs_created_by',
        'ix_recovery_generation_jobs_status',
        'ix_recovery_generation_jobs_plan_id',
    ):
        op.drop_index(index, table_name='recovery_generation_jobs')
    op.drop_table('recovery_generation_jobs')

    op.drop_index('ix_recovery_quiz_plan_topic', table_name='recovery_quizzes')
    op.drop_index('ix_recovery_quizzes_topic_id', table_name='recovery_quizzes')
    op.drop_index('ix_recovery_quizzes_plan_id', table_name='recovery_quizzes')
    op.drop_table('recovery_quizzes')

    op.drop_index('ix_recovery_topic_plan_included', table_name='recovery_topics')
    op.drop_index('ix_recovery_topics_plan_id', table_name='recovery_topics')
    op.drop_table('recovery_topics')

    for index in (
        'ix_recovery_plan_subject_status',
        'ix_recovery_plans_support_plan_version_id',
        'ix_recovery_plans_created_by',
        'ix_recovery_plans_status',
        'ix_recovery_plans_subject_id',
        'ix_recovery_plans_student_id',
    ):
        op.drop_index(index, table_name='recovery_plans')
    op.drop_table('recovery_plans')

    for index in (
        'ix_module_library_subject_status',
        'ix_module_library_uploaded_by',
        'ix_module_library_status',
        'ix_module_library_subject_id',
    ):
        op.drop_index(index, table_name='module_library')
    op.drop_table('module_library')
