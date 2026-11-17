from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import responses as output
from .chat_quiz import PREPARE_QUIZ_PROMPT, can_prepare_questions, parse_chat_quiz
from .dependencies import DB, CurrentUser, Limit, Offset, own, paginated, enforce_limit, rate
from .image_questions import DEFAULT_QUESTION, IMAGE_QUESTION_PREFIX, answer_image_question, image_data_url
from .models import ChatTurn, CoachHistory, Course, KnowledgeNode, Material, Question
from .parameters import OptionalCourse, RequiredCourse
from .practice_drafts import PracticeDraftRequest, PracticeDraftsOut, generate_practice_drafts
from .question_similarity import QuestionTitleIndex
from .review_coach import CoachConfirm, CoachHistoryEntry, CoachRequest, confirm_review, review_preview
from .review_materials import ready_materials
from .schemas import Ask
from .serializers import chat_view, coach_history_view
from .services import answer_question, model_completion, retrieve, source_context
from .workspace_guide import GuideAsk, GuideAnswer, guide_answer

router = APIRouter(tags=['资料问答'])


@router.post('/assistant/review-coach', summary='生成并保存专项复习建议', response_model=CoachHistoryEntry,
             responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 422, 429, 503)})
def review_coach(body: CoachRequest, request: Request, user: CurrentUser, db: DB) -> dict:
    """按近期错题定位薄弱点并保存当时的建议，可稍后回看。"""
    user_id = user.id
    rate(request, 'review_coach', user_id, 8)
    recommendation = review_preview(db, user_id, body, request.app.state.settings)
    course = own(db, Course, user_id, body.courseId)
    row = CoachHistory(user_id=user_id, course_id=body.courseId, course_name=course.name,
                       goal=body.goal, recommendation=recommendation)
    db.add(row)
    db.flush()
    return coach_history_view(row)


@router.get('/assistant/review-coach/history', summary='分页查询复习教练建议历史',
            response_model=output.Page[CoachHistoryEntry],
            responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 413, 422, 503)})
def review_coach_history(user: CurrentUser, db: DB, courseId: OptionalCourse = None,
                         limit: Limit = 10, offset: Offset = 0) -> dict:
    """按生成时间倒序返回本人复习建议快照，可筛选一门课程。"""
    statement = select(CoachHistory).where(CoachHistory.user_id == user.id)
    if courseId:
        own(db, Course, user.id, courseId)
        statement = statement.where(CoachHistory.course_id == courseId)
    statement = statement.order_by(CoachHistory.created_at.desc(), CoachHistory.id.desc())
    return paginated(db, statement, limit, offset, coach_history_view)


@router.post('/assistant/review-coach/confirm', summary='确认专项复习计划', response_model=output.TaskOut,
             responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 422, 503)})
def confirm_coach_plan(body: CoachConfirm, user: CurrentUser, db: DB) -> dict:
    """点击确认后保存习题计划，重复确认相同题目和时长返回同一计划。"""
    return confirm_review(db, user.id, body)


@router.post('/assistant/workspace-guide', summary='询问学习空间操作向导', response_model=GuideAnswer,
             responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 413, 422, 429, 503)})
def workspace_guide(body: GuideAsk, request: Request, user: CurrentUser, db: DB) -> dict:
    """按当前页面和本人课程提供操作帮助，可调用资料状态、题库状态与操作手册工具。"""
    rate(request, 'workspace_guide', user.id, 10)
    return guide_answer(db, user.id, body, request.app.state.settings)


@router.post(
    '/assistant/ask',
    summary='基于本人课程资料问答',
    response_model=output.ChatOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
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
    """按课程或指定资料提供问答、知识点梳理、练习和拓展，保存回答与原文引用。"""
    own(db, Course, user.id, body.courseId)
    if body.materialId is not None:
        material = own(db, Material, user.id, body.materialId)
        if material.course_id != body.courseId:
            raise HTTPException(404, '资料不存在或无权访问')
    rate(request, 'ask', user.id, 10)
    enforce_limit(db, ChatTurn, user.id, 1000)
    user_id = user.id
    materials = ready_materials(db, user_id, body.courseId, body.materialId)
    # 不在外部模型请求期间占用事务或连接；返回后重新确认课程仍存在且属于本人。
    db.commit()
    result = answer_question(request.app.state.settings, body.question, materials)
    own(db, Course, user_id, body.courseId)
    row = ChatTurn(user_id=user_id, course_id=body.courseId, question=body.question, **result)
    db.add(row)
    db.flush()
    return chat_view(row)


@router.post(
    '/assistant/history/{chat_id}/question-drafts',
    summary='整理旧聊天中的练习题',
    response_model=output.ChatOut,
    responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 413, 422, 429, 502, 503, 504)},
)
def prepare_chat_questions(
    chat_id: Annotated[str, Path(min_length=1, max_length=32, description='本人问答历史的记录 ID。')],
    request: Request, user: CurrentUser, db: DB,
) -> dict:
    """根据旧回复及其原文引用整理并保存单选题草稿，题库仍由用户确认后导入。"""
    user_id = user.id
    row = own(db, ChatTurn, user_id, chat_id)
    if row.questions:
        return chat_view(row)
    if not can_prepare_questions(row):
        raise HTTPException(422, '这条回答没有可根据原文整理的练习题')
    settings = request.app.state.settings
    if not settings.ai_enabled:
        raise HTTPException(503, '服务器未配置 AI 出题服务')
    rate(request, 'prepare_chat_questions', user_id, 5)
    sources = row.sources
    context = f'旧聊天答案：\n{row.text}\n\n编号原文片段：\n{source_context(sources)}'
    db.commit()
    response = model_completion(settings, 'prepare_chat_questions', [
        {'role': 'system', 'content': PREPARE_QUIZ_PROMPT},
        {'role': 'user', 'content': context},
    ], max_tokens=3200)
    _, drafts = parse_chat_quiz(response, sources)
    if not drafts:
        raise HTTPException(422, '原文不足以整理为单选题，请选中讲义后重新生成练习题')
    row = db.scalar(select(ChatTurn).where(ChatTurn.id == chat_id, ChatTurn.user_id == user_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '问答记录不存在或无权访问')
    if not row.questions:
        row.questions = drafts
    return chat_view(row)


@router.post(
    '/assistant/ask-image',
    summary='基于学习图片与本人课程资料问答',
    response_model=output.ChatOut,
    responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 415, 422, 429, 502, 503, 504)},
)
def ask_image(
    request: Request, user: CurrentUser, db: DB,
    courseId: Annotated[str, Form(min_length=1, max_length=64, description='本人课程 ID，用于限定资料检索范围。')],
    image: Annotated[UploadFile, File(description='单张静态 PNG、JPEG 或 WebP 图片，最多 5 MB，边长不超过 4096 像素。')],
    question: Annotated[str, Form(max_length=2000 - len(IMAGE_QUESTION_PREFIX),
                                  description='可选的图片问题，留空则解释图片中的内容与知识点。')] = '',
    requestId: Annotated[str | None, Form(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$',
                                          description='可选的重试标识，同一账号、课程及问题重复提交时复用问答记录。')] = None,
) -> dict:
    """识别图片并结合本人课程原文回答。历史保存问题、回答和引文，图片用于本次识别。"""
    user_id = user.id
    own(db, Course, user_id, courseId)
    question = question.strip() or DEFAULT_QUESTION
    turn_id = sha256(f'{user_id}\0{courseId}\0{requestId}\0{question}'.encode()).hexdigest()[:32] if requestId else None
    if turn_id and (existing := db.get(ChatTurn, turn_id)) is not None:
        return chat_view(existing)
    settings = request.app.state.settings
    if not settings.vision_enabled:
        raise HTTPException(503, '服务器未配置支持图片输入的 AI 模型')
    rate(request, 'ask_image', user_id, 5)
    enforce_limit(db, ChatTurn, user_id, 1000)
    materials = ready_materials(db, user_id, courseId)
    db.commit()
    result = answer_image_question(settings, question, image_data_url(image.file), materials)
    own(db, Course, user_id, courseId)
    row = ChatTurn(id=turn_id, user_id=user_id, course_id=courseId, question=IMAGE_QUESTION_PREFIX + question, **result)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if turn_id and (existing := db.get(ChatTurn, turn_id)) is not None:
            return chat_view(existing)
        raise
    return chat_view(row)


def draft_node(db, user_id, course_id, node_id):
    """读取当前课程指定节点的最新出题主题和说明。"""
    node = db.execute(select(KnowledgeNode.label, KnowledgeNode.description).where(
        KnowledgeNode.user_id == user_id, KnowledgeNode.course_id == course_id,
        KnowledgeNode.id == node_id)).one_or_none()
    if node is None:
        raise HTTPException(404, '知识点不存在或无权访问')
    return node


@router.post(
    '/assistant/practice-drafts',
    summary='从本人课程资料生成知识点单选题草稿',
    response_model=PracticeDraftsOut,
    response_model_exclude_none=True,
    responses={
        **{code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 422, 429, 502, 504)},
        503: {**output.ERROR_RESPONSES[503], 'description': 'AI 出题未配置或数据库暂不可用。'},
    },
)
def practice_drafts(body: PracticeDraftRequest, request: Request, user: CurrentUser, db: DB) -> dict:
    """选取指定知识点的课程资料片段，返回带可核对引用的草稿供用户确认。"""
    user_id = user.id
    own(db, Course, user_id, body.courseId)
    node = draft_node(db, user_id, body.courseId, body.knowledgeNodeId)
    if body.materialId is not None:
        material = own(db, Material, user_id, body.materialId)
        if material.course_id != body.courseId:
            raise HTTPException(404, '资料不存在或无权访问')
    settings = request.app.state.settings
    if not settings.ai_enabled:
        raise HTTPException(503, '服务器未配置 AI 出题服务')
    rate(request, 'practice_drafts', user_id, 5)
    materials = ready_materials(db, user_id, body.courseId, body.materialId)
    bank = select(Question.title).where(Question.user_id == user_id,
        Question.course_id == body.courseId, Question.deleted_at.is_(None))
    recent_bank = db.scalars(bank.order_by((Question.knowledge_node_id == body.knowledgeNodeId).desc(),
        Question.created_at.desc(), Question.id.desc()).limit(24)).all()
    excluded = list(dict.fromkeys(title[:240] for title in [*reversed(body.excludeTitles), *recent_bank]))[:32]
    # 释放数据库连接后再检索和调用模型。
    db.commit()
    sources = retrieve(node.label, materials, practice=True)
    if not sources:
        raise HTTPException(422, '课程资料中没有找到与该知识点相关的可用片段，请补充资料或选择其他资料')
    drafts = generate_practice_drafts(settings, body.knowledgeNodeId, node.label, node.description,
        sources, body.count, excluded)

    if draft_node(db, user_id, body.courseId, body.knowledgeNodeId) != node:
        raise HTTPException(409, '知识点已更新，请重新生成草稿')
    source_ids = {draft['source'] for draft in drafts}
    available = set(db.scalars(select(Material.id).where(Material.user_id == user_id,
        Material.course_id == body.courseId, Material.parse_status == 'ready', Material.id.in_(source_ids))))
    drafts = [draft for draft in drafts if draft['source'] in available]
    if not drafts:
        raise HTTPException(409, '引用资料已更新，请重新生成草稿')
    titles = QuestionTitleIndex([*db.scalars(bank), *body.excludeTitles])
    unique = []
    for draft in drafts:
        if not titles.contains(draft['title']):
            unique.append(draft)
            titles.add(draft['title'])
    if not unique:
        raise HTTPException(422, '这次生成的题目与题库或最近的草稿重复，请更换资料或知识点后重试')
    return {'questions': unique}


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
