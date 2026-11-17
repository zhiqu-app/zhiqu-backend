from . import responses as output
from .parameters import CourseId, QuestionId, RequiredCourse, OptionalCourse, OptionalKnowledgeNode, OptionalQuestionTask, MistakeStatus, ReportRange
from collections import Counter
from datetime import datetime, timedelta, timezone
import math
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import Integer, and_, case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from .dependencies import DB, CurrentUser, Limit, Offset, own, paginated, enforce_limit
from .models import Course, Material, Question, Attempt, Task, TaskAttempt, TaskQuestion, KnowledgeNode, KnowledgeEdge, CourseKnowledgeConfig, utcnow
from .task_progress import locked_task, record_task_progress, add_task_questions
from .schemas import AttemptCreate, QuestionCreate, QuestionKnowledgeLink, NodeCreate, EdgeCreate
from .question_import import active_question_count, fingerprint, locked_question_keys
from .question_links import question_fields, require_node
from .serializers import attempt_view, question_view, task_view

router = APIRouter(tags=['题库、作答、错题、报告与图谱'])
LOCAL = timezone(timedelta(hours=8))


def today_local():
    """返回 Asia/Shanghai 对应的当天日期，供任务与报告统计使用。"""
    return datetime.now(LOCAL).date()


def percent(correct, count):
    """计算与 JavaScript Math.round 一致的整数百分比，分母为 0 时返回 0。"""
    return math.floor(correct * 100 / count + 0.5) if count else 0


@router.get(
    '/questions',
    summary='分页查询题目且不返回答案',
    response_model=output.Page[output.QuestionOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def questions(user: CurrentUser, db: DB, courseId: RequiredCourse,
               limit: Limit = 50, offset: Offset = 0, knowledgeNodeId: OptionalKnowledgeNode = None,
               taskId: OptionalQuestionTask = None) -> dict:
    """读取当前课程题库或指定习题计划的题目。保留既有计划里的题目，答案只在判题后返回。"""
    own(db, Course, user.id, courseId)
    statement = select(Question).where(Question.user_id == user.id, Question.course_id == courseId)
    if taskId:
        task = own(db, Task, user.id, taskId)
        if task.kind != 'exercise' or task.course_id != courseId:
            raise HTTPException(404, '当前课程没有该习题计划')
        statement = statement.join(TaskQuestion, and_(
            TaskQuestion.user_id == Question.user_id, TaskQuestion.question_id == Question.id,
            TaskQuestion.task_id == taskId))
    else:
        statement = statement.where(Question.deleted_at.is_(None))
    if knowledgeNodeId:
        require_node(db, user.id, courseId, knowledgeNodeId)
        statement = statement.where(Question.knowledge_node_id == knowledgeNodeId)
    return paginated(db, statement.order_by(Question.created_at, Question.id), limit, offset, question_view)


@router.get(
    '/questions/{question_id}',
    summary='查询单道题目',
    response_model=output.QuestionOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def question_detail(question_id: QuestionId, user: CurrentUser, db: DB) -> dict:
    """查询单道题的题面。已移出题库的题目仅供已有作答者回看。"""
    row = own(db, Question, user.id, question_id)
    if row.deleted_at is not None and not has_question_attempt(db, row):
        raise HTTPException(404, '题目已从题库移除')
    return question_view(row)


def has_question_attempt(db, question):
    """检查本人是否有这道题的作答历史。"""
    return db.scalar(select(Attempt.id).where(
        Attempt.user_id == question.user_id, Attempt.question_id == question.id).limit(1)) is not None


@router.post(
    '/courses/{course_id}/questions',
    status_code=201,
    summary='维护本人课程题库',
    response_model=output.QuestionOut,
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
def create_question(course_id: CourseId, body: QuestionCreate, user: CurrentUser, db: DB) -> dict:
    """手工创建本人课程题目，来源必须属于同一课程。查重时统一全半角 ASCII 字符，同题干、同选项返回 409。"""
    own(db, Course, user.id, course_id)
    values = question_fields(db, user.id, course_id, body)
    if fingerprint(body.title, body.options) in locked_question_keys(db, user.id, course_id):
        raise HTTPException(409, '当前课程已存在相同题干和选项的题目，请检查题库')
    if active_question_count(db, user.id) >= 1000:
        raise HTTPException(409, '数量已达上限 1000，请先整理数据')
    row = Question(user_id=user.id, course_id=course_id, **values)
    db.add(row)
    db.flush()
    return question_view(row)


def locked_question(db, user_id, question_id):
    """串行化题目删除、关联变更、作答与复测任务创建。"""
    row = db.scalar(select(Question).where(Question.user_id == user_id,
        Question.id == question_id).with_for_update())
    if row is None:
        raise HTTPException(404, '题目不存在或无权访问')
    return row


@router.delete(
    '/courses/{course_id}/questions/{question_id}',
    status_code=204,
    summary='从课程题库删除一道题',
    response_class=Response,
    response_description='已从题库移除，无响应体。',
    responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 422, 503)},
)
def delete_question(course_id: CourseId, question_id: QuestionId, user: CurrentUser, db: DB) -> Response:
    """从本人指定课程移除一道题，保留作答、错题及原有习题计划。对同一道已移除题目重复调用仍返回 204。"""
    row = locked_question(db, user.id, question_id)
    if row.course_id != course_id:
        raise HTTPException(404, '该题不属于当前课程')
    if row.deleted_at is None:
        row.deleted_at = utcnow()
    return Response(status_code=204)


@router.patch('/courses/{course_id}/questions/{question_id}/knowledge', summary='为已有题目关联课程知识点',
              response_model=output.QuestionOut,
              responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 422, 503)})
def link_question(course_id: CourseId, question_id: QuestionId, body: QuestionKnowledgeLink,
                  user: CurrentUser, db: DB) -> dict:
    """关联已有题目，并为此前未关联的作答补齐 ID；保留既有历史归属和题面快照。"""
    own(db, Course, user.id, course_id)
    node = require_node(db, user.id, course_id, body.knowledgeNodeId)
    row = locked_question(db, user.id, question_id)
    if row.course_id != course_id:
        raise HTTPException(404, '该题不属于当前课程')
    if row.deleted_at is not None:
        raise HTTPException(404, '题目已从题库移除')
    row.knowledge_node_id, row.point = node.id, node.label
    db.execute(update(Attempt).where(Attempt.user_id == user.id, Attempt.course_id == course_id,
        Attempt.question_id == row.id, Attempt.knowledge_node_id.is_(None)).values(knowledge_node_id=node.id))
    db.flush()
    return question_view(row)


def graded_result(row):
    """从不可变题目快照构造判题响应，重试不会随之后的题库修改而改变答案。"""
    snapshot = row.question_snapshot
    return {'attempt': attempt_view(row), 'answer': snapshot['answer'],
            'explanation': snapshot['explanation'], 'source': snapshot['source'], 'point': snapshot['point'],
            'knowledgeNodeId': row.knowledge_node_id, 'sourceEvidence': snapshot.get('sourceEvidence')}


@router.post(
    '/attempts',
    summary='提交作答并由服务端判题',
    response_model=output.GradeOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        409: output.ERROR_RESPONSES[409],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def submit(body: AttemptCreate, user: CurrentUser, db: DB) -> dict:
    """按题库答案判分并保存不可变快照。相同 submissionId 和相同请求只记一次；同键不同内容返回 409。"""
    question = locked_question(db, user.id, body.questionId)
    task = locked_task(db, user.id, body.taskId) if body.taskId else None
    if task and (task.kind != 'exercise' or task.course_id != question.course_id or
                 question.id not in [q.question_id for q in task.questions]):
        raise HTTPException(422, '题目不属于此习题计划')
    if question.deleted_at is not None and task is None and not has_question_attempt(db, question):
        raise HTTPException(404, '题目已从题库移除')
    if body.choice >= len(question.options):
        raise HTTPException(422, '选项不存在')
    query = select(Attempt).where(Attempt.user_id == user.id, Attempt.submission_id == body.submissionId)
    previous = db.scalar(query)
    if previous is None:
        enforce_limit(db, Attempt, user.id, 100000)
        try:
            # SAVEPOINT：唯一约束冲突只撤销本次 INSERT，不破坏外层事务。
            with db.begin_nested():
                snapshot = question_view(question, graded=True)
                if task and task.from_question and task.from_question != question.id:
                    snapshot['retestOf'] = task.from_question
                previous = Attempt(user_id=user.id, course_id=question.course_id, question_id=question.id,
                    submission_id=body.submissionId, choice=body.choice, correct=body.choice == question.answer,
                    seconds=body.seconds, point=question.point, knowledge_node_id=question.knowledge_node_id,
                    question_snapshot=snapshot)
                db.add(previous)
                db.flush()
                if task:
                    db.add(TaskAttempt(attempt_id=previous.id, user_id=user.id, task_id=task.id))
                    db.flush()
        except IntegrityError:
            previous = db.scalar(query)
            if previous is None:
                raise HTTPException(409, '作答保存冲突，请刷新后重试') from None
    if (previous.question_id, previous.choice, previous.seconds) != (body.questionId, body.choice, body.seconds):
        raise HTTPException(409, 'submissionId 已被其他作答使用')
    binding = db.get(TaskAttempt, previous.id)
    if (binding.task_id if binding else None) != body.taskId:
        raise HTTPException(409, 'submissionId 已属于其他计划或普通作答')
    if task:
        record_task_progress(db, task, previous)
    return {**graded_result(previous), 'task': task_view(task) if task else None}


@router.get(
    '/attempts',
    summary='分页查询作答记录',
    response_model=output.Page[output.AttemptOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def attempts(user: CurrentUser, db: DB, courseId: OptionalCourse = None,
              limit: Limit = 50, offset: Offset = 0) -> dict:
    """读取当前账号的真实作答记录，可按课程筛选，按时间倒序返回。"""
    statement = select(Attempt).where(Attempt.user_id == user.id)
    if courseId:
        own(db, Course, user.id, courseId)
        statement = statement.where(Attempt.course_id == courseId)
    return paginated(db, statement.order_by(Attempt.created_at.desc(), Attempt.id.desc()), limit, offset, attempt_view)


def mistake_statement(user_id, course_id=None, node_id=None):
    """用窗口函数查询每题最近作答、最近错误和错误次数，避免按前端缓存判断错题。"""
    conditions = [Attempt.user_id == user_id]
    if course_id:
        conditions.append(Attempt.course_id == course_id)
    if node_id:
        conditions.append(Attempt.knowledge_node_id == node_id)
    def ranked(wrong_only=False):
        """为每题的作答按时间和记录 ID 排序，按需只保留错误记录供外层查询连接。"""
        statement = select(Attempt.id.label('aid'), Attempt.question_id.label('qid'), func.row_number().over(
            partition_by=Attempt.question_id, order_by=(Attempt.created_at.desc(), Attempt.id.desc())).label('rn')).where(*conditions)
        if wrong_only:
            statement = statement.where(Attempt.correct.is_(False))
        return statement.subquery()
    latest, wrong = ranked(), ranked(True)
    counts = select(Attempt.question_id.label('qid'), func.count().label('n')).where(*conditions,
        Attempt.correct.is_(False)).group_by(Attempt.question_id).subquery()
    last_attempt, last_wrong = aliased(Attempt), aliased(Attempt)
    statement = select(last_attempt, last_wrong, counts.c.n).join(latest,
        and_(last_attempt.id == latest.c.aid, latest.c.rn == 1)).join(counts,
        last_attempt.question_id == counts.c.qid).join(wrong,
        and_(wrong.c.rn == 1, wrong.c.qid == last_attempt.question_id)).join(last_wrong,
        and_(last_wrong.id == wrong.c.aid, last_wrong.question_id == last_attempt.question_id))
    return statement, last_attempt


def mistake_view(result):
    """组合错题快照和最近作答，计算 reviewed 并输出错误次数。"""
    latest, wrong, count = result
    return {**wrong.question_snapshot, 'knowledgeNodeId': wrong.knowledge_node_id,
            'reviewed': latest.correct, 'wrongCount': count,
            'lastWrong': attempt_view(wrong), 'lastAttempt': attempt_view(latest)}


@router.get(
    '/mistakes',
    summary='分页查询错题本',
    response_model=output.Page[output.MistakeOut],
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def mistakes(user: CurrentUser, db: DB, status: MistakeStatus = 'all',
              courseId: OptionalCourse = None, limit: Limit = 50, offset: Offset = 0,
              knowledgeNodeId: OptionalKnowledgeNode = None) -> dict:
    """收录历史曾答错的题；最近一次正确标记 reviewed，最近一次错误标记 pending。返回最近错误和最近作答快照。"""
    if courseId:
        own(db, Course, user.id, courseId)
    if knowledgeNodeId:
        if not courseId:
            raise HTTPException(422, '按知识点查询时必须指定课程')
        require_node(db, user.id, courseId, knowledgeNodeId)
    statement, latest = mistake_statement(user.id, courseId, knowledgeNodeId)
    if status != 'all':
        statement = statement.where(latest.correct.is_(status == 'reviewed'))
    total = db.scalar(statement.with_only_columns(func.count(), maintain_column_froms=True).order_by(None))
    rows = db.execute(statement.order_by(latest.created_at.desc(), latest.id.desc()).limit(limit).offset(offset)).all()
    return {'items': [mistake_view(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


@router.post(
    '/mistakes/{question_id}/plan',
    summary='将错题加入复习计划',
    response_model=output.PlanMistakeOut,
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
def plan_mistake(question_id: QuestionId, user: CurrentUser, db: DB) -> dict:
    """为本人已答错题创建待复习任务；已有同题待办时返回原任务，不重复创建。"""
    return schedule_mistake(db, user.id, question_id, retest=False)


@router.post('/mistakes/{question_id}/retest', summary='安排同知识点变式复测',
             response_model=output.PlanMistakeOut,
             responses={code: output.ERROR_RESPONSES[code] for code in (401, 404, 409, 422, 503)})
def plan_retest(question_id: QuestionId, user: CurrentUser, db: DB) -> dict:
    """为一道真实错题安排同知识点的未作答题，默认两天后提醒。"""
    return schedule_mistake(db, user.id, question_id, retest=True)


def schedule_mistake(db, user_id, question_id, *, retest):
    """同题同类型待办去重，保留原题重做和变式复测两个入口。"""
    question = locked_question(db, user_id, question_id)
    wrong = db.scalar(select(Attempt.id).where(Attempt.user_id == user_id,
        Attempt.question_id == question.id, Attempt.correct.is_(False)).limit(1))
    if not wrong:
        raise HTTPException(404, '没有这道题的错题记录')
    key = ('retest:' if retest else 'mistake:') + question.id
    query = select(Task).where(Task.user_id == user_id, Task.active_key == key).with_for_update()
    task = db.scalar(query)
    created = task is None
    if task is None:
        practice_id = unseen_variant(db, question) if retest else question.id
        due = utcnow() + timedelta(days=2) if retest else None
        enforce_limit(db, Task, user_id, 1000)
        try:
            with db.begin_nested():
                task = Task(user_id=user_id, title=(('变式复测：' if retest else '巩固：') + question.point)[:60],
                    description=question.title, minutes=10, target='/app/practice?question=' + practice_id,
                    from_question=question.id, active_key=key, kind='exercise', course_id=question.course_id,
                    planned_date=due.replace(tzinfo=timezone.utc).astimezone(LOCAL).date() if due else None,
                    reminder_at=due)
                db.add(task)
                db.flush()
                add_task_questions(db, task, [practice_id])
        except IntegrityError:
            task = db.scalar(query)
            if task is None:
                raise HTTPException(409, '复习任务创建冲突，请刷新') from None
            created = False
    db.info.setdefault('notification_users', set()).add(user_id)
    return {'created': created, 'task': task_view(task)}


def unseen_variant(db, question):
    """选择同节点、题干不同且当前用户未提交过的题目。"""
    if not question.knowledge_node_id or db.get(KnowledgeNode, (
        question.user_id, question.course_id, question.knowledge_node_id)) is None:
        raise HTTPException(409, '这道错题还没有关联课程星图中的知识点')
    attempted = select(Attempt.id).where(Attempt.user_id == question.user_id,
        Attempt.course_id == question.course_id, Attempt.question_id == Question.id).exists()
    candidates = db.execute(select(Question.id, Question.title, Question.options).where(
        Question.user_id == question.user_id, Question.course_id == question.course_id,
        Question.knowledge_node_id == question.knowledge_node_id, Question.id != question.id,
        Question.deleted_at.is_(None), ~attempted)
        .order_by(Question.created_at, Question.id).with_for_update())
    original_title, _ = fingerprint(question.title, question.options)
    for candidate_id, title, options in candidates:
        if fingerprint(title, options)[0] != original_title:
            return candidate_id
    raise HTTPException(409, '这个知识点还没有未作答的变式题，请先在课程题库添加一道不同的题')


def report_points(db, conditions):
    """按课程和稳定节点统计；没有活动节点的历史记录沿用名称快照。"""
    name = case((KnowledgeNode.id.is_not(None), KnowledgeNode.label), else_=Attempt.point)
    rows = db.execute(select(Attempt.course_id, KnowledgeNode.id, name, func.count(),
        func.sum(Attempt.correct.cast(Integer))).select_from(Attempt).outerjoin(KnowledgeNode, and_(
            KnowledgeNode.user_id == Attempt.user_id, KnowledgeNode.course_id == Attempt.course_id,
            KnowledgeNode.id == Attempt.knowledge_node_id)).where(*conditions)
        .group_by(Attempt.course_id, KnowledgeNode.id, name).order_by(name, Attempt.course_id, KnowledgeNode.id))
    return [{'courseId': course_id, 'knowledgeNodeId': node_id, 'name': label, 'count': count,
             'correct': int(correct), 'rate': percent(int(correct), count)}
            for course_id, node_id, label, count, correct in rows]


def report_summary(db, user_id, range_value='all', course_id=None):
    """按账号、课程和范围聚合作答，计算正确率、用时、知识点和上海时区近七日统计。"""
    conditions = [Attempt.user_id == user_id]
    if course_id:
        conditions.append(Attempt.course_id == course_id)
    if range_value != 'all':
        conditions.append(Attempt.created_at >= utcnow() - timedelta(days=int(range_value)))
    total, correct, seconds = db.execute(select(func.count(),
        func.coalesce(func.sum(Attempt.correct.cast(Integer)), 0), func.coalesce(func.sum(Attempt.seconds), 0)).where(*conditions)).one()
    correct, seconds = int(correct), int(seconds)
    points = report_points(db, conditions)
    today = today_local()
    midnight = datetime.combine(today - timedelta(days=6), datetime.min.time(), LOCAL).astimezone(timezone.utc).replace(tzinfo=None)
    # SQL 只读取近七天的时间列；按固定 +08:00 聚合，不依赖 MySQL 时区表。
    dates = db.scalars(select(Attempt.created_at).where(*conditions, Attempt.created_at >= midnight))
    counter = Counter(value.replace(tzinfo=timezone.utc).astimezone(LOCAL).date() for value in dates)
    days = [{'key': (day := today - timedelta(days=i)).isoformat(), 'label': f'{day.month}/{day.day}',
             'count': counter[day]} for i in range(6,-1,-1)]
    return {'total': total, 'correct': correct, 'rate': percent(correct,total), 'totalSeconds': seconds,
            'minutes': seconds // 60, 'seconds': seconds % 60, 'points': points, 'days': days,
            'maxCount': max(1, *(day['count'] for day in days)), 'range': range_value, 'timezone': 'Asia/Shanghai'}


@router.get(
    '/reports/summary',
    summary='查询学习报告统计',
    response_model=output.ReportOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def report(user: CurrentUser, db: DB, range: ReportRange = 'all', courseId: OptionalCourse = None) -> dict:
    """根据服务端作答记录计算正确率、累计用时、知识点统计和最近七天柱图。空账号返回零或空数组，不制造成绩。"""
    if courseId:
        own(db, Course, user.id, courseId)
    return report_summary(db, user.id, range, courseId)


@router.get(
    '/overview',
    summary='查询学习总览',
    response_model=output.OverviewOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def overview(user: CurrentUser, db: DB) -> dict:
    """聚合当前账号的课程数、资料数、作答次数、正确率和待复习任务；适合总览页一次读取。"""
    def count(model, *extra):
        """统计当前账号某类 ORM 资源的数量，可追加额外筛选条件。"""
        return db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id, *extra))
    summary = report_summary(db, user.id)
    total_tasks, done = count(Task), count(Task, Task.done.is_(True))
    return {'totalCourses': count(Course), 'activeCourses': count(Course, Course.archived.is_(False)),
            'materialCount': count(Material), 'pendingTasks': total_tasks - done,
            'taskCompletion': percent(done, total_tasks), 'attemptCount': summary['total'],
            'correctRate': summary['rate'], 'totalSeconds': summary['totalSeconds'],
            'recentTasks': [task_view(t) for t in db.scalars(select(Task).where(Task.user_id == user.id,
                Task.done.is_(False)).order_by(Task.created_at, Task.id).limit(5))]}


@router.get(
    '/knowledge',
    summary='查询课程知识星图',
    response_model=output.KnowledgeOut,
    responses={
        401: output.ERROR_RESPONSES[401],
        404: output.ERROR_RESPONSES[404],
        413: output.ERROR_RESPONSES[413],
        422: output.ERROR_RESPONSES[422],
        503: output.ERROR_RESPONSES[503],
    },
    response_description='成功，返回内容见下方 Schema；204 无响应体。',
)
def knowledge(user: CurrentUser, db: DB, courseId: RequiredCourse) -> dict:
    """读取数据库中维护的节点与连线，同时返回 ID 连接 links 和下标连接 edges。nodes/edges 必须配套传给星图组件；读取不会自动生成知识点。"""
    own(db, Course, user.id, courseId)
    rows = db.scalars(select(KnowledgeNode).where(KnowledgeNode.user_id == user.id,
        KnowledgeNode.course_id == courseId).order_by(KnowledgeNode.x, KnowledgeNode.id)).all()
    practice = node_practice(db, user.id, courseId)
    nodes = [{'id': n.id, 'label': n.label, 'description': n.description, 'x': n.x, 'y': n.y,
              'sample': n.sample, **practice.get(n.id, {'practiceCount': 0, 'practiceRate': 0,
                                                        'reviewStatus': 'unpracticed'})} for n in rows]
    positions = {node.id: i for i,node in enumerate(rows)}
    links = [{'source': e.source, 'target': e.target} for e in db.scalars(select(KnowledgeEdge).where(
        KnowledgeEdge.user_id == user.id, KnowledgeEdge.course_id == courseId).order_by(KnowledgeEdge.source, KnowledgeEdge.target))]
    return {'courseId': courseId, 'sample': bool(nodes) and all(node['sample'] for node in nodes), 'nodes': nodes, 'links': links,
            'edges': [[positions[e['source']],positions[e['target']]] for e in links]}


def node_practice(db, user_id, course_id):
    """只有在最近一次错误后答对不同题的复测，才把节点标为通过复测。"""
    conditions = (Attempt.user_id == user_id, Attempt.course_id == course_id, Attempt.knowledge_node_id.is_not(None))
    attempts = db.execute(select(Attempt.knowledge_node_id, func.count(), func.sum(Attempt.correct.cast(Integer)),
        func.max(case((Attempt.correct.is_(False), Attempt.created_at))))
        .where(*conditions).group_by(Attempt.knowledge_node_id))
    passed = dict(db.execute(select(Attempt.knowledge_node_id, func.max(Attempt.created_at))
        .where(*conditions, Attempt.question_snapshot['retestOf'].as_string().is_not(None), Attempt.correct.is_(True))
        .group_by(Attempt.knowledge_node_id)).tuples().all())
    result = {}
    for node_id, count, correct, last_wrong in attempts:
        status = 'practiced'
        if last_wrong is not None:
            last_passed = passed.get(node_id)
            status = 'retest_passed' if last_passed is not None and last_passed >= last_wrong else 'needs_review'
        result[node_id] = {'practiceCount': count, 'practiceRate': percent(int(correct), count), 'reviewStatus': status}
    return result


def touch_course_graph(course, user, db):
    """便捷节点/连线修改同步版本，防止旧整图编辑覆盖刚保存的内容。"""
    config = db.get(CourseKnowledgeConfig, (user.id, course.id))
    if config is None:
        config = CourseKnowledgeConfig(user_id=user.id, course_id=course.id, title=course.name + '知识星图', goal='', revision=1)
        db.add(config)
    else:
        config.revision += 1
    db.flush()


def lock_course_graph(db, user, course_id):
    """与整图保存使用相同课程行锁，串行化同一课程的图谱修改。"""
    course = db.scalar(select(Course).where(Course.user_id == user.id, Course.id == course_id).with_for_update())
    if course is None:
        raise HTTPException(404, '课程不存在或无权访问')
    return course


@router.post(
    '/courses/{course_id}/knowledge/nodes',
    status_code=201,
    summary='新增知识星图节点',
    response_model=output.NodeOut,
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
def create_node(course_id: CourseId, body: NodeCreate, user: CurrentUser, db: DB) -> dict:
    """保存节点名称、介绍和 SVG 坐标；返回节点 ID，后续创建连线使用此 ID。"""
    course = lock_course_graph(db, user, course_id)
    enforce_limit(db, KnowledgeNode, user.id, 2000)
    row = KnowledgeNode(user_id=user.id, course_id=course_id, **body.model_dump())
    db.add(row)
    db.flush()
    touch_course_graph(course, user, db)
    return {'id': row.id, **body.model_dump(), 'sample': False}


@router.post(
    '/courses/{course_id}/knowledge/edges',
    status_code=201,
    summary='新增知识星图连线',
    response_model=output.LinkOut,
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
def create_edge(course_id: CourseId, body: EdgeCreate, user: CurrentUser, db: DB) -> dict:
    """连接同一课程内的两个节点；source/target 使用节点 ID，不能自连，重复返回 409。"""
    course = lock_course_graph(db, user, course_id)
    enforce_limit(db, KnowledgeEdge, user.id, 5000)
    for node_id in [body.source, body.target]:
        if db.get(KnowledgeNode, (user.id, course_id, node_id)) is None:
            raise HTTPException(404, '节点不存在或不属于当前课程')
    if db.get(KnowledgeEdge, (user.id, course_id, body.source, body.target)):
        raise HTTPException(409, '连线已存在')
    children = {}
    for source, target in db.execute(select(KnowledgeEdge.source, KnowledgeEdge.target).where(
            KnowledgeEdge.user_id == user.id, KnowledgeEdge.course_id == course_id)):
        children.setdefault(source, []).append(target)
    pending, visited = [body.target], set()
    while pending:
        node = pending.pop()
        if node == body.source:
            raise HTTPException(422, '知识点先后关系不能形成循环')
        if node not in visited:
            visited.add(node)
            pending.extend(children.get(node, []))
    db.add(KnowledgeEdge(user_id=user.id, course_id=course_id, **body.model_dump()))
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, '连线创建冲突，请刷新') from None
    touch_course_graph(course, user, db)
    return body.model_dump()
