from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
# ### Alembic 自动生成的命令，请按需调整！ ###
    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=100), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('name', sa.String(length=30), nullable=False),
    sa.Column('major', sa.String(length=30), nullable=False),
    sa.Column('grade', sa.String(length=10), nullable=False),
    sa.Column('contact_email', sa.String(length=100), nullable=False),
    sa.Column('reduced_motion', sa.Boolean(), nullable=False),
    sa.Column('reminders', sa.Boolean(), nullable=False),
    sa.Column('is_guest', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('auth_sessions',
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('digest'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index(op.f('ix_auth_sessions_expires_at'), 'auth_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('courses',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=30), nullable=False),
    sa.Column('teacher', sa.String(length=20), nullable=False),
    sa.Column('chapter', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=180), nullable=False),
    sa.Column('lessons', sa.Integer(), nullable=False),
    sa.Column('completed_lessons', sa.Integer(), nullable=False),
    sa.Column('cover', sa.String(length=20), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('sample', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint('lessons >= 0 AND completed_lessons >= 0 AND completed_lessons <= lessons', name='ck_course_lessons'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_course_owner_created', 'courses', ['user_id', 'created_at', 'id'], unique=False)
    op.create_table('tasks',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=60), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('minutes', sa.Integer(), nullable=False),
    sa.Column('planned_date', sa.Date(), nullable=True),
    sa.Column('done', sa.Boolean(), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('from_question', sa.String(length=64), nullable=True),
    sa.Column('active_key', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint('minutes >= 1 AND minutes <= 240', name='ck_task_minutes'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'id'),
    sa.UniqueConstraint('user_id', 'active_key', name='uq_task_pending_question'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_task_owner_date', 'tasks', ['user_id', 'planned_date', 'created_at', 'id'], unique=False)
    op.create_table('chat_turns',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('question', sa.String(length=2000), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_chat_course_date', 'chat_turns', ['user_id', 'course_id', 'created_at', 'id'], unique=False)
    op.create_table('knowledge_nodes',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('sample', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'course_id', 'id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_table('materials',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=180), nullable=False),
    sa.Column('kind', sa.String(length=5), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('content', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=False),
    sa.Column('text_content', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('parse_status', sa.String(length=20), nullable=False),
    sa.Column('sample', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_material_course', 'materials', ['user_id', 'course_id', 'created_at', 'id'], unique=False)
    op.create_table('questions',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('answer', sa.Integer(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('point', sa.String(length=80), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=True),
    sa.Column('sample', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint('answer >= 0 AND answer < 6', name='ck_question_answer'),
    sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_question_course', 'questions', ['user_id', 'course_id', 'created_at', 'id'], unique=False)
    op.create_table('attempts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('question_id', sa.String(length=64), nullable=False),
    sa.Column('submission_id', sa.String(length=100), nullable=False),
    sa.Column('choice', sa.Integer(), nullable=False),
    sa.Column('correct', sa.Boolean(), nullable=False),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.Column('point', sa.String(length=80), nullable=False),
    sa.Column('question_snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint('choice >= 0 AND choice < 6 AND seconds >= 0 AND seconds <= 86400', name='ck_attempt_values'),
    sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id', 'question_id'], ['questions.user_id', 'questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'submission_id', name='uq_attempt_submission'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_attempt_owner_course_date', 'attempts', ['user_id', 'course_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_attempt_owner_date', 'attempts', ['user_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_attempt_owner_question', 'attempts', ['user_id', 'question_id', 'created_at', 'id'], unique=False)
    op.create_table('knowledge_edges',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('target', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['user_id', 'course_id', 'source'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id', 'course_id', 'target'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'course_id', 'source', 'target'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_bin',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_edge_target', 'knowledge_edges', ['user_id', 'course_id', 'target'], unique=False)
    # ### Alembic 命令结束 ###


def downgrade():
# ### 由 Alembic 自动生成的命令，请自行调整！ ###
    op.drop_index('ix_edge_target', table_name='knowledge_edges')
    op.drop_table('knowledge_edges')
    op.drop_index('ix_attempt_owner_question', table_name='attempts')
    op.drop_index('ix_attempt_owner_date', table_name='attempts')
    op.drop_index('ix_attempt_owner_course_date', table_name='attempts')
    op.drop_table('attempts')
    op.drop_index('ix_question_course', table_name='questions')
    op.drop_table('questions')
    op.drop_index('ix_material_course', table_name='materials')
    op.drop_table('materials')
    op.drop_table('knowledge_nodes')
    op.drop_index('ix_chat_course_date', table_name='chat_turns')
    op.drop_table('chat_turns')
    op.drop_index('ix_task_owner_date', table_name='tasks')
    op.drop_table('tasks')
    op.drop_index('ix_course_owner_created', table_name='courses')
    op.drop_table('courses')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_expires_at'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_table('users')
    # ### 结束 Alembic 命令 ###
