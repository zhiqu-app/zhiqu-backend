from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    """仅回填自定义父项；保留任务 ID、时间及练习进度不变"""
    children = op.create_table('task_subtasks',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('task_id', sa.String(64), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        sa.CheckConstraint('position >= 0', name='ck_subtask_position'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')
    op.create_index('ix_subtask_owner_task_position', 'task_subtasks', ['user_id', 'task_id', 'position'])
    connection = op.get_bind()
    tasks = sa.table('tasks', sa.column('user_id'), sa.column('id'), sa.column('title'),
        sa.column('done', sa.Boolean()), sa.column('kind'))
    # Bound, chunked inserts work on both MySQL and SQLite.
    result = connection.execute(sa.select(tasks.c.user_id, tasks.c.id, tasks.c.title, tasks.c.done)
        .where(tasks.c.kind == 'custom'))
    while rows := result.fetchmany(500):
        connection.execute(children.insert(), [dict(id=uuid4().hex, user_id=r.user_id,
            task_id=r.id, title=r.title, done=bool(r.done), position=0) for r in rows])


def downgrade():
    """仅移除子项，保留父项最新的聚合状态。"""
    op.drop_table('task_subtasks')
