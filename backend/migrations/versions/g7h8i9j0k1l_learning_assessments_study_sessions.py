"""add module learning, assessment review, and study session tables

Revision ID: g7h8i9j0k1l
Revises: f6a7b8c9d0e1
Create Date: 2026-10-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g7h8i9j0k1l'
down_revision: Union[str, Sequence[str], None] = ('e1f2a3b4c5d6', 'f6a7b8c9d0e1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'subject_modules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('module_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'module_number', name='uq_subject_module_number'),
    )
    op.create_index('ix_subject_modules_subject_id', 'subject_modules', ['subject_id'])
    op.create_index('ix_subject_modules_module_number', 'subject_modules', ['module_number'])
    op.create_index('ix_subject_modules_status', 'subject_modules', ['status'])

    op.create_table(
        'subject_module_resources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('storage_path', sa.String(length=512), nullable=False),
        sa.Column('file_type', sa.String(length=30), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=128), nullable=False),
        sa.Column('processing_status', sa.String(length=30), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('extracted_text', sa.Text(), nullable=True),
        sa.Column('extracted_topics', sa.JSON(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['subject_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('module_id', 'subject_id', 'teacher_id', 'file_type', 'processing_status', 'content_hash'):
        op.create_index(f'ix_subject_module_resources_{column}', 'subject_module_resources', [column])

    op.create_table(
        'approved_topics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('source_resource_id', sa.Integer(), nullable=True),
        sa.Column('teacher_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('learning_objectives', sa.JSON(), nullable=False),
        sa.Column('concepts', sa.JSON(), nullable=False),
        sa.Column('approval_status', sa.String(length=30), nullable=False),
        sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('source_provenance', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['module_id'], ['subject_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_resource_id'], ['subject_module_resources.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('subject_id', 'module_id', 'source_resource_id', 'teacher_id', 'approval_status', 'approved_by_user_id'):
        op.create_index(f'ix_approved_topics_{column}', 'approved_topics', [column])

    op.create_table(
        'topic_learning_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('approved_topic_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('learning_objective', sa.Text(), nullable=False),
        sa.Column('lesson_content', sa.Text(), nullable=False),
        sa.Column('expected_duration_minutes', sa.Integer(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('available_from', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('mastery_criteria', sa.Text(), nullable=True),
        sa.Column('source_provenance', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['module_id'], ['subject_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_topic_id'], ['approved_topics.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('student_id', 'subject_id', 'module_id', 'approved_topic_id', 'teacher_id', 'status'):
        op.create_index(f'ix_topic_learning_assignments_{column}', 'topic_learning_assignments', [column])

    op.create_table(
        'learning_assessments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('question_count', sa.Integer(), nullable=False),
        sa.Column('source_evidence', sa.JSON(), nullable=False),
        sa.Column('validation_results', sa.JSON(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('teacher_id', sa.Integer(), nullable=True),
        sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('review_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id'], ['topic_learning_assignments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('assignment_id', 'version', name='uq_learning_assessment_assignment_version'),
    )
    for column in ('assignment_id', 'status', 'teacher_id', 'approved_by_user_id'):
        op.create_index(f'ix_learning_assessments_{column}', 'learning_assessments', [column])

    op.create_table(
        'learning_study_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('required_duration_minutes', sa.Integer(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id'], ['topic_learning_assignments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('assignment_id', 'student_id', name='uq_learning_session_assignment_student'),
    )
    op.create_index('ix_learning_study_sessions_assignment_id', 'learning_study_sessions', ['assignment_id'])
    op.create_index('ix_learning_study_sessions_student_id', 'learning_study_sessions', ['student_id'])

    op.create_table(
        'assessment_questions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assessment_id', sa.Integer(), nullable=True),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('question_type', sa.String(length=20), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('correct_answer', sa.Text(), nullable=True),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('learning_objective', sa.Text(), nullable=True),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('rubric', sa.JSON(), nullable=False),
        sa.Column('marks', sa.Float(), nullable=False),
        sa.Column('source_provenance', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assessment_id'], ['learning_assessments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['module_id'], ['subject_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['topic_id'], ['approved_topics.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('assessment_id', 'subject_id', 'module_id', 'topic_id', 'question_type'):
        op.create_index(f'ix_assessment_questions_{column}', 'assessment_questions', [column])

    op.create_table(
        'assessment_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('assessment_id', sa.Integer(), nullable=True),
        sa.Column('study_session_id', sa.Integer(), nullable=True),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('question_set_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('total_marks', sa.Float(), nullable=True),
        sa.Column('percentage', sa.Float(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('evaluated_at', sa.DateTime(), nullable=True),
        sa.Column('question_set', sa.JSON(), nullable=False),
        sa.Column('answer_key', sa.JSON(), nullable=False),
        sa.Column('provider_metadata', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id'], ['topic_learning_assignments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assessment_id'], ['learning_assessments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['study_session_id'], ['learning_study_sessions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('assignment_id', 'assessment_id', 'study_session_id', 'student_id', 'status'):
        op.create_index(f'ix_assessment_attempts_{column}', 'assessment_attempts', [column])

    op.create_table(
        'assessment_attempt_answers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('attempt_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('selected_choice', sa.Text(), nullable=True),
        sa.Column('response_text', sa.Text(), nullable=True),
        sa.Column('marks_awarded', sa.Float(), nullable=False),
        sa.Column('original_marks_awarded', sa.Float(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('review_required', sa.Boolean(), nullable=False),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('review_reason', sa.Text(), nullable=True),
        sa.Column('rubric_evaluation', sa.JSON(), nullable=False),
        sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['attempt_id'], ['assessment_attempts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['question_id'], ['assessment_questions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('attempt_id', 'question_id', 'reviewed_by_user_id'):
        op.create_index(f'ix_assessment_attempt_answers_{column}', 'assessment_attempt_answers', [column])


def downgrade() -> None:
    for table, columns in (
        ('assessment_attempt_answers', ('attempt_id', 'question_id', 'reviewed_by_user_id')),
        ('assessment_attempts', ('assignment_id', 'assessment_id', 'study_session_id', 'student_id', 'status')),
        ('assessment_questions', ('assessment_id', 'subject_id', 'module_id', 'topic_id', 'question_type')),
        ('learning_study_sessions', ('assignment_id', 'student_id')),
        ('learning_assessments', ('assignment_id', 'status', 'teacher_id', 'approved_by_user_id')),
        ('topic_learning_assignments', ('student_id', 'subject_id', 'module_id', 'approved_topic_id', 'teacher_id', 'status')),
        ('approved_topics', ('subject_id', 'module_id', 'source_resource_id', 'teacher_id', 'approval_status', 'approved_by_user_id')),
        ('subject_module_resources', ('module_id', 'subject_id', 'teacher_id', 'file_type', 'processing_status', 'content_hash')),
        ('subject_modules', ('subject_id', 'module_number', 'status')),
    ):
        for column in columns:
            op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_table(table)