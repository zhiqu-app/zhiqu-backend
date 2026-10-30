from . import responses as output
from .parameters import RequiredCourse
from fastapi import APIRouter, Request
from sqlalchemy import select

from .dependencies import DB, CurrentUser, Limit, Offset, own, paginated, enforce_limit, rate
from .models import Course, Material, ChatTurn
from .schemas import Ask
from .serializers import chat_view
from .services import answer_question

router = APIRouter(tags=['资料问答'])


@router.post(
    '/assistant/ask',
    summary='基于本人课程资料问答',
    response_model=output.ChatOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        429: output.ERROR_RESPONSES[429],
        502: output.ERROR_RESPONSES[502],
        503: output.ERROR_RESPONSES[503],
        504: output.ERROR_RESPONSES[504],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def ask(body: Ask, request: Request, user: CurrentUser, db: DB) -> dict:
    """默认使用文字资料检索；只有配置外部模型后才调用模型。保存回答与引用，返回一次性 JSON，不是 SSE。"""
    own(db, Course, user.id, body.courseId)
    rate(request, 'ask', user.id, 10)
    enforce_limit(db, ChatTurn, user.id, 1000)
    statement = select(Material.id, Material.name, Material.text_content).where(
        Material.user_id == user.id, Material.course_id == body.courseId, Material.parse_status == 'ready')
    materials = [{'id': row.id, 'name': row.name, 'text': row.text_content} for row in db.execute(statement)]
    user_id = user.id
    # 不在外部模型请求期间占用事务或连接；返回后重新确认课程仍存在且属于本人。
    db.commit()
    result = answer_question(request.app.state.settings, body.question, materials)
    own(db, Course, user_id, body.courseId)
    row = ChatTurn(user_id=user_id, course_id=body.courseId, question=body.question, **result)
    db.add(row)
    db.flush()
    return chat_view(row)


@router.get(
    '/assistant/history',
    summary='分页查询课程问答历史',
    response_model=output.Page[output.ChatOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def history(user: CurrentUser, db: DB, courseId: RequiredCourse, limit: Limit = 50, offset: Offset = 0) -> dict:
    """返回本人指定课程的持久化问答，按时间倒序；sources 是当时引用快照。"""
    own(db, Course, user.id, courseId)
    return paginated(db, select(ChatTurn).where(ChatTurn.user_id == user.id,
        ChatTurn.course_id == courseId).order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc()),
        limit, offset, chat_view)
