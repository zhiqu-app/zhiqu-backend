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


def test_delete_question_hides_it_from_bank_without_losing_history_or_assigned_work(client, auth):
    headers = auth
    cid = course(client, headers, '删除题目验证')
    other_course = course(client, headers, '另一门课程')
    bank = f'/api/v1/courses/{cid}/questions'
    created = client.post(bank, headers=headers, json=question())
    assert created.status_code == 201, created.text
    qid = created.json()['id']
    endpoint = f'{bank}/{qid}'
    assignment = {'kind': 'exercise', 'courseId': cid, 'questionIds': [qid], 'title': '已有习题计划'}
    task = client.post('/api/v1/tasks', headers=headers, json=assignment)
    assert task.status_code == 201, task.text
    task_id = task.json()['id']
    attempt = {'questionId': qid, 'choice': 1, 'seconds': 8, 'submissionId': 'question-delete-before'}
    assert client.post('/api/v1/attempts', headers=headers, json=attempt).status_code == 200
    other = client.post('/api/v1/auth/demo').json()['accessToken']
    assert client.delete(endpoint, headers={'Authorization': 'Bearer ' + other}).status_code == 404
    assert client.delete(f'/api/v1/courses/{other_course}/questions/{qid}', headers=headers).status_code == 404
    for _ in range(2):
        deleted = client.delete(endpoint, headers=headers)
        assert deleted.status_code == 204, deleted.text
        assert deleted.content == b''
    params = {'courseId': cid}
    assert client.get('/api/v1/questions', headers=headers, params=params).json()['items'] == []
    assert client.post('/api/v1/tasks', headers=headers, json=assignment).status_code == 422
    assigned = client.get('/api/v1/questions', headers=headers, params={**params, 'taskId': task_id}).json()
    assert [item['id'] for item in assigned['items']] == [qid]
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).status_code == 200
    assert client.get('/api/v1/mistakes', headers=headers, params=params).json()['items'][0]['id'] == qid
    assert client.get('/api/v1/reports/summary', headers=headers, params=params).json()['total'] == 1
    completed = client.post('/api/v1/attempts', headers=headers,
                            json={**attempt, 'taskId': task_id, 'submissionId': 'question-delete-assigned'})
    assert completed.json()['task']['done'] is True, completed.text
    imported = client.post(f'{bank}/import', headers=headers, json={'questions': [question()]})
    assert imported.json()['imported'] == 1, imported.text


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


def test_manual_create_rejects_duplicate_within_course(client, verified_register):
    headers = account(verified_register, 'manual-duplicates@example.com')
    cid = course(client, headers, '网络')
    endpoint = f'/api/v1/courses/{cid}/questions'
    assert client.post(endpoint, headers=headers, json=question()).status_code == 201
    duplicate = {**question('  TCP 提供哪种服务？  '), 'options': ['只支持广播', ' 可靠传输 '],
                 'answer': 1, 'explanation': '另一种解析', 'point': 'TCP'}
    rejected = client.post(endpoint, headers=headers, json=duplicate)
    assert rejected.status_code == 409, rejected.text
    assert '已存在' in rejected.json()['error']['message']
    distinct = {**question(), 'options': ['可靠传输', '只支持组播']}
    assert client.post(endpoint, headers=headers, json=distinct).status_code == 201
    other = course(client, headers, '另一门课程')
    assert client.post(f'/api/v1/courses/{other}/questions', headers=headers, json=question()).status_code == 201
    assert client.get('/api/v1/questions', headers=headers, params={'courseId': cid}).json()['total'] == 2


def test_import_and_manual_create_share_duplicate_rule(client, verified_register):
    headers = account(verified_register, 'mixed-duplicates@example.com')
    cid = course(client, headers, '网络')
    endpoint = f'/api/v1/courses/{cid}/questions'
    assert client.post(endpoint, headers=headers, json=question()).status_code == 201
    new_question = question('TCP 的可靠传输是什么意思？')
    reordered = {**new_question, 'options': new_question['options'][::-1], 'answer': 1}
    response = client.post(endpoint + '/import', headers=headers, json={'questions': [
        {**question(), 'explanation': '改写后的解析'}, new_question, reordered,
    ]})
    assert response.status_code == 200, response.text
    assert response.json() == {'courseId': cid, 'imported': 1, 'skipped': 2, 'total': 2}
    assert client.post(endpoint, headers=headers, json=reordered).status_code == 409


def test_question_width_variants_are_duplicates_for_both_endpoints(client, verified_register):
    headers = account(verified_register, 'width-duplicates@example.com')
    cid = course(client, headers, '网络')
    endpoint = f'/api/v1/courses/{cid}/questions'
    original = question('TCP 最少需要几次握手？')
    original['options'] = ['３ 次', '２ 次']
    assert client.post(endpoint, headers=headers, json=original).status_code == 201
    variant = {**original, 'title': 'ＴＣＰ 最少需要几次握手?', 'options': ['2 次', '3 次'], 'answer': 1}
    result = client.post(endpoint, headers=headers, json=variant)
    assert result.status_code == 409, result.text
    imported = client.post(endpoint + '/import', headers=headers, json={'questions': [variant]})
    assert imported.status_code == 200, imported.text
    assert imported.json() == {'courseId': cid, 'imported': 0, 'skipped': 1, 'total': 1}
    assert client.get('/api/v1/questions', headers=headers, params={'courseId': cid}).json()['items'][0]['title'] == original['title']


def test_width_normalization_preserves_distinct_formula_symbols(client, verified_register):
    headers = account(verified_register, 'formula-duplicates@example.com')
    cid = course(client, headers, '数学')
    endpoint = f'/api/v1/courses/{cid}/questions'
    for title in ('x² 的导数是多少？', 'x2 的导数是多少？'):
        assert client.post(endpoint, headers=headers, json=question(title)).status_code == 201


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
