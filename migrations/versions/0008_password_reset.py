"""为找回密码建立独立的验证码与限流记录。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')
    op.create_table(
        'password_reset_verifications',
        sa.Column('email', sa.String(100), primary_key=True),
        sa.Column('request_id', sa.String(32), nullable=False),
        sa.Column('code_digest', sa.String(64), nullable=False),
        sa.Column('expires_at', timestamp, nullable=False),
        sa.Column('requested_at', timestamp, nullable=False),
        sa.Column('failed_attempts', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('window_started_at', timestamp, nullable=False),
        sa.Column('window_count', sa.Integer(), nullable=False),
        sa.CheckConstraint('failed_attempts >= 0 AND failed_attempts <= 5', name='ck_reset_code_attempts'),
        sa.CheckConstraint('window_count >= 0 AND window_count <= 5', name='ck_reset_code_quota'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin',
    )


def downgrade():
    op.drop_table('password_reset_verifications')
