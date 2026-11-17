import json
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import services


BASE = '/api/v1'
DRAFTS = BASE + '/assistant/practice-drafts'
REQUEST = {'courseId': 'linear', 'knowledgeNodeId': 'eigen'}


def mock_model(monkeypatch, settings, handler):
    settings.ai_base_url = 'https://model.example/v1'
    settings.ai_api_key = 'private-test-key'
    settings.ai_model = 'test-model'
    original_client = httpx.Client
    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))


def model_response(content):
    return httpx.Response(200, json={
        'choices': [{'message': {'content': content}}],
        'usage': {'prompt_tokens': 98, 'completion_tokens': 60, 'total_tokens': 158},
    })


def sample_draft(**changes):
    return {
        'title': '对角矩阵的哪一部分给出它的特征值？',
        'options': ['主对角线', '列和', '第一行', '副对角线'],
        'answer': 0,
        'explanation': '资料的定义说明，对角矩阵的特征值是其主对角线元素。',
        'sourceIndex': 1,
        **changes,
    }


def test_chat_sends_an_explanation_prompt_and_keeps_the_answer(client, auth, settings, monkeypatch):
    def provider(request):
        prompt = json.loads(request.content)['messages'][0]['content']
        assert '只回答' in prompt and '[1]' in prompt
        assert '出题助手' not in prompt and 'questions' not in prompt
        return model_response('主对角线上的元素就是特征值 [1]。')

    mock_model(monkeypatch, settings, provider)
    response = client.post(BASE + '/assistant/ask', headers=auth, json={
        'courseId': 'linear', 'question': '对角矩阵的特征值是什么？',
    })
    assert response.status_code == 200, response.text
    assert response.json()['text'] == '主对角线上的元素就是特征值 [1]。'


def test_drafts_attach_the_exact_selected_excerpt_without_writing_to_bank(client, auth, settings, monkeypatch, caplog):
    # 前置 emoji 在浏览器中占两个字符单位，后端 Python str 占一个码点。
    excerpt = '对角矩阵的特征值等于它的主对角线元素。'
    material = client.post(BASE + '/courses/linear/materials', headers=auth, files={
        'file': ('review.txt', ('📘\n' + excerpt).encode(), 'text/plain'),
    })
    assert material.status_code == 201, material.text
    material_id = material.json()['id']
    bank_url = BASE + '/questions?courseId=linear'
    bank_before = client.get(bank_url, headers=auth).json()

    def provider(request):
        prompt = json.loads(request.content)['messages'][-1]['content']
        assert excerpt in prompt and '特征值' in prompt
        assert '定义 3.1' not in prompt
        return model_response(json.dumps({'questions': [sample_draft()]}, ensure_ascii=False))

    mock_model(monkeypatch, settings, provider)
    caplog.set_level('INFO', logger='uvicorn.error.zhiqu.ai')
    response = client.post(DRAFTS, headers=auth, json={**REQUEST, 'materialId': material_id, 'count': 1})
    assert response.status_code == 200, response.text
    draft, = response.json()['questions']
    assert draft == {
        **{key: value for key, value in sample_draft().items() if key != 'sourceIndex'},
        'point': '特征值', 'knowledgeNodeId': 'eigen', 'source': material_id,
        'sourceEvidence': {
            'materialId': material_id, 'name': 'review.txt', 'excerpt': excerpt, 'start': 3, 'end': 22,
        },
    }
    assert client.get(bank_url, headers=auth).json() == bank_before
    assert 'total_tokens=158' in caplog.text and 'duration_ms=' in caplog.text
    assert excerpt not in caplog.text and 'private-test-key' not in caplog.text


def test_pdf_evidence_uses_the_parser_page_and_exact_excerpt(client, auth, settings, monkeypatch):
    graph = client.put(BASE + '/courses/english/knowledge', headers=auth, json={
        'title': 'Programming English', 'revision': 0, 'links': [],
        'nodes': [{'id': 'stack', 'label': 'stack', 'x': 0, 'y': 0}],
    })
    assert graph.status_code == 200, graph.text
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    excerpt = 'A stack removes the most recently added item first.'
    stream.set_data(f'BT /F1 12 Tf 20 200 Td ({excerpt}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    file = BytesIO()
    writer.write(file)
    material = client.post(BASE + '/courses/english/materials', headers=auth, files={
        'file': ('stack.pdf', file.getvalue(), 'application/pdf'),
    })
    assert material.status_code == 201, material.text
    material_id = material.json()['id']
    question = sample_draft(title='Which item is removed from a stack first?',
        options=['Most recently added', 'Least recently added', 'Smallest', 'Largest'],
        explanation='A stack removes the most recently added item first.')

    def provider(request):
        assert '最多生成 3 道题' in json.loads(request.content)['messages'][-1]['content']
        return model_response(json.dumps({'questions': [question]}))

    mock_model(monkeypatch, settings, provider)
    response = client.post(DRAFTS, headers=auth, json={
        'courseId': 'english', 'knowledgeNodeId': 'stack', 'materialId': material_id,
    })
    assert response.status_code == 200, response.text
    assert response.json()['questions'][0]['sourceEvidence'] == {
        'materialId': material_id, 'name': 'stack.pdf', 'excerpt': excerpt, 'start': 8, 'end': 59, 'page': 2,
    }


@pytest.mark.parametrize('body', [
    {'courseId': 'english', 'knowledgeNodeId': 'eigen'},
    {**REQUEST, 'knowledgeNodeId': 'missing'},
    {**REQUEST, 'materialId': 'missing'},
    {**REQUEST, 'materialId': 'foreign-course'},
])
def test_drafts_authorize_course_node_and_material_before_calling_model(client, auth, settings, monkeypatch, body):
    # 使用真实账号材料构造同账号、不同课程的资源，验证不仅检查账号。
    material = client.post(BASE + '/courses/english/materials', headers=auth, files={
        'file': ('foreign.txt', '对角矩阵的特征值等于主对角线元素。'.encode(), 'text/plain'),
    })
    assert material.status_code == 201, material.text
    if body.get('materialId') == 'foreign-course':
        body = {**body, 'materialId': material.json()['id']}

    def provider(_):
        pytest.fail('越权请求不得调用模型')

    mock_model(monkeypatch, settings, provider)
    response = client.post(DRAFTS, headers=auth, json=body)
    assert response.status_code == 404
    assert '无权访问' in response.json()['error']['message']


def test_disabled_model_is_reported_explicitly(client, auth):
    response = client.post(DRAFTS, headers=auth, json=REQUEST)
    assert response.status_code == 503
    assert '未配置' in response.json()['error']['message']


def test_irrelevant_material_is_rejected_without_model_call(client, auth, settings, monkeypatch):
    material = client.post(BASE + '/courses/linear/materials', headers=auth, files={
        'file': ('notes.txt', '完全无关的英语笔记，主题是旅行。'.encode(), 'text/plain'),
    })
    assert material.status_code == 201, material.text

    def provider(_):
        pytest.fail('资料不足时不得调用模型')

    mock_model(monkeypatch, settings, provider)
    response = client.post(DRAFTS, headers=auth, json={**REQUEST, 'materialId': material.json()['id']})
    assert response.status_code == 422
    assert '资料' in response.json()['error']['message']


def test_invalid_and_duplicate_items_are_discarded(client, auth, settings, monkeypatch):
    questions = [
        sample_draft(options=['A', 'a', 'B', 'C']),
        sample_draft(answer=True),
        sample_draft(sourceIndex=99),
        sample_draft(),
        sample_draft(title='对角矩阵的哪一部分给出它的特征值?'),
    ]
    mock_model(monkeypatch, settings, lambda _: model_response(json.dumps({'questions': questions})))
    response = client.post(DRAFTS, headers=auth, json={**REQUEST, 'count': 5})
    assert response.status_code == 200, response.text
    drafts = response.json()['questions']
    assert [draft['title'] for draft in drafts] == ['对角矩阵的哪一部分给出它的特征值？']


def test_bank_titles_are_not_generated_again(client, auth, settings, monkeypatch):
    questions = [sample_draft(title='二阶单位矩阵的特征值是什么?'), sample_draft()]
    mock_model(monkeypatch, settings, lambda _: model_response(json.dumps({'questions': questions})))
    response = client.post(DRAFTS, headers=auth, json=REQUEST)
    assert response.status_code == 200, response.text
    assert [draft['title'] for draft in response.json()['questions']] == [sample_draft()['title']]


def test_recent_drafts_and_bank_titles_are_used_to_avoid_reworded_questions(client, auth, settings, monkeypatch):
    previous = '线性变换中，特征向量应当满足什么条件？'
    reworded = '在线性变换中，特征向量应当满足哪些条件？'
    first_variant = '在给定线性变换下，若 Av=2v，向量对应的特征值是多少？'
    second_variant = '在给定线性变换下，若 Av=3v，向量对应的特征值是多少？'

    def provider(request):
        messages = json.loads(request.content)['messages']
        assert previous in messages[-1]['content']
        assert '二阶单位矩阵的特征值是什么' in messages[-1]['content']
        assert '避免换句话' in messages[0]['content']
        return model_response(json.dumps({'questions': [sample_draft(title=title) for title in (
            reworded, first_variant, second_variant)]}))

    mock_model(monkeypatch, settings, provider)
    response = client.post(DRAFTS, headers=auth, json={**REQUEST, 'excludeTitles': [previous]})
    assert response.status_code == 200, response.text
    assert [draft['title'] for draft in response.json()['questions']] == [first_variant, second_variant]


def test_approved_drafts_are_checked_against_current_bank_at_import(client, auth):
    endpoint = BASE + '/courses/linear/questions/import'
    original = '在线性变换中，特征向量应当满足什么条件？'
    reworded = '在线性变换中，特征向量应当满足哪些条件？'

    def question(title, **changes):
        return {**{key: value for key, value in sample_draft(title=title, **changes).items() if key != 'sourceIndex'},
                'point': '特征值', 'knowledgeNodeId': 'eigen'}

    assert client.post(endpoint, headers=auth, json={'questions': [question(original)]}).json()['imported'] == 1
    with_duplicate = question(reworded, options=['同方向', '方向未知', '方向相反', '与变换无关'])
    response = client.post(endpoint, headers=auth, json={'deduplicateSimilar': True,
        'questions': [with_duplicate, question('若 Av=2v，特征值是多少？'), question('若 Av=3v，特征值是多少？')]})
    assert response.status_code == 200, response.text
    assert response.json()['imported'] == 2
    assert response.json()['skipped'] == 1


@pytest.mark.parametrize(('content', 'status'), [
    ('{"questions":[]}', 422),
    ('```json\n{"questions":[]}\n```', 502),
    ('{"questions":[{"title":"凭空编造"}]}', 502),
])
def test_empty_or_malformed_model_result_has_a_specific_error(client, auth, settings, monkeypatch, content, status):
    mock_model(monkeypatch, settings, lambda _: model_response(content))
    response = client.post(DRAFTS, headers=auth, json=REQUEST)
    assert response.status_code == status, response.text


def test_drafts_reject_evidence_fields_authored_by_the_model(client, auth, settings, monkeypatch):
    invalid = sample_draft(sourceEvidence={'materialId': 'fabricated', 'excerpt': 'invented'})
    mock_model(monkeypatch, settings, lambda _: model_response(json.dumps({'questions': [invalid]})))
    response = client.post(DRAFTS, headers=auth, json=REQUEST)
    assert response.status_code == 502
    assert 'fabricated' not in response.text and 'invented' not in response.text


def test_provider_timeout_returns_504_without_exposing_provider_details(client, auth, settings, monkeypatch):
    def provider(request):
        raise httpx.ReadTimeout('private provider failure', request=request)

    mock_model(monkeypatch, settings, provider)
    response = client.post(DRAFTS, headers=auth, json=REQUEST)
    assert response.status_code == 504
    assert 'private provider failure' not in response.text


@pytest.mark.parametrize('count', [0, 6, '3', True])
def test_draft_count_must_be_a_bounded_integer(client, auth, count):
    response = client.post(DRAFTS, headers=auth, json={**REQUEST, 'count': count})
    assert response.status_code == 422
    assert response.json()['error']['details'][0]['field'] == 'body.count'
