from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuthSession, User, utcnow
from .security import token_digest

bearer = HTTPBearer(
    auto_error=False,
    description='在 Authorize 中输入登录/注册返回的 accessToken 本身；普通请求头使用 Authorization: Bearer <令牌>。',
)
Limit = Annotated[int, Query(ge=1, le=100, description='每页记录数，1～100，默认 50。')]
Offset = Annotated[int, Query(ge=0, description='跳过记录数，从 0 开始；第二页可传 offset=limit。')]


def session_dependency(request: Request):
    """为请求提供事务 Session，成功时在响应发送前提交，异常时回滚，并关闭连接。"""
    with request.app.state.database.sessions() as session:
        try:
            yield session
            session.commit()
            hub = getattr(request.app.state, 'notification_hub', None)
            if hub:
                hub.publish(session.info.get('notification_users', set()))
        except Exception:
            session.rollback()
            raise


# 必须在成功响应发出前提交事务，避免收到登录成功后下一请求却读不到会话。
DB = Annotated[Session, Depends(session_dependency, scope='function')]


def authenticate(db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    """读取 Bearer 令牌，按摘要和有效期查找会话，返回当前用户；无效时抛出 401。"""
    if credentials:
        user = db.scalar(select(User).join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.digest == token_digest(credentials.credentials), AuthSession.expires_at > utcnow()))
        if user:
            return user
    raise HTTPException(401, '请登录，或会话已经过期', headers={'WWW-Authenticate': 'Bearer'})


CurrentUser = Annotated[User, Depends(authenticate)]


def own(db, model, user_id, resource_id):
    """按用户 ID 与资源 ID 检查所有权，找不到或无权访问统一返回 404。"""
    row = db.scalar(select(model).where(model.user_id == user_id, model.id == resource_id))
    if row is None:
        raise HTTPException(404, '资源不存在或无权访问')
    return row


def paginated(db, statement, limit, offset, render):
    # ORM select(entity).subquery() 会展开 deferred BLOB/正文列；总数只选 COUNT。
    """执行分页查询和总数统计，使用 render 将每条 ORM 记录转为响应字典。"""
    total = db.scalar(statement.with_only_columns(func.count(), maintain_column_froms=True).order_by(None))
    return {'items': [render(row) for row in db.scalars(statement.limit(limit).offset(offset))],
            'total': total, 'limit': limit, 'offset': offset}


def enforce_limit(db, model, user_id, maximum):
    """检查当前账号某类资源的数量上限，超额返回 409。"""
    if db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) >= maximum:
        raise HTTPException(409, f'数量已达上限 {maximum}，请先整理数据')


def rate(request, category, key, limit):
    """将请求类别与账号/IP 交给进程内限流器，超限返回 429。"""
    request.app.state.limiter.check((category, key), limit)
