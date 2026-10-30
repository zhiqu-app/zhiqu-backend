"""Custom parents complete only through owned, persisted checklist children."""
import pytest
from sqlalchemy import func, select

from app.models import Task

BASE = '/api/v1'


def create_custom(client, auth, **values):
    response = client.post(BASE + '/tasks', headers=auth, json={'title': '大任务',
        'subtasks': [{'title': '小任务一'}, {'title': '小任务二'}], **values})
    assert response.status_code == 201, response.text
    return response.json()


def toggle(client, auth, task, index, done):
    return client.patch(f"{BASE}/tasks/{task['id']}/subtasks/{task['subtasks'][index]['id']}",
        headers=auth, json={'done': done})


def test_children_aggregate_completion_cancel_and_deduplicate_notifications(client, auth):
    task = create_custom(client, auth)
    assert len(task['subtasks']) == 2 and all(not item['done'] for item in task['subtasks'])
    first = toggle(client, auth, task, 0, True).json()
    assert first['done'] is False and first['completedAt'] is None
    assert not [n for n in client.get(BASE + '/notifications', headers=auth).json()['items'] if n['taskId'] == task['id']]
    finished = toggle(client, auth, task, 1, True).json()
    assert finished['done'] is True and finished['completedAt']
    assert toggle(client, auth, task, 1, True).json()['completedAt'] == finished['completedAt']
    undone = toggle(client, auth, task, 0, False).json()
    assert undone['done'] is False and undone['completedAt'] is None
    assert toggle(client, auth, task, 0, True).json()['done'] is True
    notices = [n for n in client.get(BASE + '/notifications', headers=auth).json()['items'] if n['taskId'] == task['id']]
    assert len(notices) == 1 and notices[0]['kind'] == 'completed'
    assert client.get(BASE + '/tasks/' + task['id'], headers=auth).json()['done'] is True


def test_editor_preserves_database_done_and_add_remove_reaggregate(client, auth):
    task = create_custom(client, auth)
    assert toggle(client, auth, task, 0, True).status_code == 200
    stale = [{'id': item['id'], 'title': item['title'] + '改名', 'done': False} for item in task['subtasks']]
    edited = client.patch(BASE + '/tasks/' + task['id'], headers=auth, json={'subtasks': stale}).json()
    assert edited['subtasks'][0]['done'] is True and edited['done'] is False
    # Removing the only pending child completes; adding a fresh child reopens.
    edited = client.patch(BASE + '/tasks/' + task['id'], headers=auth,
        json={'subtasks': [stale[0]]}).json()
    assert edited['done'] is True
    edited = client.patch(BASE + '/tasks/' + task['id'], headers=auth,
        json={'subtasks': [stale[0], {'title': '新项'}]}).json()
    assert edited['done'] is False and edited['completedAt'] is None
    assert [s['done'] for s in edited['subtasks']] == [True, False]
    reordered = client.patch(BASE + '/tasks/' + task['id'], headers=auth,
        json={'subtasks': list(reversed(edited['subtasks']))}).json()
    assert [s['id'] for s in reordered['subtasks']] == [s['id'] for s in reversed(edited['subtasks'])]


@pytest.mark.parametrize('invalid', [[], [{'title': ''}], [{'title': '   '}], [{'title': 'x' * 201}],
    [{'title': 'x'}] * 101, [{'title': 'x', 'done': True}], [{'id': 'forged', 'title': 'x'}], None])
def test_custom_create_rejects_invalid_children_without_writes(client, auth, database, invalid):
    with database.sessions() as db:
        before = db.scalar(select(func.count()).select_from(Task))
    response = client.post(BASE + '/tasks', headers=auth, json={'title': 'bad', 'subtasks': invalid})
    assert response.status_code == 422, response.text
    with database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == before


def test_parent_done_cannot_bypass_children_legacy_post_and_exercise_unchanged(client, auth):
    response = client.post(BASE + '/tasks', headers=auth, json={'title': '旧客户端', 'done': False})
    assert response.status_code == 201
    task = response.json()
    assert [(s['title'], s['done']) for s in task['subtasks']] == [('旧客户端', False)]
    assert client.post(BASE + '/tasks', headers=auth, json={'title': '绕过', 'done': True}).status_code == 422
    for done in [False, True]:
        assert client.patch(BASE + '/tasks/' + task['id'], headers=auth, json={'done': done}).status_code == 422
    exercise = client.post(BASE + '/tasks', headers=auth, json={'title': '习题', 'kind': 'exercise',
        'courseId': 'linear', 'questionIds': ['q1']}).json()
    assert exercise['subtasks'] == []
    assert client.patch(BASE + '/tasks/' + exercise['id'], headers=auth, json={'subtasks': [{'title': 'x'}]}).status_code == 422
    assert client.post(BASE + '/tasks', headers=auth, json={'title': '习题', 'kind': 'exercise',
        'courseId': 'linear', 'questionIds': ['q1'], 'subtasks': [{'title': 'x'}]}).status_code == 422


def test_checklist_ownership_duplicate_foreign_ids_and_atomic_rejection(client, auth):
    task = create_custom(client, auth)
    second = create_custom(client, auth)
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    assert toggle(client, other, task, 0, True).status_code == 404
    assert client.patch(f"{BASE}/tasks/{task['id']}/subtasks/{second['subtasks'][0]['id']}",
        headers=auth, json={'done': True}).status_code == 404
    for invalid in [[], [{'title': ' '}], [task['subtasks'][0], task['subtasks'][0]], [second['subtasks'][0]],
        [{'title': 'new', 'done': True}], [{'id': 'missing', 'title': 'new'}]]:
        response = client.patch(BASE + '/tasks/' + task['id'], headers=auth,
            json={'title': 'should rollback', 'subtasks': invalid})
        assert response.status_code in {409, 422}, response.text
        current = client.get(BASE + '/tasks/' + task['id'], headers=auth).json()
        assert current['title'] == task['title'] and current['subtasks'] == task['subtasks']
    assert client.delete(BASE + '/tasks/' + task['id'], headers=auth).status_code == 204
    assert toggle(client, auth, task, 0, True).status_code == 404


def test_subtask_toggle_publishes_change_to_owners_open_pages(client, auth, settings):
    task = create_custom(client, auth)
    with client.websocket_connect(BASE + '/notifications/ws', headers={'origin': settings.cors_origins[0]}) as ws:
        ws.send_json({'type': 'authenticate', 'token': auth['Authorization'][7:]})
        assert ws.receive_json() == {'type': 'ready'}
        assert toggle(client, auth, task, 0, True).status_code == 200
        assert ws.receive_json() == {'type': 'notifications.changed'}


def test_demo_seed_tasks_have_persisted_legacy_children(client, auth):
    tasks = client.get(BASE + '/tasks', headers=auth).json()['items']
    assert tasks
    for task in tasks:
        assert task['kind'] == 'custom' and len(task['subtasks']) == 1
        assert task['subtasks'][0]['title'] == task['title']
        assert task['subtasks'][0]['done'] is False and task['done'] is False
        assert toggle(client, auth, task, 0, True).json()['done'] is True
