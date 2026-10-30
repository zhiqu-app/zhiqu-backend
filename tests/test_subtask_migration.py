"""0006 backfills one checklist child without altering legacy task data."""
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select

from test_review_migration import seed_legacy_review_tasks, assert_legacy_review_upgrade


def check_0006_backfill(connection):
    tasks = Table('tasks', MetaData(), autoload_with=connection)
    children = Table('task_subtasks', MetaData(), autoload_with=connection)
    rows = list(connection.execute(select(children)))
    assert len(rows) == 2
    by_task = {s.task_id: s for s in rows}
    assert bool(by_task['custom_done'].done) is True and bool(by_task['custom_pending'].done) is False
    assert by_task['custom_done'].title == 'custom_done'
    assert by_task['custom_pending'].title == '待完成'
    assert all(s.user_id == 'owner' and s.position == 0 and len(s.id) == 32 for s in rows)
    assert len({s.id for s in rows}) == 2
    saved = {t.id: t for t in connection.execute(select(tasks))}
    assert saved['custom_pending'].created_at == datetime(2021, 2, 3, 4, 5, 6)
    assert saved['custom_pending'].completed_at is None and not saved['custom_pending'].done
    assert_legacy_review_upgrade(connection)


def seed_0005_custom(connection):
    tasks = Table('tasks', MetaData(), autoload_with=connection)
    connection.execute(tasks.insert().values(user_id='owner', id='custom_pending', title='待完成',
        description='保留说明', minutes=23, done=False, target='/app/plan', kind='custom',
        created_at=datetime(2021, 2, 3, 4, 5, 6)))


def test_0005_to_0006_preserves_parent_completion_ids_time_and_exercise(tmp_path):
    root = Path(__file__).resolve().parents[1]
    engine = create_engine('sqlite:///' + (tmp_path / 'subtasks-upgrade.db').as_posix())
    config = Config(str(root / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0004')
        seed_legacy_review_tasks(connection)
        command.upgrade(config, '0005')
        seed_0005_custom(connection)
        original = {name: list(connection.execute(select(Table(name, MetaData(), autoload_with=connection))))
            for name in ['tasks', 'task_questions', 'attempts', 'notifications']}
        command.upgrade(config, '0006')
        check_0006_backfill(connection)
        for name, rows in original.items():
            assert list(connection.execute(select(Table(name, MetaData(), autoload_with=connection)))) == rows
    engine.dispose()
