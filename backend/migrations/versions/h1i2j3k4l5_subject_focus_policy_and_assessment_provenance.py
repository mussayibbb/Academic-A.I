"""add versioned subject focus policy and explicit assessment provenance

Revision ID: h1i2j3k4l5
Revises: g7h8i9j0k1l
Create Date: 2026-10-19 00:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h1i2j3k4l5'
down_revision: Union[str, None] = 'g7h8i9j0k1l'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('assessments') as batch:
        batch.add_column(sa.Column('maximum_marks_verified', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('marks') as batch:
        batch.add_column(sa.Column('official_result', sa.String(length=20), nullable=True))

    op.create_table(
        'subject_focus_policies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('threshold_score', sa.Float(), nullable=False),
        sa.Column('threshold_maximum', sa.Float(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('change_reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version', name='uq_subject_focus_policy_version'),
    )
    op.create_index('ix_subject_focus_policies_version', 'subject_focus_policies', ['version'], unique=False)
    op.get_bind().execute(sa.text(
        'INSERT INTO subject_focus_policies '
        '(version, threshold_score, threshold_maximum, actor_user_id, change_reason, created_at) '
        'VALUES (:version, :score, :maximum, NULL, :reason, :created_at)'
    ), {
        'version': 1,
        'score': 12.0,
        'maximum': 20.0,
        'reason': 'Initial project policy specified as strictly below 12 out of 20.',
        'created_at': datetime.utcnow(),
    })

    if op.get_bind().dialect.name == 'sqlite':
        op.execute("""
            CREATE TRIGGER trg_subject_focus_policies_no_update
            BEFORE UPDATE ON subject_focus_policies
            BEGIN
                SELECT RAISE(ABORT, 'subject focus policies are immutable');
            END
        """)
        op.execute("""
            CREATE TRIGGER trg_subject_focus_policies_no_delete
            BEFORE DELETE ON subject_focus_policies
            BEGIN
                SELECT RAISE(ABORT, 'subject focus policies are immutable');
            END
        """)


def downgrade() -> None:
    bind = op.get_bind()
    policy_count = bind.execute(sa.text('SELECT COUNT(*) FROM subject_focus_policies')).scalar_one()
    verified_assessments = bind.execute(sa.text(
        'SELECT COUNT(*) FROM assessments WHERE maximum_marks_verified = 1'
    )).scalar_one()
    official_results = bind.execute(sa.text(
        'SELECT COUNT(*) FROM marks WHERE official_result IS NOT NULL'
    )).scalar_one()
    if policy_count > 1 or verified_assessments or official_results:
        raise RuntimeError('Downgrade would discard subject-focus policy history or verified assessment data.')

    if bind.dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS trg_subject_focus_policies_no_delete')
        op.execute('DROP TRIGGER IF EXISTS trg_subject_focus_policies_no_update')
    op.drop_index('ix_subject_focus_policies_version', table_name='subject_focus_policies')
    op.drop_table('subject_focus_policies')
    with op.batch_alter_table('marks') as batch:
        batch.drop_column('official_result')
    with op.batch_alter_table('assessments') as batch:
        batch.drop_column('maximum_marks_verified')
