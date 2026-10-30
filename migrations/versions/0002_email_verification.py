"""邮箱验证码注册：新增验证码槽与用户邮箱已验证标记，保留原有业务数据。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    """在原版本上增加邮箱验证结构；历史用户默认不伪造验证状态。"""
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')
    op.create_table('email_verifications',
        sa.Column('email', sa.String(100), primary_key=True),
        sa.Column('request_id', sa.String(32), nullable=False),
        sa.Column('code_digest', sa.String(64), nullable=False),
        sa.Column('expires_at', timestamp, nullable=False),
        sa.Column('requested_at', timestamp, nullable=False),
        sa.Column('failed_attempts', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('window_started_at', timestamp, nullable=False),
        sa.Column('window_count', sa.Integer(), nullable=False),
        sa.CheckConstraint('failed_attempts >= 0 AND failed_attempts <= 5', name='ck_email_code_attempts'),
        sa.CheckConstraint('window_count >= 0 AND window_count <= 5', name='ck_email_code_quota'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')


def downgrade():
    """撤销本次结构；仅在确认备份后由开发者显式执行。"""
    op.drop_table('email_verifications')
    op.drop_column('users','email_verified')
