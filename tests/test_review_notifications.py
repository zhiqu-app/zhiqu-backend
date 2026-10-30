"""Task-scoped completion and durable notification behavior, on isolated SQLite."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketDisconnect

from app.models import AuthSession, Task, TaskAttempt, utcnow
from app.security import token_digest

BASE = '/api/v1'


def create_plan(client, auth, **values):
    response = client.post(BASE + '/tasks', headers=auth, json={'title': '复习', **values})
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, auth, question, submission, task=None, choice=0):
    body = {'questionId': question, 'submissionId': submission, 'choice': choice}
    if task:
        body['taskId'] = task
    return client.post(BASE + '/attempts', headers=auth, json=body)


def exercise(client, auth, questions=None):
    course = client.get(BASE + '/questions/q1', headers=auth).json()['courseId']
    return create_plan(client, auth, kind='exercise', courseId=course, questionIds=questions or ['q1', 'q2'])


def test_exercise_progress_is_task_scoped_and_wrong_answers_complete(client, auth):
    assert submit(client, auth, 'q1', 'history_0001').status_code == 200
    task = exercise(client, auth)
    assert task['kind'] == 'exercise'
    assert task['completedQuestionIds'] == []
    assert task['target'] == '/app/practice?task=' + task['id'] + '&course=' + task['courseId']
    assert client.patch(BASE + '/tasks/' + task['id'], headers=auth, json={'done': True}).status_code == 422
    first = submit(client, auth, 'q1', 'task_submit_0001', task['id'], choice=0)
    assert first.status_code == 200, first.text
    assert first.json()['task']['completedQuestionIds'] == ['q1']
    assert first.json()['task']['done'] is False
    assert first.json()['attempt']['correct'] is False
    assert submit(client, auth, 'q2', 'task_submit_0002', task['id'], choice=0).json()['task']['done'] is True
    saved = client.get(BASE + '/tasks/' + task['id'], headers=auth).json()
    assert saved['completedAt'] and set(saved['completedQuestionIds']) == {'q1', 'q2'}
    assert submit(client, auth, 'q2', 'task_submit_0002', task['id'], choice=0).status_code == 200
    notifications = client.get(BASE + '/notifications', headers=auth).json()
    assert [n['kind'] for n in notifications['items'] if n['taskId'] == task['id']] == ['completed']


def test_exercise_validates_owner_course_membership_and_submission_binding(client, auth):
    task = exercise(client, auth, ['q1'])
    other = client.post(BASE + '/auth/demo').json()['accessToken']
    other_auth = {'Authorization': 'Bearer ' + other}
    assert client.get(BASE + '/tasks/' + task['id'], headers=other_auth).status_code == 404
    assert submit(client, other_auth, 'q1', 'foreign_0001', task['id']).status_code == 404
    assert submit(client, auth, 'q2', 'outside_0001', task['id']).status_code == 422
    assert submit(client, auth, 'q1', 'old_submit_0001').status_code == 200
    assert submit(client, auth, 'q1', 'old_submit_0001', task['id']).status_code == 409
    for values in [dict(questionIds=[]), dict(questionIds=['missing']), dict(courseId='missing')]:
        body = {'title': 'bad', 'kind': 'exercise', 'courseId': task['courseId'], 'questionIds': ['q1'], **values}
        assert client.post(BASE + '/tasks', headers=auth, json=body).status_code in {404, 422}
    assert client.get(BASE + '/tasks/' + task['id'], headers=auth).json()['completedQuestionIds'] == []


def test_custom_completion_and_read_records_are_persistent_and_deduplicated(client, auth):
    task = create_plan(client, auth, reminderAt=None)
    assert task['kind'] == 'custom' and task['reminderAt'] is None
    for done in [True, True, False, True]:
        response = client.patch(BASE + '/tasks/' + task['id'] + '/subtasks/' + task['subtasks'][0]['id'], headers=auth, json={'done': done})
        assert response.status_code == 200, response.text
    page = client.get(BASE + '/notifications', headers=auth).json()
    matches = [item for item in page['items'] if item['taskId'] == task['id']]
    assert len(matches) == 1 and matches[0]['taskDone'] is True
    assert page['unreadCount'] == 1
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    assert client.patch(BASE + '/notifications/' + matches[0]['id'], headers=other, json={'read': True}).status_code == 404
    assert client.patch(BASE + '/notifications/' + matches[0]['id'], headers=auth, json={'read': True}).json()['read'] is True
    assert client.get(BASE + '/notifications', headers=auth).json()['unreadCount'] == 0
    assert client.post(BASE + '/notifications/read-all', headers=auth).json() == {'updated': 0}


def test_due_notifications_normalize_timezone_skip_completed_and_survive_new_session(client, auth, database):
    from app.notifications import scan_due
    task = create_plan(client, auth, reminderAt='2020-01-01T09:00:00+08:00')
    done = create_plan(client, auth, reminderAt='2020-01-01T09:00:00+08:00')
    client.patch(BASE + '/tasks/' + done['id'] + '/subtasks/' + done['subtasks'][0]['id'], headers=auth, json={'done': True})
    assert task['reminderAt'] == '2020-01-01T01:00:00Z'
    scan_due(database)
    scan_due(database)
    page = client.get(BASE + '/notifications', headers=auth).json()
    assert len([n for n in page['items'] if n['kind'] == 'due' and n['taskId'] == task['id']]) == 1
    assert not [n for n in page['items'] if n['kind'] == 'due' and n['taskId'] == done['id']]
    assert client.post(BASE + '/tasks', headers=auth, json={'title': 'bad', 'reminderAt': '2026-01-01T12:00:00'}).status_code == 422


def test_mistake_plan_is_linked_exercise(client, auth):
    assert submit(client, auth, 'q1', 'wrong_for_plan', choice=0).status_code == 200
    response = client.post(BASE + '/mistakes/q1/plan', headers=auth)
    assert response.status_code == 200, response.text
    task = response.json()['task']
    assert task['kind'] == 'exercise' and task['questionIds'] == ['q1']
    assert task['completedQuestionIds'] == []
    assert submit(client, auth, 'q1', 'wrong_completes_plan', task['id'], choice=0).json()['task']['done'] is True


def test_websocket_authentication_origin_ping_and_logout(client, auth, settings):
    token = auth['Authorization'][7:]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(BASE + '/notifications/ws', headers={'origin': 'https://evil.invalid'}):
            pass
    with client.websocket_connect(BASE + '/notifications/ws', headers={'origin': settings.cors_origins[0]}) as ws:
        ws.send_json({'type': 'authenticate', 'token': token})
        assert ws.receive_json() == {'type': 'ready'}
        ws.send_json({'type': 'ping'})
        assert ws.receive_json() == {'type': 'pong'}
        client.post(BASE + '/auth/logout', headers=auth)
        ws.send_json({'type': 'ping'})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_task_progress_rejects_invalid_choices_and_deleting_plan_keeps_attempts(client, auth):
    task = exercise(client, auth, ['q1'])
    assert submit(client, auth, 'q1', 'invalid_choice_001', task['id'], choice=5).status_code == 422
    assert client.get(BASE + '/tasks/' + task['id'], headers=auth).json()['completedQuestionIds'] == []
    assert client.patch(BASE + '/tasks/' + task['id'], headers=auth, json={'questionIds': ['q2']}).status_code == 422
    assert submit(client, auth, 'q1', 'valid_choice_001', task['id']).status_code == 200
    assert client.delete(BASE + '/tasks/' + task['id'], headers=auth).status_code == 204
    assert client.get(BASE + '/attempts', headers=auth).json()['total'] == 1
    assert client.get(BASE + '/notifications', headers=auth).json()['total'] == 0


def test_nullable_reminder_patch_and_foreign_unique_questions(client, auth):
    task = create_plan(client, auth, reminderAt='2040-01-01T00:00:00Z')
    assert client.patch(BASE + '/tasks/' + task['id'], headers=auth, json={'reminderAt': None}).json()['reminderAt'] is None
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    question = client.post(BASE + '/courses/linear/questions', headers=other,
        json={'title': 'private', 'options': ['a', 'b'], 'answer': 0, 'explanation': 'because', 'point': 'point'}).json()
    response = client.post(BASE + '/tasks', headers=auth,
        json={'title': 'foreign', 'kind': 'exercise', 'courseId': 'linear', 'questionIds': [question['id']]})
    assert response.status_code == 422


def test_rescheduling_generates_a_new_due_occurrence_without_duplicate_scans(client, auth, database):
    from app.notifications import scan_due
    task = create_plan(client, auth, reminderAt='2020-01-01T09:00:00+08:00')
    scan_due(database)
    scan_due(database)
    assert client.patch(BASE + '/tasks/' + task['id'], headers=auth,
        json={'reminderAt': '2020-01-02T09:00:00+08:00'}).status_code == 200
    scan_due(database)
    scan_due(database)
    page = client.get(BASE + '/notifications', headers=auth).json()
    assert len([item for item in page['items'] if item['taskId'] == task['id'] and item['kind'] == 'due']) == 2
    assert client.patch(BASE + '/tasks/' + task['id'], headers=auth,
        json={'reminderAt': '2020-01-01T09:00:00+08:00'}).status_code == 200
    scan_due(database)
    assert client.get(BASE + '/notifications', headers=auth).json()['total'] == 2


def test_websocket_change_is_user_scoped_and_expired_session_is_rejected(client, auth, settings, database):
    other_token = client.post(BASE + '/auth/demo').json()['accessToken']
    origin = {'origin': settings.cors_origins[0]}
    with client.websocket_connect(BASE + '/notifications/ws', headers=origin) as mine:
        mine.send_json({'type': 'authenticate', 'token': auth['Authorization'][7:]})
        assert mine.receive_json() == {'type': 'ready'}
        with client.websocket_connect(BASE + '/notifications/ws', headers=origin) as other:
            other.send_json({'type': 'authenticate', 'token': other_token})
            assert other.receive_json() == {'type': 'ready'}
            create_plan(client, auth)
            assert mine.receive_json() == {'type': 'notifications.changed'}
            other.send_json({'type': 'ping'})
            assert other.receive_json() == {'type': 'pong'}
            with database.sessions.begin() as db:
                row = db.get(AuthSession, token_digest(other_token))
                row.expires_at = utcnow() - timedelta(seconds=1)
            other.send_json({'type': 'ping'})
            with pytest.raises(WebSocketDisconnect):
                other.receive_json()
    for frame in [{'type': 'authenticate', 'token': 'invalid'}, {'type': 'ping'}, []]:
        with client.websocket_connect(BASE + '/notifications/ws', headers=origin) as ws:
            ws.send_json(frame)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_websocket_binary_auth_frame_is_closed_cleanly(client, settings):
    with client.websocket_connect(BASE + '/notifications/ws', headers={'origin': settings.cors_origins[0]}) as ws:
        ws.send_bytes(b'not a text authentication frame')
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_deleting_task_notifies_other_open_pages(client, auth, settings):
    task = create_plan(client, auth)
    with client.websocket_connect(BASE + '/notifications/ws', headers={'origin': settings.cors_origins[0]}) as ws:
        ws.send_json({'type': 'authenticate', 'token': auth['Authorization'][7:]})
        assert ws.receive_json() == {'type': 'ready'}
        assert client.delete(BASE + '/tasks/' + task['id'], headers=auth).status_code == 204
        ws.send_json({'type': 'ping'})
        assert ws.receive_json() == {'type': 'notifications.changed'}
        assert ws.receive_json() == {'type': 'pong'}


def test_database_rejects_binding_another_users_attempt_to_a_task(client, auth, database):
    task = exercise(client, auth, ['q1'])
    user_id = client.get(BASE + '/auth/me', headers=auth).json()['id']
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    foreign_attempt = submit(client, other, 'q1', 'foreign_attempt_001').json()['attempt']['id']
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as db:
            db.add(TaskAttempt(attempt_id=foreign_attempt, user_id=user_id, task_id=task['id']))
            db.flush()
