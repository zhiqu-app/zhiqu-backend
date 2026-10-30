"""大知识点分组：容量、持久化、旧数据、移动与事务边界。"""
from math import ceil

from app.models import KnowledgeNode


def course_graph(client, auth):
    course = client.post('/api/v1/courses', headers=auth, json={'name': '网络大知识点测试'}).json()
    return f"/api/v1/courses/{course['id']}/knowledge"


def grouped(count=50):
    return {'title': '网络课程', 'goal': '', 'revision': 0,
            'groups': [{'id': f'c{i}', 'title': f'第{i + 1}章'} for i in range(ceil(count / 6))],
            'nodes': [{'id': f'n{i:02}', 'label': f'知识点{i + 1}', 'groupId': f'c{i // 6}',
                       'x': 80 + i % 3 * 250, 'y': 80 + (i % 6 // 3) * 140} for i in range(count)],
            'links': [{'source': 'n05', 'target': 'n06'}] if count > 6 else []}


def test_fifty_points_in_nine_groups_roundtrip_and_cross_links(client, auth):
    endpoint = course_graph(client, auth)
    response = client.put(endpoint, headers=auth, json=grouped())
    assert response.status_code == 200, response.text
    result = response.json()
    assert [c['title'] for c in result['groups']] == [f'第{i + 1}章' for i in range(9)]
    assert all(sum(n['groupId'] == c['id'] for n in result['nodes']) <= 6 for c in result['groups'])
    assert result['links'] == [{'source': 'n05', 'target': 'n06'}]
    assert client.get(endpoint, headers=auth).json() == result


def test_capacity_duplicate_and_missing_group_fail_without_overwrite(client, auth):
    endpoint = course_graph(client, auth)
    saved = client.put(endpoint, headers=auth, json=grouped()).json()
    for change in ('overfull', 'missing', 'duplicate', 'removed', 'blank'):
        payload = grouped()
        payload['revision'] = 1
        if change == 'overfull': payload['nodes'][6]['groupId'] = 'c0'
        if change == 'missing': payload['nodes'][0]['groupId'] = 'not-here'
        if change == 'duplicate': payload['groups'][1]['id'] = 'c0'
        if change == 'removed': payload['groups'].pop(0)
        if change == 'blank': payload['groups'][0]['title'] = '  '
        response = client.put(endpoint, headers=auth, json=payload)
        assert response.status_code == 422, (change, response.text)
        assert client.get(endpoint, headers=auth).json() == saved


def test_legacy_unassigned_points_get_stable_groups_of_six(client, auth, database):
    endpoint = course_graph(client, auth)
    course_id = endpoint.split('/')[-2]
    user_id = client.get('/api/v1/auth/me', headers=auth).json()['id']
    with database.sessions.begin() as db:
        db.add_all([KnowledgeNode(user_id=user_id, course_id=course_id, id=f'old{i:02}', label=f'旧知识{i}', x=100, y=100) for i in range(23)])
    graph = client.get(endpoint, headers=auth).json()
    assert len(graph['groups']) == 4
    assert [sum(n['groupId'] == c['id'] for n in graph['nodes']) for c in graph['groups']] == [6, 6, 6, 5]
    assert all(c['legacy'] for c in graph['groups'])
    assert client.get(endpoint, headers=auth).json()['groups'] == graph['groups']
    payload = {k: graph[k] for k in ('title', 'goal', 'revision', 'links')}
    payload['groups'] = [{'id': c['id'], 'title': c['title']} for c in graph['groups']]
    payload['nodes'] = [{k: n[k] for k in ('id', 'label', 'description', 'x', 'y', 'groupId', 'taskId')} for n in graph['nodes']]
    result = client.put(endpoint, headers=auth, json=payload)
    assert result.status_code == 200, result.text
    assert not any(c['legacy'] for c in result.json()['groups'])
    assert {n['id'] for n in result.json()['nodes']} == {n['id'] for n in graph['nodes']}


def test_move_rename_delete_empty_group_preserves_ids_task_and_links(client, auth):
    endpoint = course_graph(client, auth)
    task = client.post('/api/v1/tasks', headers=auth, json={'title': '网络实验'}).json()
    payload = grouped(8)
    payload['nodes'][0]['taskId'] = task['id']
    assert client.put(endpoint, headers=auth, json=payload).status_code == 200
    payload['revision'] = 1
    payload['nodes'][0]['groupId'] = 'c1'
    payload['groups'] = [{'id': 'c1', 'title': '运输层'}, {'id': 'c0', 'title': '网络基础'}]
    result = client.put(endpoint, headers=auth, json=payload)
    assert result.status_code == 200, result.text
    graph = result.json()
    assert [c['id'] for c in graph['groups']] == ['c1', 'c0']
    assert graph['links'] == payload['links']
    assert next(n for n in graph['nodes'] if n['id'] == 'n00')['taskId'] == task['id']


def test_old_client_without_groups_keeps_existing_assignments(client, auth):
    endpoint = course_graph(client, auth)
    payload = grouped(8)
    assert client.put(endpoint, headers=auth, json=payload).status_code == 200
    payload.pop('groups')
    for node in payload['nodes']: node.pop('groupId')
    payload['revision'] = 1
    result = client.put(endpoint, headers=auth, json=payload)
    assert result.status_code == 200, result.text
    graph = result.json()
    assert graph['groups'][0]['title'] == '第1章'
    assert next(n for n in graph['nodes'] if n['id'] == 'n06')['groupId'] == 'c1'


def test_course_owner_still_protects_group_save(client, auth):
    endpoint = course_graph(client, auth)
    other_token = client.post('/api/v1/auth/demo').json()['accessToken']
    response = client.put(endpoint, headers={'Authorization': 'Bearer ' + other_token}, json=grouped())
    assert response.status_code == 404
