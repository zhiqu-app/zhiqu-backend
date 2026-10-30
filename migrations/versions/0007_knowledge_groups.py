"""增加大知识点与可空归属，原知识点、连线、任务和课程资料全部保留。"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    """历史节点保留空大知识点，查询时每十点兼容分组，用户保存后再持久化归属。"""
    op.create_table('knowledge_groups',
        sa.Column('user_id', sa.String(32), primary_key=True),
        sa.Column('course_id', sa.String(64), primary_key=True),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')
    with op.batch_alter_table('knowledge_nodes') as batch:
        batch.add_column(sa.Column('group_id', sa.String(64), nullable=True))
        batch.create_foreign_key('fk_knowledge_node_group', 'knowledge_groups',
            ['user_id', 'course_id', 'group_id'], ['user_id', 'course_id', 'id'], ondelete='CASCADE')


def downgrade():
    """仅撤销大知识点归属，不删除知识点及其业务关联。"""
    with op.batch_alter_table('knowledge_nodes') as batch:
        batch.drop_constraint('fk_knowledge_node_group', type_='foreignkey')
        batch.drop_column('group_id')
    op.drop_table('knowledge_groups')
