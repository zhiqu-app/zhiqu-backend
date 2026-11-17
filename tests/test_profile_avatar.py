"""回归账号头像保存、跨会话读取、清除与旧账号迁移。"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select

from app.models import User


JPEG_AVATAR = 'data:image/jpeg;base64,' + (
    '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQ'
    'DQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQU'
    'FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA'
    'AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEI'
    'I0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4'
    'eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo'
    '6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQA'
    'AQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNE'
    'RUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3'
    'uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDq6KKK/os/Kj//2Q=='
)


def test_avatar_follows_account_and_can_be_cleared(client, verified_register):
    credentials = {'email': 'avatar@example.test', 'password': 'Safe-password-123'}
    registered = verified_register({**credentials, 'name': '测试同学'}).json()
    headers = {'Authorization': 'Bearer ' + registered['accessToken']}
    assert registered['user']['profile']['avatar'] == ''
    saved = client.patch('/api/v1/profile', headers=headers, json={'avatar': JPEG_AVATAR})
    assert saved.status_code == 200, saved.text
    assert saved.json()['avatar'] == JPEG_AVATAR
    assert client.patch('/api/v1/profile', headers=headers, json={'major': '计算机'}).json()['avatar'] == JPEG_AVATAR
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
    login = client.post('/api/v1/auth/login', json=credentials).json()
    assert login['user']['profile']['avatar'] == JPEG_AVATAR
    headers = {'Authorization': 'Bearer ' + login['accessToken']}
    other = client.post('/api/v1/auth/demo').json()
    assert other['user']['profile']['avatar'] == ''
    assert client.get('/api/v1/auth/me', headers=headers).json()['profile']['avatar'] == JPEG_AVATAR
    assert client.patch('/api/v1/profile', headers=headers, json={'avatar': ''}).json()['avatar'] == ''
    assert client.get('/api/v1/profile', headers=headers).json()['avatar'] == ''


def test_invalid_avatar_does_not_change_existing_profile(client, auth):
    assert client.patch('/api/v1/profile', headers=auth, json={'avatar': JPEG_AVATAR}).status_code == 200
    for value in ('https://example.test/user.jpg', 'data:image/svg+xml;base64,PHN2Zz4=',
                  'data:image/jpeg;base64,invalid==', 'data:image/jpeg;base64,YWJjZA==',
                  'data:image/jpeg;base64,' + 'A' * 49152):
        response = client.patch('/api/v1/profile', headers=auth, json={'avatar': value, 'major': '不保存'})
        assert response.status_code == 422, response.text
    profile = client.get('/api/v1/profile', headers=auth).json()
    assert profile['avatar'] == JPEG_AVATAR
    assert profile['major'] != '不保存'


def test_avatar_migration_keeps_existing_users(tmp_path):
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    engine = create_engine('sqlite:///' + (tmp_path / 'avatar.db').as_posix())
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0008')
        connection.execute(User.__table__.insert().values(
            id='legacy', email='legacy-avatar@example.test', name='老用户'))
        command.upgrade(config, '0009')
        users = Table('users', MetaData(), autoload_with=connection)
        name, avatar = connection.execute(select(users.c.name, users.c.avatar)).one()
        assert name == '老用户'
        assert avatar is None
    engine.dispose()
