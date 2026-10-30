from . import responses as output
from .parameters import CourseId, MaterialId, TaskId, SubtaskId, OptionalCourse, Search, CourseStatus, TaskStatus, InlinePreview
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import Integer, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .dependencies import DB, CurrentUser, Limit, Offset, own, paginated, enforce_limit, rate
from .models import Course, Material, Question, Task, User
from .schemas import CourseCreate, CoursePatch, TaskCreate, TaskPatch, SubtaskToggle
from .serializers import course_view, material_view, task_view
from .services import parse_material, PDF_LIMIT, TEXT_LIMIT, STORAGE_LIMIT
from .task_progress import prepare_task, add_task_questions, locked_task, complete_task, replace_subtasks, locked_subtasks, aggregate_subtasks

router = APIRouter(tags=['课程、资料与计划'])


@router.get(
    '/courses',
    summary='分页查询课程',
    response_model=output.CoursePage,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def courses(user: CurrentUser, db: DB, q: Search = '',
            status: CourseStatus = 'all', limit: Limit = 50, offset: Offset = 0) -> dict:
    """按关键词和归档状态查询本人课程，返回资料数量、分页结果和课程页签计数。"""
    statement = select(Course).where(Course.user_id == user.id)
    if q:
        statement = statement.where((Course.name.contains(q, autoescape=True)) | (Course.description.contains(q, autoescape=True)))
    if status != 'all':
        statement = statement.where(Course.archived.is_(status == 'archived'))
    # 一次聚合得到资料数，避免每张卡片发起 N+1 查询。
    counts = dict(db.execute(select(Material.course_id, func.count()).where(Material.user_id == user.id).group_by(Material.course_id)).all())
    result = paginated(db, statement.order_by(Course.created_at, Course.id), limit, offset,
        lambda row: {**course_view(row), 'materialCount': counts.get(row.id, 0)})
    total, archived = db.execute(select(func.count(), func.coalesce(func.sum(Course.archived.cast(Integer)), 0)).where(Course.user_id == user.id)).one()
    result['counts'] = {'all': total, 'active': total - int(archived), 'archived': int(archived)}
    return result


@router.post(
    '/courses',
    status_code=201,
    summary='新建课程',
    response_model=output.CourseOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def create_course(body: CourseCreate, user: CurrentUser, db: DB) -> dict:
    """创建属于当前账号的课程；ID 和时间由服务端生成，初始已完成课时为 0。"""
    enforce_limit(db, Course, user.id, 100)
    row = Course(user_id=user.id, **body.model_dump())
    db.add(row)
    db.flush()
    return course_view(row)


@router.get(
    '/courses/{course_id}',
    summary='查询课程详情',
    response_model=output.CourseOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def course_detail(course_id: CourseId, user: CurrentUser, db: DB) -> dict:
    """返回本人课程的名称、简介、课时与进度，可用于动态显示知识星图课程标题。"""
    return course_view(own(db, Course, user.id, course_id))


@router.patch(
    '/courses/{course_id}',
    summary='修改课程或归档状态',
    response_model=output.CourseOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def update_course(course_id: CourseId, body: CoursePatch, user: CurrentUser, db: DB) -> dict:
    """部分更新课程；已完成课时不能超过总课时，progress 由后端计算，不接受前端伪造百分比。"""
    row = own(db, Course, user.id, course_id)
    values = body.model_dump(exclude_unset=True)
    lessons = values.get('lessons', row.lessons)
    completed = values.get('completedLessons', row.completed_lessons)
    if completed > lessons:
        raise HTTPException(422, '已完成课时不能超过总课时')
    for key, value in values.items():
        setattr(row, 'completed_lessons' if key == 'completedLessons' else key, value)
    db.flush()
    return course_view(row)


@router.delete(
    '/courses/{course_id}',
    status_code=204,
    summary='删除课程及其关联学习数据',
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def delete_course(course_id: CourseId, user: CurrentUser, db: DB):
    """删除本人课程及由该课错题生成的任务；外键级联删除资料、题库、作答、图谱和问答，返回 204。"""
    course = own(db, Course, user.id, course_id)
    # 自动错题任务记录来源题目 ID；只清理这些任务，保留用户手工建立的复习计划。
    questions = select(Question.id).where(Question.user_id == user.id, Question.course_id == course_id)
    db.execute(delete(Task).where(Task.user_id == user.id, Task.from_question.in_(questions)))
    db.delete(course)
    db.flush()
    db.info.setdefault('notification_users', set()).add(user.id)
    return Response(status_code=204)


@router.get(
    '/materials',
    summary='分页查询课程资料',
    response_model=output.Page[output.MaterialOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def materials(user: CurrentUser, db: DB, courseId: OptionalCourse = None,
               q: Search = '', limit: Limit = 50, offset: Offset = 0) -> dict:
    """查询本人资料元信息，可按课程和文件名过滤；列表不加载原文件和大段正文。"""
    statement = select(Material).where(Material.user_id == user.id)
    if courseId:
        own(db, Course, user.id, courseId)
        statement = statement.where(Material.course_id == courseId)
    if q:
        statement = statement.where(Material.name.contains(q, autoescape=True))
    return paginated(db, statement.order_by(Material.created_at.desc(), Material.id), limit, offset, material_view)


@router.post(
    '/courses/{course_id}/materials',
    status_code=201,
    summary='上传并解析课程资料',
    response_model=output.MaterialDetailOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        415: output.ERROR_RESPONSES[415],
        422: output.ERROR_RESPONSES[422],
        429: output.ERROR_RESPONSES[429],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def upload_material(course_id: CourseId, request: Request, user: CurrentUser, db: DB,
                          file: Annotated[UploadFile, File(description='课程原始资料文件，multipart/form-data 字段名必须为 file；TXT/MD ≤500 KB，PDF ≤20 MB。')]) -> dict:
    """通过 multipart/form-data 的 file 字段上传 TXT/MD/PDF。文本上限 500 KB，PDF 20 MB/200 页；原文件和解析正文持久保存在 MySQL。扫描 PDF 不做 OCR。"""
    own(db, Course, user.id, course_id)
    rate(request, 'upload', user.id, 20)
    filename = PurePosixPath((file.filename or '').replace('\\', '/')).name
    filename = ''.join(char for char in filename if char.isprintable()).strip()
    if not filename or len(filename) > 180:
        raise HTTPException(422, '文件名为空或过长')
    limit = PDF_LIMIT if filename.lower().endswith('.pdf') else TEXT_LIMIT
    try:
        content = file.file.read(limit + 1)
    finally:
        file.file.close()
    text, kind, mime, status = parse_material(filename, content)
    # 同一用户的并发上传在 MySQL 上串行校验配额，不能同时绕过总量限制。
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    enforce_limit(db, Material, user.id, 200)
    used = db.scalar(select(func.coalesce(func.sum(Material.size_bytes), 0)).where(Material.user_id == user.id))
    if used + len(content) > STORAGE_LIMIT:
        raise HTTPException(413, '每个账号原文件总量上限为 100 MB')
    row = Material(user_id=user.id, course_id=course_id, name=filename, kind=kind,
        content=content, text_content=text, size_bytes=len(content), media_type=mime, parse_status=status)
    db.add(row)
    db.flush()
    return material_view(row, detail=True)


@router.get(
    '/materials/{material_id}',
    summary='查询资料正文',
    response_model=output.MaterialDetailOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def material_detail(material_id: MaterialId, user: CurrentUser, db: DB) -> dict:
    """返回资料元信息和解析后的 text，用于阅读、检索和引用定位；原文件使用单独下载接口。"""
    return material_view(own(db, Material, user.id, material_id), detail=True)


@router.get(
    '/materials/{material_id}/file',
    summary='下载或预览原始文件',
    response_class=Response,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
        200: {'description': '原始文件二进制', 'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}, 'text/plain': {'schema': {'type': 'string', 'format': 'binary'}}}},
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def material_file(material_id: MaterialId, user: CurrentUser, db: DB, inline: InlinePreview = False):
    """返回原始二进制文件。请求需要 Bearer；前端可先 fetch 得到 Blob，再创建 iframe 预览地址。"""
    row = own(db, Material, user.id, material_id)
    mode = 'inline' if inline and row.kind == 'PDF' else 'attachment'
    return Response(row.content, media_type=row.media_type,
        headers={'Content-Disposition': f"{mode}; filename*=UTF-8''{quote(row.name)}"})


@router.delete(
    '/materials/{material_id}',
    status_code=204,
    summary='删除课程资料',
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def delete_material(material_id: MaterialId, user: CurrentUser, db: DB):
    """删除本人资料，已有问答和判题保留历史快照，但之后跳转到被删来源会返回 404。"""
    row = own(db, Material, user.id, material_id)
    # 新作答不再链接被删除资料；既有作答/问答保留当时的快照，并允许来源跳转返回 404。
    db.execute(update(Question).where(Question.user_id == user.id, Question.source_id == row.id).values(source_id=None))
    db.delete(row)
    db.flush()
    return Response(status_code=204)


@router.get(
    '/tasks',
    summary='分页查询复习任务',
    response_model=output.Page[output.TaskOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def tasks(user: CurrentUser, db: DB, status: TaskStatus = 'all',
           limit: Limit = 50, offset: Offset = 0) -> dict:
    """按任务状态查询本人复习计划；today 按 Asia/Shanghai 日期匹配。"""
    from .study import today_local
    statement = select(Task).where(Task.user_id == user.id)
    if status in {'pending', 'done'}:
        statement = statement.where(Task.done.is_(status == 'done'))
    if status == 'today':
        statement = statement.where(Task.planned_date == today_local())
    return paginated(db, statement.order_by(Task.created_at, Task.id), limit, offset, task_view)


@router.post(
    '/tasks',
    status_code=201,
    summary='新增复习任务',
    response_model=output.TaskOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def create_task(body: TaskCreate, user: CurrentUser, db: DB) -> dict:
    """保存父任务与未完成小任务清单，或关联习题；服务端生成 ID。"""
    enforce_limit(db, Task, user.id, 1000)
    values = body.model_dump()
    items = values.pop('subtasks')
    question_ids = prepare_task(db, user.id, values)
    planned = values.pop('date')
    row = Task(user_id=user.id, planned_date=date.fromisoformat(planned) if planned else None, **values)
    db.add(row)
    db.flush()
    add_task_questions(db, row, question_ids)
    if row.kind == 'custom':
        replace_subtasks(db, row, items if items is not None else [{'title': row.title}])
    db.info.setdefault('notification_users', set()).add(user.id)
    return task_view(row)


@router.get('/tasks/{task_id}', response_model=output.TaskOut, summary='读取本人复习任务及持久化进度', responses=output.ERROR_RESPONSES)
def task_detail(task_id: TaskId, user: CurrentUser, db: DB):
    """返回有序小任务或习题进度；历史作答不会计入新习题计划。"""
    return task_view(own(db, Task, user.id, task_id))


@router.patch(
    '/tasks/{task_id}',
    summary='编辑任务与小任务清单',
    response_model=output.TaskOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def update_task(task_id: TaskId, body: TaskPatch, user: CurrentUser, db: DB) -> dict:
    """原子编辑本人任务与清单；已有小任务保留数据库勾选状态，父任务自动聚合。"""
    row = locked_task(db, user.id, task_id)
    values = body.model_dump(exclude_unset=True)
    fixed = {'kind': row.kind, 'courseId': row.course_id, 'questionIds': [q.question_id for q in row.questions]}
    for key, expected in fixed.items():
        if key in values and values.pop(key) != expected:
            raise HTTPException(422, '计划类型、课程和题目创建后不可更改，请新建计划')
    if 'done' in values:
        raise HTTPException(422, '父任务由小任务或作答进度自动完成，不能直接勾选')
    if row.kind == 'exercise' and 'subtasks' in values:
        raise HTTPException(422, '习题计划不能设置小任务清单')
    if row.kind == 'exercise' and 'target' in values:
        raise HTTPException(422, '习题跳转路径由后端生成')
    if 'subtasks' in values:
        replace_subtasks(db, row, values.pop('subtasks'))
    for key, value in values.items():
        if key == 'date':
            row.planned_date = date.fromisoformat(value) if value else None
        elif key == 'reminderAt':
            row.reminder_at = value
        else:
            setattr(row, key, value)
    if row.from_question:
        row.active_key = None if row.done else 'mistake:' + row.from_question
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, '该题已有待完成复习任务，不能重复恢复') from None
    db.info.setdefault('notification_users', set()).add(user.id)
    return task_view(row)


@router.patch('/tasks/{task_id}/subtasks/{subtask_id}', response_model=output.TaskOut,
    summary='勾选本人小任务并自动聚合父任务完成状态', responses=output.ERROR_RESPONSES)
def toggle_subtask(task_id: TaskId, subtask_id: SubtaskId, body: SubtaskToggle,
                   user: CurrentUser, db: DB) -> dict:
    """锁定本人父任务后勾选子项；全部完成才发完成提醒，取消任一项恢复待办。"""
    row = locked_task(db, user.id, task_id)
    if row.kind != 'custom':
        raise HTTPException(422, '习题计划没有可勾选的小任务')
    children = locked_subtasks(db, row)
    selected = next((child for child in children if child.id == subtask_id), None)
    if selected is None:
        raise HTTPException(404, '资源不存在或无权访问')
    selected.done = body.done
    aggregate_subtasks(db, row)
    return task_view(row)


@router.delete(
    '/tasks/{task_id}',
    status_code=204,
    summary='删除复习任务',
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def delete_task(task_id: TaskId, user: CurrentUser, db: DB):
    """删除当前账号的指定任务，成功返回 204 无正文。"""
    db.delete(own(db, Task, user.id, task_id))
    db.flush()
    db.info.setdefault('notification_users', set()).add(user.id)
    return Response(status_code=204)
