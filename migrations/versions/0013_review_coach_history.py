"""保存复习教练每次生成的建议。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    """创建按账号和课程归属的复习建议快照表。"""
    timestamp = sa.DateTime().with_variant(DATETIME(fsp=6), 'mysql')
    op.create_table(
        'review_coach_history',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('course_id', sa.String(64), nullable=False),
        sa.Column('course_name', sa.String(30), nullable=False),
        sa.Column('goal', sa.String(200), nullable=False),
        sa.Column('recommendation', sa.JSON(), nullable=False),
        sa.Column('created_at', timestamp, nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'],
                                name='fk_coach_history_course', ondelete='CASCADE'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin',
    )
    op.create_index('ix_coach_history_owner_date', 'review_coach_history', ['user_id', 'created_at', 'id'])
    op.create_index('ix_coach_history_course_date', 'review_coach_history',
                    ['user_id', 'course_id', 'created_at', 'id'])


def downgrade():
    """移除复习建议快照表。"""
    op.drop_table('review_coach_history')
