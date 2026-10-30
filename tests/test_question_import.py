"""JSON 题库：整批校验、去重、隔离、真实后端判分与报告。"""
from sqlalchemy import select, func
from app.models import Question


def account(register, email):
    result = register({'email': email, 'password': 'Safe-password-123', 'name': '测试同学'})
    assert result.status_code == 201
    return {'Authorization': 'Bearer ' + result.json()['accessToken']}


def course(client, headers, name):
    result = client.post('/api/v1/courses', headers=headers, json={'name': name})
    assert result.status_code == 201
    return result.json()['id']


def question(title='TCP 提供哪种服务？'):
    return {'title': title, 'options': ['可靠传输', '只支持广播'], 'answer': 0,
            'explanation': 'TCP 提供面向连接的可靠字节流服务。', 'point': '可靠传输'}


def test_import_persists_public_questions_without_leaking_answers(client, verified_register):
    headers = account(verified_register, 'import@example.com')
    cid = course(client, headers, '网络原理')
    result = client.post(f'/api/v1/courses/{cid}/questions/import', headers=headers,
                         json={'questions': [question(), question('哪一种服务由 TCP 提供？')]})
    assert result.status_code == 200, result.text
    assert result.json()['imported'] == 2
    public = client.get('/api/v1/questions', headers=headers, params={'courseId': cid}).json()
    assert public['total'] == 2
    assert all('answer' not in item and 'explanation' not in item for item in public['items'])


def test_import_deduplicates_file_and_retry_without_touching_attempts(client, verified_register, database):
    headers = account(verified_register, 'dedupe@example.com')
    cid = course(client, headers, '网络')
    endpoint = f'/api/v1/courses/{cid}/questions/import'
    first = client.post(endpoint, headers=headers, json={'questions': [question(), question()]}).json()
    assert (first['imported'], first['skipped']) == (1, 1)
    listed = client.get('/api/v1/questions', headers=headers, params={'courseId': cid}).json()['items']
    answer = client.post('/api/v1/attempts', headers=headers,
        json={'questionId': listed[0]['id'], 'choice': 1, 'seconds': 8, 'submissionId': 'import-submit-001'})
    assert answer.status_code == 200
    assert answer.json()['attempt']['correct'] is False
    second = client.post(endpoint, headers=headers, json={'questions': [question()]}).json()
    assert (second['imported'], second['skipped']) == (0, 1)
    assert client.get('/api/v1/reports/summary', headers=headers).json()['total'] == 1
    assert client.get('/api/v1/mistakes', headers=headers).json()['total'] == 1


def test_invalid_question_rolls_back_entire_batch(client, verified_register, database):
    headers = account(verified_register, 'invalid-import@example.com')
    cid = course(client, headers, '课程')
    wrong = {**question('第二题'), 'answer': 5}
    response = client.post(f'/api/v1/courses/{cid}/questions/import', headers=headers,
                           json={'questions': [question(), wrong]})
    assert response.status_code == 422
    with database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Question)) == 0


def test_import_requires_owned_course_and_same_course_material(client, verified_register):
    first = account(verified_register, 'owner-import@example.com')
    second = account(verified_register, 'other-import@example.com')
    cid, other = course(client, first, '第一门'), course(client, first, '第二门')
    endpoint = f'/api/v1/courses/{cid}/questions/import'
    assert client.post(endpoint, headers=second, json={'questions': [question()]}).status_code == 404
    material = client.post(f'/api/v1/courses/{other}/materials', headers=first,
                           files={'file': ('notes.txt', b'notes', 'text/plain')}).json()
    result = client.post(endpoint, headers=first, json={'questions': [question(), {**question('带来源'), 'source': material['id']}]})
    assert result.status_code == 422
    assert client.get('/api/v1/questions', headers=first, params={'courseId': cid}).json()['total'] == 0


def test_limits_and_auth_are_enforced(client, verified_register):
    headers = account(verified_register, 'limits-import@example.com')
    cid = course(client, headers, '课程')
    endpoint = f'/api/v1/courses/{cid}/questions/import'
    assert client.post(endpoint, json={'questions': [question()]}).status_code == 401
    assert client.post(endpoint, headers=headers, json={'questions': []}).status_code == 422
    assert client.post(endpoint, headers=headers, json={'questions': [question()] * 201}).status_code == 422
