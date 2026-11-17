"""覆盖复习教练的推荐分支、引用和显式确认保存。"""
import json

import httpx

from test_assistant_practice_drafts import mock_model
from test_workspace_guide import tool_call


API = '/api/v1'
PREVIEW = API + '/assistant/review-coach'
CONFIRM = PREVIEW + '/confirm'
HISTORY = PREVIEW + '/history'


def make_course(client, auth):
    course = client.post(API + '/courses', headers=auth, json={'name': '复习教练测试'}).json()['id']
    path = API + '/courses/' + course
    graph = client.put(path + '/knowledge', headers=auth, json={
        'title': '网络知识', 'revision': 0, 'links': [],
        'nodes': [{'id': 'retransmit', 'label': '超时重传', 'x': 90, 'y': 100},
                  {'id': 'handshake', 'label': '三次握手', 'x': 250, 'y': 100}],
    })
    assert graph.status_code == 200, graph.text
    return course, path


def add_question(client, auth, path, title, node=None):
    response = client.post(path + '/questions', headers=auth, json={
        'title': title, 'options': ['正确', '错误'], 'answer': 0, 'explanation': '讲义中的结论。',
        'point': '计算机网络', **({'knowledgeNodeId': node} if node else {}),
    })
    assert response.status_code == 201, response.text
    return response.json()['id']


def answer(client, auth, question, choice, seq):
    response = client.post(API + '/attempts', headers=auth, json={
        'questionId': question, 'choice': choice, 'submissionId': f'coach-test-{seq}',
    })
    assert response.status_code == 200, response.text


def test_coach_preview_uses_model_tools_and_confirm_is_idempotent(client, auth, settings, monkeypatch):
    course, path = make_course(client, auth)
    header = '📝 网络讲义\n'
    excerpt = 'TCP 的超时重传用于在确认超时后重新发送。'
    material = client.post(path + '/materials', headers=auth, files={
        'file': ('讲义.txt', (header + excerpt).encode(), 'text/plain'),
    })
    assert material.status_code == 201, material.text
    original = add_question(client, auth, path, '超时后需要重传吗？', 'retransmit')
    variant = add_question(client, auth, path, '未收到确认报文时接下来应该如何处理？', 'retransmit')
    repaired = add_question(client, auth, path, '什么是连接建立流程？', 'handshake')
    answer(client, auth, original, 1, 1)
    answer(client, auth, repaired, 1, 2)
    answer(client, auth, repaired, 0, 3)

    def provider(request):
        body = json.loads(request.content)
        assert {tool['function']['name'] for tool in body['tools']} == {'search_course_evidence', 'find_unseen_questions'}
        assert excerpt not in body['messages'][-1]['content']
        assert '三次握手' not in body['messages'][-1]['content']
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [
            tool_call('search_course_evidence', {'focus': 0}), tool_call('find_unseen_questions', {'focus': 0}),
        ]}}]})

    mock_model(monkeypatch, settings, provider)
    request = {'courseId': course, 'minutes': 20}
    before = client.get(API + '/tasks', headers=auth).json()['total']
    result = client.post(PREVIEW, headers=auth, json=request)
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview['mode'] == 'ai' and preview['status'] == 'ready'
    assert preview['focus'] == {'id': 'retransmit', 'label': '超时重传', 'wrong': 1, 'total': 1}
    assert preview['questions'] == [{'id': variant, 'title': '未收到确认报文时接下来应该如何处理？'}]
    assert preview['evidence'][0]['excerpt'] == excerpt
    assert preview['evidence'][0]['start'] == len(header.encode('utf-16-le')) // 2
    history = client.get(HISTORY, headers=auth, params={'courseId': course}).json()
    assert history['items'] == [preview]
    assert client.get(API + '/tasks', headers=auth).json()['total'] == before
    confirm = {**request, 'knowledgeNodeId': 'retransmit', 'questionIds': [variant]}
    invalid = client.post(CONFIRM, headers=auth, json={**confirm, 'questionIds': [repaired]})
    assert invalid.status_code == 409
    first = client.post(CONFIRM, headers=auth, json=confirm)
    second = client.post(CONFIRM, headers=auth, json=confirm)
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()['id'] == second.json()['id']
    assert first.json()['questionIds'] == [variant]
    assert client.get(API + '/tasks', headers=auth).json()['total'] == before + 1


def test_coach_distinguishes_no_history_no_evidence_and_drafts(client, auth, settings, monkeypatch):
    course, path = make_course(client, auth)
    request = {'courseId': course, 'minutes': 10}
    assert client.post(PREVIEW, headers=auth, json=request).json()['status'] == 'no_history'
    orphan = add_question(client, auth, path, '什么是重传？')
    answer(client, auth, orphan, 1, 1)
    assert client.post(PREVIEW, headers=auth, json=request).json()['status'] == 'needs_link'
    linked = add_question(client, auth, path, '超时重传如何触发？', 'retransmit')
    answer(client, auth, linked, 1, 2)
    assert client.post(PREVIEW, headers=auth, json=request).json()['status'] == 'no_evidence'
    client.post(path + '/materials', headers=auth,
                files={'file': ('讲义.txt', '超时重传的作用是保证可靠传输。'.encode(), 'text/plain')})
    assert client.post(PREVIEW, headers=auth, json=request).json()['status'] == 'model_needed'
    mock_model(monkeypatch, settings, lambda _: httpx.Response(503))
    result = client.post(PREVIEW, headers=auth, json=request).json()
    assert result['mode'] == 'rules' and result['status'] == 'drafts'


def test_coach_checks_course_ownership(client, auth):
    course, _ = make_course(client, auth)
    token = client.post(API + '/auth/demo').json()['accessToken']
    other = {'Authorization': 'Bearer ' + token}
    request = {'courseId': course, 'minutes': 20}
    assert client.post(PREVIEW, headers=other, json=request).status_code == 404
    assert client.post(CONFIRM, headers=other, json={
        **request, 'knowledgeNodeId': 'retransmit', 'questionIds': ['missing'],
    }).status_code == 404


def test_coach_history_is_paginated_scoped_and_preserves_named_snapshots(client, auth):
    course, path = make_course(client, auth)
    first = client.post(PREVIEW, headers=auth, json={'courseId': course, 'minutes': 10, 'goal': '回顾 TCP'}).json()
    assert client.patch(path, headers=auth, json={'name': '更新后的课程名'}).status_code == 200
    second = client.post(PREVIEW, headers=auth, json={'courseId': course, 'minutes': 20, 'goal': '回顾 UDP'}).json()
    for entry in (first, second):
        assert entry['createdAt'].endswith('Z') and entry['status'] == 'no_history'
    assert first['courseName'] == '复习教练测试' and first['goal'] == '回顾 TCP'
    assert second['courseName'] == '更新后的课程名' and second['goal'] == '回顾 UDP'
    page = client.get(HISTORY, headers=auth, params={'limit': 1}).json()
    assert page['total'] == 2 and page['items'] == [second]
    assert client.get(HISTORY, headers=auth, params={'limit': 1, 'offset': 1}).json()['items'] == [first]
    assert client.get(HISTORY, headers=auth, params={'courseId': 'linear'}).json()['items'] == []
    other = {'Authorization': 'Bearer ' + client.post(API + '/auth/demo').json()['accessToken']}
    assert client.get(HISTORY, headers=other).json()['items'] == []
    assert client.get(HISTORY, headers=other, params={'courseId': course}).status_code == 404
    assert client.get(HISTORY).status_code == 401
    exported = client.get(API + '/data/export', headers=auth).json()['coachHistory']
    assert {entry['id'] for entry in exported} == {first['id'], second['id']}
    assert client.delete(path, headers=auth).status_code == 204
    assert client.get(HISTORY, headers=auth).json()['total'] == 0
