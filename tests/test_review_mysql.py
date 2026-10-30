"""Opt-in real MySQL migrations and row-lock races; only fixture-owned random schemas."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import re
import threading

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
import pytest

from app.models import Base, Attempt, Notification, Task, TaskAttempt, TaskQuestion
from app.notifications import scan_due
from test_review_migration import seed_legacy_review_tasks, assert_legacy_review_upgrade

pytestmark = pytest.mark.skipif(os.getenv('RUN_MYSQL_TESTS') != '1', reason='Requires RUN_MYSQL_TESTS=1 and fixture-owned random MySQL database')
BASE = '/api/v1'


def assert_fixture_schema(connection):
    """Refuse metadata deletion unless both the URL and actual server schema are the random test database."""
    schema = connection.engine.url.database
    assert connection.dialect.name == 'mysql'
    assert isinstance(schema, str) and re.fullmatch(r'zhiqu_orm_test_[0-9a-f]{24}', schema), 'Refusing to modify a non-test schema'
    assert connection.scalar(text('SELECT DATABASE()')) == schema, 'Connection schema differs from fixture URL'


def test_mysql_0004_to_0005_preserves_legacy_completion_and_pending_progress(database):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        Base.metadata.drop_all(connection)
        config.attributes['connection'] = connection
        command.upgrade(config, '0004')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0004'
        seed_legacy_review_tasks(connection)
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        config.attributes['connection'] = connection
        command.upgrade(config, '0005')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0005'
        assert_legacy_review_upgrade(connection)
        assert connection.scalar(select(func.count()).select_from(Notification)) == 0
        assert connection.scalar(select(func.count()).select_from(TaskAttempt)) == 0


def test_mysql_two_question_submissions_retries_and_due_scan_race(client, auth, database):
    with database.engine.connect() as connection:
        assert_fixture_schema(connection)
    user_id = client.get(BASE + '/auth/me', headers=auth).json()['id']
    historical = client.post(BASE + '/attempts', headers=auth,
        json={'questionId': 'q1', 'choice': 1, 'submissionId': 'mysql_history_001'})
    assert historical.status_code == 200, historical.text
    created = client.post(BASE + '/tasks', headers=auth, json={'title': '并发复习', 'kind': 'exercise',
        'courseId': 'linear', 'questionIds': ['q1', 'q2'], 'reminderAt': '2020-01-01T00:00:00Z'})
    assert created.status_code == 201, created.text
    task_id = created.json()['id']
    assert created.json()['completedQuestionIds'] == []
    scan_due(database)
    rescheduled = client.patch(BASE + '/tasks/' + task_id, headers=auth,
        json={'reminderAt': '2020-01-02T00:00:00Z'})
    assert rescheduled.status_code == 200, rescheduled.text

    barrier = threading.Barrier(6)
    def submit_question(question_id, submission_id, choice):
        """Run requests simultaneously, retaining the same idempotency key for retries."""
        barrier.wait(timeout=10)
        return client.post(BASE + '/attempts', headers=auth,
            json={'questionId': question_id, 'submissionId': submission_id, 'choice': choice, 'taskId': task_id})

    def race_due_scan():
        """Compete for the same task row using independent worker-thread transactions."""
        barrier.wait(timeout=10)
        for _ in range(3):
            scan_due(database)

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(submit_question, question, key, choice)
            for question, key, choice in [('q1', 'mysql_q1_001', 0), ('q2', 'mysql_q2_001', 1)]]
        scans = [pool.submit(race_due_scan) for _ in range(4)]
        responses = [job.result(timeout=30) for job in jobs]
        for job in scans:
            job.result(timeout=30)
    assert all(response.status_code == 200 for response in responses), [(response.status_code, response.text) for response in responses]
    assert len({response.json()['attempt']['id'] for response in responses}) == 2
    # Check before retries: replaying a submission must not be able to hide a
    # missed completion transition caused by concurrent progress writes.
    first_wave = client.get(BASE + '/tasks/' + task_id, headers=auth).json()
    assert first_wave['done'] is True and set(first_wave['completedQuestionIds']) == {'q1', 'q2'}
    barrier = threading.Barrier(6)
    with ThreadPoolExecutor(max_workers=6) as pool:
        retries = [pool.submit(submit_question, question, key, choice)
            for question, key, choice in [('q1', 'mysql_q1_001', 0), ('q1', 'mysql_q1_001', 0),
                                         ('q2', 'mysql_q2_001', 1), ('q2', 'mysql_q2_001', 1)]]
        scans = [pool.submit(race_due_scan) for _ in range(2)]
        replayed = [job.result(timeout=30) for job in retries]
        for job in scans:
            job.result(timeout=30)
    assert all(response.status_code == 200 for response in replayed), [(response.status_code, response.text) for response in replayed]
    assert {response.json()['attempt']['id'] for response in replayed} == {response.json()['attempt']['id'] for response in responses}
    scan_due(database)
    final = client.get(BASE + '/tasks/' + task_id, headers=auth).json()
    assert final['done'] is True and final['completedAt']
    assert set(final['completedQuestionIds']) == {'q1', 'q2'}
    assert client.get(BASE + '/attempts', headers=auth).json()['total'] == 3
    with database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attempt).where(Attempt.user_id == user_id)) == 3
        assert db.scalar(select(func.count()).select_from(TaskAttempt).where(TaskAttempt.user_id == user_id, TaskAttempt.task_id == task_id)) == 2
        progress = list(db.scalars(select(TaskQuestion).where(TaskQuestion.user_id == user_id, TaskQuestion.task_id == task_id)))
        assert len(progress) == 2 and all(question.completed_at for question in progress)
        notifications = list(db.scalars(select(Notification).where(Notification.user_id == user_id, Notification.task_id == task_id)))
        occurrences = Counter((item.kind, item.occurrence_at) for item in notifications)
        assert all(count == 1 for count in occurrences.values())
        assert len([item for item in notifications if item.kind == 'completed']) == 1
        due_times = {item.occurrence_at for item in notifications if item.kind == 'due'}
        assert datetime(2020, 1, 1) in due_times
        assert due_times <= {datetime(2020, 1, 1), datetime(2020, 1, 2)}
