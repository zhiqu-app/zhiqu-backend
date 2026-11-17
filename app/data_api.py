from . import responses as output

import json

from fastapi import APIRouter, Response
from sqlalchemy import delete, select

from .dependencies import DB, CurrentUser
from .models import Course, Material, Task, Attempt, ChatTurn, CoachHistory, utcnow
from .schemas import Reset
from .seed import seed_user
from .serializers import (iso, profile, course_view, material_view, task_view, attempt_view, chat_view,
                          coach_history_view)

router = APIRouter(tags=['学习数据管理'])


@router.get(
    '/data/export',
    summary='导出本人学习记录 JSON',
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def export_data(user: CurrentUser, db: DB):
    """下载课程、解析文字、任务、作答、设置、问答和复习建议的 JSON，不含原文件二进制、题库、图谱、密码或令牌。"""
    def rows(model, render):
        """查询当前账号指定模型的记录，并使用传入的序列化函数转换成导出结构。"""
        return [render(row) for row in db.scalars(select(model).where(model.user_id == user.id))]
    value = {'version': 2, 'exportedAt': iso(utcnow()), 'settings': profile(user),
        'courses': rows(Course, course_view), 'materials': rows(Material, lambda m: material_view(m, True)),
        'tasks': rows(Task, task_view), 'attempts': rows(Attempt, attempt_view), 'chatHistory': rows(ChatTurn, chat_view),
        'coachHistory': rows(CoachHistory, coach_history_view)}
    return Response(json.dumps(value, ensure_ascii=False, indent=2), media_type='application/json',
        headers={'Content-Disposition': 'attachment; filename="zhiqu-learning-data.json"'})


@router.post(
    '/data/reset',
    summary='清空本人学习数据',
    response_model=output.ResetOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def reset_data(body: Reset, user: CurrentUser, db: DB) -> dict:
    """必须提供指定 confirm 文字；删除当前账号学习数据，保留账号和偏好。只有 seedDemo=true 才重建样例。"""
    db.execute(delete(Course).where(Course.user_id == user.id))
    db.execute(delete(Task).where(Task.user_id == user.id))
    if body.seedDemo:
        seed_user(db, user.id)
    db.info.setdefault('notification_users', set()).add(user.id)
    return {'message': '学习数据已重置，账号和偏好保留', 'seedDemo': body.seedDemo}
