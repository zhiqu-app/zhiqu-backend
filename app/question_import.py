"""按已有课程批量导入个人 JSON 题库；整批校验、事务保存、完整内容去重。"""
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .dependencies import CurrentUser, DB, own
from .models import Course, Material, Question, User
from .parameters import CourseId
from .responses import ERROR_RESPONSES
from .schemas import Input, QuestionCreate


class QuestionImport(Input):
    questions: Annotated[list[QuestionCreate], Field(min_length=1, max_length=200)] = Field(
        description='待导入的单选题数组，1～200 题。先校验整批；任一题非法时全部不写入。')


class QuestionImportOut(BaseModel):
    courseId: str = Field(description='保存题库的本人课程 ID。')
    imported: int = Field(description='本次实际新增的题目数量。')
    skipped: int = Field(description='与已有题目或本文件前面题目完整内容重复、被跳过的数量。')
    total: int = Field(description='导入后该课程的题目总数。')


router = APIRouter(tags=['课程题库配置'], responses={code: ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 422, 503)})


def fingerprint(values):
    """对规范化题干、选项、答案、解析、知识点、来源计算可比较的完整内容键。"""
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@router.post('/courses/{course_id}/questions/import', summary='批量导入课程 JSON 题库', response_model=QuestionImportOut)
def import_questions(course_id: CourseId, body: QuestionImport, user: CurrentUser, db: DB) -> dict:
    """只向本人已存在课程追加题目；同一用户行锁防并发重复，完整重复跳过，原题和作答保留。"""
    own(db, Course, user.id, course_id)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    for material_id in {item.source for item in body.questions if item.source}:
        if own(db, Material, user.id, material_id).course_id != course_id:
            raise HTTPException(422, '题目来源必须属于当前课程；整批尚未导入')
    existing = db.scalars(select(Question).where(Question.user_id == user.id, Question.course_id == course_id)).all()
    seen = {fingerprint({'title': row.title, 'options': row.options, 'answer': row.answer,
        'explanation': row.explanation, 'point': row.point, 'source': row.source_id}) for row in existing}
    additions = []
    for item in body.questions:
        values = item.model_dump()
        key = fingerprint(values)
        if key not in seen:
            seen.add(key)
            additions.append(values)
    current_count = db.scalar(select(func.count()).select_from(Question).where(Question.user_id == user.id))
    if current_count + len(additions) > 1000:
        raise HTTPException(409, '账号题库上限为 1000 题，本次整批尚未导入')
    for values in additions:
        source = values.pop('source')
        db.add(Question(user_id=user.id, course_id=course_id, source_id=source, sample=False, **values))
    db.flush()
    return {'courseId': course_id, 'imported': len(additions), 'skipped': len(body.questions) - len(additions),
            'total': len(existing) + len(additions)}
