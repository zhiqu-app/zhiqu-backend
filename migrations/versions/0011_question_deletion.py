"""从课程题库移除题目时保留作答和计划关联。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    """为已有题目添加可空删除时间，默认继续在题库中显示。"""
    timestamp = sa.DateTime().with_variant(DATETIME(fsp=6), 'mysql')
    op.add_column('questions', sa.Column('deleted_at', timestamp, nullable=True))


def downgrade():
    """移除题库删除标记，恢复此前标记删除的题目。"""
    op.drop_column('questions', 'deleted_at')
