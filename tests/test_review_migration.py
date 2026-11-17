"""Upgrade old task data without losing completion or consuming historical attempts."""
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select

from app.models import User, Course, Question, Attempt


def seed_legacy_review_tasks(connection):
    """Insert representative 0004 records without using the new Task columns."""
    created_at = datetime(2020, 2, 3, 4, 5, 6)
    connection.execute(User.__table__.insert().values(id='owner', email='owner@example.test'))
    connection.execute(Course.__table__.insert().values(user_id='owner', id='course', name='课程'))
    connection.execute(Question.__table__.insert().values(user_id='owner', id='question', course_id='course',
        title='题目', options=['a', 'b'], answer=0, explanation='解析', point='知识点'))
    connection.execute(Attempt.__table__.insert().values(id='old_attempt', user_id='owner', course_id='course',
        question_id='question', submission_id='old_submission', choice=0, correct=True, seconds=1,
        point='知识点', question_snapshot={}))
    old_tasks = Table('tasks', MetaData(), autoload_with=connection)
    for task_id, source, done in [('custom_done', None, True), ('mistake_done', 'question', True), ('mistake_pending', 'question', False)]:
        connection.execute(old_tasks.insert().values(user_id='owner', id=task_id, title=task_id,
            description='', minutes=10, done=done, target='/app/practice', from_question=source,
            active_key='mistake:question' if source and not done else None, created_at=created_at))


def assert_legacy_review_upgrade(connection):
    """Check preservation using fixed expectations independent of migration code."""
    created_at = datetime(2020, 2, 3, 4, 5, 6)
    tasks = Table('tasks', MetaData(), autoload_with=connection)
    selected = Table('task_questions', MetaData(), autoload_with=connection)
    rows = {row.id: row for row in connection.execute(select(tasks)).all()}
    assert rows['custom_done'].done and rows['custom_done'].completed_at == created_at
    assert rows['custom_done'].kind == 'custom'
    assert rows['mistake_done'].done and rows['mistake_done'].completed_at == created_at
    assert rows['mistake_done'].kind == 'exercise' and rows['mistake_done'].course_id == 'course'
    progress = {row.task_id: row for row in connection.execute(select(selected)).all()}
    assert progress['mistake_done'].completed_at == created_at
    assert progress['mistake_pending'].completed_at is None
    assert not rows['mistake_pending'].done
    assert rows['mistake_pending'].completed_at is None
    assert '/app/practice?task=mistake_pending&course=course' == rows['mistake_pending'].target
    assert connection.scalars(select(Attempt.id)).all() == ['old_attempt']


def test_0005_preserves_completed_legacy_tasks_and_pending_progress(tmp_path):
    root = Path(__file__).resolve().parents[1]
    engine = create_engine('sqlite:///' + (tmp_path / 'legacy-review.db').as_posix())
    config = Config(str(root / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0004')
        seed_legacy_review_tasks(connection)
        command.upgrade(config, 'head')
        assert_legacy_review_upgrade(connection)
    engine.dispose()
