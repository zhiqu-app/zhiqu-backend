"""邮箱验证码的预留、发送与一次性消费。"""
from datetime import timedelta
import hashlib
import hmac
import logging
import math
import secrets
import smtplib

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .models import EmailVerification, PasswordResetVerification, User, utcnow

CODE_TTL = 300
RESEND_SECONDS = 60
MAX_ATTEMPTS = 5
HOURLY_LIMIT = 5


def code_digest(secret, email, request_id, code, *, account_id=None):
    """用独立服务端密钥生成邮箱/发送批次绑定的 HMAC，数据库泄漏时不能直接枚举六位明文。"""
    payload = f'{email}\0{request_id}\0{code}'.encode()
    if account_id is not None:
        payload = f'password-reset\0{account_id}\0'.encode() + payload
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _reserve_code(db, settings, email, *, account_id=None):
    """锁定邮箱验证码槽，原子预留发送额度和新批次。"""
    model = EmailVerification if account_id is None else PasswordResetVerification
    locked = select(model).where(model.email == email).with_for_update()
    now = utcnow()
    row = db.scalar(locked)
    if row is None:
        try:
            with db.begin_nested():
                row = model(email=email, request_id=secrets.token_hex(16), code_digest='',
                    requested_at=now-timedelta(seconds=RESEND_SECONDS+1), expires_at=now,
                    window_started_at=now, window_count=0, failed_attempts=0, active=False)
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.scalar(locked)
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
    row.code_digest = code_digest(settings.verification_secret, email, generation, code, account_id=account_id)
    row.requested_at = now
    row.expires_at = now + timedelta(seconds=CODE_TTL)
    row.failed_attempts = 0
    row.active = False
    row.window_count += 1
    return generation, code


def _send_email(settings, email, code, sender):
    """执行邮件适配器调用，并隐藏 SMTP 错误细节。"""
    try:
        sender(settings, email, code)
    except (smtplib.SMTPException, OSError, ValueError):
        # 摘要槽保持不可用；不打印 SMTP 原始错误、授权码、验证码或收件地址。
        logging.getLogger('zhiqu').warning('Verification email delivery failed')
        raise HTTPException(503, '邮件发送失败，请检查 SMTP 配置或稍后重试',
                            headers={'Retry-After':str(RESEND_SECONDS)}) from None


def _activate_code(db, model, email, generation):
    """仅激活与当前预留批次匹配的验证码。"""
    result = db.execute(update(model).where(model.email == email,
        model.request_id == generation).values(active=True))
    if result.rowcount != 1:
        raise HTTPException(409, '本次验证码已被更新，请使用最新邮件')


def code_response(message):
    """返回与后端校验一致的有效期和重发间隔。"""
    return {'message': message, 'expiresIn': CODE_TTL, 'resendAfter': RESEND_SECONDS}


def send_code(db, settings, email, sender):
    """预留发送额度，释放事务后发信，SMTP 成功后才激活注册码。"""
    if not settings.smtp_ready:
        raise HTTPException(503, '邮件服务尚未配置，请联系管理员完成 QQ 邮箱 SMTP 配置')
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, '该邮箱已注册，请直接登录')
    generation, code = _reserve_code(db, settings, email)
    db.commit()
    _send_email(settings, email, code, sender)
    _activate_code(db, EmailVerification, email, generation)
    return code_response('验证码邮件已发送，请检查收件箱或垃圾邮件')


def reset_account(email):
    """只有验证过登录邮箱的正式账号可以找回密码。"""
    return select(User).where(User.email == email, User.email_verified.is_(True), User.is_guest.is_(False))


def reserve_reset_code(db, settings, email):
    """对所有邮箱使用相同发送额度，只有符合条件的账号进入发信任务。"""
    if not settings.smtp_ready:
        raise HTTPException(503, '邮件服务尚未配置，请联系管理员')
    user = db.scalar(reset_account(email))
    challenge = _reserve_code(db, settings, email, account_id=user.id if user else '')
    return challenge if user else None


def deliver_reset_code(database, settings, email, sender, generation, code):
    """在响应发出后发送邮件，成功才用独立事务激活这一批次。"""
    try:
        _send_email(settings, email, code, sender)
        with database.sessions.begin() as db:
            _activate_code(db, PasswordResetVerification, email, generation)
    except HTTPException:
        # SMTP 错误已记录，旧批次被替换后不再激活。
        return
    except SQLAlchemyError:
        logging.getLogger('zhiqu').error('Password reset code activation failed')


def consume_code(db, settings, email, code, *, account_id=None):
    """锁定验证码。失败计数单独提交，成功消费和账号更新在同一事务。"""
    if len(settings.verification_secret) < 32:
        raise HTTPException(503, '邮件验证码服务尚未配置')
    model = EmailVerification if account_id is None else PasswordResetVerification
    row = db.scalar(select(model).where(model.email == email).with_for_update())
    if row is None or not row.active or row.expires_at <= utcnow() or row.failed_attempts >= MAX_ATTEMPTS:
        raise HTTPException(400, '验证码错误、已过期或已失效，请重新获取')
    expected = code_digest(settings.verification_secret, email, row.request_id, code, account_id=account_id)
    if not hmac.compare_digest(row.code_digest, expected):
        row.failed_attempts += 1
        if row.failed_attempts >= MAX_ATTEMPTS:
            row.active = False
        db.commit()  # HTTPException 会让请求依赖回滚，因此失败次数必须先提交。
        raise HTTPException(400, '验证码错误、已过期或已失效，请重新获取')
    row.active = False
