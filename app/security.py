"""密码加盐哈希、可撤销的随机 Bearer 会话、轻量单进程频率限制。"""
from collections import defaultdict, deque
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import HTTPException

ITERATIONS = 600_000


def hash_password(password):
    """使用随机盐和 PBKDF2-SHA256 生成密码哈希，禁止存储明文密码。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def check_password(password, encoded):
    """按保存的盐与迭代次数计算密码摘要，并以恒定时间比较结果。"""
    if not encoded:
        return False
    _, iterations, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)


def token_digest(token):
    """将客户端随机令牌转换为 SHA-256 摘要，数据库只保存摘要。"""
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """只供本地单实例使用；多实例部署时改成 Redis/网关限流。"""
    def __init__(self):
        """初始化单进程频率窗口和线程锁；多实例部署时应改为共享限流。"""
        self.entries = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key, limit, window=60):
        """按 key 在滚动时间窗中计数，超过 limit 时抛出带 Retry-After 的 429。"""
        now = time.monotonic()
        with self.lock:
            # 防止不同地址留下无限空桶。
            if len(self.entries) > 1000:
                for old_key in list(self.entries):
                    if not self.entries[old_key] or self.entries[old_key][-1] <= now - window:
                        del self.entries[old_key]
            rows = self.entries[key]
            while rows and rows[0] <= now - window:
                rows.popleft()
            if len(rows) >= limit:
                raise HTTPException(429, '操作过于频繁，请稍后重试', headers={'Retry-After': str(window)})
            rows.append(now)
