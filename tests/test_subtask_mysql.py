"""Real MySQL parent-lock serialization and additive migration in random test schemas."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select

from app.models import Base, Notification, TaskSubtask
from test_review_mysql import assert_fixture_schema
from test_review_migration import seed_legacy_review_tasks
from test_subtask_migration import seed_0005_custom, check_0006_backfill
from test_task_subtasks import create_custom, toggle, BASE

pytestmark = pytest.mark.skipif(os.getenv('RUN_MYSQL_TESTS') != '1', reason='Requires fixture-owned random MySQL schema')


def test_mysql_0005_to_0006_preserves_custom_and_exercise(database):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        Base.metadata.drop_all(connection)
        config.attributes['connection'] = connection
        command.upgrade(config, '0004')
        seed_legacy_review_tasks(connection)
        command.upgrade(config, '0005')
        seed_0005_custom(connection)
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        config.attributes['connection'] = connection
        command.upgrade(config, '0006')
        check_0006_backfill(connection)


def test_mysql_two_child_checks_finish_without_lost_completion_or_snapshot_overwrite(client, auth, database):
    with database.engine.connect() as connection:
        assert_fixture_schema(connection)
    # A barrier launches independent HTTP transactions together. Check completion
    # before retries so replays cannot conceal a lost final aggregation transition.
    for round_no in range(4):
        task = create_custom(client, auth, title=f'并发大任务{round_no}')
        barrier = threading.Barrier(2)
        def check(index):
            barrier.wait(timeout=10)
            return toggle(client, auth, task, index, True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(check, index) for index in range(2)]
            responses = [job.result(timeout=20) for job in jobs]
        assert all(r.status_code == 200 for r in responses), [(r.status_code, r.text) for r in responses]
        saved = client.get(BASE + '/tasks/' + task['id'], headers=auth).json()
        assert saved['done'] is True and saved['completedAt']
        assert all(child['done'] for child in saved['subtasks'])
        edited = client.patch(BASE + '/tasks/' + task['id'], headers=auth,
            json={'subtasks': task['subtasks']})
        assert edited.status_code == 200 and edited.json()['done'] is True
        assert all(child['done'] for child in edited.json()['subtasks'])
        assert toggle(client, auth, task, 0, False).json()['completedAt'] is None
        with database.sessions() as db:
            assert db.scalar(select(func.count()).select_from(TaskSubtask).where(TaskSubtask.task_id == task['id'])) == 2
            assert db.scalar(select(func.count()).select_from(Notification).where(Notification.task_id == task['id'], Notification.kind == 'completed')) == 1
