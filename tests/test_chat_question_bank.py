import json

from sqlalchemy import select

from app.models import AuthSession, ChatTurn
from app.security import token_digest
from test_assistant_practice_drafts import mock_model, model_response
from test_study_chat import add_notes


API = '/api/v1'
NOTES = 'TCP 超时重传会重新发送定时器到期仍未确认的报文段。'
DRAFT = {
    'title': 'TCP 重传定时器到期后如何处理未确认的报文段？',
    'options': ['重新发送该报文段', '立即结束连接', '等待下一次应用层请求', '删除接收窗口'],
    'answer': 0, 'explanation': '重传定时器到期后重新发送未确认的报文段。',
    'point': 'TCP 超时重传', 'sourceIndex': 1,
}


def test_chat_draft_preserves_answer_and_evidence_through_import(client, auth, settings, monkeypatch):
    material = add_notes(client, auth, '计算机网络.txt', NOTES)

    def provider(request):
        body = json.loads(request.content)
        assert 'questions' in body['messages'][0]['content']
        return model_response(json.dumps({
            'text': '围绕超时重传做一道练习。',
            'questions': [{**DRAFT, 'sourceIndex': 99}, DRAFT, DRAFT],
        }))

    mock_model(monkeypatch, settings, provider)
    reply = client.post(API + '/assistant/ask', headers=auth, json={
        'courseId': 'english', 'materialId': material, 'question': '根据这份资料出一道练习题',
    })
    assert reply.status_code == 200, reply.text
    response = reply.json()
    draft, = response['questions']
    assert draft['sourceEvidence']['excerpt'] == NOTES
    assert draft['source'] == material and draft['options'][draft['answer']] == '重新发送该报文段'
    history = client.get(API + '/assistant/history?courseId=english', headers=auth).json()['items']
    assert history[0]['questions'] == [draft]
    payload = {'questions': [draft], 'deduplicateSimilar': True}
    route = API + '/courses/english/questions/import'
    imported = client.post(route, headers=auth, json=payload)
    assert imported.status_code == 200, imported.text
    assert imported.json()['imported'] == 1
    assert client.post(route, headers=auth, json=payload).json()['skipped'] == 1
    bank = client.get(API + '/questions?courseId=english', headers=auth).json()['items']
    stored = next(item for item in bank if item['title'] == draft['title'])
    assert stored['options'] == draft['options']
    attempt = client.post(API + '/attempts', headers=auth, json={
        'questionId': stored['id'], 'choice': draft['answer'], 'seconds': 3, 'submissionId': 'chat_quiz_test_1',
    })
    assert attempt.status_code == 200, attempt.text
    graded = attempt.json()
    assert graded['answer'] == draft['answer'] and graded['sourceEvidence']['materialId'] == material


def test_legacy_chat_exercise_can_be_prepared_once_and_only_by_owner(client, auth, settings, monkeypatch):
    material = add_notes(client, auth, '旧讲义.txt', NOTES)
    with settings._testing_database.sessions() as db:
        user_id = db.scalar(select(AuthSession.user_id).where(
            AuthSession.digest == token_digest(auth['Authorization'].split()[1])))
        old = ChatTurn(user_id=user_id, course_id='english', mode='llm', model='old',
                       question='按讲义出题', text='练习题：TCP 超时后如何处理？',
                       sources=[{'materialId': material, 'name': '旧讲义.txt', 'excerpt': NOTES,
                                 'score': 1, 'start': 0, 'end': len(NOTES)}])
        db.add(old)
        db.flush()
        old_id = old.id
        db.commit()

    calls = []

    def provider(request):
        body = json.loads(request.content)
        assert 'TCP 超时后如何处理' in body['messages'][-1]['content']
        calls.append(1)
        return model_response(json.dumps({'text': '', 'questions': [DRAFT]}))

    mock_model(monkeypatch, settings, provider)
    route = API + f'/assistant/history/{old_id}/question-drafts'
    stranger = {'Authorization': 'Bearer ' + client.post(API + '/auth/demo').json()['accessToken']}
    assert client.post(route, headers=stranger).status_code == 404
    result = client.post(route, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()['questions'][0]['point'] == 'TCP 超时重传'
    assert client.post(route, headers=auth).json() == result.json()
    assert len(calls) == 1
