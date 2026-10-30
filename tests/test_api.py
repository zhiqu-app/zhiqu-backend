from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

from fastapi.testclient import TestClient
import httpx
from pypdf import PdfWriter
import pytest

from app.models import User, AuthSession, Attempt
from sqlalchemy import select, update
from app.main import create_app
from app.security import token_digest
from app import services

BASE = '/api/v1'


def attempt(client, auth, question='q1', choice=0, submission='submit_0001', seconds=20):
    return client.post(BASE + '/attempts', headers=auth, json={
        'questionId': question, 'choice': choice, 'seconds': seconds, 'submissionId': submission})


def test_auth_registration_login_and_revocation(client, settings, verified_register):
    payload = {'email': 'Student@Example.com', 'password': '  pass1234  ', 'name': '小林', 'seedDemo': False}
    response = verified_register(payload)
    assert response.status_code == 201, response.text
    token = response.json()['accessToken']
    auth = {'Authorization': 'Bearer ' + token}
    assert response.json()['user']['email'] == 'student@example.com'
    assert client.get(BASE + '/courses', headers=auth).json()['total'] == 0
    assert verified_register(payload).status_code == 409
    assert client.post(BASE + '/auth/login', json={'email': payload['email'], 'password': 'pass1234'}).status_code == 401
    assert client.post(BASE + '/auth/login', json={k: payload[k] for k in ['email', 'password']}).status_code == 200
    with settings._testing_database.sessions.begin() as db:
        user = db.scalar(select(User))
        assert payload['password'] not in user.password_hash
        assert db.scalar(select(AuthSession).where(AuthSession.digest == token)) is None
    assert client.post(BASE + '/auth/logout', headers=auth).status_code == 204
    assert client.get(BASE + '/auth/me', headers=auth).status_code == 401


def test_guest_and_expired_session(client, settings, auth):
    assert client.get(BASE + '/auth/me', headers=auth).json()['isGuest'] is True
    with settings._testing_database.sessions.begin() as db:
        db.execute(update(AuthSession).where(AuthSession.digest == token_digest(auth['Authorization'][7:])).values(expires_at=datetime(2000,1,1)))
    assert client.get(BASE + '/courses', headers=auth).status_code == 401
    assert client.get(BASE + '/courses').status_code == 401
    assert client.get(BASE + '/courses', headers={'Authorization': 'Bearer invalid'}).status_code == 401


def test_demo_can_be_disabled(settings):
    settings.demo_enabled = False
    with TestClient(create_app(settings, database=settings._testing_database)) as client:
        assert client.post(BASE + '/auth/demo').status_code == 403


def test_course_crud_search_archive_and_validation(client, auth):
    assert client.get(BASE + '/courses', headers=auth).json()['counts'] == {'all': 4, 'active': 3, 'archived': 1}
    response = client.post(BASE + '/courses', headers=auth, json={'name': '计算机网络'})
    assert response.status_code == 201
    course = response.json()
    assert course['sample'] is False and course['progress'] == 0
    assert client.get(BASE + '/courses?q=网络', headers=auth).json()['total'] == 1
    assert client.patch(BASE + '/courses/' + course['id'], headers=auth, json={'archived': True}).json()['archived'] is True
    assert client.get(BASE + '/courses?status=archived', headers=auth).json()['total'] == 2
    for invalid in [{'progress': 101}, {'progress': True}, {'name': ''}, {'id': 'forged'}, {'name': None}, {}]:
        assert client.patch(BASE + '/courses/' + course['id'], headers=auth, json=invalid).status_code == 422
    assert client.delete(BASE + '/courses/' + course['id'], headers=auth).status_code == 204
    assert client.get(BASE + '/courses/' + course['id'], headers=auth).status_code == 404


def test_account_isolation_across_all_resources(client, auth):
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    course = client.post(BASE + '/courses', headers=auth, json={'name': '私有课程'}).json()
    material = client.post(BASE + f'/courses/{course["id"]}/materials', headers=auth,
                           files={'file': ('private.txt', '秘密课程知识'.encode(), 'text/plain')}).json()
    task = client.post(BASE + '/tasks', headers=auth, json={'title': '私有任务'}).json()
    checks = [
        ('get', '/courses/' + course['id'], None),
        ('patch', '/courses/' + course['id'], {'name': '恶意修改'}),
        ('delete', '/courses/' + course['id'], None),
        ('get', '/materials/' + material['id'], None),
        ('get', '/materials/' + material['id'] + '/file', None),
        ('delete', '/materials/' + material['id'], None),
        ('patch', '/tasks/' + task['id'], {'done': True}),
        ('delete', '/tasks/' + task['id'], None),
        ('post', '/assistant/ask', {'courseId': course['id'], 'question': '秘密是什么'}),
        ('get', '/assistant/history?courseId=' + course['id'], None),
        ('get', '/reports/summary?courseId=' + course['id'], None),
        ('get', '/questions?courseId=' + course['id'], None),
        ('get', '/knowledge?courseId=' + course['id'], None),
    ]
    for method, route, body in checks:
        kwargs = {'headers': other}
        if body is not None:
            kwargs['json'] = body
        assert client.request(method, BASE + route, **kwargs).status_code == 404, route
    assert client.get(BASE + '/materials', headers=other).json()['total'] == 2
    assert client.get(BASE + '/courses/' + course['id'], headers=auth).status_code == 200


def test_upload_download_delete_text_and_pdf(client, auth):
    original = '特征值：对角矩阵的主对角线元素。'.encode('utf-8')
    response = client.post(BASE + '/courses/linear/materials', headers=auth,
                           files={'file': ('../../讲义.md', original, 'text/markdown')})
    assert response.status_code == 201, response.text
    material = response.json()
    assert material['name'] == '讲义.md' and material['text'].startswith('特征值')
    row = client.get(BASE + '/materials?courseId=linear&q=讲义.md', headers=auth).json()['items'][0]
    assert 'text' not in row
    download = client.get(BASE + '/materials/' + material['id'] + '/file', headers=auth)
    assert download.content == original and 'attachment' in download.headers['content-disposition']
    assert download.headers['x-content-type-options'] == 'nosniff'
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    pdf = client.post(BASE + '/courses/linear/materials', headers=auth,
                      files={'file': ('blank.pdf', buffer.getvalue(), 'application/pdf')})
    assert pdf.status_code == 201 and pdf.json()['parseStatus'] == 'no_text'
    preview = client.get(BASE + '/materials/' + pdf.json()['id'] + '/file?inline=true', headers=auth)
    assert preview.content == buffer.getvalue() and preview.headers['content-type'] == 'application/pdf'
    assert 'inline' in preview.headers['content-disposition']
    assert client.delete(BASE + '/materials/' + material['id'], headers=auth).status_code == 204
    assert client.get(BASE + '/materials/' + material['id'] + '/file', headers=auth).status_code == 404


@pytest.mark.parametrize('filename,content,status', [
    ('payload.html', b'<script>alert(1)</script>', 415),
    ('bad.pdf', b'not a pdf', 422), ('empty.txt', b'', 422),
    ('large.txt', b'a' * (500 * 1024 + 1), 413), ('binary.txt', b'abc\x00def', 422),
], ids=['html', 'invalid-pdf', 'empty', 'too-large', 'binary'])
def test_upload_rejections(client, auth, filename, content, status):
    response = client.post(BASE + '/courses/linear/materials', headers=auth,
                           files={'file': (filename, content, 'application/octet-stream')})
    assert response.status_code == status, response.text


def test_server_grading_idempotency_and_report(client, auth):
    questions = client.get(BASE + '/questions?courseId=linear', headers=auth).json()['items']
    assert len(questions) == 5
    assert all('answer' not in q and 'explanation' not in q for q in questions)
    assert 'answer' not in client.get(BASE + '/questions/q1', headers=auth).json()
    assert client.get(BASE + '/reports/summary', headers=auth).json()['total'] == 0
    choices = [0, 1, 1, 0, 2]
    for index, choice in enumerate(choices, 1):
        response = attempt(client, auth, 'q' + str(index), choice, 'submit_000' + str(index))
        assert response.status_code == 200, response.text
        assert response.json()['attempt']['correct'] is [False, True, True, False, True][index - 1]
    assert attempt(client, auth).status_code == 200
    assert attempt(client, auth, choice=1).status_code == 409
    summary = client.get(BASE + '/reports/summary', headers=auth).json()
    assert (summary['total'], summary['correct'], summary['rate'], summary['totalSeconds']) == (5, 3, 60, 100)
    assert sum(day['count'] for day in summary['days']) == 5
    assert summary['timezone'] == 'Asia/Shanghai'
    assert client.get(BASE + '/attempts?limit=2&offset=1', headers=auth).json()['total'] == 5
    assert len(client.get(BASE + '/attempts?limit=2', headers=auth).json()['items']) == 2
    assert client.get(BASE + '/overview', headers=auth).json()['correctRate'] == 60
    assert client.get(BASE + '/reports/summary?range=7', headers=auth).json()['total'] == 5
    assert client.get(BASE + '/reports/summary?range=invalid', headers=auth).status_code == 422


def test_grading_does_not_trust_frontend(client, auth):
    payload = {'questionId': 'q1', 'choice': 0, 'seconds': 2, 'submissionId': 'tamper_0001'}
    for field, value in [('correct', True), ('date', '2001-01-01'), ('answer', 0)]:
        assert client.post(BASE + '/attempts', headers=auth, json={**payload, field: value}).status_code == 422
    assert attempt(client, auth, choice=9).status_code == 422
    assert attempt(client, auth, question='not-existing').status_code == 404
    assert attempt(client, auth, seconds=-1).status_code == 422
    assert attempt(client, auth, choice=True).status_code == 422


def test_concurrent_submission_is_counted_once(client, auth, database):
    if database.engine.dialect.name != 'mysql':
        pytest.skip('并发事务与锁行为必须在真实 MySQL 环境验证')
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: attempt(client, auth), range(4)))
    assert all(r.status_code == 200 for r in rows), [r.text for r in rows]
    assert len({r.json()['attempt']['id'] for r in rows}) == 1
    assert client.get(BASE + '/reports/summary', headers=auth).json()['total'] == 1


def test_mistakes_retraining_and_plan_dedup(client, auth):
    assert client.post(BASE + '/mistakes/q1/plan', headers=auth).status_code == 404
    attempt(client, auth)
    pending = client.get(BASE + '/mistakes?status=pending', headers=auth).json()
    assert pending['total'] == 1 and pending['items'][0]['answer'] == 1
    one = client.post(BASE + '/mistakes/q1/plan', headers=auth).json()
    two = client.post(BASE + '/mistakes/q1/plan', headers=auth).json()
    assert one['created'] is True and two['created'] is False
    assert one['task']['id'] == two['task']['id']
    attempt(client, auth, choice=1, submission='retrain_0001')
    assert client.get(BASE + '/mistakes?status=pending', headers=auth).json()['total'] == 0
    assert client.get(BASE + '/mistakes?status=reviewed', headers=auth).json()['total'] == 1
    attempt(client, auth, choice=0, submission='retrain_0002')
    pending = client.get(BASE + '/mistakes?status=pending', headers=auth).json()['items'][0]
    assert pending['wrongCount'] == 2


def test_task_crud_date_and_profile(client, auth):
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    response = client.post(BASE + '/tasks', headers=auth, json={'title': '复习', 'date': today})
    assert response.status_code == 201
    task_id = response.json()['id']
    assert client.get(BASE + '/tasks?status=today', headers=auth).json()['total'] == 1
    assert client.patch(BASE + '/tasks/' + task_id + '/subtasks/' + response.json()['subtasks'][0]['id'], headers=auth, json={'done': True}).json()['done'] is True
    assert client.get(BASE + '/tasks?status=done', headers=auth).json()['total'] == 1
    for invalid in [{'date': '2026-02-30'}, {'minutes': 241}, {'title': ''}, {'target': '//evil.example'}, {'target': 'https://evil.example'}]:
        assert client.patch(BASE + '/tasks/' + task_id, headers=auth, json=invalid).status_code == 422
    assert client.delete(BASE + '/tasks/' + task_id, headers=auth).status_code == 204
    profile = client.patch(BASE + '/profile', headers=auth,
                           json={'name': '测试同学', 'major': '计算机', 'reducedMotion': True, 'email': 'contact@example.com'})
    assert profile.status_code == 200 and profile.json()['reducedMotion'] is True
    assert client.get(BASE + '/auth/me', headers=auth).json()['email'] == ''
    assert client.get(BASE + '/profile', headers=auth).json()['name'] == '测试同学'


def test_retrieval_history_and_no_cross_course_sources(client, auth):
    response = client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'linear', 'question': '特征值是什么'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['mode'] == 'retrieval' and data['sources']
    assert all(s['materialId'] == 'sample-linear' for s in data['sources'])
    history = client.get(BASE + '/assistant/history?courseId=linear', headers=auth).json()
    assert history['total'] == 1 and history['items'][0]['id'] == data['id']
    missing = client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'english', 'question': '特征值是什么'}).json()
    assert missing['sources'] == [] and missing['mode'] == 'retrieval'
    assert client.get(BASE + '/knowledge?courseId=linear', headers=auth).json()['sample'] is True
    assert client.get(BASE + '/knowledge?courseId=english', headers=auth).json()['nodes'] == []


def test_model_adapter_and_error_redaction(client, auth, settings, monkeypatch):
    settings.ai_base_url, settings.ai_api_key, settings.ai_model = 'https://model.example/v1', 'private-key', 'test-model'
    original_client = httpx.Client
    calls = []
    def mock_response(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer private-key'
        body = json.loads(request.content)
        assert '特征值' in body['messages'][-1]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': '依据资料，特征值为主对角线元素 [1]。'}}]})
    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(mock_response), **kwargs))
    response = client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'linear', 'question': '特征值是什么'})
    assert response.status_code == 200 and response.json()['mode'] == 'llm'
    assert str(calls[0].url) == 'https://model.example/v1/chat/completions'
    def mock_fail(request):
        return httpx.Response(401, json={'error': 'private-key secret-vendor-info'})
    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(mock_fail), **kwargs))
    failed = client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'linear', 'question': '特征值是什么'})
    assert failed.status_code == 502
    assert 'private-key' not in failed.text and 'secret-vendor-info' not in failed.text
    assert client.get(BASE + '/assistant/history?courseId=linear', headers=auth).json()['total'] == 1


def test_report_date_window_and_shanghai_boundary(client, auth, settings):
    attempt(client, auth)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with settings._testing_database.sessions.begin() as db:
        db.execute(update(Attempt).values(created_at=datetime.fromisoformat(old).replace(tzinfo=None)))
    assert client.get(BASE + '/reports/summary?range=30', headers=auth).json()['total'] == 0
    assert client.get(BASE + '/reports/summary?range=all', headers=auth).json()['total'] == 1
    from app.study import today_local
    assert today_local() == datetime.now(timezone(timedelta(hours=8))).date()


def test_data_export_reset_isolation_and_cascade(client, auth):
    other = {'Authorization': 'Bearer ' + client.post(BASE + '/auth/demo').json()['accessToken']}
    attempt(client, auth)
    attempt(client, other)
    client.patch(BASE + '/profile', headers=auth, json={'name': '保留昵称'})
    export = client.get(BASE + '/data/export', headers=auth)
    assert export.status_code == 200 and len(export.json()['attempts']) == 1
    assert 'password_hash' not in export.text and 'accessToken' not in export.text
    assert client.post(BASE + '/data/reset', headers=auth, json={'confirm': True}).status_code == 422
    assert client.post(BASE + '/data/reset', headers=auth, json={'confirm': 'RESET_MY_LEARNING_DATA', 'seedDemo': True}).status_code == 200
    assert client.get(BASE + '/reports/summary', headers=auth).json()['total'] == 0
    assert client.get(BASE + '/profile', headers=auth).json()['name'] == '保留昵称'
    assert client.get(BASE + '/reports/summary', headers=other).json()['total'] == 1
    attempt(client, auth, submission='new_00001')
    client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'linear', 'question': '特征值是什么'})
    assert client.delete(BASE + '/courses/linear', headers=auth).status_code == 204
    assert client.get(BASE + '/materials/sample-linear', headers=auth).status_code == 404
    assert client.get(BASE + '/attempts', headers=auth).json()['total'] == 0
    assert client.get(BASE + '/data/export', headers=auth).json()['chatHistory'] == []


def test_persistence_after_app_restart(client, auth, settings):
    created = client.post(BASE + '/courses', headers=auth, json={'name': '持久化课程'}).json()
    uploaded = client.post(BASE + f'/courses/{created["id"]}/materials', headers=auth,
                            files={'file': ('persistent.txt', b'hello world', 'text/plain')}).json()
    with TestClient(create_app(settings, database=settings._testing_database)) as second:
        assert second.get(BASE + '/courses/' + created['id'], headers=auth).json()['name'] == '持久化课程'
        assert second.get(BASE + '/materials/' + uploaded['id'] + '/file', headers=auth).content == b'hello world'


def test_cors_error_format_and_rate_limit(client, auth):
    preflight = client.options(BASE + '/courses', headers={'Origin': 'http://127.0.0.1:4173',
        'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization'})
    assert preflight.status_code == 200
    assert preflight.headers['access-control-allow-origin'] == 'http://127.0.0.1:4173'
    denied = client.options(BASE + '/courses', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET'})
    assert 'access-control-allow-origin' not in denied.headers
    assert client.get(BASE + '/courses?limit=1000', headers=auth).status_code == 422
    invalid = client.post(BASE + '/auth/register', json={'email': 'bad', 'password': 'secret'})
    assert invalid.status_code == 422 and 'secret' not in invalid.text
    assert 'requestId' in invalid.json() and invalid.headers['x-request-id']
    for _ in range(10):
        response = client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'english', 'question': '随机问题'})
        assert response.status_code == 200
    assert client.post(BASE + '/assistant/ask', headers=auth, json={'courseId': 'english', 'question': '随机问题'}).status_code == 429


def test_openapi_and_health(client):
    assert client.get(BASE + '/health').json()['status'] == 'ok'
    schema = client.get('/openapi.json').json()
    assert BASE + '/attempts' in schema['paths']
    assert schema['components']['securitySchemes']['HTTPBearer']['scheme'] == 'bearer'


def test_pdf_text_extraction_and_encryption(client, auth):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 20 200 Td (eigenvalue of a diagonal matrix) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    buffer = BytesIO()
    writer.write(buffer)
    response = client.post(BASE + '/courses/linear/materials', headers=auth,
        files={'file': ('text.pdf', buffer.getvalue(), 'application/pdf')})
    assert response.status_code == 201 and response.json()['parseStatus'] == 'ready'
    assert 'eigenvalue of a diagonal matrix' in response.json()['text']
    writer.encrypt('protected-password')
    encrypted = BytesIO()
    writer.write(encrypted)
    rejected = client.post(BASE + '/courses/linear/materials', headers=auth,
        files={'file': ('encrypted.pdf', encrypted.getvalue(), 'application/pdf')})
    assert rejected.status_code == 422


def test_large_request_and_gb18030_text(client, auth):
    response = client.post(BASE + '/courses/linear/materials', headers=auth,
        files={'file': ('gb.txt', '特征值的定义'.encode('gb18030'), 'text/plain')})
    assert response.status_code == 201 and response.json()['text'] == '特征值的定义'
    large = client.post(BASE + '/courses', headers=auth, json={'name': 'x' * (128 * 1024)})
    assert large.status_code == 413
