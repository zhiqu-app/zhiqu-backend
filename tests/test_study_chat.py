import json

from test_assistant_practice_drafts import mock_model, model_response


API = '/api/v1'


def add_notes(client, auth, name, text):
    response = client.post(API + '/courses/english/materials', headers=auth,
                           files={'file': (name, text.encode(), 'text/plain')})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_generic_study_request_uses_selected_notes_and_returns_grounded_exercises(client, auth, settings, monkeypatch):
    opening = '📘 TCP 超时重传：发送方在重传定时器到期后重新发送未确认的报文。'
    end = '窗口缩小用于减少网络发生拥塞时的发送量。'
    document = '\n'.join([opening, *['中间章节 ' + '内部示例。' * 105] * 12, end])
    chosen = add_notes(client, auth, 'TCP.txt', document)
    add_notes(client, auth, '其他讲义.txt', '不得用于本次回答的片段 OTHER-DOCUMENT')

    def provider(request):
        messages = json.loads(request.content)['messages']
        prompt = messages[0]['content']
        evidence = messages[1]['content']
        assert '练习题' in prompt and '课外拓展' in prompt and '不生成练习题' not in prompt
        assert opening in evidence and end in evidence and 'OTHER-DOCUMENT' not in evidence
        return model_response('知识点：超时重传 [1]\n练习题：何时重传？\n答案解析：定时器到期。\n课外拓展：比较快速重传。')

    mock_model(monkeypatch, settings, provider)
    result = client.post(API + '/assistant/ask', headers=auth, json={
        'courseId': 'english', 'materialId': chosen,
        'question': '根据资料将资料相关的知识点和拓展都生成一下，再出几道练习题。',
    })
    assert result.status_code == 200, result.text
    answer = result.json()
    assert '答案解析' in answer['text'] and answer['mode'] == 'llm'
    assert {source['materialId'] for source in answer['sources']} == {chosen}
    last = answer['sources'][-1]
    utf16 = document.encode('utf-16-le')
    assert utf16[last['start'] * 2:last['end'] * 2].decode('utf-16-le') == last['excerpt']
    assert client.get(API + '/assistant/history?courseId=english', headers=auth).json()['items'][0] == answer


def test_free_text_study_requests_ignore_generic_words_but_keep_the_topic(client, auth, settings, monkeypatch):
    notes = 'TCP 超时重传发生在等待确认超时之后。'
    add_notes(client, auth, '网络讲义.txt', notes)
    prompts = []

    def provider(request):
        prompts.append(json.loads(request.content)['messages'][-1]['content'])
        return model_response('练习题：什么条件下会重传？ [1]')

    mock_model(monkeypatch, settings, provider)
    for question in ('根据资料给我出三道题', '围绕 TCP 超时重传出三道练习题'):
        response = client.post(API + '/assistant/ask', headers=auth,
                               json={'courseId': 'english', 'question': question})
        assert response.status_code == 200 and response.json()['mode'] == 'llm', response.text
        assert notes in prompts[-1]
    response = client.post(API + '/assistant/ask', headers=auth,
                           json={'courseId': 'english', 'question': '围绕量子纠缠出三道练习题'})
    assert response.json()['mode'] == 'retrieval'
    assert len(prompts) == 2


def test_scope_rejects_material_from_another_course(client, auth):
    material = add_notes(client, auth, '讲义.txt', 'TCP 重传用于确认丢包后发送。')
    response = client.post(API + '/assistant/ask', headers=auth, json={
        'courseId': 'linear', 'materialId': material, 'question': '请整理核心知识点',
    })
    assert response.status_code == 404


def test_advice_and_explanations_do_not_generate_or_offer_question_drafts(client, auth, settings, monkeypatch):
    notes = 'TCP 超时重传发生在等待确认超时之后。'
    material = add_notes(client, auth, 'TCP.txt', notes)
    calls = []

    def provider(request):
        messages = json.loads(request.content)['messages']
        calls.append(messages)
        assert '不生成新的练习题' in messages[0]['content']
        assert 'questions' not in messages[0]['content']
        assert notes in messages[-1]['content']
        return model_response('练习建议：先复习超时重传的触发条件。')

    mock_model(monkeypatch, settings, provider)
    prompts = (
        '针对 TCP 超时重传给我一些复习建议',
        '给我讲解一下题库里这道 TCP 超时重传的题',
        'TCP 超时重传的出题方向有什么建议？',
        'TCP 超时重传怎么出题比较合适？',
        '先解释 TCP 超时重传，不用生成练习题',
        '这份资料怎么复习？请给我一些建议',
        '只给我复习建议，不用生成练习题',
    )
    for question in prompts:
        result = client.post(API + '/assistant/ask', headers=auth, json={
            'courseId': 'english', 'materialId': material, 'question': question,
        })
        assert result.status_code == 200, result.text
        reply = result.json()
        assert reply['mode'] == 'llm'
        assert reply['questions'] == [] and reply['canPrepareQuestions'] is False
    assert len(calls) == len(prompts)


def test_explicit_quiz_requests_cover_common_phrasing(client, auth, settings, monkeypatch):
    notes = 'TCP 超时重传发生在等待确认超时之后。'
    material = add_notes(client, auth, 'TCP.txt', notes)
    calls = []

    def provider(request):
        system = json.loads(request.content)['messages'][0]['content']
        calls.append(system)
        assert 'questions' in system
        return model_response(json.dumps({'text': '练习', 'questions': []}))

    mock_model(monkeypatch, settings, provider)
    prompts = ('请编两道题', '给我三道选择题', '请设计三道练习题', '先给复习建议，再出两道题',
               '给我几道关于 TCP 超时重传的选择题')
    for question in prompts:
        result = client.post(API + '/assistant/ask', headers=auth, json={
            'courseId': 'english', 'materialId': material, 'question': question,
        })
        assert result.status_code == 200 and result.json()['mode'] == 'llm', result.text
    assert len(calls) == len(prompts)
