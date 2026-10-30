"""新增课程级手动星图与任务关联，保留 0003 配置和所有历史课程节点。"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    """仅新增两张表，不复制、删除或改写现有课程与星图数据。"""
    options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')
    op.create_table('course_knowledge_configs',
        sa.Column('user_id', sa.String(32), primary_key=True),
        sa.Column('course_id', sa.String(64), primary_key=True),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('goal', sa.String(180), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'), **options)
    op.create_table('knowledge_task_links',
        sa.Column('user_id', sa.String(32), primary_key=True),
        sa.Column('course_id', sa.String(64), primary_key=True),
        sa.Column('node_id', sa.String(64), primary_key=True),
        sa.Column('task_id', sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'course_id', 'node_id'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'), **options)


def downgrade():
    """撤销新元数据和任务关联表，课程、知识点和连线保持原样。"""
    op.drop_table('knowledge_task_links')
    op.drop_table('course_knowledge_configs')
