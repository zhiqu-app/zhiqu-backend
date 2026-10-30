"""注册验证码的数据库状态机：先预留发送额度，SMTP 成功后才激活，注册时一次性消费。"""
from datetime import timedelta
import hashlib
import hmac
import logging
import math
import secrets
import smtplib

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .models import EmailVerification, User, utcnow

CODE_TTL = 300
RESEND_SECONDS = 60
MAX_ATTEMPTS = 5
HOURLY_LIMIT = 5


def code_digest(secret, email, request_id, code):
    """用独立服务端密钥生成邮箱/发送批次绑定的 HMAC，数据库泄漏时不能直接枚举六位明文。"""
    payload = f'{email}\0{request_id}\0{code}'.encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def send_code(db, settings, email, sender):
    """在数据库原子预留冷却和小时配额，释放事务后发信；仅成功邮件对应的验证码可用于注册。"""
    if not settings.smtp_ready:
        raise HTTPException(503, '邮件服务尚未配置，请联系管理员完成 QQ 邮箱 SMTP 配置')
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, '该邮箱已注册，请直接登录')
    now = utcnow()
    row = db.scalar(select(EmailVerification).where(EmailVerification.email == email).with_for_update())
    if row is None:
        try:
            with db.begin_nested():
                row = EmailVerification(email=email, request_id=secrets.token_hex(16), code_digest='',
                    requested_at=now-timedelta(seconds=RESEND_SECONDS+1), expires_at=now,
                    window_started_at=now, window_count=0, failed_attempts=0, active=False)
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.scalar(select(EmailVerification).where(EmailVerification.email == email).with_for_update())
            if row is None:
                raise HTTPException(409, '验证码请求冲突，请稍后重试') from None
    remaining = RESEND_SECONDS - (now-row.requested_at).total_seconds()
    if remaining > 0:
        raise HTTPException(429, '发送过于频繁，请稍后重试', headers={'Retry-After':str(math.ceil(remaining))})
    if (now-row.window_started_at).total_seconds() >= 3600:
        row.window_started_at = now
        row.window_count = 0
    if row.window_count >= HOURLY_LIMIT:
        wait = max(1, math.ceil(3600-(now-row.window_started_at).total_seconds()))
        raise HTTPException(429, '该邮箱本小时发送次数已达上限，请稍后重试', headers={'Retry-After':str(wait)})
    code = f'{secrets.randbelow(1_000_000):06d}'
    generation = secrets.token_hex(16)
    row.request_id = generation
    row.code_digest = code_digest(settings.verification_secret, email, generation, code)
    row.requested_at = now
    row.expires_at = now + timedelta(seconds=CODE_TTL)
    row.failed_attempts = 0
    row.active = False
    row.window_count += 1
    db.commit()
    try:
        sender(settings, email, code)
    except (smtplib.SMTPException, OSError, ValueError):
        # 摘要槽保持不可用；不打印 SMTP 原始错误、授权码、验证码或收件地址。
        logging.getLogger('zhiqu').warning('Registration email delivery failed')
        raise HTTPException(503, '邮件发送失败，请检查 SMTP 配置或稍后重试',
                            headers={'Retry-After':str(RESEND_SECONDS)}) from None
    result = db.execute(update(EmailVerification).where(EmailVerification.email == email,
        EmailVerification.request_id == generation).values(active=True))
    if result.rowcount != 1:
        raise HTTPException(409, '本次验证码已被更新，请使用最新邮件')
    return {'message':'验证码邮件已发送，请检查收件箱或垃圾邮件',
            'expiresIn':CODE_TTL,'resendAfter':RESEND_SECONDS}


def consume_code(db, settings, email, code):
    """锁定并验证一次验证码；失败计数单独提交，成功消费与账号创建处于同一外层事务。"""
    if len(settings.verification_secret) < 32:
        raise HTTPException(503, '邮件验证码服务尚未配置')
    row = db.scalar(select(EmailVerification).where(EmailVerification.email == email).with_for_update())
    if row is None or not row.active or row.expires_at <= utcnow() or row.failed_attempts >= MAX_ATTEMPTS:
        raise HTTPException(400, '验证码错误、已过期或已失效，请重新获取')
    expected = code_digest(settings.verification_secret, email, row.request_id, code)
    if not hmac.compare_digest(row.code_digest, expected):
        row.failed_attempts += 1
        if row.failed_attempts >= MAX_ATTEMPTS:
            row.active = False
        db.commit()  # HTTPException 会让请求依赖回滚，因此失败次数必须先提交。
        raise HTTPException(400, '验证码错误、已过期或已失效，请重新获取')
    row.active = False
