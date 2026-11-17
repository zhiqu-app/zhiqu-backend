from base64 import b64decode
from io import BytesIO
import json

from PIL import Image
import pytest

from app.config import Settings
from test_assistant_practice_drafts import mock_model, model_response


URL = '/api/v1/assistant/ask-image'
OBSERVATIONS = {'readable': True, 'observations': 'TCP 重传超时设定示意', 'keywords': ['TCP', '重传']}


def upload(image_format='PNG', size=(320, 180)):
    picture = BytesIO()
    Image.new('RGB', size, '#198dcc').save(picture, format=image_format)
    return ('screenshot.png', picture.getvalue(), 'image/png')


def test_images_use_current_course_excerpts_and_save_text_history(client, auth, settings, monkeypatch):
    excerpt = 'TCP 重传会重新发送超时仍未确认的报文段。'
    for course, text in [('linear', excerpt), ('english', 'TCP 重传 OTHER-COURSE-PRIVATE')]:
        material = client.post(f'/api/v1/courses/{course}/materials', headers=auth,
                               files={'file': ('network.txt', ('📘\n' + text).encode(), 'text/plain')})
        assert material.status_code == 201, material.text
    calls = []

    def provider(request):
        model_input = json.loads(request.content)
        calls.append(model_input['model'])
        if len(calls) == 1:
            text, picture = model_input['messages'][1]['content']
            assert '为什么会重传？' in text['text']
            url = picture['image_url']['url']
            assert url.startswith('data:image/jpeg;base64,')
            with Image.open(BytesIO(b64decode(url.split(',')[1]))) as decoded:
                assert decoded.format == 'JPEG' and not decoded.getexif()
            return model_response(json.dumps(OBSERVATIONS))
        assert calls == ['vision-model', 'test-model']
        assert '不生成新的练习题' in model_input['messages'][0]['content']
        context = json.loads(model_input['messages'][1]['content'])
        assert context['imageObservations'] == OBSERVATIONS['observations']
        assert any(entry['excerpt'] == excerpt for entry in context['courseExcerpts'])
        assert 'OTHER-COURSE-PRIVATE' not in json.dumps(context)
        real_index = next(entry['index'] for entry in context['courseExcerpts'] if entry['excerpt'] == excerpt)
        return model_response(json.dumps({'answer': '超时未收到确认会触发重传。[999]',
                                          'sourceIndices': [999, real_index, real_index]}))

    mock_model(monkeypatch, settings, provider)
    settings.ai_vision_model = 'vision-model'
    form = {'courseId': 'linear', 'question': '为什么会重传？', 'requestId': 'same-request'}
    response = client.post(URL, headers=auth, data=form, files={'image': upload()})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['question'] == '图片提问 · 为什么会重传？'
    assert answer['text'] == '超时未收到确认会触发重传。'
    source, = answer['sources']
    assert source['excerpt'] == excerpt and source['start'] == 3
    assert client.post(URL, headers=auth, data=form, files={'image': upload()}).json() == answer
    history = client.get('/api/v1/assistant/history?courseId=linear', headers=auth).json()
    assert history['items'] == [answer]
    assert len(calls) == 2


def test_image_without_lecture_accepts_default_question(client, auth, settings, monkeypatch):
    def provider(request):
        message = json.loads(request.content)['messages'][-1]['content']
        if isinstance(message, list):
            assert '请说明图片' in message[0]['text']
            return model_response(json.dumps(OBSERVATIONS))
        assert json.loads(message)['courseExcerpts'] == []
        return model_response(json.dumps({'answer': '图片显示 TCP 重传。', 'sourceIndices': [1]}))

    mock_model(monkeypatch, settings, provider)
    settings.ai_vision_model = 'vision-model'
    course = client.post('/api/v1/courses', headers=auth, json={'name': '无资料课程'})
    assert course.status_code == 201, course.text
    response = client.post(URL, headers=auth, data={'courseId': course.json()['id']}, files={'image': upload()})
    assert response.status_code == 200, response.text
    assert response.json()['sources'] == []
    assert '没有引用课程资料' in response.json()['text']


def test_image_route_rejects_invalid_unauthorized_or_unreadable_uploads(client, auth, settings, monkeypatch):
    def provider(request):
        return model_response(json.dumps({'readable': False, 'observations': '', 'keywords': []}))

    form = {'courseId': 'linear'}
    assert client.post(URL, data=form, files={'image': upload()}).status_code == 401
    assert client.post(URL, headers=auth, data=form, files={'image': upload()}).status_code == 503
    mock_model(monkeypatch, settings, provider)
    settings.ai_vision_model = 'vision-model'
    assert client.post(URL, headers=auth, data={'courseId': 'missing'}, files={'image': upload()}).status_code == 404
    for file, expected in [
        (('fake.png', b'just text', 'image/png'), 415),
        (upload(size=(4097, 12)), 413),
        (('big.png', b'0' * (5 * 1024 * 1024 + 1), 'image/png'), 413),
        (upload(), 422),
    ]:
        response = client.post(URL, headers=auth, data=form, files={'image': file})
        assert response.status_code == expected, response.text
    assert client.get('/api/v1/assistant/history?courseId=linear', headers=auth).json()['total'] == 0


def test_vision_settings_default_and_key_isolation():
    main = {'ai_base_url': 'https://api.deepseek.com', 'ai_api_key': 'text-secret', 'ai_model': 'deepseek-chat'}
    settings = Settings(**main)
    assert settings.vision_enabled and settings.vision_model == 'deepseek-flash'
    assert settings.vision_api_key == 'text-secret'
    with pytest.raises(ValueError, match='AI_VISION_API_KEY'):
        Settings(**main, ai_vision_base_url='https://other-model.example/v1')
    settings = Settings(**main, ai_vision_base_url='https://other-model.example/v1',
                        ai_vision_api_key='vision-secret', ai_vision_model='vision-model')
    assert settings.vision_enabled and settings.vision_api_key == 'vision-secret'
