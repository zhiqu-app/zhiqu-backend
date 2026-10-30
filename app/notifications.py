"""Durable notifications, a single-worker WebSocket hub and a non-blocking due scanner."""
import asyncio
from contextlib import suppress
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .dependencies import CurrentUser, DB, Limit, Offset, own
from .models import AuthSession, Notification, Task, utcnow
from .security import token_digest
from .serializers import iso
from .responses import ERROR_RESPONSES

router = APIRouter(tags=['学习提醒'])


class NotificationOut(BaseModel):
    id: str = Field(description='提醒 ID。')
    taskId: str = Field(description='关联本人任务 ID。')
    kind: Literal['due', 'completed'] = Field(description='到期或完成提醒。')
    title: str = Field(description='创建提醒时的标题快照。')
    body: str = Field(description='提醒说明快照。')
    target: str = Field(description='站内跳转路径。')
    createdAt: str = Field(description='UTC ISO8601 创建时间。')
    read: bool = Field(description='是否已读。')
    taskDone: bool = Field(description='关联任务当前是否完成。')


class NotificationPage(BaseModel):
    items: list[NotificationOut] = Field(description='当前页提醒。')
    total: int = Field(description='本人提醒总数。')
    unreadCount: int = Field(description='本人未读总数，独立于分页。')
    limit: int = Field(description='分页大小。')
    offset: int = Field(description='分页偏移。')


class ReadRequest(BaseModel):
    read: Literal[True] = Field(description='将该条提醒设为已读。')


class ReadAllOut(BaseModel):
    updated: int = Field(description='本次新增已读条数。')


def notification_view(row, task_done):
    """Return persisted snapshots with current task completion status."""
    return dict(id=row.id, taskId=row.task_id, kind=row.kind, title=row.title, body=row.body,
        target=row.target, createdAt=iso(row.created_at), read=row.read, taskDone=task_done)


def record_notification(db, task, kind):
    """Deduplicate each reminder time, and completion once, inside the task transaction."""
    occurrence = task.reminder_at if kind == 'due' else datetime(1970, 1, 1)
    exists = db.scalar(select(Notification.id).where(Notification.user_id == task.user_id,
        Notification.task_id == task.id, Notification.kind == kind, Notification.occurrence_at == occurrence))
    if exists:
        return False
    try:
        with db.begin_nested():
            db.add(Notification(user_id=task.user_id, task_id=task.id, kind=kind,
                occurrence_at=occurrence,
                title=('已完成：' if kind == 'completed' else '开始复习：') + task.title,
                body=task.description or ('复习计划已完成' if kind == 'completed' else '已到计划提醒时间'),
                target=task.target))
            db.flush()
    except IntegrityError:
        return False
    db.info.setdefault('notification_users', set()).add(task.user_id)
    return True


@router.get('/notifications', response_model=NotificationPage, summary='读取持久化学习提醒', responses=ERROR_RESPONSES)
def notifications(user: CurrentUser, db: DB, limit: Limit = 50, offset: Offset = 0):
    """分页返回本人到期与完成记录；已完成记录继续保留，可在重新登录后读取。"""
    total = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id))
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)))
    rows = db.execute(select(Notification, Task.done).join(Task,
        (Task.user_id == Notification.user_id) & (Task.id == Notification.task_id))
        .where(Notification.user_id == user.id).order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit).offset(offset))
    return dict(items=[notification_view(row, done) for row, done in rows], total=total, unreadCount=unread, limit=limit, offset=offset)


@router.post('/notifications/read-all', response_model=ReadAllOut, summary='将本人提醒全部设为已读', responses=ERROR_RESPONSES)
def read_all(user: CurrentUser, db: DB):
    """仅修改本人未读记录，并在事务提交后通知当前账号的其他页面刷新。"""
    result = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)).values(read=True))
    if result.rowcount:
        db.info.setdefault('notification_users', set()).add(user.id)
    return {'updated': result.rowcount}


@router.patch('/notifications/{notification_id}', response_model=NotificationOut, summary='将一条本人提醒设为已读', responses=ERROR_RESPONSES)
def read_notification(notification_id: Annotated[str, Path(min_length=1, max_length=64, description='提醒 ID。')],
                      body: ReadRequest, user: CurrentUser, db: DB):
    """按账号检查提醒所有权；重复标已读是幂等操作。"""
    row = own(db, Notification, user.id, notification_id)
    row.read = True
    db.info.setdefault('notification_users', set()).add(user.id)
    return notification_view(row, own(db, Task, user.id, row.task_id).done)


def scan_due(database):
    """Run synchronous ORM work in a worker thread; lock each task before checking completion."""
    changed = set()
    with database.sessions() as db:
        already_sent = select(Notification.id).where(Notification.user_id == Task.user_id,
            Notification.task_id == Task.id, Notification.kind == 'due', Notification.occurrence_at == Task.reminder_at).exists()
        candidates = db.execute(select(Task.user_id, Task.id).where(Task.done.is_(False),
            Task.reminder_at <= utcnow(), ~already_sent).order_by(Task.reminder_at).limit(1000)).all()
    for user_id, task_id in candidates:
        with database.sessions.begin() as db:
            task = db.scalar(select(Task).where(Task.user_id == user_id, Task.id == task_id).with_for_update())
            if task and not task.done and task.reminder_at and task.reminder_at <= utcnow():
                if record_notification(db, task, 'due'):
                    changed.add(user_id)
    return changed


def session_user(database, digest):
    """Open a fresh short transaction so logout/expiry is visible on every check."""
    with database.sessions() as db:
        return db.scalar(select(AuthSession.user_id).where(AuthSession.digest == digest, AuthSession.expires_at > utcnow()))


class NotificationHub:
    """Only change signals are sent; authoritative records always come from REST."""
    def __init__(self):
        """Initialize connection ownership and per-connection send locks."""
        self.clients = {}
        self.loop = None

    async def send(self, ws, payload):
        """Serialize bounded sends so one slow connection cannot stall other users."""
        client = self.clients.get(ws)
        if client:
            async with client[2]:
                await asyncio.wait_for(ws.send_json(payload), timeout=2)

    async def broadcast(self, user_id):
        """Notify only connections authenticated as this user."""
        sockets = [ws for ws, data in self.clients.items() if data[0] == user_id]
        results = await asyncio.gather(*(self.send(ws, {'type': 'notifications.changed'}) for ws in sockets), return_exceptions=True)
        for ws, result in zip(sockets, results):
            if isinstance(result, BaseException):
                self.clients.pop(ws, None)
                with suppress(Exception):
                    await asyncio.wait_for(ws.close(code=1011), timeout=1)

    def publish(self, users):
        """Schedule after-commit signals from synchronous request worker threads."""
        if self.loop and not self.loop.is_closed():
            for user_id in users:
                self.loop.call_soon_threadsafe(self.loop.create_task, self.broadcast(user_id))

    async def close(self):
        """Close active sockets during application shutdown."""
        for ws in list(self.clients):
            with suppress(Exception):
                await asyncio.wait_for(ws.close(code=1001), timeout=1)
        self.clients.clear()


async def notification_scheduler(app):
    """Poll durable due tasks and expire logged-out sockets, without blocking the event loop."""
    hub = app.state.notification_hub
    while True:
        await asyncio.sleep(2)
        try:
            changed = await asyncio.to_thread(scan_due, app.state.database)
            for ws, (user_id, digest, _) in list(hub.clients.items()):
                valid = await asyncio.to_thread(session_user, app.state.database, digest)
                if valid != user_id:
                    hub.clients.pop(ws, None)
                    with suppress(Exception):
                        await asyncio.wait_for(ws.close(code=4401), timeout=1)
            await asyncio.gather(*(hub.broadcast(user_id) for user_id in changed))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger('zhiqu').error('Notification scheduler failure %s', type(exc).__name__)


@router.websocket('/notifications/ws')
async def notification_socket(ws: WebSocket):
    """Authenticate in the first frame within five seconds; never accept URL credentials."""
    origin = ws.headers.get('origin')
    if ws.query_params or (origin and origin not in ws.app.state.settings.cors_origins):
        await ws.close(code=4403)
        return
    await ws.accept()
    hub = ws.app.state.notification_hub
    try:
        data = await asyncio.wait_for(ws.receive_json(), timeout=5)
        token = data.get('token') if isinstance(data, dict) and data.get('type') == 'authenticate' else None
        if not isinstance(token, str) or not 1 <= len(token) <= 500:
            await ws.close(code=4401)
            return
        digest = token_digest(token)
        user_id = await asyncio.to_thread(session_user, ws.app.state.database, digest)
        if not user_id:
            await ws.close(code=4401)
            return
        hub.clients[ws] = (user_id, digest, asyncio.Lock())
        await hub.send(ws, {'type': 'ready'})
        while True:
            data = await ws.receive_json()
            if await asyncio.to_thread(session_user, ws.app.state.database, digest) != user_id:
                await ws.close(code=4401)
                break
            if isinstance(data, dict) and data.get('type') == 'ping':
                await hub.send(ws, {'type': 'pong'})
            else:
                await ws.close(code=4400)
                break
    except (asyncio.TimeoutError, ValueError, TypeError, KeyError, WebSocketDisconnect, RuntimeError):
        with suppress(Exception):
            await ws.close(code=4401)
    finally:
        hub.clients.pop(ws, None)
