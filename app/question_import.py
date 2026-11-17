"""按已有课程批量导入个人 JSON 题库；整批校验、事务保存、按题干和选项去重。"""
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import func, select

from .dependencies import CurrentUser, DB, own
from .models import Course, Question, User
from .parameters import CourseId
from .responses import ERROR_RESPONSES
from .schemas import Input, QuestionCreate
from .question_links import question_fields
from .question_similarity import QuestionTitleIndex


class QuestionImport(Input):
    questions: Annotated[list[QuestionCreate], Field(min_length=1, max_length=200)] = Field(
        description='待导入的单选题数组，1～200 题。先校验整批；任一题非法时全部不写入。')
    deduplicateSimilar: StrictBool = Field(default=False, description='确认 AI 草稿时，对相同条件下的相似题干也查重。')


class QuestionImportOut(BaseModel):
    courseId: str = Field(description='保存题库的本人课程 ID。')
    imported: int = Field(description='本次实际新增的题目数量。')
    skipped: int = Field(description='跳过的重复题数。通常比较题干及选项，确认 AI 草稿时还比较相似题干。')
    total: int = Field(description='导入后该课程的题目总数。')


router = APIRouter(tags=['课程题库配置'], responses={code: ERROR_RESPONSES[code] for code in (401, 404, 409, 413, 422, 503)})


# 全角 ASCII 字符和全角空格用对应的半角形式比较，保留上标等具有独立含义的字符。
_WIDTH_TRANSLATION = {
    **{code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)},
    0x3000: ord(' '),
}


def fingerprint(title: str, options: list[str]) -> tuple[str, tuple[str, ...]]:
    """比较题干与选项，统一全半角、首尾空白和换行格式，忽略选项顺序。"""
    def comparable_text(text: str) -> str:
        """转换用于查重的字符形式，不改写题目原文。"""
        return text.translate(_WIDTH_TRANSLATION).replace('\r\n', '\n').strip()

    return comparable_text(title), tuple(sorted(comparable_text(option) for option in options))


def locked_course_questions(db, user_id: str, course_id: str):
    """手动创建和批量导入共用账号行锁，在锁内读取当前课程题目。"""
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    return db.execute(select(Question.title, Question.options).where(
        Question.user_id == user_id, Question.course_id == course_id, Question.deleted_at.is_(None))).all()


def locked_question_keys(db, user_id: str, course_id: str):
    """获取账号行锁后构造标准查重指纹。"""
    return [fingerprint(title, options) for title, options in locked_course_questions(db, user_id, course_id)]


def active_question_count(db, user_id: str) -> int:
    """仅统计当前仍在题库中的题目，供手动创建和批量导入检查数量上限。"""
    return db.scalar(select(func.count()).select_from(Question).where(
        Question.user_id == user_id, Question.deleted_at.is_(None)))


@router.post('/courses/{course_id}/questions/import', summary='批量导入课程 JSON 题库', response_model=QuestionImportOut)
def import_questions(course_id: CourseId, body: QuestionImport, user: CurrentUser, db: DB) -> dict:
    """向本人课程追加题目，同题干、同选项的题目跳过，原题和作答保留。"""
    own(db, Course, user.id, course_id)
    validated = [question_fields(db, user.id, course_id, item) for item in body.questions]
    existing = locked_course_questions(db, user.id, course_id)
    seen = {fingerprint(title, options) for title, options in existing}
    titles = QuestionTitleIndex(title for title, _ in existing) if body.deduplicateSimilar else None
    additions = []
    for values in validated:
        key = fingerprint(values['title'], values['options'])
        if key in seen or (titles is not None and titles.contains(values['title'])):
            continue
        seen.add(key)
        if titles is not None:
            titles.add(values['title'])
        additions.append(values)
    current_count = active_question_count(db, user.id)
    if current_count + len(additions) > 1000:
        raise HTTPException(409, '账号题库上限为 1000 题，本次整批尚未导入')
    for values in additions:
        db.add(Question(user_id=user.id, course_id=course_id, sample=False, **values))
    db.flush()
    return {'courseId': course_id, 'imported': len(additions), 'skipped': len(body.questions) - len(additions),
            'total': len(existing) + len(additions)}
