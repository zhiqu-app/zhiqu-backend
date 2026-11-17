"""Shared task validation and transactional completion; callers lock the task row."""
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from .dependencies import own
from .models import Course, Question, Task, TaskQuestion, TaskSubtask, utcnow


def locked_task(db, user_id, task_id):
    """Serialize submissions, edits and due scans against the same owned task."""
    task = db.scalar(select(Task).where(Task.user_id == user_id, Task.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(404, '资源不存在或无权访问')
    return task


def task_target(task):
    """Build the trusted practice destination, including task and course context."""
    return '/app/practice?' + urlencode({'task': task.id, 'course': task.course_id})


def prepare_task(db, user_id, values):
    """Validate course and question ownership before adding a new task."""
    course_id = values.pop('courseId')
    question_ids = values.pop('questionIds')
    values['reminder_at'] = values.pop('reminderAt')
    values['course_id'] = course_id
    if values['kind'] == 'exercise':
        if values['done'] or not course_id or not question_ids or len(set(question_ids)) != len(question_ids):
            raise HTTPException(422, '习题计划需要课程和不重复的题目，不能手动完成')
        own(db, Course, user_id, course_id)
        found = set(db.scalars(select(Question.id).where(Question.user_id == user_id,
            Question.course_id == course_id, Question.id.in_(question_ids), Question.deleted_at.is_(None))
            .order_by(Question.id).with_for_update()))
        if found != set(question_ids):
            raise HTTPException(422, '请选择本人同一课程中仍在题库里的题目')
    elif course_id is not None or question_ids:
        raise HTTPException(422, '自定义计划不能关联习题')
    return question_ids


def add_task_questions(db, task, question_ids):
    """Persist selected questions without importing any prior attempt progress."""
    for position, question_id in enumerate(question_ids):
        db.add(TaskQuestion(user_id=task.user_id, task_id=task.id, question_id=question_id, position=position))
    if task.kind == 'exercise':
        task.target = task_target(task)
    db.flush()
    db.expire(task, ['questions'])


def attempt_counts_for_completion(attempt):
    """The agreed rule: every successfully saved submission counts, including wrong answers."""
    return True


def complete_task(db, task):
    """Atomically mark completion and persist exactly one completion notification."""
    from .notifications import record_notification
    if not task.done:
        task.done = True
        task.completed_at = utcnow()
        if task.from_question:
            task.active_key = None
        record_notification(db, task, 'completed')


def record_task_progress(db, task, attempt):
    """Count only a validated submission belonging to the locked task."""
    if attempt_counts_for_completion(attempt):
        selected = next(q for q in task.questions if q.question_id == attempt.question_id)
        if selected.completed_at is None:
            selected.completed_at = utcnow()
        if task.questions and all(q.completed_at is not None for q in task.questions):
            complete_task(db, task)
    db.info.setdefault('notification_users', set()).add(task.user_id)
    db.flush()


def locked_subtasks(db, task):
    """Use current locking reads after the parent lock, avoiding stale MySQL snapshots."""
    children = list(db.scalars(select(TaskSubtask).where(TaskSubtask.user_id == task.user_id,
        TaskSubtask.task_id == task.id).order_by(TaskSubtask.position)
        .with_for_update().execution_options(populate_existing=True)))
    set_committed_value(task, 'subtasks', children)
    return children


def aggregate_subtasks(db, task):
    """Complete only a nonempty fully checked list, reopening on any pending child."""
    if task.subtasks and all(child.done for child in task.subtasks):
        complete_task(db, task)
    else:
        task.done = False
        task.completed_at = None
    db.info.setdefault('notification_users', set()).add(task.user_id)
    db.flush()


def replace_subtasks(db, task, items):
    """Validate the whole edit then replace titles/order without trusting snapshot done."""
    existing = {child.id: child for child in locked_subtasks(db, task)}
    supplied = [item['id'] for item in items if item.get('id')]
    if len(supplied) != len(set(supplied)) or not set(supplied) <= set(existing):
        raise HTTPException(409, '小任务已删除、重复或不属于此计划，请刷新后重试')
    if any(item.get('done') is True for item in items if not item.get('id')):
        raise HTTPException(422, '新增小任务必须未完成')
    children = []
    for position, item in enumerate(items):
        child = existing.get(item.get('id'))
        if child is None:
            child = TaskSubtask(user_id=task.user_id, task_id=task.id, done=False)
        child.title = item['title']
        child.position = position
        children.append(child)
    task.subtasks = children
    db.flush()
    aggregate_subtasks(db, task)
