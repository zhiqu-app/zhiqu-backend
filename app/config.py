"""配置只在启动时读取；API 不会返回密钥。"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


@dataclass
class Settings:
    mysql_host: str = field(default_factory=lambda: os.getenv('MYSQL_HOST', '127.0.0.1'))
    mysql_port: int = field(default_factory=lambda: int(os.getenv('MYSQL_PORT', '3306')))
    mysql_user: str = field(default_factory=lambda: os.getenv('MYSQL_USER', 'zhiqu_team_app'))
    mysql_password: str = field(default_factory=lambda: os.getenv('MYSQL_PASSWORD', ''), repr=False)
    mysql_database: str = field(default_factory=lambda: os.getenv('MYSQL_DATABASE', 'zhiqu_team'))
    cors_origins: list[str] = field(default_factory=lambda: [
        value.strip() for value in os.getenv('CORS_ORIGINS',
            'http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173'
        ).split(',') if value.strip()
    ])
    session_hours: int = field(default_factory=lambda: int(os.getenv('SESSION_HOURS', '168')))
    demo_enabled: bool = field(default_factory=lambda: os.getenv('DEMO_ENABLED', 'true').lower() == 'true')
    ai_base_url: str = field(default_factory=lambda: os.getenv('AI_BASE_URL', '').rstrip('/'))
    ai_api_key: str = field(default_factory=lambda: os.getenv('AI_API_KEY', ''), repr=False)
    ai_model: str = field(default_factory=lambda: os.getenv('AI_MODEL', ''))
    ai_vision_model: str = field(default_factory=lambda: os.getenv('AI_VISION_MODEL', '').strip())
    ai_vision_base_url: str = field(default_factory=lambda: os.getenv('AI_VISION_BASE_URL', '').strip().rstrip('/'))
    ai_vision_api_key: str = field(default_factory=lambda: os.getenv('AI_VISION_API_KEY', ''), repr=False)
    ai_timeout_seconds: float = field(default_factory=lambda: float(os.getenv('AI_TIMEOUT_SECONDS', '45')))
    smtp_host: str = field(default_factory=lambda: os.getenv('SMTP_HOST', 'smtp.qq.com'))
    smtp_port: int = field(default_factory=lambda: int(os.getenv('SMTP_PORT', '465')))
    smtp_security: str = field(default_factory=lambda: os.getenv('SMTP_SECURITY', 'ssl'))
    smtp_username: str = field(default_factory=lambda: os.getenv('SMTP_USERNAME', ''))
    smtp_password: str = field(default_factory=lambda: os.getenv('SMTP_PASSWORD', ''), repr=False)
    smtp_from: str = field(default_factory=lambda: os.getenv('SMTP_FROM', '') or os.getenv('SMTP_USERNAME', ''))
    smtp_timeout: float = field(default_factory=lambda: float(os.getenv('SMTP_TIMEOUT', '10')))
    verification_secret: str = field(default_factory=lambda: os.getenv('EMAIL_CODE_SECRET', ''), repr=False)

    def __post_init__(self):
        """校验 MySQL 端口、库名、会话时长和 AI 配置，发现无效配置时尽早报错。"""
        if not 1 <= self.mysql_port <= 65535:
            raise ValueError('MYSQL_PORT 必须在 1–65535 之间')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', self.mysql_database):
            raise ValueError('MYSQL_DATABASE 必须为字母开头的 1–64 位字母、数字、下划线')
        if not 1 <= self.session_hours <= 720:
            raise ValueError('SESSION_HOURS 必须在 1–720 之间')
        configured = [bool(self.ai_base_url), bool(self.ai_api_key), bool(self.ai_model)]
        if any(configured) and not all(configured):
            raise ValueError('AI_BASE_URL、AI_API_KEY、AI_MODEL 必须同时配置')
        if self.ai_base_url and not self.ai_base_url.startswith(('http://', 'https://')):
            raise ValueError('AI_BASE_URL 必须为 HTTP(S) 地址')
        if self.ai_vision_base_url:
            address = urlsplit(self.ai_vision_base_url)
            if (address.scheme not in {'http', 'https'} or not address.hostname or address.username
                    or address.query or address.fragment):
                raise ValueError('AI_VISION_BASE_URL 必须为有效的 HTTP(S) API 地址')
            if self.ai_vision_base_url != self.ai_base_url and not self.ai_vision_api_key:
                raise ValueError('不同的图片模型地址需要单独配置 AI_VISION_API_KEY')
        if self.smtp_security not in {'ssl', 'starttls'}:
            raise ValueError('SMTP_SECURITY 只支持 ssl 或 starttls，不允许明文 SMTP')
        if not 1 <= self.smtp_port <= 65535 or not 1 <= self.smtp_timeout <= 30:
            raise ValueError('SMTP 端口或超时无效')
        if self.verification_secret and len(self.verification_secret) < 32:
            raise ValueError('EMAIL_CODE_SECRET 至少 32 个字符，请生成独立随机密钥')

    @property
    def database_url(self):
        # URL.create 处理密码中的 @、#、冒号等字符，不手工拼接连接串。
        """使用 SQLAlchemy URL.create 构造 MySQL 连接信息，正确处理密码中的特殊字符。"""
        return URL.create('mysql+pymysql', username=self.mysql_user, password=self.mysql_password,
                          host=self.mysql_host, port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})

    @property
    def ai_enabled(self):
        """仅在模型地址、密钥和模型名同时配置时返回 true。"""
        return bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    @property
    def vision_base_url(self) -> str:
        """使用专用图片模型地址，留空时复用文字问答的 API 地址。"""
        return self.ai_vision_base_url or self.ai_base_url

    @property
    def vision_api_key(self) -> str:
        """为图片模型取授权密钥，主密钥仅用于原地址。"""
        if self.ai_vision_api_key:
            return self.ai_vision_api_key
        return self.ai_api_key if self.vision_base_url == self.ai_base_url else ''

    @property
    def vision_model(self) -> str:
        """优先使用指定的图片模型，DeepSeek 官方地址默认使用 deepseek-flash。"""
        if self.ai_vision_model:
            return self.ai_vision_model
        address = urlsplit(self.vision_base_url)
        if address.scheme == 'https' and address.netloc == 'api.deepseek.com':
            return 'deepseek-flash'
        return ''

    @property
    def vision_enabled(self) -> bool:
        """确认图片模型地址、密钥和模型名均可用。"""
        return bool(self.vision_base_url and self.vision_api_key and self.vision_model)

    @property
    def smtp_ready(self):
        """只有发件地址、SMTP 授权码与持久化验证码密钥齐备时允许真实发信。"""
        return bool(self.smtp_host and self.smtp_username and self.smtp_password
                    and self.smtp_from and len(self.verification_secret) >= 32)
