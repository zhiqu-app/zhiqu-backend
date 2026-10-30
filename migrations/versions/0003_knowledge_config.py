"""保存每个账号的当前星图配置，不修改原课程、节点或学习记录。"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    """新增本人当前路线关联；历史用户默认尚未配置，不注入数学示例。"""
    op.create_table('knowledge_configs',
        sa.Column('user_id', sa.String(32), primary_key=True),
        sa.Column('course_id', sa.String(64), nullable=False),
        sa.Column('subject', sa.String(30), nullable=False),
        sa.Column('level', sa.String(20), nullable=False),
        sa.Column('goal', sa.String(180), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')


def downgrade():
    """只撤销当前配置表；课程及学习资料保留。"""
    op.drop_table('knowledge_configs')
