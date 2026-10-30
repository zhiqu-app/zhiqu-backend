"""课程先创建，星图后手动配置；禁止由星图接口创建模板课程。"""
from sqlalchemy import func, select
from app.models import Course


def user(verified_register, email):
    response = verified_register({'email': email, 'password': 'Safe-password-123', 'name': '测试同学'})
    assert response.status_code == 201
    return {'Authorization': 'Bearer ' + response.json()['accessToken']}


def create(client, headers, name):
    response = client.post('/api/v1/courses', headers=headers, json={'name': name})
    assert response.status_code == 201
    return response.json()['id']


def body(revision=0):
    return {'title': '我的网络学习任务图', 'goal': '完成协议实验', 'revision': revision,
            'nodes': [{'id': 'a', 'label': '认识网络分层', 'description': '阅读第一章并画分层图', 'x': 100, 'y': 150, 'taskId': None},
                      {'id': 'b', 'label': '分析 TCP 报文', 'description': '完成抓包实验', 'x': 500, 'y': 80, 'taskId': None}],
            'links': [{'source': 'a', 'target': 'b'}]}


def test_new_course_can_exist_without_a_graph(client, verified_register):
    headers = user(verified_register, 'course-first@example.com')
    course = create(client, headers, '自己创建的课程')
    response = client.get(f'/api/v1/courses/{course}/knowledge', headers=headers)
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert response.json()['nodes'] == []


def test_manual_graph_belongs_to_one_existing_course(client, verified_register, database):
    headers = user(verified_register, 'course-graphs@example.com')
    first, second = create(client, headers, '网络实验'), create(client, headers, '数据库实验')
    endpoint = f'/api/v1/courses/{first}/knowledge'
    response = client.put(endpoint, headers=headers, json=body())
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph['title'] == '我的网络学习任务图'
    assert graph['generationMode'] == 'manual'
    assert graph['revision'] == 1
    assert [n['label'] for n in graph['nodes']] == ['认识网络分层', '分析 TCP 报文']
    assert graph['edges'] == [[0, 1]]
    assert client.get(endpoint, headers=headers).json() == graph
    assert client.get(f'/api/v1/courses/{second}/knowledge', headers=headers).json()['nodes'] == []
    with database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Course)) == 2


def test_optional_task_link_updates_and_survives_task_deletion(client, verified_register):
    headers = user(verified_register, 'linked-task@example.com')
    course = create(client, headers, '抓包实验')
    task = client.post('/api/v1/tasks', headers=headers, json={'title': '完成抓包', 'minutes': 20}).json()
    payload = body()
    payload['nodes'][1]['taskId'] = task['id']
    endpoint = f'/api/v1/courses/{course}/knowledge'
    response = client.put(endpoint, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['nodes'][1]['taskTitle'] == '完成抓包'
    assert response.json()['nodes'][0]['taskId'] is None
    client.patch('/api/v1/tasks/' + task['id'] + '/subtasks/' + task['subtasks'][0]['id'], headers=headers, json={'done': True})
    assert client.get(endpoint, headers=headers).json()['nodes'][1]['taskDone'] is True
    assert client.delete('/api/v1/tasks/' + task['id'], headers=headers).status_code == 204
    graph = client.get(endpoint, headers=headers).json()
    assert len(graph['nodes']) == 2
    assert graph['nodes'][1]['taskId'] is None


def test_invalid_or_stale_save_does_not_destroy_existing_graph(client, verified_register):
    headers = user(verified_register, 'atomic-graph@example.com')
    course = create(client, headers, '课程')
    endpoint = f'/api/v1/courses/{course}/knowledge'
    saved = client.put(endpoint, headers=headers, json=body()).json()
    invalid = body(1)
    invalid['links'].append({'source': 'b', 'target': 'a'})
    assert client.put(endpoint, headers=headers, json=invalid).status_code == 422
    invalid = body(1)
    invalid['links'][0]['target'] = 'missing'
    assert client.put(endpoint, headers=headers, json=invalid).status_code == 422
    assert client.put(endpoint, headers=headers, json=body(0)).status_code == 409
    assert client.get(endpoint, headers=headers).json() == saved


def test_ownership_and_task_ownership_are_enforced(client, verified_register):
    first = user(verified_register, 'owner-first@example.com')
    second = user(verified_register, 'owner-second@example.com')
    course = create(client, first, '个人课程')
    endpoint = f'/api/v1/courses/{course}/knowledge'
    assert client.get(endpoint, headers=second).status_code == 404
    assert client.put(endpoint, headers=second, json=body()).status_code == 404
    task = client.post('/api/v1/tasks', headers=second, json={'title': '别人的任务'}).json()
    payload = body()
    payload['nodes'][0]['taskId'] = task['id']
    assert client.put(endpoint, headers=first, json=payload).status_code == 404
    assert client.get(endpoint, headers=first).json()['nodes'] == []


def test_editing_keeps_course_materials_and_can_clear_nodes(client, verified_register):
    headers = user(verified_register, 'keep-course@example.com')
    course = create(client, headers, '已有课程')
    upload = client.post(f'/api/v1/courses/{course}/materials', headers=headers,
                         files={'file': ('notes.txt', '实验要求'.encode(), 'text/plain')})
    assert upload.status_code == 201
    endpoint = f'/api/v1/courses/{course}/knowledge'
    assert client.put(endpoint, headers=headers, json=body()).status_code == 200
    empty = {**body(1), 'nodes': [], 'links': []}
    response = client.put(endpoint, headers=headers, json=empty)
    assert response.status_code == 200
    assert response.json()['nodes'] == []
    assert client.get('/api/v1/materials/' + upload.json()['id'], headers=headers).status_code == 200


def test_old_template_generation_is_retired(client, verified_register):
    headers = user(verified_register, 'no-template@example.com')
    response = client.post('/api/v1/knowledge/configure', headers=headers,
                           json={'subject': 'network', 'level': 'beginner', 'goal': '不要自动新建课程'})
    assert response.status_code == 404
    assert client.get('/api/v1/courses', headers=headers).json()['items'] == []
