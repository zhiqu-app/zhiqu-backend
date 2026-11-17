"""数据库 snake_case 与现有 Vue camelCase 的边界。禁止直接序列化 ORM __dict__。"""
from datetime import timezone

from .chat_quiz import can_prepare_questions


def iso(value):
    """将数据库 UTC 时间转为以 Z 结尾的 ISO 8601 字符串。"""
    return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def profile(user):
    """将用户展示资料转换为 Vue 使用的 camelCase 字段，不返回密码。"""
    return {'name': user.name, 'major': user.major, 'grade': user.grade, 'email': user.contact_email,
            'avatar': user.avatar or '', 'reducedMotion': user.reduced_motion, 'reminders': user.reminders}


def user_view(user):
    """构造登录身份响应；体验账号隐藏内部生成的登录邮箱。"""
    return {'id': user.id, 'email': '' if user.is_guest else user.email, 'isGuest': user.is_guest,
            'emailVerified': user.email_verified,
            'profile': profile(user)}


def course_view(row):
    """将课程 ORM 转为响应字段，并按完成课时计算进度百分比。"""
    return {'id': row.id, 'name': row.name, 'teacher': row.teacher, 'description': row.description,
            'chapter': row.chapter, 'lessons': row.lessons, 'completedLessons': row.completed_lessons,
            'progress': row.completed_lessons * 100 // row.lessons if row.lessons else 0,
            'progressSource': 'lessonCompletion', 'cover': row.cover, 'archived': row.archived,
            'sample': row.sample, 'createdAt': iso(row.created_at)}


def material_view(row, detail=False):
    """构造资料元信息；detail=true 才附带解析正文，不返回原始二进制。"""
    result = {'id': row.id, 'courseId': row.course_id, 'name': row.name, 'type': row.kind,
              'sizeBytes': row.size_bytes, 'size': f'{row.size_bytes / 1024:.1f} KB',
              'created': iso(row.created_at)[:10], 'createdAt': iso(row.created_at),
              'sample': row.sample, 'parseStatus': row.parse_status}
    if detail:
        result['text'] = row.text_content
    return result


def question_view(row, graded=False):
    """构造题目结构；仅 graded=true 时加入答案和解析，供判题快照使用。"""
    result = {'id': row.id, 'courseId': row.course_id, 'title': row.title, 'options': row.options,
              'point': row.point, 'source': row.source_id, 'sample': row.sample,
              'knowledgeNodeId': row.knowledge_node_id}
    if graded:
        result.update(answer=row.answer, explanation=row.explanation, sourceEvidence=row.source_evidence)
    return result


def attempt_view(row):
    """序列化作答记录，包含判题结果、幂等键和服务端时间。"""
    return {'id': row.id, 'courseId': row.course_id, 'questionId': row.question_id,
            'title': row.question_snapshot.get('title', '练习题'), 'point': row.point,
            'knowledgeNodeId': row.knowledge_node_id, 'sourceEvidence': row.question_snapshot.get('sourceEvidence'),
            'submissionId': row.submission_id, 'choice': row.choice, 'correct': row.correct,
            'seconds': row.seconds, 'date': iso(row.created_at)}


def task_view(row):
    """序列化复习任务，将未指定计划日期转换为空字符串。"""
    return {'id': row.id, 'title': row.title, 'description': row.description, 'minutes': row.minutes,
            'date': row.planned_date.isoformat() if row.planned_date else '', 'done': row.done,
            'target': row.target, 'createdAt': iso(row.created_at),
            'kind': row.kind, 'courseId': row.course_id,
            'subtasks': [{'id': s.id, 'title': s.title, 'done': s.done} for s in row.subtasks],
            'questionIds': [q.question_id for q in row.questions],
            'completedQuestionIds': [q.question_id for q in row.questions if q.completed_at],
            'reminderAt': iso(row.reminder_at) if row.reminder_at else None,
            'completedAt': iso(row.completed_at) if row.completed_at else None}


def coach_history_view(row):
    """读取已保存的历史建议以及生成时的课程名称、目标和时间。"""
    return {**row.recommendation, 'id': row.id, 'courseId': row.course_id,
            'courseName': row.course_name, 'goal': row.goal, 'createdAt': iso(row.created_at)}


def chat_view(row):
    """序列化持久化问答和来源快照，标明真实使用的模型/检索模式。"""
    return {'id': row.id, 'courseId': row.course_id, 'question': row.question, 'text': row.text,
            'sources': row.sources, 'questions': row.questions or [], 'canPrepareQuestions': can_prepare_questions(row),
            'mode': row.mode, 'model': row.model, 'createdAt': iso(row.created_at)}
