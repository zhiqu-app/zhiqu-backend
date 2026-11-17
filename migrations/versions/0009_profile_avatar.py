"""为用户资料增加可空的头像字段。"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    """添加头像字段，已有账号继续显示默认头像。"""
    op.add_column('users', sa.Column('avatar', sa.Text(), nullable=True))


def downgrade():
    """移除自定义头像字段。"""
    op.drop_column('users', 'avatar')
