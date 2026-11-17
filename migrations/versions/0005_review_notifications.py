"""Add task-scoped exercise progress and durable reminders; preserve legacy tasks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None
TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')
OPTIONS = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')


def upgrade():
    """Add schema and link existing valid mistake plans without consuming historical attempts."""
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('kind', sa.String(10), nullable=False, server_default='custom'))
        batch.add_column(sa.Column('course_id', sa.String(64), nullable=True))
        batch.add_column(sa.Column('reminder_at', TS, nullable=True))
        batch.add_column(sa.Column('completed_at', TS, nullable=True))
        batch.create_foreign_key('fk_task_course', 'courses', ['user_id', 'course_id'], ['user_id', 'id'], ondelete='CASCADE')
        batch.create_check_constraint('ck_task_kind', "kind IN ('custom', 'exercise')")
        batch.create_index('ix_task_due', ['done', 'reminder_at'])
    op.create_table('task_questions',
        sa.Column('user_id', sa.String(32), primary_key=True),
        sa.Column('task_id', sa.String(64), primary_key=True),
        sa.Column('question_id', sa.String(64), primary_key=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('completed_at', TS, nullable=True),
        sa.ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'question_id'], ['questions.user_id', 'questions.id'], ondelete='CASCADE'), **OPTIONS)
    with op.batch_alter_table('attempts') as batch:
        batch.create_unique_constraint('uq_attempt_owner_id', ['user_id', 'id'])
    op.create_table('task_attempts',
        sa.Column('attempt_id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('task_id', sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'attempt_id'], ['attempts.user_id', 'attempts.id'], ondelete='CASCADE'), **OPTIONS)
    op.create_table('notifications',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('task_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('occurrence_at', TS, nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('body', sa.String(500), nullable=False),
        sa.Column('target', sa.String(200), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('read', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'task_id', 'kind', 'occurrence_at', name='uq_notification_task_occurrence'),
        sa.CheckConstraint("kind IN ('due', 'completed')", name='ck_notification_kind'), **OPTIONS)
    op.create_index('ix_notification_owner_created', 'notifications', ['user_id', 'created_at', 'id'])
    if op.get_context().dialect.name == 'sqlite':
        op.execute("""UPDATE tasks SET kind='exercise',
            course_id=(SELECT course_id FROM questions WHERE questions.user_id=tasks.user_id AND questions.id=tasks.from_question),
            target='/app/practice?task='||id||'&course='||(SELECT course_id FROM questions WHERE questions.user_id=tasks.user_id AND questions.id=tasks.from_question)
            WHERE EXISTS(SELECT 1 FROM questions WHERE questions.user_id=tasks.user_id AND questions.id=tasks.from_question)""")
    else:
        op.execute("""UPDATE tasks t JOIN questions q ON q.user_id=t.user_id AND q.id=t.from_question
            SET t.kind='exercise', t.course_id=q.course_id,
                t.target=CONCAT('/app/practice?task=',t.id,'&course=',q.course_id)""")
    # The old schema did not record completion time. Preserve done explicitly,
    # using created_at only as a documented historical approximation.
    op.execute('UPDATE tasks SET completed_at=created_at WHERE done=1')
    op.execute("""INSERT INTO task_questions (user_id,task_id,question_id,position,completed_at)
        SELECT user_id,id,from_question,0,completed_at FROM tasks WHERE kind='exercise'""")


def downgrade():
    """Remove only the added progress and reminder schema; preserve tasks and attempts."""
    op.drop_table('notifications')
    op.drop_table('task_attempts')
    with op.batch_alter_table('attempts') as batch:
        batch.drop_constraint('uq_attempt_owner_id', type_='unique')
    op.drop_table('task_questions')
    with op.batch_alter_table('tasks') as batch:
        batch.drop_constraint('fk_task_course', type_='foreignkey')
        batch.drop_constraint('ck_task_kind', type_='check')
        batch.drop_index('ix_task_due')
        for column in ['completed_at', 'reminder_at', 'course_id', 'kind']:
            batch.drop_column(column)
