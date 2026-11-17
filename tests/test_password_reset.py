"""Validate recovery against the real API with an isolated database and captured mail."""
from datetime import timedelta
import smtplib

import pytest

from app.models import User, utcnow


EMAIL = 'student@example.com'
OLD = 'StudyOld123!'
NEW = '  StudyNew456!  '
AUTH = '/api/v1/auth'


@pytest.fixture
def outbox(client, settings, verified_register):
    response = verified_register({'email': EMAIL, 'password': OLD, 'name': '同学'})
    assert response.status_code == 201
    messages = []
    client.app.state.password_reset_mailer = lambda settings, email, code: messages.append((email, code))
    return messages, {'Authorization': 'Bearer ' + response.json()['accessToken']}


def send(client, email=EMAIL):
    return client.post(AUTH + '/password-reset/code', json={'email': email})


def reset(client, code, email=EMAIL, password=NEW):
    return client.post(AUTH + '/password-reset', json={
        'email': email, 'verificationCode': code, 'password': password,
    })


def login(client, password):
    return client.post(AUTH + '/login', json={'email': EMAIL, 'password': password})


def test_reset_replaces_password_and_revokes_sessions_without_losing_data(client, outbox):
    messages, first_session = outbox
    second = login(client, OLD)
    second_session = {'Authorization': 'Bearer ' + second.json()['accessToken']}
    assert client.post('/api/v1/courses', headers=first_session, json={'name': '保留课程'}).status_code == 201
    issued = send(client, 'Student@Example.com')
    assert issued.status_code == 200, issued.text
    assert set(issued.json()) == {'message', 'expiresIn', 'resendAfter'}
    email, code = messages[0]
    assert email == EMAIL and len(code) == 6
    assert reset(client, code, email='other@example.com').status_code == 400
    assert reset(client, code).status_code == 204
    assert reset(client, code).status_code == 400
    assert client.get(AUTH + '/me', headers=first_session).status_code == 401
    assert client.get(AUTH + '/me', headers=second_session).status_code == 401
    assert login(client, OLD).status_code == 401
    new_session = login(client, NEW)
    assert new_session.status_code == 200
    assert client.get('/api/v1/courses', headers={
        'Authorization': 'Bearer ' + new_session.json()['accessToken'],
    }).json()['items'][0]['name'] == '保留课程'


def test_generic_response_and_independent_email_cooldowns(client, outbox, database):
    messages, _ = outbox
    with database.sessions.begin() as db:
        db.add(User(email='legacy@example.com', name='历史账号', email_verified=False, is_guest=False))
    known = send(client)
    unknown = send(client, 'new@example.com')
    legacy = send(client, 'legacy@example.com')
    assert known.status_code == unknown.status_code == legacy.status_code == 200
    assert known.json() == unknown.json() == legacy.json()
    assert len(messages) == 1
    assert send(client).status_code == send(client, 'new@example.com').status_code == 429
    assert client.post(AUTH + '/email-code', json={'email': 'new@example.com'}).status_code == 200


def test_expired_and_exhausted_codes_cannot_reset_password(client, outbox, database):
    from app.models import PasswordResetVerification
    messages, _ = outbox
    assert send(client).status_code == 200
    code = messages[-1][1]
    with database.sessions.begin() as db:
        slot = db.get(PasswordResetVerification, EMAIL)
        assert slot.code_digest != code
        slot.expires_at = utcnow() - timedelta(seconds=1)
    assert reset(client, code).status_code == 400
    with database.sessions.begin() as db:
        db.get(PasswordResetVerification, EMAIL).requested_at = utcnow() - timedelta(seconds=61)
    assert send(client).status_code == 200
    code = messages[-1][1]
    wrong = '000000' if code != '000000' else '000001'
    for _ in range(5):
        assert reset(client, wrong).status_code == 400
    assert reset(client, code).status_code == 400
    assert login(client, OLD).status_code == 200


def test_smtp_failure_leaves_inactive_code_and_generic_response(client, outbox, database):
    from app.models import PasswordResetVerification
    def fail(settings, email, code):
        raise smtplib.SMTPException('private SMTP details')
    client.app.state.password_reset_mailer = fail
    response = send(client)
    assert response.status_code == 200
    assert response.json() == send(client, 'unknown@example.com').json()
    with database.sessions() as db:
        assert not db.get(PasswordResetVerification, EMAIL).active
