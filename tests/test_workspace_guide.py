"""验证向导模型调用、权限边界和手册回退。"""
import json

import httpx

from test_assistant_practice_drafts import mock_model


ENDPOINT = '/api/v1/assistant/workspace-guide'


def create_course(client, auth):
    response = client.post('/api/v1/courses', headers=auth, json={'name': '向导测试课程'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def tool_call(name, args):
    return {'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def test_model_uses_course_tools_to_choose_next_action(client, auth, settings, monkeypatch):
    course_id = create_course(client, auth)

    def provider(request):
        body = json.loads(request.content)
        assert 'get_course_practice_status' in [tool['function']['name'] for tool in body['tools']]
        assert course_id not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'message': {'content': None, 'tool_calls': [
            tool_call('get_operation_help', {'topic': 'drafts'}),
            tool_call('get_course_material_status', {}),
            tool_call('get_course_practice_status', {}),
        ]}}]})

    mock_model(monkeypatch, settings, provider)
    body = {'page': 'question-bank', 'question': '这门课怎么还不能生成题目？', 'courseId': course_id}
    result = client.post(ENDPOINT, headers=auth, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['mode'] == 'ai'
    assert result.json()['action'] == 'materials'
    assert len(result.json()['checks']) == 2

    uploaded = client.post(f'/api/v1/courses/{course_id}/materials', headers=auth,
                           files={'file': ('notes.txt', b'TCP is reliable.', 'text/plain')})
    assert uploaded.status_code == 201, uploaded.text
    result = client.post(ENDPOINT, headers=auth, json=body).json()
    assert result['action'] == 'knowledge'
    assert '1 份可用于检索' in result['checks'][0]


def test_workspace_guide_refuses_foreign_course_before_model_call(client, auth, settings, monkeypatch):
    course_id = create_course(client, auth)
    other = client.post('/api/v1/auth/demo').json()['accessToken']

    def provider(_request):
        raise AssertionError('Foreign course must never reach the model')

    mock_model(monkeypatch, settings, provider)
    result = client.post(ENDPOINT, headers={'Authorization': f'Bearer {other}'}, json={
        'page': 'question-bank', 'question': '查一下这门课的题目', 'courseId': course_id,
    })
    assert result.status_code == 404


def test_workspace_guide_falls_back_when_model_fails_or_requests_other_course(client, auth, settings, monkeypatch):
    course_id = create_course(client, auth)
    body = {'page': 'question-bank', 'question': '如何生成题目', 'courseId': course_id}
    responses = iter([
        httpx.Response(503),
        httpx.Response(200, json={'choices': [{'message': {'content': None, 'tool_calls': [
            tool_call('get_course_material_status', {'courseId': 'another-course'}),
            tool_call('delete_course', {}),
        ]}}]}),
    ])
    mock_model(monkeypatch, settings, lambda _request: next(responses))
    for _ in range(2):
        response = client.post(ENDPOINT, headers=auth, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['mode'] == 'manual'
        assert response.json()['action'] == 'materials'
