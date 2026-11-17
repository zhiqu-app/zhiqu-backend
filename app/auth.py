from . import responses as output

from datetime import timedelta
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .dependencies import DB, CurrentUser, bearer, rate
from .models import User, AuthSession, utcnow
from .schemas import (AuthOut, Credentials, Register, ProfilePatch, UserOut, EmailCodeRequest,
                      PasswordReset, PasswordResetCodeRequest)
from .verification import (code_response, consume_code, deliver_reset_code, reserve_reset_code,
                           reset_account, send_code)
from .security import check_password, hash_password, token_digest
from .seed import seed_user
from .serializers import iso, profile, user_view

router = APIRouter(tags=['账号与个人资料'])


def session_result(db, user, hours):
    """生成随机登录令牌，保存摘要与到期时间，并构造前端登录响应；提交事务由请求依赖管理。"""
    token = secrets.token_urlsafe(32)
    expiry = utcnow() + timedelta(hours=hours)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    db.add(AuthSession(digest=token_digest(token), user_id=user.id, expires_at=expiry))
    db.flush()
    return {'accessToken': token, 'tokenType': 'bearer', 'expiresAt': iso(expiry), 'user': user_view(user)}


def create_user(db, email, password, name, guest=False, seed=False, email_verified=False):
    """通过 ORM 创建用户、哈希密码，并按显式 seed 选择是否导入样例；不自行提交外层事务。"""
    user = User(email=email, password_hash=hash_password(password) if password else None,
                name=name, is_guest=guest, email_verified=email_verified)
    db.add(user)
    db.flush()
    if seed:
        seed_user(db, user.id)
    return user


@router.post(
    '/auth/email-code',
    summary='发送注册邮箱验证码',
    response_model=output.EmailCodeOut,
    responses={code:output.ERROR_RESPONSES[code] for code in [409,422,429,503]},
)
def email_code(body: EmailCodeRequest, request: Request, db: DB):
    """给未注册邮箱发六位验证码；60 秒重发、五分钟有效，每邮箱每小时最多五次，不返回验证码。"""
    request.app.state.limiter.check(('email-code-ip',request.client.host),30,window=3600)
    return send_code(db,request.app.state.settings,body.email,request.app.state.mailer)


@router.post(
    '/auth/password-reset/code',
    summary='申请密码重置验证码',
    response_model=output.PasswordResetCodeOut,
    responses={code: output.ERROR_RESPONSES[code] for code in [409, 422, 429, 503]},
)
def password_reset_code(body: PasswordResetCodeRequest, request: Request, db: DB, background: BackgroundTasks):
    """统一返回受理提示，只有已验证登录邮箱的正式账号收到邮件。"""
    state = request.app.state
    state.limiter.check(('password-reset-code-ip', request.client.host), 30, window=3600)
    challenge = reserve_reset_code(db, state.settings, body.email)
    if challenge is not None:
        background.add_task(deliver_reset_code, state.database, state.settings, body.email,
                            state.password_reset_mailer, *challenge)
    return code_response('如果该邮箱已注册并完成验证，你将收到重置验证码。请检查收件箱或垃圾邮件。')


@router.post(
    '/auth/password-reset',
    status_code=204,
    summary='使用邮箱验证码重置密码',
    responses={code: output.ERROR_RESPONSES[code] for code in [400, 422, 429, 503]},
)
def password_reset(body: PasswordReset, request: Request, db: DB):
    """同一事务内消费重置码、更新密码哈希、撤销账号的所有登录会话。"""
    rate(request, 'password-reset-ip', request.client.host, 30)
    user = db.scalar(reset_account(body.email).with_for_update())
    if user is None:
        raise HTTPException(400, '验证码错误、已过期或已失效，请重新获取')
    consume_code(db, request.app.state.settings, body.email, body.verificationCode, account_id=user.id)
    user.password_hash = hash_password(body.password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return Response(status_code=204)


@router.post(
    '/auth/register',
    status_code=201,
    summary='注册学习账号',
    response_model=output.AuthOut,
    responses={
        400: output.ERROR_RESPONSES[400],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        429: output.ERROR_RESPONSES[429],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def register(body: Register, request: Request, db: DB):
    """验证同一邮箱的有效验证码后创建账号并登录；验证码成功只用一次，不接受省略或绕过验证。"""
    rate(request, 'register', request.client.host, 10)
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(409, '邮箱已注册')
    consume_code(db,request.app.state.settings,body.email,body.verificationCode)
    try:
        user = create_user(db, body.email, body.password, body.name, seed=body.seedDemo, email_verified=True)
        return session_result(db, user, request.app.state.settings.session_hours)
    except IntegrityError:
        raise HTTPException(409, '邮箱已注册') from None


@router.post(
    '/auth/login',
    summary='使用邮箱和密码登录',
    response_model=output.AuthOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        429: output.ERROR_RESPONSES[429],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def login(body: Credentials, request: Request, db: DB):
    """查询用户并校验密码哈希，成功后返回会话令牌和资料；错误账号/密码统一返回 401。"""
    rate(request, 'login-ip', request.client.host, 30)
    rate(request, 'login-account', body.email, 10)
    # 与密码重置共用账号行锁，避免用旧密码校验后又创建有效会话。
    user = db.scalar(select(User).where(User.email == body.email, User.is_guest.is_(False)).with_for_update())
    valid = check_password(body.password, user.password_hash if user else request.app.state.dummy_hash)
    if not user or not valid:
        raise HTTPException(401, '邮箱或密码错误', headers={'WWW-Authenticate': 'Bearer'})
    return session_result(db, user, request.app.state.settings.session_hours)


@router.post(
    '/auth/demo',
    status_code=201,
    summary='创建独立体验账号',
    response_model=output.AuthOut,
    responses={
        403: output.ERROR_RESPONSES[403],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        429: output.ERROR_RESPONSES[429],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def demo(request: Request, db: DB):
    """为本次访问创建独立样例空间和 24 小时会话；不是共享公共账号，关闭体验入口时返回 403。"""
    if not request.app.state.settings.demo_enabled:
        raise HTTPException(403, '体验入口已关闭')
    rate(request, 'demo', request.client.host, 10)
    user = create_user(db, secrets.token_hex(16) + '@demo.local', None, '体验同学', guest=True, seed=True)
    return session_result(db, user, 24)


@router.post(
    '/auth/logout',
    status_code=204,
    summary='退出当前登录会话',
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def logout(user: CurrentUser, db: DB, credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    """撤销请求头中的当前令牌，成功返回 204 无正文；不会清空学习数据或其他设备会话。"""
    db.execute(delete(AuthSession).where(AuthSession.digest == token_digest(credentials.credentials), AuthSession.user_id == user.id))
    return Response(status_code=204)


@router.get(
    '/auth/me',
    summary='查询当前登录用户',
    response_model=output.UserOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def me(user: CurrentUser):
    """校验当前令牌并返回用户身份及资料，适用于刷新页面后的会话恢复。"""
    return user_view(user)


@router.get(
    '/profile',
    summary='读取个人资料和偏好',
    response_model=output.ProfileOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def get_profile(user: CurrentUser) -> dict:
    """返回当前账号的姓名、专业、年级、联系邮箱和动画/提醒偏好。"""
    return profile(user)


@router.patch(
    '/profile',
    summary='部分更新个人资料',
    response_model=output.ProfileOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def update_profile(body: ProfilePatch, user: CurrentUser, db: DB) -> dict:
    """只修改请求中出现的字段；省略表示不修改，null 和空对象会被拒绝。联系邮箱不改变登录邮箱。"""
    names = {'email': 'contact_email', 'reducedMotion': 'reduced_motion'}
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, names.get(key, key), value)
    db.flush()
    return profile(user)
