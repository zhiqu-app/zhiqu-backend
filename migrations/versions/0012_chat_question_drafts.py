"""让聊天问答保留经校验的单选题草稿。"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    """添加可空 JSON 字段，既有聊天自动保持原有文字和引用。"""
    op.add_column('chat_turns', sa.Column('questions', sa.JSON(), nullable=True))


def downgrade():
    """移除结构化题目草稿字段。"""
    op.drop_column('chat_turns', 'questions')
